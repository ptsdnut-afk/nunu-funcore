#!/usr/bin/env python3
"""
Nunu Solana memecoin filter (paper research only — no trading).

Core layer: pulls newest/boosted Solana tokens from DEX Screener public APIs,
optionally enriches with RugCheck public summary, scores them, and
writes flagged.json + latest.json (+ runs.log) for the Flask panel.

Use bot.py for a timed loop; server.py only reads the JSON files (no import).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import game as game_mod
from mcap_progress import apply_mcap
from research import research_card

# ---------------------------------------------------------------------------
# Tunable scoring constants (edit these; re-run filter.py)
# ---------------------------------------------------------------------------

MIN_LIQUIDITY_USD = 15_000.0          # hard floor — below = never flag
MIN_VOLUME_H1_USD = 5_000.0           # hard floor on 1h volume
MAX_PAIR_AGE_HOURS = 72.0             # only consider relatively new pairs
MIN_SCORE_TO_FLAG = 55.0              # total score threshold to appear in panel

# Score weights (should roughly sum to 100 for a "perfect" token)
W_LIQUIDITY = 30.0
W_VOLUME = 25.0
W_HOLDERS = 25.0                      # RugCheck proxy (LP lock + risk score)
W_SOCIALS = 20.0

# Liquidity scoring curve (USD → partial credit toward W_LIQUIDITY)
LIQ_GOOD = 80_000.0                   # full liquidity points
LIQ_OK = 30_000.0

# Volume h1 scoring curve
VOL_GOOD = 100_000.0
VOL_OK = 20_000.0

# RugCheck: lower score_normalised is safer; lpLockedPct higher is better
RUG_SCORE_SAFE_MAX = 200              # above this = heavy penalty
RUG_LP_LOCK_GOOD = 90.0               # % LP locked for full holder points

# Run-up / late-chase heuristic (tunable)
LATE_CHASE_MIN_AGE_H = 2.0
LATE_CHASE_MIN_H1_PCT = 100.0

# Sitting-time: score considered "same band" within this delta
SCORE_STABLE_DELTA = 1.0
FLAG_HISTORY_TTL_H = 24.0

# Sitting window. Pair age stays MAX_PAIR_AGE_HOURS (72h).
# SHIP S: SITTING_DROP_MIN is a Sitting chrome mark, not a hard-drop.
# A flagged coin with sitting_minutes > 5.0 stays on Token Flow (band "over").
# SHIP F's "no silent forever-stale" bound is the Sitting label plus the
# existing flag gates — see apply_sitting_rule() and
# qa/ship-s-sitting-keep-rule.md. Nothing is removed for sitting time.
SITTING_DROP_MIN = 5.0
# Amber near-window cue while the coin is still inside the mark.
# 4.0m of a 5m mark reads near; 3.6m stays a normal row. Near rows stay listed.
SITTING_NEAR_LEAD_MIN = 1.0
# Green "fresh" sit. Display only — does not change who stays on the board.
SITTING_FRESH_MIN = 2.0
# Aged chrome (minutes). Past this, the row stays listed and the hint
# escalates to "aged · still listed". 180.0 stays a normal Sitting row;
# 180.1 is aged. This does not remove the coin.
SITTING_AGED_MIN = 180.0

# Rate limiting / fetch / cache (tuned for speed; fail soft)
HTTP_TIMEOUT_S = 8
SLEEP_BETWEEN_BATCH_S = 0.05
PAIR_BATCH_SIZE = 30                  # DEX Screener tokens/v1 batch size
PAIR_WORKERS = 3                      # parallel token-batch fetches
RUG_WORKERS = 6
RUG_SLEEP_S = 0.04
CACHE_DEX_TTL_S = 60                  # fresher lists
CACHE_RUG_TTL_S = 240                 # ~4 min
USER_AGENT = "nunu-filter/1.2 (paper research; contact: local)"

DIR = Path(__file__).resolve().parent
FLAGGED_PATH = DIR / "flagged.json"
LATEST_PATH = DIR / "latest.json"
RUNS_LOG = DIR / "runs.log"
FLAG_HISTORY_PATH = DIR / "flag_history.json"
CACHE_DIR = DIR / ".cache"

DEX_BOOSTS = "https://api.dexscreener.com/token-boosts/latest/v1"
DEX_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/{addresses}"
RUG_SUMMARY = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
RUG_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: str | None) -> float | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


_tls = threading.local()


def _opener() -> urllib.request.OpenerDirector:
    op = getattr(_tls, "opener", None)
    if op is None:
        op = urllib.request.build_opener()
        _tls.opener = op
    return op


def http_get_json(url: str, timeout: float | None = None) -> Any | None:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with _opener().open(req, timeout=HTTP_TIMEOUT_S if timeout is None else timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"WARN fetch failed: {url} ({e})", file=sys.stderr)
        return None


def atomic_write_text(path: Path, text: str) -> bool:
    """Write atomically; return True if content changed (or previous missing)."""
    try:
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == text:
                    return False
            except OSError:
                pass
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as e:
        print(f"WARN could not write {path}: {e}", file=sys.stderr)
        return True


def _cache_key(prefix: str, key: str) -> Path:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"{prefix}_{h}.json"


def cache_get(prefix: str, key: str, ttl_s: float) -> Any | None:
    path = _cache_key(prefix, key)
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > ttl_s:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("data")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def cache_put(prefix: str, key: str, data: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_key(prefix, key)
        path.write_text(
            json.dumps({"cached_at": utc_now_iso(), "data": data}, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"WARN cache write failed: {e}", file=sys.stderr)


def collect_solana_mints() -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Return ordered unique mints + side info from boosts/profiles."""
    side: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def add(item: dict[str, Any], source: str) -> None:
        if item.get("chainId") != "solana":
            return
        mint = item.get("tokenAddress")
        if not mint or not isinstance(mint, str):
            return
        if mint not in side:
            order.append(mint)
            side[mint] = {
                "sources": [source],
                "boost_amount": item.get("amount") or item.get("totalAmount"),
                "profile_links": item.get("links") or [],
                "description": item.get("description") or "",
                "dex_url": item.get("url") or f"https://dexscreener.com/solana/{mint}",
                "icon": item.get("icon") or item.get("image") or item.get("header"),
            }
        else:
            if source not in side[mint]["sources"]:
                side[mint]["sources"].append(source)
            if item.get("links") and not side[mint]["profile_links"]:
                side[mint]["profile_links"] = item.get("links") or []
            if item.get("description") and not side[mint]["description"]:
                side[mint]["description"] = item.get("description") or ""
            ba = item.get("amount") or item.get("totalAmount")
            if ba and (side[mint]["boost_amount"] is None or ba > side[mint]["boost_amount"]):
                side[mint]["boost_amount"] = ba
            icon = item.get("icon") or item.get("image") or item.get("header")
            if icon and not side[mint].get("icon"):
                side[mint]["icon"] = icon

    def _load_list(url: str) -> Any:
        cached = cache_get("dex_list", url, CACHE_DEX_TTL_S)
        if cached is not None:
            return cached
        data = http_get_json(url)
        if data is not None:
            cache_put("dex_list", url, data)
        return data

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_boost = ex.submit(_load_list, DEX_BOOSTS)
        f_prof = ex.submit(_load_list, DEX_PROFILES)
        boost_data = f_boost.result()
        prof_data = f_prof.result()

    for data, src_name in ((boost_data, "boosts"), (prof_data, "profiles")):
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    add(item, src_name)

    return order, side


