#!/usr/bin/env python3
"""X Tracker — read-only watchlist and feed for the Nunu panel.

The owner manages handles (seed @slingoorio). The list is stored in
x_watchlist.json on the server so it survives a reload and a process
restart. Posts come from the X API with X_BEARER_TOKEN. If that variable
is unset, the feed is empty and says "X not configured". Nothing is invented.

Read only: no posting, no DMs, no trades.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DIR = Path(__file__).resolve().parent
SEED_HANDLE = "slingoorio"
MAX_HANDLES = 25
# Server refetch bound. The panel polls faster than this (see X_POLL_MS).
# Cache age + poll stays inside 2 minutes.
REFRESH_SEC = 60
API_ROOT = "https://api.x.com/2/"

BAD_HANDLE_ERROR = (
    "Bad handle. Use @name — letters, numbers, and underscore, up to 15 characters."
)

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_CA_RE = re.compile(
    r"(?<![1-9A-HJ-NP-Za-km-z])([1-9A-HJ-NP-Za-km-z]{32,44})(?![1-9A-HJ-NP-Za-km-z])"
)
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9$])\$([A-Za-z][A-Za-z0-9]{0,14})(?![A-Za-z0-9])")

_lock = threading.Lock()
_cache_lock = threading.Lock()
_feed_cache: dict[str, Any] | None = None
_clock: Callable[[], float] = time.monotonic
_transport: Callable[[str, dict[str, str]], tuple[int, Any]] | None = None


def bearer_token() -> str:
    return (os.environ.get("X_BEARER_TOKEN") or "").strip()


def watchlist_path() -> Path:
    override = (os.environ.get("X_WATCHLIST_PATH") or "").strip()
    if override:
        return Path(override)
    return DIR / "x_watchlist.json"


def set_transport(fn: Callable[[str, dict[str, str]], tuple[int, Any]] | None) -> None:
    """Test hook. Replaces the X HTTP call. Production leaves this unset."""
    global _transport
    _transport = fn


def set_clock(fn: Callable[[], float] | None) -> None:
    """Test hook for the feed cache clock. None restores time.monotonic."""
    global _clock
    _clock = fn or time.monotonic


def expire_feed_cache() -> None:
    global _feed_cache
    with _cache_lock:
        _feed_cache = None


def clear_memory() -> None:
    """Drop the in-process feed cache. The watchlist file is left as-is."""
    expire_feed_cache()


def normalize_handle(raw: str | None) -> str | None:
    text = str(raw or "").strip()
    if text.startswith("@"):
        text = text[1:].strip()
    if not _HANDLE_RE.fullmatch(text):
        return None
    return text.lower()


def annotate(text: str) -> dict[str, Any]:
    """Solana CAs and $TICKER marks. Highlight is true when either is present."""
    raw = text or ""
    cas: list[str] = []
    seen_ca: set[str] = set()
    for match in _CA_RE.finditer(raw):
        ca = match.group(1)
        if ca in seen_ca:
            continue
        seen_ca.add(ca)
        cas.append(ca)
    tickers: list[str] = []
    seen_t: set[str] = set()
    for match in _TICKER_RE.finditer(raw):
        ticker = match.group(1).upper()
        if ticker in seen_t:
            continue
        seen_t.add(ticker)
        tickers.append(ticker)
    return {
        "cas": cas,
        "tickers": tickers,
        "highlight": bool(cas or tickers),
    }


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_unlocked(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    raw = data.get("handles") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        norm = normalize_handle(str(item) if item is not None else "")
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _save_unlocked(handles: list[str]) -> None:
    path = watchlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"handles": list(handles), "updated_at": _utc()}
    blob = json.dumps(payload, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(blob, encoding="utf-8")
    tmp.replace(path)


def _load_unlocked() -> list[str]:
    path = watchlist_path()
    if not path.exists():
        _save_unlocked([SEED_HANDLE])
        return [SEED_HANDLE]
    return _read_unlocked(path)


def load_handles() -> list[str]:
    with _lock:
        return _load_unlocked()


def watchlist_payload() -> dict[str, Any]:
    handles = load_handles()
    updated = None
    path = watchlist_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            updated = data.get("updated_at")
    except (OSError, json.JSONDecodeError, UnicodeError):
        updated = None
    return {
        "ok": True,
        "feature": "x_tracker",
        "handles": [{"handle": handle} for handle in handles],
        "seed": SEED_HANDLE,
        "updated_at": updated,
        "max_handles": MAX_HANDLES,
    }


def _problem(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    blob = json.dumps(errors).lower()
    if "suspend" in blob:
        return "suspended"
    if "not found" in blob or "could not find user" in blob or "resource-not-found" in blob:
        return "missing"
    return "other"


def _handle_error(handle: str, kind: str | None, code: int) -> dict[str, Any]:
    if kind == "missing" or code == 404:
        return {
            "ok": False,
            "error": f"No X account for @{handle}.",
            "status": 400,
            "handle": handle,
        }
    if kind == "suspended":
        return {
            "ok": False,
            "error": f"@{handle} is suspended on X.",
            "status": 400,
            "handle": handle,
        }
    if code in (401, 403):
        return {
            "ok": False,
            "error": "X rejected the bearer token.",
            "status": 502,
            "handle": handle,
        }
    if code == 429:
        return {
            "ok": False,
            "error": "X rate limit. Try that handle again in a minute.",
            "status": 429,
            "handle": handle,
        }
    return {
        "ok": False,
        "error": f"Could not check @{handle} on X. Nothing was added.",
        "status": 502,
        "handle": handle,
    }


def _x_get(path: str, params: dict[str, str]) -> tuple[int, Any]:
    if _transport is not None:
        return _transport(path, params)
    token = bearer_token()
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value})
    url = API_ROOT + path.lstrip("/")
    if query:
        url += "?" + query
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "nunu-filter",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        parsed: Any = None
        try:
            raw = exc.read().decode("utf-8")
            parsed = json.loads(raw) if raw else None
        except (OSError, json.JSONDecodeError, UnicodeError):
            parsed = None
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, UnicodeError):
        return 0, None


def lookup_user(handle: str) -> dict[str, Any]:
    code, body = _x_get(
        "users/by/username/" + urllib.parse.quote(handle),
        {"user.fields": "id,name,username,profile_image_url"},
    )
    data = body.get("data") if isinstance(body, dict) else None
    if code == 200 and isinstance(data, dict) and data.get("id") and data.get("username"):
        return {
            "ok": True,
            "id": str(data["id"]),
            "username": str(data["username"]),
            "name": str(data.get("name") or ""),
            "avatar": str(data.get("profile_image_url") or ""),
        }
    return _handle_error(handle, _problem(body), code)


def add_handle(raw: str) -> dict[str, Any]:
    norm = normalize_handle(raw)
    if not norm:
        return {"ok": False, "error": BAD_HANDLE_ERROR, "status": 400}
    canon = norm
    if bearer_token():
        checked = lookup_user(norm)
        if not checked.get("ok"):
            return checked
        canon = normalize_handle(checked.get("username") or norm) or norm
    with _lock:
        handles = _load_unlocked()
        if canon in handles:
            return {"ok": True, "handle": canon, "already": True, "handles": handles}
        if len(handles) >= MAX_HANDLES:
            return {
                "ok": False,
                "error": f"Watchlist is full ({MAX_HANDLES} handles). Remove one first.",
                "status": 400,
                "handles": handles,
            }
        handles.append(canon)
        _save_unlocked(handles)
        saved = list(handles)
    expire_feed_cache()
    return {"ok": True, "handle": canon, "already": False, "handles": saved}


def remove_handle(raw: str) -> dict[str, Any]:
    norm = normalize_handle(raw)
    if not norm:
        return {"ok": False, "error": BAD_HANDLE_ERROR, "status": 400}
    with _lock:
        handles = _load_unlocked()
        if norm not in handles:
            return {
                "ok": False,
                "error": f"@{norm} is not on the watchlist.",
                "status": 400,
                "handles": handles,
            }
        handles = [handle for handle in handles if handle != norm]
        _save_unlocked(handles)
        saved = list(handles)
    expire_feed_cache()
    return {"ok": True, "handle": norm, "removed": True, "handles": saved}


def _unconfigured(handles: list[str]) -> dict[str, Any]:
    return {
        "ok": True,
        "feature": "x_tracker",
        "configured": False,
        "error": "X not configured",
        "detail": "Set X_BEARER_TOKEN on the server. No posts are invented.",
        "refresh_sec": REFRESH_SEC,
        "handles": [
            {"handle": handle, "name": "", "avatar": "", "space": None, "error": None}
            for handle in handles
        ],
        "posts": [],
        "fetched_at": None,
        "stale": False,
    }


def _token_key() -> str:
    token = bearer_token()
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _cache_get(key: tuple) -> dict[str, Any] | None:
    with _cache_lock:
        cached = _feed_cache
        if not cached or cached.get("key") != key:
            return None
        age = _clock() - float(cached.get("at") or 0)
        if age >= REFRESH_SEC:
            return None
        payload = cached.get("payload")
    if not isinstance(payload, dict):
        return None
    return json.loads(json.dumps(payload))


def _cache_stale(key: tuple) -> dict[str, Any] | None:
    with _cache_lock:
        cached = _feed_cache
        if not cached or cached.get("key") != key:
            return None
        payload = cached.get("payload")
    if not isinstance(payload, dict):
        return None
    return json.loads(json.dumps(payload))


def _cache_put(key: tuple, payload: dict[str, Any]) -> None:
    global _feed_cache
    with _cache_lock:
        _feed_cache = {"key": key, "at": _clock(), "payload": payload}


def _metric(metrics: dict[str, Any], key: str) -> int | None:
    if key not in metrics:
        return None
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _created_key(value: str) -> str:
    raw = (value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    if "." in raw:
        raw = raw.split(".", 1)[0]
    return raw


def _texts_for(tweet: dict[str, Any], included: dict[str, dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    note = tweet.get("note_tweet")
    if isinstance(note, dict) and note.get("text"):
        parts.append(str(note["text"]))
    if tweet.get("text"):
        parts.append(str(tweet["text"]))
    refs = tweet.get("referenced_tweets")
    if isinstance(refs, list):
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            other = included.get(str(ref.get("id") or ""))
            if isinstance(other, dict) and other.get("text"):
                parts.append(str(other["text"]))
    return parts


def _posts_from(handle: str, user: dict[str, Any], body: Any) -> list[dict[str, Any]]:
    included: dict[str, dict[str, Any]] = {}
    if isinstance(body, dict):
        tweets = (body.get("includes") or {}).get("tweets") if isinstance(body.get("includes"), dict) else None
        if isinstance(tweets, list):
            for row in tweets:
                if isinstance(row, dict) and row.get("id"):
                    included[str(row["id"])] = row
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    username = str(user.get("username") or handle)
    posts: list[dict[str, Any]] = []
    for tweet in data:
        if not isinstance(tweet, dict) or not tweet.get("id"):
            continue
        parts = _texts_for(tweet, included)
        display = max(parts, key=len) if parts else ""
        marks = annotate("\n".join(parts))
        metrics = tweet.get("public_metrics") if isinstance(tweet.get("public_metrics"), dict) else {}
        posts.append(
            {
                "id": str(tweet["id"]),
                "handle": handle,
                "name": str(user.get("name") or ""),
                "text": display,
                "url": f"https://x.com/{username}/status/{tweet['id']}",
                "created_at": str(tweet.get("created_at") or ""),
                "metrics": {
                    "like_count": _metric(metrics, "like_count"),
                    "retweet_count": _metric(metrics, "retweet_count"),
                    "reply_count": _metric(metrics, "reply_count"),
                },
                "cas": marks["cas"],
                "tickers": marks["tickers"],
                "highlight": marks["highlight"],
            }
        )
    return posts


def _lookup_users(handles: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str], str | None]:
    code, body = _x_get(
        "users/by",
        {
            "usernames": ",".join(handles),
            "user.fields": "id,name,username,profile_image_url",
        },
    )
    if code in (401, 403):
        return {}, {}, "X rejected the bearer token."
    if code == 429:
        return {}, {}, "X rate limit. The feed will retry within 2 minutes."
    if code == 0 or not isinstance(body, dict):
        return {}, {}, "X did not respond. Nothing invented."
    users: dict[str, dict[str, Any]] = {}
    data = body.get("data")
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            username = normalize_handle(str(row.get("username") or ""))
            if not username:
                continue
            users[username] = {
                "id": str(row["id"]),
                "username": str(row.get("username") or username),
                "name": str(row.get("name") or ""),
                "avatar": str(row.get("profile_image_url") or ""),
            }
    errors: dict[str, str] = {}
    raw_errors = body.get("errors") if isinstance(body.get("errors"), list) else []
    for err in raw_errors:
        if not isinstance(err, dict):
            continue
        value = normalize_handle(str(err.get("value") or ""))
        if not value:
            continue
        kind = _problem({"errors": [err]})
        if kind == "suspended":
            errors[value] = f"@{value} is suspended on X."
        elif kind == "missing":
            errors[value] = f"No X account for @{value}."
        else:
            errors[value] = f"Could not load @{value} from X."
    if code != 200 and not users and not errors:
        return {}, {}, "X did not respond. Nothing invented."
    for handle in handles:
        if handle in users or handle in errors:
            continue
        if code == 200 or errors:
            errors[handle] = f"No X account for @{handle}."
        else:
            errors[handle] = f"Could not load @{handle} from X."
    return users, errors, None


def _live_space(user_id: str, spaces: list[dict[str, Any]]) -> dict[str, Any] | None:
    uid = str(user_id)
    for space in spaces:
        if str(space.get("state") or "").lower() != "live":
            continue
        hosts = {str(host) for host in (space.get("host_ids") or []) if host is not None}
        creator = str(space.get("creator_id") or "")
        if uid not in hosts and creator != uid:
            continue
        return {
            "live": True,
            "id": str(space.get("id") or ""),
            "title": str(space.get("title") or ""),
            "state": "live",
        }
    return None


def _fetch_spaces(user_ids: list[str]) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    code, body = _x_get(
        "spaces/by/creator_ids",
        {
            "user_ids": ",".join(user_ids),
            "space.fields": "title,state,host_ids,creator_id,started_at",
        },
    )
    if code != 200 or not isinstance(body, dict):
        return []
    data = body.get("data")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _empty_feed(handles: list[str], error: str | None) -> dict[str, Any]:
    return {
        "ok": True,
        "feature": "x_tracker",
        "configured": True,
        "error": error,
        "detail": None,
        "refresh_sec": REFRESH_SEC,
        "handles": [
            {"handle": handle, "name": "", "avatar": "", "space": None, "error": None}
            for handle in handles
        ],
        "posts": [],
        "fetched_at": None,
        "stale": False,
    }


def _build_feed(handles: list[str]) -> dict[str, Any]:
    users, per_handle, fatal = _lookup_users(handles)
    if fatal and not users:
        return _empty_feed(handles, fatal)
    rows: list[dict[str, Any]] = []
    posts: list[dict[str, Any]] = []
    id_to_row: dict[str, dict[str, Any]] = {}
    for handle in handles:
        user = users.get(handle)
        row: dict[str, Any] = {
            "handle": handle,
            "name": "",
            "avatar": "",
            "space": None,
            "error": per_handle.get(handle),
            "user_id": None,
        }
        if user:
            row["handle"] = normalize_handle(user.get("username") or handle) or handle
            row["name"] = user.get("name") or ""
            row["avatar"] = user.get("avatar") or ""
            row["user_id"] = user["id"]
            user_id = str(user["id"])
            if not user_id.isdigit():
                row["error"] = row["error"] or f"Could not load posts for @{row['handle']}."
            else:
                code, body = _x_get(
                    f"users/{user_id}/tweets",
                    {
                        "max_results": "10",
                        "tweet.fields": "created_at,public_metrics,note_tweet,referenced_tweets",
                        "expansions": "referenced_tweets.id",
                    },
                )
                if code == 200:
                    posts.extend(_posts_from(row["handle"], user, body))
                elif code == 429:
                    row["error"] = row["error"] or "X rate limit on posts for this handle."
                else:
                    # Zero posts is HTTP 200 with an empty data list. Anything else
                    # stays empty — posts are never invented to fill the gap.
                    row["error"] = row["error"] or f"Could not load posts for @{row['handle']}."
        rows.append(row)
        if row.get("user_id"):
            id_to_row[str(row["user_id"])] = row
    spaces = _fetch_spaces(list(id_to_row))
    for user_id, row in id_to_row.items():
        row["space"] = _live_space(user_id, spaces)
    posts.sort(key=lambda post: _created_key(str(post.get("created_at") or "")), reverse=True)
    public_rows = [
        {
            "handle": row["handle"],
            "name": row["name"],
            "avatar": row["avatar"],
            "space": row["space"],
            "error": row["error"],
        }
        for row in rows
    ]
    error = fatal
    if error is None and posts == []:
        row_errors = [row["error"] for row in public_rows if row.get("error")]
        if row_errors and len(row_errors) == len(public_rows):
            error = row_errors[0]
    return {
        "ok": True,
        "feature": "x_tracker",
        "configured": True,
        "error": error,
        "detail": None,
        "refresh_sec": REFRESH_SEC,
        "handles": public_rows,
        "posts": posts[:40],
        "fetched_at": _utc(),
        "stale": False,
    }


def feed_payload() -> dict[str, Any]:
    handles = load_handles()
    if not bearer_token():
        return _unconfigured(handles)
    if not handles:
        payload = _empty_feed([], "Watchlist is empty. Add an @handle.")
        payload["handles"] = []
        return payload
    key = (tuple(handles), _token_key())
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        payload = _build_feed(handles)
    except Exception:
        payload = _empty_feed(handles, "X did not respond. Nothing invented.")
    if payload.get("error") and not payload.get("posts"):
        stale = _cache_stale(key)
        if stale is not None and stale.get("posts"):
            stale["stale"] = True
            stale["error"] = payload.get("error")
            _cache_put(key, stale)
            return json.loads(json.dumps(stale))
    _cache_put(key, payload)
    return json.loads(json.dumps(payload))
