#!/usr/bin/env python3
"""Live Social Tap feed (panel v59).

Coin-relevant viral only: public posts that might make or move a coin
(memecoin, meme, crypto). Real sources only. Never invents posts, handles,
metrics, or timestamps. Unrelated politics, celebrity, sports, and weather
are dropped. Served at GET /api/tweets. tweets.json is not a fallback.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = "nunu-funcore-social-tap/59 (paper research; +https://nunu-funcore.onrender.com)"
MAX_POSTS = 16
MAX_AGE_SEC = 36 * 3600
FETCH_TIMEOUT = 4.0
CACHE_TTL_SEC = 120
FAIL_TTL_SEC = 45
# Social Tap stays with the filter feed loop (INTERVAL_MIN default 5).
# A cached payload older than this is not painted as current cards.
SOCIAL_TAP_FRESH_MIN = 5.0
SOCIAL_TAP_FRESH_SEC = SOCIAL_TAP_FRESH_MIN * 60.0
READ_LIMIT = 1_000_000

DISCLAIMER = (
    "Paper research only. Not financial advice. "
    "Items are copied from the named public source. "
    "Coin-relevant viral only. Nothing invented."
)

# Buckets. The open pane picks coin items ahead of memes so the first screen
# stays about coins; a meme still appears once coins have the majority.
_KIND_ORDER = ("coin", "meme", "trend")
_PICK_CYCLE = ("coin", "coin", "coin", "meme", "trend")

# policy "keep": crypto community; a copied post is already about coins.
# policy "coin": keep a row only when the copied text is coin/meme/crypto relevant.
# r/memes uses policy "coin" so a random joke with no coin or meme wording is dropped.
_SOURCES = (
    {
        "id": "google_news_coin",
        "label": "Google News",
        "kind": "coin",
        "policy": "coin",
        "url": (
            "https://news.google.com/rss/search?q="
            "%22memecoin%22+OR+%22meme+coin%22+OR+%22meme+coins%22+OR+pump.fun+OR+memecoins"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    },
    {
        "id": "cointelegraph",
        "label": "Cointelegraph",
        "kind": "coin",
        "policy": "coin",
        "url": "https://cointelegraph.com/rss",
    },
    {
        "id": "reddit_memecoins",
        "label": "r/memecoins",
        "kind": "coin",
        "policy": "keep",
        "url": "https://www.reddit.com/r/memecoins/hot/.rss?limit=12",
    },
    {
        "id": "reddit_memes",
        "label": "r/memes",
        "kind": "meme",
        "policy": "coin",
        "url": "https://www.reddit.com/r/memes/hot/.rss?limit=12",
    },
    {
        "id": "google_trends",
        "label": "Google Trends",
        "kind": "trend",
        "policy": "coin",
        "url": "https://trends.google.com/trending/rss?geo=US",
    },
)

# Coin / meme-coin / crypto wording. Bare "coin" is allowed except toss/gold/slot noise.
_COIN_RE = re.compile(
    r"("
    r"\b(?:"
    r"memecoins?|meme[\s-]?coins?|memes?|"
    r"cryptos?|cryptocurrenc(?:y|ies)|"
    r"bitcoins?|btc|ethereum|ether|"
    r"solana|altcoins?|"
    r"defi|nfts?|web3|blockchains?|"
    r"dogecoins?|doge|shiba|shib|"
    r"pepe|bonk|dogwifhat|"
    r"airdrops?|stablecoins?|usdt|usdc|"
    r"binance|coinbase|"
    r"satoshi|hodl|degen|"
    r"token(?:s|ized|isation|ization)?|"
    r"coins?"
    r")\b|"
    r"pump\.fun|pumpfun|"
    r"\$(?:sol|btc|eth|doge|pepe|bonk|wif)\b"
    r")",
    re.IGNORECASE,
)
_COIN_NOISE_RE = re.compile(
    r"\b(?:coin toss(?:es)?|gold coins?|silver coins?|challenge coins?|coin slots?|coin[\s-]operated)\b",
    re.IGNORECASE,
)

# Sticky megathreads are not viral. Dropped, never rewritten.
_MEGATHREAD_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:daily|weekly|monthly|bi-?weekly)\b.{0,24}\b(?:discussion|thread|general)\b|"
    r"\b(?:discussion|megathread)\b.{0,24}\b(?:daily|weekly|monthly)\b|"
    r"^\[(?:daily|weekly|megathread)\]"
    r")"
)

_lock = threading.Lock()
_cache: dict = {"payload": None, "at": 0.0, "until": 0.0, "refreshing": False}


def _utc(dt: datetime | None = None) -> str:
    when = dt or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> float:
    return time.time()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag else ""


def _child_text(el: ET.Element, name: str) -> str:
    for child in list(el):
        if _local(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _to_iso(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        if text.endswith("Z") or "T" in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return _utc(dt)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _utc(dt)


def _age_ok(iso: str | None, now_dt: datetime, max_age_sec: int = MAX_AGE_SEC) -> bool:
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = (now_dt - dt.astimezone(timezone.utc)).total_seconds()
    return 0 <= age <= max_age_sec or -300 <= age <= max_age_sec


def _title_key(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


_EN_STOPS = frozenset(
    "the an of to for in on and as with at from by is are was were after before over into".split()
)


def _latin_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isascii()) / len(letters)


def _headline_score(title: str) -> float:
    words = re.findall(r"[A-Za-z']+", title.lower())
    stops = sum(1 for w in words if w in _EN_STOPS)
    score = _latin_ratio(title)
    if len(title) >= 12:
        score += 0.05
    if words:
        score += stops / len(words)
    return score


def _http_get(url: str, timeout: float = FETCH_TIMEOUT) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(READ_LIMIT)


def _parse_xml(raw: bytes) -> ET.Element:
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    return ET.fromstring(text)


def _pick_trend_news(item: ET.Element) -> ET.Element | None:
    news = [child for child in list(item) if _local(child.tag) == "news_item"]
    if not news:
        return None
    usable = []
    for node in news:
        title = _child_text(node, "news_item_title")
        url = _child_text(node, "news_item_url")
        if not title or not url:
            continue
        low = title.lower()
        if "watch live sport" in low or low.startswith("sports news, transfers"):
            continue
        usable.append(node)
    pool = usable or news
    best = None
    best_score = -1.0
    for node in pool:
        title = _child_text(node, "news_item_title")
        if not title:
            continue
        score = _headline_score(title)
        if score > best_score:
            best = node
            best_score = score
    return best


def parse_google_trends(raw: bytes, now_dt: datetime | None = None) -> list[dict]:
    now_dt = now_dt or datetime.now(timezone.utc)
    root = _parse_xml(raw)
    out: list[dict] = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        # Channel-level <item> only; news_item is a different local name.
        query = _child_text(item, "title")
        if not query:
            continue
        created = _to_iso(_child_text(item, "pubDate"))
        if not _age_ok(created, now_dt):
            continue
        news = _pick_trend_news(item)
        headline = _child_text(news, "news_item_title") if news is not None else ""
        news_url = _child_text(news, "news_item_url") if news is not None else ""
        news_source = _child_text(news, "news_item_source") if news is not None else ""
        traffic = ""
        for child in list(item):
            if _local(child.tag) == "approx_traffic" and child.text:
                traffic = child.text.strip()
                break
        url = news_url or (
            "https://trends.google.com/trends/explore?geo=US&q="
            + urllib.parse.quote(query)
        )
        text = headline if headline and _title_key(headline) != _title_key(query) else ""
        out.append(
            {
                "id": "trend:" + hashlib.sha1(query.lower().encode("utf-8")).hexdigest()[:16],
                "kind": "trend",
                "title": query,
                "text": text,
                "username": "Google Trends",
                "name": news_source or "Google Trends",
                "source": "google_trends",
                "source_label": news_source or "Google Trends",
                "via": "Google Trends",
                "url": url,
                "created_at": created,
                "traffic": traffic,
            }
        )
        if len(out) >= 8:
            break
    return out


def _atom_href(entry: ET.Element) -> str:
    fallback = ""
    for child in list(entry):
        if _local(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        if not href:
            continue
        rel = (child.get("rel") or "alternate").strip()
        if rel == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def parse_reddit_atom(
    raw: bytes,
    *,
    kind: str,
    source: str,
    label: str,
    now_dt: datetime | None = None,
    limit: int = 6,
) -> list[dict]:
    now_dt = now_dt or datetime.now(timezone.utc)
    root = _parse_xml(raw)
    posts: list[dict] = []
    for entry in root.iter():
        if _local(entry.tag) != "entry":
            continue
        title = _child_text(entry, "title")
        if not title or title.lower() in ("[deleted]", "[removed]"):
            continue
        author = ""
        for child in list(entry):
            if _local(child.tag) == "author":
                author = _child_text(child, "name")
                break
        handle = author.strip()
        if handle.lower().startswith("/u/"):
            handle = handle[3:]
        if handle.lower() in ("automoderator", "automod"):
            continue
        created = _to_iso(_child_text(entry, "published") or _child_text(entry, "updated"))
        if not _age_ok(created, now_dt):
            continue
        url = _atom_href(entry)
        if not url:
            continue
        rid = _child_text(entry, "id") or url
        posts.append(
            {
                "id": source + ":" + rid,
                "kind": kind,
                "title": title,
                "text": "",
                "username": handle,
                "name": label,
                "source": source,
                "source_label": label,
                "via": label,
                "url": url,
                "created_at": created,
                "traffic": "",
            }
        )
    posts.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return posts[:limit]


def parse_rss_channel(
    raw: bytes,
    *,
    kind: str,
    source: str,
    label: str,
    via: str,
    now_dt: datetime | None = None,
    limit: int = 6,
    strip_source_suffix: bool = False,
) -> list[dict]:
    now_dt = now_dt or datetime.now(timezone.utc)
    root = _parse_xml(raw)
    posts: list[dict] = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        if not title or not link:
            continue
        source_name = ""
        for child in list(item):
            if _local(child.tag) == "source" and child.text:
                source_name = child.text.strip()
                break
        if strip_source_suffix and source_name and title.endswith(" - " + source_name):
            title = title[: -(len(source_name) + 3)].strip()
        if not title:
            continue
        created = _to_iso(_child_text(item, "pubDate"))
        if not _age_ok(created, now_dt):
            continue
        shown = source_name or label
        posts.append(
            {
                "id": source + ":" + hashlib.sha1(link.encode("utf-8")).hexdigest()[:16],
                "kind": kind,
                "title": title,
                "text": "",
                "username": via,
                "name": shown,
                "source": source,
                "source_label": shown,
                "via": via,
                "url": link,
                "created_at": created,
                "traffic": "",
            }
        )
    posts.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return posts[:limit]


def parse_google_news(raw: bytes, now_dt: datetime | None = None, limit: int = 6) -> list[dict]:
    """Generic Google News RSS parser. Live Social Tap uses the memecoin query plus a coin filter."""
    return parse_rss_channel(
        raw,
        kind="event",
        source="google_news",
        label="Google News",
        via="Google News",
        now_dt=now_dt,
        limit=limit,
        strip_source_suffix=True,
    )


def coin_relevant_text(text: str | None) -> bool:
    """True when copied text is about a coin, memecoin, or crypto. Does not invent text."""
    if not text:
        return False
    if not _COIN_RE.search(text):
        return False
    # "coin toss" / "gold coin" match the bare coin word and nothing else.
    if _COIN_NOISE_RE.search(text):
        stripped = _COIN_NOISE_RE.sub(" ", text)
        return _COIN_RE.search(stripped) is not None
    return True


def _source_spec(source_id: str | None) -> dict | None:
    for spec in _SOURCES:
        if spec["id"] == source_id:
            return spec
    return None


def _accept_item(item: dict, policy: str) -> bool:
    """Keep a parsed row. policy 'keep' trusts a meme/crypto source. policy 'coin' requires coin text."""
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    if not title or not url:
        return False
    if _MEGATHREAD_RE.search(title):
        return False
    if policy == "keep":
        return True
    blob = title + "\n" + (item.get("text") or "")
    return coin_relevant_text(blob)


_PARSERS = {
    "google_news_coin": lambda raw, now: parse_rss_channel(
        raw,
        kind="coin",
        source="google_news_coin",
        label="Google News",
        via="Google News",
        now_dt=now,
        limit=6,
        strip_source_suffix=True,
    ),
    "cointelegraph": lambda raw, now: parse_rss_channel(
        raw,
        kind="coin",
        source="cointelegraph",
        label="Cointelegraph",
        via="Cointelegraph",
        now_dt=now,
        limit=6,
        strip_source_suffix=False,
    ),
    "reddit_memecoins": lambda raw, now: parse_reddit_atom(
        raw,
        kind="coin",
        source="reddit_memecoins",
        label="r/memecoins",
        now_dt=now,
        limit=6,
    ),
    "reddit_memes": lambda raw, now: parse_reddit_atom(
        raw, kind="meme", source="reddit_memes", label="r/memes", now_dt=now, limit=6
    ),
    "google_trends": lambda raw, now: parse_google_trends(raw, now),
}


def _empty_shell(
    error: str | None,
    sources: list[dict],
    updated_at: str | None,
    stale: bool,
    note: str | None = None,
) -> dict:
    return {
        "ok": not error and any(s.get("count") for s in sources),
        "feed": "viral_live",
        "bias": "coin_relevant",
        "live": True,
        "stale": stale,
        "updated_at": updated_at,
        "accounts": [],
        "sources": sources,
        "posts": [],
        "error": error,
        "note": note,
        "disclaimer": DISCLAIMER,
    }


def assemble(source_results: list[dict], *, updated_at: str | None = None, stale: bool = False) -> dict:
    """source_results: {id, label, ok, error, items}. Only parsed rows. Coin bias drops the rest."""
    spec_by_id = {s["id"]: s for s in _SOURCES}
    order = {s["id"]: i for i, s in enumerate(_SOURCES)}
    rows = sorted(source_results, key=lambda r: order.get(r.get("id"), 99))

    per_kind: dict[str, list[list[dict]]] = {k: [] for k in _KIND_ORDER}
    sources_out = []
    for row in rows:
        items = row.get("items") if row.get("ok") else []
        if not isinstance(items, list):
            items = []
        spec = spec_by_id.get(row.get("id"))
        kept: list[dict] = []
        if spec:
            policy = spec.get("policy") or "coin"
            kind = spec.get("kind")
            if kind:
                accepted = [
                    item
                    for item in items
                    if isinstance(item, dict) and _accept_item(item, policy)
                ]
                # Lexicon matches lead. Newer rows stay in order within each group.
                accepted.sort(
                    key=lambda item: 0
                    if coin_relevant_text((item.get("title") or "") + "\n" + (item.get("text") or ""))
                    else 1
                )
                kept = accepted[:4]
                if kept:
                    per_kind.setdefault(kind, []).append(kept)
        sources_out.append(
            {
                "id": row.get("id"),
                "label": row.get("label") or (spec or {}).get("label"),
                "ok": bool(row.get("ok")),
                "count": len(kept) if row.get("ok") else 0,
                "error": None if row.get("ok") else (row.get("error") or "failed"),
            }
        )

    buckets: dict[str, list[dict]] = {}
    for kind in _KIND_ORDER:
        queues = per_kind.get(kind) or []
        merged: list[dict] = []
        while len(merged) < 8 and any(queues):
            for queue in queues:
                if not queue:
                    continue
                merged.append(queue.pop(0))
                if len(merged) >= 8:
                    break
        buckets[kind] = merged

    seen: set[str] = set()
    posts: list[dict] = []
    idx = {k: 0 for k in _KIND_ORDER}
    while len(posts) < MAX_POSTS:
        progressed = False
        for kind in _PICK_CYCLE:
            i = idx[kind]
            bucket = buckets.get(kind) or []
            while i < len(bucket):
                item = bucket[i]
                i += 1
                key = _title_key(item.get("title") or "")
                if not key or key in seen:
                    continue
                if not item.get("url") or not item.get("title"):
                    continue
                seen.add(key)
                posts.append(item)
                progressed = True
                break
            idx[kind] = i
            if len(posts) >= MAX_POSTS:
                break
        if not progressed:
            break

    updated = updated_at or _utc()
    if not posts:
        failed = [f"{s['label']}: {s['error']}" for s in sources_out if not s["ok"] and s.get("label")]
        if failed and all(not s["ok"] for s in sources_out):
            error = "Live sources failed. " + "; ".join(failed)
            note = None
        else:
            # Sources answered, but nothing copied was coin/meme/crypto viral.
            error = None
            note = "No coin-relevant viral from public sources right now. Nothing invented."
            if failed:
                note += " Some sources unavailable."
        shell = _empty_shell(error, sources_out, updated, stale, note)
        shell["ok"] = False
        return shell

    return {
        "ok": True,
        "feed": "viral_live",
        "bias": "coin_relevant",
        "live": True,
        "stale": stale,
        "updated_at": updated,
        "accounts": [],
        "sources": sources_out,
        "posts": posts,
        "error": None,
        "note": None,
        "disclaimer": DISCLAIMER,
    }


def _fetch_one(spec: dict, fetch, now_dt: datetime) -> dict:
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            raw = fetch(spec["url"])
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            parser = _PARSERS[spec["id"]]
            items = parser(raw, now_dt)
            return {
                "id": spec["id"],
                "label": spec["label"],
                "ok": True,
                "error": None,
                "items": items,
            }
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429 and attempt == 1:
                time.sleep(0.7)
                continue
            break
        except Exception as exc:  # noqa: BLE001 — one source must not blank the others
            last_exc = exc
            break
    msg = str(last_exc).strip() if last_exc else "failed"
    if len(msg) > 180:
        msg = msg[:177] + "..."
    return {
        "id": spec["id"],
        "label": spec["label"],
        "ok": False,
        "error": msg or "failed",
        "items": [],
    }


def collect(fetch=None, now_dt: datetime | None = None) -> dict:
    fetch = fetch or _http_get
    now_dt = now_dt or datetime.now(timezone.utc)
    from concurrent.futures import ThreadPoolExecutor

    results = []
    with ThreadPoolExecutor(max_workers=len(_SOURCES)) as pool:
        futs = [pool.submit(_fetch_one, spec, fetch, now_dt) for spec in _SOURCES]
        for fut in futs:
            results.append(fut.result())
    return assemble(results, updated_at=_utc(now_dt), stale=False)


def reset_cache() -> None:
    with _lock:
        _cache["payload"] = None
        _cache["at"] = 0.0
        _cache["until"] = 0.0
        _cache["refreshing"] = False


def _store(payload: dict, ttl: float, now: float) -> None:
    _cache["payload"] = payload
    _cache["at"] = now
    _cache["until"] = now + ttl


def _refresh(fetch, now: float) -> dict:
    payload = collect(fetch=fetch)
    ttl = CACHE_TTL_SEC if payload.get("posts") else FAIL_TTL_SEC
    with _lock:
        _store(payload, ttl, now)
        _cache["refreshing"] = False
    return payload


def _refresh_bg(fetch) -> None:
    try:
        _refresh(fetch, _now())
    except Exception:
        with _lock:
            _cache["refreshing"] = False
            if _cache["payload"] is not None:
                _cache["until"] = _now() + FAIL_TTL_SEC


_PAST_LOOP_NOTE = (
    "Social Tap is past the 5-min feed loop. Nothing invented."
)


def present_feed(payload: dict, *, age_sec: float, refreshing: bool = False) -> dict:
    """Public Social Tap body.

    Inside the 5-min loop the copied posts are returned as-is.
    Past that loop the old cards are omitted so an idle cache cannot
    sit on screen as current chrome. Nothing is invented to fill the gap.
    """
    out = dict(payload)
    posts = payload.get("posts") if isinstance(payload.get("posts"), list) else []
    out["fresh_sec"] = SOCIAL_TAP_FRESH_SEC
    out["feed_loop_min"] = SOCIAL_TAP_FRESH_MIN
    out["refreshing"] = bool(refreshing)
    over = age_sec > SOCIAL_TAP_FRESH_SEC or bool(payload.get("stale"))
    if over:
        out["stale"] = True
        out["posts"] = []
        out["ok"] = False
        if refreshing and not payload.get("error"):
            out["note"] = "Refreshing Social Tap…"
            out["error"] = None
        elif not payload.get("error"):
            out["note"] = _PAST_LOOP_NOTE
        return out
    out["stale"] = False
    out["posts"] = list(posts)
    return out


def feed_payload(*, fetch=None, now: float | None = None, use_cache: bool = True) -> dict:
    """Cached live feed. Failed sources never fall back to tweets.json.

    A cache older than SOCIAL_TAP_FRESH_SEC is not served as live cards.
    """
    fetch = fetch or _http_get
    if not use_cache:
        return present_feed(collect(fetch=fetch), age_sec=0.0)

    clock = _now() if now is None else now
    with _lock:
        payload = _cache["payload"]
        age = clock - _cache["at"] if payload else 1e12
        age = max(0.0, age)
        fresh = payload is not None and clock < _cache["until"]
        if fresh:
            return present_feed(payload, age_sec=age)
        has_posts = bool(payload and payload.get("posts"))
        if has_posts and age <= SOCIAL_TAP_FRESH_SEC:
            if not _cache["refreshing"]:
                _cache["refreshing"] = True
                threading.Thread(target=_refresh_bg, args=(fetch,), daemon=True).start()
            return present_feed(payload, age_sec=age)
        if _cache["refreshing"] and payload is not None:
            # In flight. Past the loop, withhold the idle cards.
            return present_feed(
                payload,
                age_sec=age,
                refreshing=age > SOCIAL_TAP_FRESH_SEC,
            )

    try:
        return present_feed(_refresh(fetch, clock if now is not None else _now()), age_sec=0.0)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).strip() or "live fetch failed"
        failed = assemble(
            [
                {"id": s["id"], "label": s["label"], "ok": False, "error": msg, "items": []}
                for s in _SOURCES
            ],
            updated_at=_utc(),
            stale=False,
        )
        with _lock:
            _store(failed, FAIL_TTL_SEC, _now())
            _cache["refreshing"] = False
        return present_feed(failed, age_sec=0.0)