def _merge_pair(best: dict[str, dict[str, Any]], pair: dict[str, Any]) -> None:
    if pair.get("chainId") != "solana":
        return
    base = pair.get("baseToken") or {}
    mint = base.get("address")
    if not mint:
        return
    try:
        liq = float(((pair.get("liquidity") or {}).get("usd")) or 0)
    except (TypeError, ValueError):
        liq = 0.0
    prev = best.get(mint)
    prev_liq = 0.0
    if prev:
        try:
            prev_liq = float(((prev.get("liquidity") or {}).get("usd")) or 0)
        except (TypeError, ValueError):
            prev_liq = 0.0
    quote_sym = ((pair.get("quoteToken") or {}).get("symbol") or "").upper()
    prev_quote = ((prev.get("quoteToken") or {}).get("symbol") or "").upper() if prev else ""
    better = False
    if prev is None:
        better = True
    elif liq > prev_liq:
        better = True
    elif liq == prev_liq and quote_sym == "SOL" and prev_quote != "SOL":
        better = True
    if better:
        best[mint] = pair


def _fetch_one_batch(batch: list[str]) -> list[dict[str, Any]]:
    batch_key = ",".join(batch)
    url = DEX_TOKENS.format(addresses=batch_key)
    data = cache_get("dex_tokens", batch_key, CACHE_DEX_TTL_S)
    if data is None:
        data = http_get_json(url)
        if data is not None:
            cache_put("dex_tokens", batch_key, data)
        else:
            return []
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict)]


def fetch_pairs_for_mints(mints: list[str]) -> dict[str, dict[str, Any]]:
    """Best pair per mint (highest liquidity USD among SOL-quoted preferred)."""
    best: dict[str, dict[str, Any]] = {}
    batches = [mints[i : i + PAIR_BATCH_SIZE] for i in range(0, len(mints), PAIR_BATCH_SIZE)]
    if not batches:
        return best

    # Serve cache hits first synchronously; network-miss batches in parallel
    need_net: list[list[str]] = []
    for batch in batches:
        batch_key = ",".join(batch)
        cached = cache_get("dex_tokens", batch_key, CACHE_DEX_TTL_S)
        if isinstance(cached, list):
            for pair in cached:
                if isinstance(pair, dict):
                    _merge_pair(best, pair)
        else:
            need_net.append(batch)

    if need_net:
        with ThreadPoolExecutor(max_workers=min(PAIR_WORKERS, len(need_net))) as ex:
            futs = [ex.submit(_fetch_one_batch, b) for b in need_net]
            for fut in as_completed(futs):
                try:
                    pairs = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"WARN pair batch: {e}", file=sys.stderr)
                    continue
                for pair in pairs:
                    _merge_pair(best, pair)
                if SLEEP_BETWEEN_BATCH_S:
                    time.sleep(SLEEP_BETWEEN_BATCH_S)
    return best


def fetch_rug_summary(mint: str) -> dict[str, Any] | None:
    cached = cache_get("rug", mint, CACHE_RUG_TTL_S)
    if cached is not None:
        return cached if isinstance(cached, dict) else None
    data = http_get_json(RUG_SUMMARY.format(mint=mint))
    if isinstance(data, dict):
        cache_put("rug", mint, data)
        return data
    return None


