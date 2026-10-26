#!/usr/bin/env python3
"""
Manual tweets.json helper (paper research only). Not the live Social Tap.

Panel v59 Social Tap is viral.py (coin-relevant public viral only) at
GET /api/tweets. This file does not feed the panel. Do not invent posts.
Do NOT scrape x.com.

  python3 tweets.py --once    rewrite tweets.json from the old built-in seed
  python3 tweets.py --merge   merge a posts JSON file into tweets.json

posts.json shape for --merge:
  { "posts": [ {id, username, name, text, url, created_at, metrics, kind?, meme_note?} ] }
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent
TWEETS_PATH = DIR / "tweets.json"
MAX_POSTS = 30

ACCOUNTS = {
    "elonmusk": {
        "name": "Elon Musk",
        "avatar": "https://pbs.twimg.com/profile_images/2103567689515954176/aR-Kjpp4_normal.jpg",
    },
    "realDonaldTrump": {
        "name": "Donald J. Trump",
        "avatar": "https://pbs.twimg.com/profile_images/874276197357596672/kUuht00m_normal.jpg",
    },
}

# Fallback seed (known posts from 2026-10-24/25). Prefer live overwrite by parent.
SEED_POSTS = [
    {
        "id": "2114170724130734195",
        "username": "elonmusk",
        "text": "RT @Austen: I remember sitting around a campfire shortly after the Ukraine war had just started with some Ukrainian friends.\n\nThey shared t…",
        "url": "https://x.com/elonmusk/status/2114170724130734195",
        "created_at": "2026-10-25T01:42:34Z",
        "metrics": {"like_count": 0, "retweet_count": 221, "reply_count": 0, "impression_count": 332901},
        "kind": "retweet",
        "meme_note": "RT — usually lower meme spark than originals",
    },
    {
        "id": "2114169506784260471",
        "username": "elonmusk",
        "text": "Wild times\nhttps://t.co/952dIb7LA8",
        "url": "https://x.com/elonmusk/status/2114169506784260471",
        "created_at": "2026-10-25T01:37:44Z",
        "metrics": {"like_count": 1829, "retweet_count": 214, "reply_count": 485, "impression_count": 474758},
        "kind": "original",
        "meme_note": "Elon original — watch for ticker/meme follow-ons",
    },
    {
        "id": "2114167682283036687",
        "username": "elonmusk",
        "text": "They are channeling Beria https://t.co/oqaYxQVvbS https://t.co/yihXjA6uew",
        "url": "https://x.com/elonmusk/status/2114167682283036687",
        "created_at": "2026-10-25T01:30:29Z",
        "metrics": {"like_count": 6382, "retweet_count": 895, "reply_count": 598, "impression_count": 520574},
        "kind": "quote",
        "meme_note": "Elon quote — political angle, weaker meme spark",
    },
    {
        "id": "2114119209546342910",
        "username": "realDonaldTrump",
        "text": "[media]",
        "url": "https://x.com/realDonaldTrump/status/2114119209546342910",
        "created_at": "2026-10-24T22:17:52Z",
        "metrics": {"like_count": 7629, "retweet_count": 1637, "reply_count": 803, "impression_count": 1022668},
        "kind": "original",
        "meme_note": "Media-heavy — check image/video for meme bait",
    },
    {
        "id": "2113995902880780744",
        "username": "realDonaldTrump",
        "text": "🚨 NEW JERSEY!\n\nEarly Voting, in-person, is UNDERWAY NOW in the Great State of New Jersey. If you have a mail-in ballot, return it immediately and, if not, GO VOTE at the Polls RIGHT NOW, and get everyone you know to VOTE FOR THE REPUBLICAN IN EVERY RACE, UP AND DOWN THE BALLOT!",
        "url": "https://x.com/realDonaldTrump/status/2113995902880780744",
        "created_at": "2026-10-24T14:07:54Z",
        "metrics": {"like_count": 10053, "retweet_count": 2448, "reply_count": 1172, "impression_count": 1354748},
        "kind": "original",
        "meme_note": "Keyword hint: trump, vote",
    },
]

# Notes that are empty or too generic — never let these wipe a better existing note.
_GENERIC_NOTE_FRAGMENTS = (
    "low meme relevance",
    "low meme spark",
    "no meme",
    "n/a",
    "none",
    "political, low meme",
    "politics, low meme",
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_created(s: str | None) -> str:
    if not s:
        return _utc()
    s = str(s).strip()
    if s.endswith(".000Z"):
        return s.replace(".000Z", "Z")
    if s.endswith("Z"):
        return s
    return s


def _note_quality(note: str | None) -> int:
    """Higher = better / more specific. Empty/generic score 0."""
    n = (note or "").strip()
    if not n:
        return 0
    low = n.lower()
    if any(frag in low for frag in _GENERIC_NOTE_FRAGMENTS):
        return 1  # weak but non-empty
    return 10 + min(len(n), 80)  # prefer longer specific notes


def normalize_post(p: dict) -> dict | None:
    if not isinstance(p, dict):
        return None
    pid = str(p.get("id") or "").strip()
    username = str(p.get("username") or "").lstrip("@").strip()
    if not pid or not username:
        return None
    acct = ACCOUNTS.get(username, {})
    metrics = p.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    like = metrics.get("like_count", p.get("like_count", 0))
    rt = metrics.get("retweet_count", metrics.get("repost_count", p.get("retweet_count", 0)))
    reply = metrics.get("reply_count", p.get("reply_count", 0))
    impr = metrics.get("impression_count", p.get("impression_count", 0))
    kind = str(p.get("kind") or "original")
    if kind not in ("original", "quote", "retweet", "reply"):
        kind = "original"
    text = p.get("text")
    if text is None:
        text = ""
    url = p.get("url") or f"https://x.com/{username}/status/{pid}"
    note_raw = p.get("meme_note")
    if note_raw is None:
        note_raw = p.get("note") or ""
    return {
        "id": pid,
        "username": username,
        "name": p.get("name") or acct.get("name") or username,
        "avatar": p.get("avatar") or acct.get("avatar") or "",
        "text": str(text),
        "url": url,
        "created_at": _norm_created(p.get("created_at")),
        "metrics": {
            "like_count": int(like or 0),
            "retweet_count": int(rt or 0),
            "reply_count": int(reply or 0),
            "impression_count": int(impr or 0),
        },
        "kind": kind,
        "meme_note": str(note_raw or "").strip(),
    }


def load_feed(path: Path = TWEETS_PATH) -> dict:
    if not path.exists():
        return {
            "updated_at": None,
            "accounts": list(ACCOUNTS.keys()),
            "posts": [],
        }
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"updated_at": None, "accounts": list(ACCOUNTS.keys()), "posts": []}
        return data
    except (OSError, json.JSONDecodeError):
        return {"updated_at": None, "accounts": list(ACCOUNTS.keys()), "posts": []}


def write_feed(posts: list[dict], path: Path = TWEETS_PATH, source: str | None = None) -> dict:
    cleaned: list[dict] = []
    seen: set[str] = set()
    for raw in posts:
        n = normalize_post(raw)
        if not n or n["id"] in seen:
            continue
        seen.add(n["id"])
        cleaned.append(n)
    cleaned.sort(key=lambda p: p["created_at"], reverse=True)
    cleaned = cleaned[:MAX_POSTS]
    out = {
        "updated_at": _utc(),
        "accounts": list(ACCOUNTS.keys()),
        "source": source or "tweets.py",
        "disclaimer": "Paper research only. Not financial advice.",
        "posts": cleaned,
    }
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _merge_one(prev: dict | None, incoming: dict) -> dict:
    """Merge two normalized posts with same id; preserve better meme_note."""
    if not prev:
        return incoming
    out = dict(incoming)
    # Keep richer text if incoming blanked it
    if not (out.get("text") or "").strip() and (prev.get("text") or "").strip():
        out["text"] = prev["text"]
    # Sticky meme notes: empty/generic never wipe a better existing note
    prev_note = prev.get("meme_note") or ""
    new_note = out.get("meme_note") or ""
    pq = _note_quality(prev_note)
    iq = _note_quality(new_note)
    if iq == 0:
        out["meme_note"] = prev_note
    elif iq <= 1 and pq > 1:
        out["meme_note"] = prev_note  # generic cannot replace specific
    elif pq > iq:
        out["meme_note"] = prev_note
    else:
        out["meme_note"] = new_note
    # Prefer non-empty avatar/name from either
    if not out.get("avatar") and prev.get("avatar"):
        out["avatar"] = prev["avatar"]
    if (not out.get("name") or out["name"] == out.get("username")) and prev.get("name"):
        out["name"] = prev["name"]
    # Keep higher engagement metrics
    pm = prev.get("metrics") or {}
    om = out.get("metrics") or {}
    out["metrics"] = {
        "like_count": max(int(pm.get("like_count") or 0), int(om.get("like_count") or 0)),
        "retweet_count": max(int(pm.get("retweet_count") or 0), int(om.get("retweet_count") or 0)),
        "reply_count": max(int(pm.get("reply_count") or 0), int(om.get("reply_count") or 0)),
        "impression_count": max(
            int(pm.get("impression_count") or 0), int(om.get("impression_count") or 0)
        ),
    }
    return out


def merge_posts(existing: list[dict], incoming: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    # Existing first so we have prev notes, then merge incoming on top without wiping notes
    for p in existing:
        n = normalize_post(p)
        if not n:
            continue
        by_id[n["id"]] = n
    for p in incoming:
        n = normalize_post(p)
        if not n:
            continue
        by_id[n["id"]] = _merge_one(by_id.get(n["id"]), n)
    merged = list(by_id.values())
    merged.sort(key=lambda p: p["created_at"], reverse=True)
    return merged[:MAX_POSTS]


def cmd_once() -> int:
    """Rewrite tweets.json from built-in seed (demo / offline)."""
    out = write_feed(SEED_POSTS, source="tweets.py --once (built-in seed; parent routine should overwrite for live)")
    print(f"wrote {TWEETS_PATH} posts={len(out['posts'])} updated_at={out['updated_at']}")
    print("Note: no live X fetch here. Parent 'Elon Trump tweet alerts' routine should refresh tweets.json.")
    return 0


def cmd_merge(src: Path) -> int:
    feed = load_feed()
    try:
        with src.open("r", encoding="utf-8") as f:
            incoming = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"failed to read {src}: {e}", file=sys.stderr)
        return 1
    posts_in = incoming.get("posts") if isinstance(incoming, dict) else incoming
    if not isinstance(posts_in, list):
        print("merge file must be a list or {posts: [...]}", file=sys.stderr)
        return 1
    merged = merge_posts(feed.get("posts") or [], posts_in)
    out = write_feed(merged, source=f"tweets.py --merge {src.name}")
    print(f"merged -> {TWEETS_PATH} posts={len(out['posts'])} updated_at={out['updated_at']}")
    return 0


def cmd_show() -> int:
    feed = load_feed()
    posts = feed.get("posts") or []
    print(f"updated_at={feed.get('updated_at')} posts={len(posts)}")
    for p in posts[:8]:
        print(f"  @{p.get('username')} {p.get('created_at')} {p.get('kind')} — {(p.get('text') or '')[:60]!r}")
        if p.get("meme_note"):
            print(f"    note: {p['meme_note'][:80]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Nunu viral tweet feed (paper research)")
    ap.add_argument("--once", action="store_true", help="Rewrite tweets.json from built-in seed")
    ap.add_argument("--merge", metavar="FILE", help="Merge posts from JSON file into tweets.json")
    ap.add_argument("--show", action="store_true", help="Print current feed summary")
    args = ap.parse_args(argv)
    if args.merge:
        return cmd_merge(Path(args.merge))
    if args.once:
        return cmd_once()
    if args.show:
        return cmd_show()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