def _rug_report_ok(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("mint") or "topHolders" in data or "score_normalised" in data:
        return True
    return False


def fetch_rug_report(mint: str, bypass_cache: bool = False) -> dict[str, Any] | None:
    """Full RugCheck report (holders + rugged). Cached like the summary. Fail soft."""
    if not bypass_cache:
        cached = cache_get("rug_report", mint, CACHE_RUG_TTL_S)
        if isinstance(cached, dict) and _rug_report_ok(cached):
            return cached
    data = http_get_json(RUG_REPORT.format(mint=mint), timeout=12)
    if _rug_report_ok(data):
        cache_put("rug_report", mint, data)
        return data
    return None


def load_flagged_row(mint: str) -> dict[str, Any] | None:
    if not FLAGGED_PATH.exists():
        return None
    try:
        data = json.loads(FLAGGED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    for row in data:
        if isinstance(row, dict) and row.get("mint") == mint:
            return row
    return None


def fetch_rugs_parallel(mints: list[str]) -> dict[str, dict[str, Any] | None]:
    """Fetch RugCheck summaries with a small pool; respect short sleeps."""
    out: dict[str, dict[str, Any] | None] = {}
    if not mints:
        return out

    def _one(m: str) -> tuple[str, dict[str, Any] | None]:
        time.sleep(RUG_SLEEP_S)
        return m, fetch_rug_summary(m)

    with ThreadPoolExecutor(max_workers=RUG_WORKERS) as ex:
        futs = [ex.submit(_one, m) for m in mints]
        for fut in as_completed(futs):
            try:
                m, rug = fut.result()
                out[m] = rug
            except Exception as e:  # noqa: BLE001
                print(f"WARN rug worker: {e}", file=sys.stderr)
    return out


def curve_score(value: float, ok: float, good: float, weight: float) -> float:
    """0..weight based on value vs ok/good thresholds."""
    if value <= 0:
        return 0.0
    if value >= good:
        return weight
    if value >= ok:
        t = (value - ok) / (good - ok)
        return weight * (0.6 + 0.4 * t)
    t = value / ok
    return weight * 0.6 * t


def extract_image_url(pair: dict[str, Any], side: dict[str, Any]) -> str | None:
    info = pair.get("info") or {}
    for key in ("imageUrl", "image", "header", "icon"):
        val = info.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    # Some payloads nest under info.imageUrl already handled; try side/profile
    for key in ("icon", "image", "header"):
        val = side.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    return None


def build_links(mint: str, dex_url: str) -> dict[str, str]:
    return {
        "pump": f"https://pump.fun/coin/{mint}",
        "fomo": f"https://fomo.family/coin/{mint}?chainId=1399811149",
        "gmgn": f"https://gmgn.ai/sol/token/{mint}",
        "dex": dex_url or f"https://dexscreener.com/solana/{mint}",
    }


def _http_url(val: object) -> str | None:
    """Keep only http(s) URLs that arrived on the payload. Never synthesize one."""
    if not isinstance(val, str):
        return None
    u = val.strip()
    if u.startswith("https://") or u.startswith("http://"):
        return u
    return None


def score_socials(pair: dict[str, Any], side: dict[str, Any]) -> tuple[float, list[str], dict[str, Any]]:
    notes: list[str] = []
    info = pair.get("info") or {}
    socials = info.get("socials") or []
    websites = info.get("websites") or []
    profile_links = side.get("profile_links") or []

    has_twitter = False
    has_telegram = False
    has_website = bool(websites)
    link_urls: list[str] = []
    twitter_url: str | None = None
    telegram_url: str | None = None
    website_url: str | None = None

    def _note_url(bucket: str, raw: object) -> None:
        nonlocal twitter_url, telegram_url, website_url
        http = _http_url(raw)
        if not http:
            return
        if bucket == "twitter" and twitter_url is None:
            twitter_url = http
        elif bucket == "telegram" and telegram_url is None:
            telegram_url = http
        elif bucket == "website" and website_url is None:
            website_url = http

    for s in socials:
        if not isinstance(s, dict):
            continue
        t = (s.get("type") or "").lower()
        u = s.get("url") or ""
        if u:
            link_urls.append(u)
        if t in ("twitter", "x") or "x.com" in u or "twitter.com" in u:
            has_twitter = True
            _note_url("twitter", u)
        if t == "telegram" or "t.me" in u:
            has_telegram = True
            _note_url("telegram", u)

    for link in profile_links:
        if not isinstance(link, dict):
            continue
        u = link.get("url") or ""
        t = (link.get("type") or "").lower()
        if u:
            link_urls.append(u)
        if t in ("twitter", "x") or "x.com" in u or "twitter.com" in u:
            has_twitter = True
            _note_url("twitter", u)
        if t == "telegram" or "t.me" in u:
            has_telegram = True
            _note_url("telegram", u)
        if t in ("website", "") and u and "x.com" not in u and "t.me" not in u:
            has_website = True
            _note_url("website", u)

    for w in websites if isinstance(websites, list) else []:
        if website_url:
            break
        raw = w.get("url") if isinstance(w, dict) else w
        _note_url("website", raw)

    points = 0.0
    if has_twitter:
        points += W_SOCIALS * 0.45
        notes.append("has X/twitter")
    if has_telegram:
        points += W_SOCIALS * 0.25
        notes.append("has telegram")
    if has_website:
        points += W_SOCIALS * 0.20
        notes.append("has website")
    if side.get("description"):
        points += W_SOCIALS * 0.10
        notes.append("has description")
    points = min(points, W_SOCIALS)

    detail = {
        "twitter": has_twitter,
        "telegram": has_telegram,
        "website": has_website,
        "link_count": len(set(link_urls)),
        "twitter_url": twitter_url,
        "telegram_url": telegram_url,
        "website_url": website_url,
    }
    return points, notes, detail


def score_holders(rug: dict[str, Any] | None) -> tuple[float, list[str], dict[str, Any], list[str]]:
    """
    Best-effort holder/safety score from RugCheck summary.
    Full holder distribution needs Birdeye/Helius (API key) — not used in v1.
    """
    red: list[str] = []
    notes: list[str] = []
    detail: dict[str, Any] = {
        "rugcheck_available": rug is not None,
        "score_normalised": None,
        "lp_locked_pct": None,
        "risk_count": 0,
    }
    if rug is None:
        notes.append("RugCheck unavailable — holders unscored")
        return W_HOLDERS * 0.35, notes, detail, ["rugcheck_missing"]

    score_n = rug.get("score_normalised")
    if score_n is None:
        score_n = rug.get("score")
    try:
        score_n = float(score_n) if score_n is not None else None
    except (TypeError, ValueError):
        score_n = None

    lp = rug.get("lpLockedPct")
    try:
        lp = float(lp) if lp is not None else None
    except (TypeError, ValueError):
        lp = None

    risks = rug.get("risks") or []
    if not isinstance(risks, list):
        risks = []
    risk_names = []
    for r in risks:
        if isinstance(r, dict):
            risk_names.append(str(r.get("name") or r.get("level") or "risk"))
        else:
            risk_names.append(str(r))

    detail["score_normalised"] = score_n
    detail["lp_locked_pct"] = lp
    detail["risk_count"] = len(risks)
    detail["risks"] = risk_names[:8]

    points = 0.0
    if lp is not None:
        if lp >= RUG_LP_LOCK_GOOD:
            points += W_HOLDERS * 0.55
            notes.append(f"LP ~{lp:.0f}% locked")
        elif lp >= 50:
            points += W_HOLDERS * 0.35
            notes.append(f"LP partially locked ({lp:.0f}%)")
            red.append(f"lp_partial_{lp:.0f}")
        else:
            points += W_HOLDERS * 0.10
            notes.append(f"LP weakly locked ({lp:.0f}%)")
            red.append(f"lp_weak_{lp:.0f}")
    else:
        points += W_HOLDERS * 0.15
        notes.append("LP lock unknown")
        red.append("lp_unknown")

    if score_n is not None:
        if score_n <= 10:
            points += W_HOLDERS * 0.45
            notes.append(f"RugCheck score {score_n:.0f} (clean)")
        elif score_n <= 50:
            points += W_HOLDERS * 0.30
            notes.append(f"RugCheck score {score_n:.0f} (ok)")
        elif score_n <= RUG_SCORE_SAFE_MAX:
            points += W_HOLDERS * 0.15
            notes.append(f"RugCheck score {score_n:.0f} (elevated)")
            red.append(f"rug_elevated_{score_n:.0f}")
        else:
            points += 0.0
            notes.append(f"RugCheck score {score_n:.0f} (danger)")
            red.append(f"rug_danger_{score_n:.0f}")
    else:
        points += W_HOLDERS * 0.15
        notes.append("RugCheck score missing")

    if len(risks) >= 3:
        red.append(f"risks_x{len(risks)}")
        points = max(0.0, points - 5.0)

    return min(points, W_HOLDERS), notes, detail, red


def pair_age_hours(pair: dict[str, Any]) -> float | None:
    created = pair.get("pairCreatedAt")
    if created is None:
        return None
    try:
        created_s = float(created) / 1000.0
        return max(0.0, time.time() - created_s) / 3600.0
    except (TypeError, ValueError):
        return None


def load_flag_history() -> dict[str, Any]:
    if not FLAG_HISTORY_PATH.exists():
        return {}
    try:
        data = json.loads(FLAG_HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def update_flag_history(
    flagged: list[dict[str, Any]], now_iso: str
) -> dict[str, Any]:
    """
    Maintain mint → first_flagged_at, score, score_since.
    Attach sitting_minutes + score_stable_minutes onto each flagged row.
    Drop history for mints not flagged for >24h.
    """
    hist = load_flag_history()
    now_ts = parse_iso(now_iso) or time.time()
    active: set[str] = set()

    for row in flagged:
        mint = row.get("mint")
        if not mint:
            continue
        active.add(mint)
        score = float(row.get("score") or 0)
        prev = hist.get(mint) if isinstance(hist.get(mint), dict) else None

        if prev is None:
            entry = {
                "first_flagged_at": now_iso,
                "score": score,
                "score_since": now_iso,
                "last_seen": now_iso,
            }
        else:
            first = prev.get("first_flagged_at") or now_iso
            prev_score = float(prev.get("score") or score)
            score_since = prev.get("score_since") or now_iso
            if abs(score - prev_score) > SCORE_STABLE_DELTA:
                score_since = now_iso
            entry = {
                "first_flagged_at": first,
                "score": score,
                "score_since": score_since,
                "last_seen": now_iso,
            }
        hist[mint] = entry

        first_ts = parse_iso(entry["first_flagged_at"]) or now_ts
        stable_ts = parse_iso(entry["score_since"]) or now_ts
        sitting_min = max(0.0, (now_ts - first_ts) / 60.0)
        stable_min = max(0.0, (now_ts - stable_ts) / 60.0)
        row["first_flagged_at"] = entry["first_flagged_at"]
        row["score_since"] = entry["score_since"]
        row["sitting_minutes"] = round(sitting_min, 1)
        row["score_stable_minutes"] = round(stable_min, 1)

    # Drop stale mints not seen (and not active) beyond TTL
    keep: dict[str, Any] = {}
    for mint, entry in hist.items():
        if not isinstance(entry, dict):
            continue
        if mint in active:
            keep[mint] = entry
            continue
        last = parse_iso(entry.get("last_seen") or entry.get("first_flagged_at"))
        if last is None:
            continue
        age_h = (now_ts - last) / 3600.0
        if age_h <= FLAG_HISTORY_TTL_H:
            # still within window but not flagged this run — keep for sitting continuity
            keep[mint] = entry
    atomic_write_text(FLAG_HISTORY_PATH, json.dumps(keep, indent=2))
    return keep


def sitting_band(minutes: float | None) -> str:
    """fresh / inside / near / over / unknown.

    `over` is past SITTING_DROP_MIN. SHIP S keeps `over` rows on the board
    with Sitting chrome. It does not mean the row was removed.
    """
    if minutes is None:
        return "unknown"
    try:
        mins = float(minutes)
    except (TypeError, ValueError):
        return "unknown"
    if mins > SITTING_DROP_MIN:
        return "over"
    if mins >= SITTING_DROP_MIN - SITTING_NEAR_LEAD_MIN:
        return "near"
    if mins < SITTING_FRESH_MIN:
        return "fresh"
    return "inside"


def _sitting_minutes(row: dict[str, Any]) -> float | None:
    raw = row.get("sitting_minutes")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def apply_sitting_rule(
    flagged: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep sitting coins on Token Flow. Do not hard-drop for sitting time.

    SHIP S rule (qa/ship-s-sitting-keep-rule.md):

    - Rounded sitting_minutes > SITTING_DROP_MIN (5.0) stays flagged.
      sitting_band is "over", sitting_kept is true. 5.0 stays "near".
    - Rounded sitting_minutes > SITTING_AGED_MIN (180) stays too, with
      sitting_aged_hold. The panel hint becomes "aged · still listed".
    - The second list is always empty. sitting_dropped_count stays 0.
      A coin leaves only when it misses the normal flag gates (score,
      liquidity, 1h volume, or pair age), which is the SHIP F anti-spam
      bound. Sitting time never deletes a qualifying row.
    - A row with no sitting_minutes stays. Score fields are untouched.
      Comparison uses the rounded sitting_minutes already on the row.
    """
    stamped: list[dict[str, Any]] = []
    for row in flagged:
        mins = _sitting_minutes(row)
        band = sitting_band(mins)
        aged = mins is not None and mins > SITTING_AGED_MIN
        row["sitting_drop_min"] = SITTING_DROP_MIN
        row["sitting_aged_min"] = SITTING_AGED_MIN
        row["sitting_band"] = band
        row["sitting_kept"] = band == "over" or aged
        row["sitting_aged_hold"] = aged
        row.pop("sitting_dropped", None)
        row.pop("sitting_aged_out", None)
        row["flagged"] = True
        stamped.append(row)
    return stamped, []


def finalize_flagged(
    flagged: list[dict[str, Any]], now_iso: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach sitting time, then keep sitters on the board (SHIP S).

    The second list is always empty. The 5-minute hard-drop is retired.
    """
    update_flag_history(flagged, now_iso)
    return apply_sitting_rule(flagged)


def evaluate(
    mint: str,
    pair: dict[str, Any],
    side: dict[str, Any],
    rug: dict[str, Any] | None,
    data_as_of: str,
) -> dict[str, Any] | None:
    base = pair.get("baseToken") or {}
    symbol = base.get("symbol") or "?"
    name = base.get("name") or symbol

    try:
        liq = float(((pair.get("liquidity") or {}).get("usd")) or 0)
    except (TypeError, ValueError):
        liq = 0.0
    vol = pair.get("volume") or {}
    try:
        vol_h1 = float(vol.get("h1") or 0)
    except (TypeError, ValueError):
        vol_h1 = 0.0
    try:
        vol_h24 = float(vol.get("h24") or 0)
    except (TypeError, ValueError):
        vol_h24 = 0.0

    age_h = pair_age_hours(pair)
    txns = pair.get("txns") or {}
    h1 = txns.get("h1") or {}
    try:
        buys = int(h1.get("buys") or 0)
        sells = int(h1.get("sells") or 0)
    except (TypeError, ValueError):
        buys, sells = 0, 0
    buy_sell_ratio = (buys / sells) if sells > 0 else (float(buys) if buys else None)

    hard_fails: list[str] = []
    if liq < MIN_LIQUIDITY_USD:
        hard_fails.append(f"liq<{MIN_LIQUIDITY_USD:.0f}")
    if vol_h1 < MIN_VOLUME_H1_USD:
        hard_fails.append(f"vol_h1<{MIN_VOLUME_H1_USD:.0f}")
    if age_h is not None and age_h > MAX_PAIR_AGE_HOURS:
        hard_fails.append(f"age>{MAX_PAIR_AGE_HOURS:.0f}h")

    liq_pts = curve_score(liq, LIQ_OK, LIQ_GOOD, W_LIQUIDITY)
    vol_pts = curve_score(vol_h1, VOL_OK, VOL_GOOD, W_VOLUME)
    hold_pts, hold_notes, hold_detail, hold_red = score_holders(rug)
    soc_pts, soc_notes, soc_detail = score_socials(pair, side)

    flow_adj = 0.0
    flow_notes: list[str] = []
    if buy_sell_ratio is not None:
        if buy_sell_ratio >= 1.2:
            flow_adj += 3.0
            flow_notes.append(f"buy-heavy h1 ({buy_sell_ratio:.2f})")
        elif buy_sell_ratio <= 0.7:
            flow_adj -= 4.0
            flow_notes.append(f"sell-heavy h1 ({buy_sell_ratio:.2f})")
            hold_red.append("sell_heavy")

    if liq > 0 and vol_h1 / liq > 30:
        flow_adj -= 5.0
        flow_notes.append(f"vol/liq h1 = {vol_h1/liq:.1f}x (wash risk)")
        hold_red.append("high_vol_liq")

    total = max(0.0, min(100.0, liq_pts + vol_pts + hold_pts + soc_pts + flow_adj))
    score_12 = round(total / 100.0 * 12.0, 1)

    price_change = pair.get("priceChange") or {}
    try:
        pch1 = float(price_change.get("h1")) if price_change.get("h1") is not None else None
    except (TypeError, ValueError):
        pch1 = None
    try:
        pch24 = float(price_change.get("h24")) if price_change.get("h24") is not None else None
    except (TypeError, ValueError):
        pch24 = None

    late_chase = False
    if (
        age_h is not None
        and pch1 is not None
        and age_h >= LATE_CHASE_MIN_AGE_H
        and pch1 >= LATE_CHASE_MIN_H1_PCT
    ):
        late_chase = True
        hold_red.append("late_chase")
        flow_notes.append(
            f"late chase risk (age {age_h:.1f}h, h1 {pch1:+.0f}%)"
        )

    why: list[str] = []
    why.append(f"liq ${liq:,.0f} → {liq_pts:.0f}/{W_LIQUIDITY:.0f}")
    why.append(f"vol1h ${vol_h1:,.0f} → {vol_pts:.0f}/{W_VOLUME:.0f}")
    why.extend(hold_notes)
    why.extend(soc_notes)
    why.extend(flow_notes)

    dex_url = pair.get("url") or side.get("dex_url") or f"https://dexscreener.com/solana/{mint}"
    links = build_links(mint, dex_url)
    image_url = extract_image_url(pair, side)
    lp_locked = hold_detail.get("lp_locked_pct")

    flagged = (not hard_fails) and total >= MIN_SCORE_TO_FLAG
    trench_band = score_12 >= 10.0

    row = {
        "ticker": symbol,
        "name": name,
        "mint": mint,
        "ca_short": f"{mint[:4]}…{mint[-4:]}" if len(mint) > 10 else mint,
        "score": round(total, 1),
        "score_12": score_12,
        "trench_band": trench_band,
        "flagged": flagged,
        "hard_fails": hard_fails,
        "late_chase": late_chase,
        "breakdown": {
            "liquidity": round(liq_pts, 1),
            "volume": round(vol_pts, 1),
            "holders": round(hold_pts, 1),
            "socials": round(soc_pts, 1),
            "flow_adj": round(flow_adj, 1),
        },
        "metrics": {
            "liquidity_usd": round(liq, 2),
            "volume_h1_usd": round(vol_h1, 2),
            "volume_h24_usd": round(vol_h24, 2),
            "price_usd": pair.get("priceUsd"),
            "price_change_h1": pch1,
            "price_change_h24": pch24,
            "buys_h1": buys,
            "sells_h1": sells,
            "buy_sell_ratio_h1": round(buy_sell_ratio, 3) if buy_sell_ratio is not None else None,
            "buy_sell_ratio": round(buy_sell_ratio, 3) if buy_sell_ratio is not None else None,
            "pair_age_hours": round(age_h, 2) if age_h is not None else None,
            "lp_locked_pct": lp_locked,
            "dex_id": pair.get("dexId"),
            "pair_address": pair.get("pairAddress"),
        },
        "liquidity_usd": round(liq, 2),
        "volume_h1_usd": round(vol_h1, 2),
        "volume_h24_usd": round(vol_h24, 2),
        "lp_locked_pct": lp_locked,
        "pair_age_hours": round(age_h, 2) if age_h is not None else None,
        "image_url": image_url,
        "data_as_of": data_as_of,
        "links": links,
        "holders": hold_detail,
        "socials": soc_detail,
        "red_flags": hold_red,
        "why": why,
        "sources": side.get("sources") or [],
        "boost_amount": side.get("boost_amount"),
        "dex_url": dex_url,
        "description": (side.get("description") or "")[:240],
    }
    row = apply_mcap(row, pair)
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    if "market_cap_usd" not in metrics:
        prog = row.get("mcap_progress") if isinstance(row.get("mcap_progress"), dict) else {}
        metrics = dict(metrics)
        metrics["market_cap_usd"] = prog.get("market_cap_usd")
        row["metrics"] = metrics
    row["research"] = research_card(row)
    return row


_scan_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_SCAN_TTL_S = 20.0


def coin_payload(mint: str, bypass_cache: bool = False) -> dict[str, Any]:
    """One coin for the detail surface.

    Uses the flagged row when the mint is already on the list so the open
    matches what was clicked. Otherwise scores once via scan_mint. A RugCheck
    report, when it returns, supplies holder percentages and live risk inputs.
    """
    mint = (mint or "").strip()
    if not mint:
        return {"ok": False, "error": "mint required"}

    row = None if bypass_cache else load_flagged_row(mint)
    source = "flagged" if row is not None else "scan"

    if row is None:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_scan = ex.submit(scan_mint, mint, bypass_cache)
            fut_report = ex.submit(fetch_rug_report, mint, bypass_cache)
            scanned = fut_scan.result()
            report = fut_report.result()
        if not isinstance(scanned, dict) or not scanned.get("ok"):
            if isinstance(scanned, dict):
                return scanned
            return {"ok": False, "error": "scan failed", "mint": mint}
        row = scanned.get("row") if isinstance(scanned.get("row"), dict) else None
        if not row:
            return {"ok": False, "error": "scan returned no row", "mint": mint}
    else:
        report = fetch_rug_report(mint, bypass_cache)

    # DEX label only from the same token quote Game uses. A flagged snapshot
    # stays a snapshot when that quote fails or is past the refresh SLA.
    out = game_mod.attach_live_dex_quote(row, mint, fresh=bypass_cache)
    out["research"] = research_card(out, report if isinstance(report, dict) else None)
    return {
        "ok": True,
        "mint": mint,
        "source": source,
        "report_used": isinstance(report, dict),
        "row": out,
        "research": out["research"],
        "disclaimer": (
            "Paper research only. Not financial advice. No trades placed. "
            "Rug % is a heuristic from the inputs listed, not a probability."
        ),
    }


def scan_mint(mint: str, bypass_cache: bool = False) -> dict[str, Any]:
    """On-demand score for one mint using the same evaluate() stack as the bot.

    Does not write flagged.json. Brief in-memory cache so repeat clicks are cheap.
    """
    mint = (mint or "").strip()
    if not mint:
        return {"ok": False, "error": "mint required"}
    now = time.time()
    hit = _scan_cache.get(mint)
    if not bypass_cache and hit and now - hit[0] < _SCAN_TTL_S:
        row = dict(hit[1])
        row["scan_cached"] = True
        # Re-check the shared quote so a cached score cannot keep a stale DEX label.
        row = game_mod.attach_live_dex_quote(row, mint, fresh=False)
        return {
            "ok": True,
            "cached": True,
            "mint": mint,
            "row": row,
            "disclaimer": "Paper research only. Not financial advice. No trades placed.",
        }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pairs = fetch_pairs_for_mints([mint])
    pair = pairs.get(mint) if isinstance(pairs, dict) else None
    if not pair:
        return {
            "ok": False,
            "error": "No Solana pair on DEX Screener for that mint",
            "mint": mint,
        }

    # light side info from pair itself
    side = {
        "sources": ["on_demand_scan"],
        "dex_url": pair.get("url") or f"https://dexscreener.com/solana/{mint}",
        "description": "",
        "boost_amount": None,
    }
    # also pull socials/image already on pair via evaluate helpers
    rug = fetch_rug_summary(mint)
    data_as_of = utc_now_iso()
    row = evaluate(mint, pair, side, rug, data_as_of)
    if not row:
        return {"ok": False, "error": "evaluate returned nothing", "mint": mint}
    row["scan_cached"] = False
    row["on_demand"] = True
    _scan_cache[mint] = (now, row)
    shown = game_mod.attach_live_dex_quote(row, mint, fresh=bypass_cache)
    return {
        "ok": True,
        "cached": False,
        "mint": mint,
        "row": shown,
        "disclaimer": "Paper research only. Not financial advice. No trades placed.",
    }


def main() -> int:
    t0 = time.perf_counter()
    started = utc_now_iso()
    print(f"[{started}] nunu-filter starting…")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    mints, side = collect_solana_mints()
    print(f"  collected {len(mints)} unique Solana mints from boosts/profiles")

    pairs = fetch_pairs_for_mints(mints)
    print(f"  resolved pairs for {len(pairs)} mints")

    # RugCheck only for candidates that clear basic liq/vol (saves requests)
    rug_candidates: list[str] = []
    for mint, pair in pairs.items():
        try:
            liq = float(((pair.get("liquidity") or {}).get("usd")) or 0)
        except (TypeError, ValueError):
            liq = 0.0
        vol = pair.get("volume") or {}
        try:
            vol_h1 = float(vol.get("h1") or 0)
        except (TypeError, ValueError):
            vol_h1 = 0.0
        if liq >= MIN_LIQUIDITY_USD * 0.5 and vol_h1 >= MIN_VOLUME_H1_USD * 0.5:
            rug_candidates.append(mint)

    print(f"  fetching RugCheck for {len(rug_candidates)} candidates (workers={RUG_WORKERS})…")
    rugs = fetch_rugs_parallel(rug_candidates)
    rug_ok = sum(1 for v in rugs.values() if v is not None)
    rug_fail = sum(1 for v in rugs.values() if v is None)

    data_as_of = utc_now_iso()
    evaluated: list[dict[str, Any]] = []
    for mint, pair in pairs.items():
        row = evaluate(mint, pair, side.get(mint, {}), rugs.get(mint), data_as_of)
        if row:
            evaluated.append(row)

    evaluated.sort(key=lambda r: r["score"], reverse=True)
    flagged = [r for r in evaluated if r["flagged"]]

    finished = utc_now_iso()
    flagged, _aged_out = finalize_flagged(flagged, finished)

    trench_count = sum(1 for r in flagged if r.get("trench_band"))
    late_count = sum(1 for r in flagged if r.get("late_chase"))
    sitting_kept = [r for r in flagged if r.get("sitting_kept")]
    sitting_aged_hold = [r for r in flagged if r.get("sitting_aged_hold")]

    def _sit_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "ticker": r.get("ticker"),
                "mint": r.get("mint"),
                "sitting_minutes": r.get("sitting_minutes"),
                "score": r.get("score"),
                "sitting_band": r.get("sitting_band"),
            }
            for r in rows[:20]
        ]

    kept_summary = _sit_summary(sitting_kept)
    aged_hold_summary = _sit_summary(sitting_aged_hold)

    latest = {
        "run_started": started,
        "run_finished": finished,
        "data_as_of": data_as_of,
        "scanned_mints": len(mints),
        "pairs_resolved": len(pairs),
        "evaluated": len(evaluated),
        "flagged_count": len(flagged),
        "trench_band_count": trench_count,
        "late_chase_count": late_count,
        "sitting_dropped_count": 0,
        "sitting_dropped": [],
        "sitting_kept_count": len(sitting_kept),
        "sitting_kept": kept_summary,
        "sitting_aged_hold_count": len(sitting_aged_hold),
        "sitting_aged_hold": aged_hold_summary,
        "sitting_aged_out_count": 0,
        "sitting_aged_out": [],
        "thresholds": {
            "min_liquidity_usd": MIN_LIQUIDITY_USD,
            "min_volume_h1_usd": MIN_VOLUME_H1_USD,
            "max_pair_age_hours": MAX_PAIR_AGE_HOURS,
            "min_score_to_flag": MIN_SCORE_TO_FLAG,
            "sitting_drop_min": SITTING_DROP_MIN,
            "sitting_aged_min": SITTING_AGED_MIN,
            "sitting_keep": True,
            "sitting_near_lead_min": SITTING_NEAR_LEAD_MIN,
            "sitting_fresh_min": SITTING_FRESH_MIN,
            "late_chase_min_age_h": LATE_CHASE_MIN_AGE_H,
            "late_chase_min_h1_pct": LATE_CHASE_MIN_H1_PCT,
            "trench_band_score_12_min": 10.0,
            "weights": {
                "liquidity": W_LIQUIDITY,
                "volume": W_VOLUME,
                "holders": W_HOLDERS,
                "socials": W_SOCIALS,
            },
        },
        "apis": {
            "dexscreener": "ok",
            "rugcheck_summaries_ok": rug_ok,
            "rugcheck_summaries_fail": rug_fail,
            "cache_dir": str(CACHE_DIR),
        },
        "disclaimer": "Paper research only. Not financial advice. No trades placed.",
        "top_scores": [
            {
                "ticker": r["ticker"],
                "mint": r["mint"],
                "score": r["score"],
                "score_12": r.get("score_12"),
                "flagged": r["flagged"],
                "trench_band": r.get("trench_band"),
            }
            for r in evaluated[:10]
        ],
    }

    flagged_txt = json.dumps(flagged, indent=2)
    latest_txt = json.dumps(latest, indent=2)
    wrote_f = atomic_write_text(FLAGGED_PATH, flagged_txt)
    wrote_l = atomic_write_text(LATEST_PATH, latest_txt)
    elapsed = time.perf_counter() - t0

    log_line = (
        f"{finished} scanned={len(mints)} pairs={len(pairs)} "
        f"evaluated={len(evaluated)} flagged={len(flagged)} "
        f"trench={trench_count} late={late_count} "
        f"sitting_dropped=0 sitting_kept={len(sitting_kept)} "
        f"sitting_aged_hold={len(sitting_aged_hold)} "
        f"rug_ok={rug_ok} rug_fail={rug_fail} elapsed_s={elapsed:.2f}\n"
    )
    with RUNS_LOG.open("a", encoding="utf-8") as f:
        f.write(log_line)

    print(f"  flagged {len(flagged)} / evaluated {len(evaluated)} "
          f"(trench_band={trench_count} late_chase={late_count})")
    print(f"  wall_time={elapsed:.2f}s "
          f"flagged_write={'yes' if wrote_f else 'skip'} "
          f"latest_write={'yes' if wrote_l else 'skip'}")
    print(f"  wrote {FLAGGED_PATH}")
    print(f"  wrote {LATEST_PATH}")
    print(f"  wrote {FLAG_HISTORY_PATH}")
    for r in flagged[:5]:
        print(
            f"    FLAG {r['ticker']:12} score={r['score']:5.1f} "
            f"({r.get('score_12')}/12) liq=${r['metrics']['liquidity_usd']:,.0f} "
            f"vol1h=${r['metrics']['volume_h1_usd']:,.0f}  {r['mint']}"
        )
    if not flagged:
        print("    (no tokens cleared thresholds this run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
