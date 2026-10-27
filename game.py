#!/usr/bin/env python3
"""
24h $5 paper game + DEX quote helpers (research only — no wallets/orders).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen

from mcap_progress import apply_mcap, build_mcap_progress, merge_live_progress, progress_from_pair, progress_from_row

DIR = Path(__file__).resolve().parent
STATE_PATH = DIR / "game_state.json"
SCORES_PATH = DIR / "game_scores.json"
FLAGGED_PATH = DIR / "flagged.json"
PAPER_CSV = Path("/workspace/trading/papertrades.csv")
# also allow Chromebook-relative sibling if present
_ALT_PAPER = DIR.parent / "papertrades.csv"

START_BANKROLL = 5.0
SESSION_HOURS = 24
MAX_SCORES = 50
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
_UA = "nunu-filter-paper/1.0"

_lock = threading.RLock()
_quote_cache: dict[str, tuple[float, dict | None]] = {}
_QUOTE_TTL = 2.5  # seconds — happy-path cache so 2s polls do not refetch every tick
# Labeled "DEX Screener" / Live USD may only show a quote fetched inside this window.
# A failed refresh may reuse the cache while it is still inside the SLA.
# Older than the SLA: Unknown / — . Never keep that print under a live DEX label.
QUOTE_FRESH_SLA_S = 15.0
QUOTE_BAND = 0.05  # ±5% vs the same-path DexScreener quote, else not a live DEX print
_DEX_TIMEOUT = 3.5
_fetch_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="dexq")


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None


def _paper_path() -> Path:
    if PAPER_CSV.parent.exists():
        return PAPER_CSV
    return _ALT_PAPER


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


_BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q={q}"


def looks_like_mint(s: str) -> bool:
    s = (s or "").strip()
    if not (32 <= len(s) <= 50):
        return False
    return all(c in _BASE58 for c in s)


def _flagged_candidates(query: str) -> list[dict]:
    """Match flagged rows by exact ticker, then name/ticker contains."""
    want = (query or "").strip().lower()
    if not want:
        return []
    rows = _read_json(FLAGGED_PATH, [])
    if not isinstance(rows, list):
        return []
    exact_sym: list[dict] = []
    soft: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        mint = str(r.get("mint") or "").strip()
        if not looks_like_mint(mint):
            continue
        sym = str(r.get("ticker") or r.get("symbol") or "").strip()
        name = str(r.get("name") or "").strip()
        m = r.get("metrics") or {}
        try:
            score = float(r.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            liq = float(m.get("liquidity_usd") or r.get("liquidity_usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        try:
            px = float(m.get("price_usd") or r.get("price_usd") or 0) or None
        except (TypeError, ValueError):
            px = None
        prog = progress_from_row(r, now_source="snapshot")
        row = {
            "mint": mint,
            "symbol": sym or "?",
            "name": name or sym or "?",
            "liquidity_usd": liq,
            "market_cap_usd": prog["market_cap_usd"],
            "mcap_progress": prog,
            "price_usd": px,
            "pair_url": (r.get("links") or {}).get("dex") or r.get("dex_url") or f"https://dexscreener.com/solana/{mint}",
            "image_url": r.get("image_url") or "",
            "score": score,
            "score_12": r.get("score_12"),
            "source": "flagged",
            "ca_short": (mint[:4] + "…" + mint[-4:]) if len(mint) > 10 else mint,
        }
        sl = sym.lower()
        nl = name.lower()
        if sl == want:
            exact_sym.append(row)
        elif want in sl or want in nl or nl == want:
            # short queries must hit ticker exactly or full name
            if len(want) <= 3 and sl != want and nl != want:
                continue
            soft.append(row)
    exact_sym.sort(key=lambda r: (float(r.get("score") or 0), float(r.get("liquidity_usd") or 0)), reverse=True)
    soft.sort(key=lambda r: (float(r.get("score") or 0), float(r.get("liquidity_usd") or 0)), reverse=True)
    # de-dupe by mint, prefer exact
    seen: set[str] = set()
    out: list[dict] = []
    for row in exact_sym + soft:
        if row["mint"] in seen:
            continue
        seen.add(row["mint"])
        out.append(row)
    return out


def resolve_mint_from_flagged(ticker: str) -> str | None:
    """Best flagged match by ticker or name; prefer exact ticker, then score/liq."""
    hits = _flagged_candidates(ticker)
    return hits[0]["mint"] if hits else None


def _dex_search_raw(query: str) -> list[dict]:
    """Solana DEX Screener search hits scored for symbol/name match."""
    q = (query or "").strip()
    if not q or len(q) > 64:
        return []
    url = DEX_SEARCH_URL.format(q=url_quote(q))
    try:
        req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urlopen(req, timeout=_DEX_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    pairs = raw.get("pairs") if isinstance(raw, dict) else None
    if not isinstance(pairs, list) or not pairs:
        return []
    want = q.lower()
    scored: list[tuple] = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        if str(p.get("chainId") or "").lower() != "solana":
            continue
        base = p.get("baseToken") or {}
        sym = str(base.get("symbol") or "").strip()
        name = str(base.get("name") or "").strip()
        addr = str(base.get("address") or "").strip()
        if not looks_like_mint(addr):
            continue
        try:
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        prog = progress_from_pair(p, now_source="dex")
        mcap = prog["market_cap_usd"]
        px = _f(p.get("priceUsd"))
        sl = sym.lower()
        nl = name.lower()
        exact_sym = 1 if sl == want else 0
        exact_name = 1 if nl == want else 0
        soft_sym = 1 if (want in sl and not exact_sym) else 0
        soft_name = 1 if (want in nl and not exact_name) else 0
        # short tickers: require exact symbol (or exact full name)
        if len(want) <= 4 and not exact_sym and not exact_name:
            continue
        if not (exact_sym or exact_name or soft_sym or soft_name):
            continue
        # Prefer real markets: match quality, then non-dust liq, then raw liq
        match_tier = 4 if exact_sym else (3 if exact_name else (2 if soft_sym else 1))
        liq_ok = 1 if liq >= 1000.0 else 0
        rank = (match_tier, liq_ok, liq)
        scored.append(
            (
                rank,
                {
                    "mint": addr,
                    "symbol": sym or "?",
                    "name": name or sym or "?",
                    "liquidity_usd": liq,
                    "market_cap_usd": mcap,
                    "mcap_progress": prog,
                    "price_usd": px,
                    "pair_url": p.get("url")
                    or f"https://dexscreener.com/solana/{p.get('pairAddress') or addr}",
                    "image_url": (p.get("info") or {}).get("imageUrl") or "",
                    "source": "dex_search",
                    "ca_short": (addr[:4] + "…" + addr[-4:]) if len(addr) > 10 else addr,
                    "pair_address": p.get("pairAddress") or "",
                },
            )
        )
    scored.sort(key=lambda t: t[0], reverse=True)
    # de-dupe mint, keep best rank
    seen: set[str] = set()
    out: list[dict] = []
    for _rank, row in scored:
        if row["mint"] in seen:
            continue
        seen.add(row["mint"])
        out.append(row)
    return out


def resolve_mint_from_dex_search(query: str) -> str | None:
    """DEX Screener search; prefer Solana exact symbol/name, highest liquidity."""
    hits = _dex_search_raw(query)
    return hits[0]["mint"] if hits else None


def search_candidates(query: str, limit: int = 8) -> list[dict]:
    """Unified name/ticker/CA search for picker UI. Solana only, highest liq first among ties."""
    q = (query or "").strip()
    if not q:
        return []
    if looks_like_mint(q):
        quote = fetch_dex_token(q, bypass_cache=False)
        if not quote:
            return [
                {
                    "mint": q,
                    "symbol": "?",
                    "name": "?",
                    "liquidity_usd": 0,
                    "market_cap_usd": None,
                    "price_usd": None,
                    "pair_url": f"https://dexscreener.com/solana/{q}",
                    "image_url": "",
                    "source": "mint",
                    "ca_short": (q[:4] + "…" + q[-4:]) if len(q) > 10 else q,
                }
            ]
        return [
            {
                "mint": q,
                "symbol": quote.get("symbol") or "?",
                "name": quote.get("name") or "?",
                "liquidity_usd": quote.get("liquidity_usd") or 0,
                "market_cap_usd": quote.get("market_cap_usd"),
                "mcap_progress": quote.get("mcap_progress"),
                "price_usd": quote.get("price_usd"),
                "pair_url": quote.get("pair_url") or f"https://dexscreener.com/solana/{q}",
                "image_url": quote.get("image_url") or "",
                "source": "mint",
                "ca_short": (q[:4] + "…" + q[-4:]) if len(q) > 10 else q,
            }
        ]
    flagged = _flagged_candidates(q)
    dex = _dex_search_raw(q)
    # merge: flagged first (has scores), then dex; de-dupe mint
    seen: set[str] = set()
    out: list[dict] = []
    for row in flagged + dex:
        m = row.get("mint")
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(row)
    return out[: max(1, min(int(limit or 8), 20))]


def search_payload(query: str, limit: int = 8) -> dict:
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "Search query required — name, ticker, or CA"}
    hits = search_candidates(q, limit=limit)
    if not hits:
        return {
            "ok": False,
            "error": f"No Solana matches for {q!r} — try full CA from Pump/DEX",
            "query": q,
            "candidates": [],
        }
    return {
        "ok": True,
        "query": q,
        "count": len(hits),
        "candidates": hits,
        "best_mint": hits[0]["mint"],
        "disclaimer": "Paper research only.",
    }


def resolve_mint(query: str) -> tuple[str | None, str | None, str | None]:
    """Return (mint, error, source). Accepts CA, ticker, or name (flagged first, then DEX)."""
    q = (query or "").strip()
    if not q:
        return None, "Paste a mint/CA, ticker, or name", None
    if looks_like_mint(q):
        return q, None, "mint"
    hits = search_candidates(q, limit=1)
    if hits:
        src = hits[0].get("source") or "dex_search"
        return hits[0]["mint"], None, src
    return None, f"Could not resolve name/ticker/CA: {q!r} — paste the full CA from Filter Play or Pump", None


def within_quote_band(shown: float | None, reference: float | None, band: float = QUOTE_BAND) -> bool:
    """True when `shown` is within ±band of the same-path reference quote."""
    if shown is None or reference is None:
        return False
    try:
        got = float(shown)
        ref = float(reference)
    except (TypeError, ValueError):
        return False
    if ref == 0:
        return got == 0
    return abs(got - ref) / abs(ref) <= band + 1e-12


def hide_live_now(prog: dict | None) -> dict:
    """Drop a labeled-live NOW. Keep an already-chosen ceiling; do not recompute one.

    Blanking NOW must not turn FDV ≈ NOW into a fresh ceiling or a 100% bar.
    """
    if not isinstance(prog, dict):
        return build_mcap_progress()
    out = dict(prog)
    out["market_cap_usd"] = None
    out["now_available"] = False
    out["now_source"] = None
    out["pct_gone"] = None
    out["bar_pct"] = None
    out["progress_available"] = False
    out["quote_stale"] = True
    return out


def _decorate_quote(quote: dict, fetched_at: float, now: float) -> dict:
    """Copy a cached quote and stamp age. Does not extend the fetch time."""
    out = dict(quote)
    age = max(0.0, now - fetched_at)
    px = _f(out.get("price_usd"))
    out["quoted_epoch"] = fetched_at
    out["quote_age_s"] = round(age, 3)
    out["quote_sla_s"] = QUOTE_FRESH_SLA_S
    out["quote_band"] = QUOTE_BAND
    fresh = age <= QUOTE_FRESH_SLA_S
    out["quote_live"] = bool(fresh and px is not None and px > 0)
    if not fresh:
        prog = out.get("mcap_progress")
        out["mcap_progress"] = hide_live_now(prog if isinstance(prog, dict) else None)
        out["market_cap_usd"] = None
        out["price_usd"] = None
        out["quote_live"] = False
    return out


def _fresh_cached(hit: tuple[float, dict | None] | None, now: float) -> dict | None:
    """Last successful quote, only while it is still inside the refresh SLA."""
    if not hit or not isinstance(hit[1], dict):
        return None
    if now - hit[0] > QUOTE_FRESH_SLA_S:
        return None
    return _decorate_quote(hit[1], hit[0], now)


def fetch_dex_token(mint: str, bypass_cache: bool = False) -> dict | None:
    """Best Solana pair for mint from the DEX Screener token endpoint.

    Price, market cap, and pair URL come from that one pair. A labeled-live
    result is only returned when the fetch (or a cache hit still inside
    QUOTE_FRESH_SLA_S) succeeded. A failed refresh does not hand back an
    older print.
    """
    mint = (mint or "").strip()
    if not mint or len(mint) < 32:
        return None
    now = time.time()
    hit = _quote_cache.get(mint)
    if not bypass_cache and hit and now - hit[0] < _QUOTE_TTL:
        if not isinstance(hit[1], dict):
            return None
        return _fresh_cached(hit, now)
    url = DEX_TOKEN_URL.format(mint=mint)
    try:
        req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urlopen(req, timeout=_DEX_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return _fresh_cached(hit, now)
    pairs = raw.get("pairs") if isinstance(raw, dict) else None
    if not isinstance(pairs, list) or not pairs:
        fresh = _fresh_cached(hit, now)
        if fresh:
            return fresh
        _quote_cache[mint] = (now, None)  # type: ignore
        return None
    sol = [p for p in pairs if str(p.get("chainId") or "").lower() == "solana"]
    pool = sol or pairs

    def liq(p):
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    best = max(pool, key=liq)
    base = best.get("baseToken") or {}
    prog = progress_from_pair(best, now_source="dex")
    # A flagged row may already hold bonding / ATH / a higher FDV the pair omits.
    fr = flagged_row(mint)
    if isinstance(fr, dict):
        prog = merge_live_progress(fr, prog)
    quote = {
        "mint": mint,
        "symbol": base.get("symbol") or "?",
        "name": base.get("name") or base.get("symbol") or "?",
        "price_usd": _f(best.get("priceUsd")),
        "market_cap_usd": prog["market_cap_usd"],
        "fdv_usd": prog["fdv_usd"],
        "mcap_progress": prog,
        "liquidity_usd": liq(best),
        "volume_h1_usd": _f((best.get("volume") or {}).get("h1")),
        "price_change_h1": _f((best.get("priceChange") or {}).get("h1")),
        "buys_h1": int((best.get("txns") or {}).get("h1", {}).get("buys") or 0),
        "sells_h1": int((best.get("txns") or {}).get("h1", {}).get("sells") or 0),
        "pair_address": best.get("pairAddress") or "",
        "pair_url": best.get("url")
        or f"https://dexscreener.com/solana/{best.get('pairAddress') or mint}",
        "dex_id": best.get("dexId") or "",
        "image_url": (best.get("info") or {}).get("imageUrl") or "",
        "pair_created_at": best.get("pairCreatedAt"),
        "quoted_at": _utc(),
    }
    if not quote["image_url"]:
        quote["image_url"] = image_for_mint(mint)
    # Prefer filter name/ticker when DEX is sparse
    if fr:
        if (not quote.get("name") or quote.get("name") == "?") and fr.get("name"):
            quote["name"] = fr.get("name")
        if (not quote.get("symbol") or quote.get("symbol") == "?") and (fr.get("ticker") or fr.get("symbol")):
            quote["symbol"] = fr.get("ticker") or fr.get("symbol")
    _quote_cache[mint] = (now, quote)
    return _decorate_quote(quote, now, now)


def _f(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def flagged_row(mint: str) -> dict | None:
    """Return the flagged.json row for mint, if any."""
    rows = _read_json(FLAGGED_PATH, [])
    if not isinstance(rows, list):
        return None
    for r in rows:
        if isinstance(r, dict) and str(r.get("mint") or "") == mint:
            return r
    return None


def flags_for_mint(mint: str) -> list[str]:
    """Compact warning labels from flagged.json if known."""
    r = flagged_row(mint)
    return warning_labels(r) if r else []


def image_for_mint(mint: str, fallback: str | None = None) -> str:
    """Prefer DEX image; else Filter flagged image_url."""
    if fallback:
        return str(fallback)
    r = flagged_row(mint)
    if r:
        img = r.get("image_url") or ""
        if img:
            return str(img)
    return ""


def warning_labels(r: dict) -> list[str]:
    """Derive compact badge labels from a flagged row."""
    labels: list[str] = []
    seen: set[str] = set()

    def add(lab: str):
        if lab and lab not in seen:
            seen.add(lab)
            labels.append(lab)

    for rf in r.get("red_flags") or []:
        s = str(rf).lower()
        if s.startswith("lp_weak") or s in ("lp_unlocked", "lp_weak"):
            add("LP weak")
        elif s == "high_vol_liq" or "wash" in s:
            add("Wash")
        elif s == "sell_heavy":
            add("Sell-heavy")
        elif "copycat" in s or s == "copy":
            add("Copycat")
        elif s == "late_chase":
            add("Late")
    holders = r.get("holders") or {}
    for risk in holders.get("risks") or []:
        if isinstance(risk, dict):
            name = str(risk.get("name") or risk.get("level") or "")
        else:
            name = str(risk)
        low = name.lower()
        if "copycat" in low:
            add("Copycat")
        elif "wash" in low:
            add("Wash")
    return labels


def empty_state() -> dict:
    return {
        "session_id": None,
        "status": "idle",
        "started_at": None,
        "ends_at": None,
        "cash_usd": START_BANKROLL,
        "start_bankroll": START_BANKROLL,
        "peak_equity": START_BANKROLL,
        "positions": [],
        "closed": [],
        "events": [],
        "disclaimer": "Paper research only. Not financial advice. No real trades.",
    }


def load_state() -> dict:
    with _lock:
        st = _read_json(STATE_PATH, None)
        base = empty_state()
        if not isinstance(st, dict):
            return base
        # merge so corrupt/partial files never crash the UI
        for k, v in base.items():
            if k not in st:
                st[k] = v
        if not isinstance(st.get("positions"), list):
            st["positions"] = []
        if not isinstance(st.get("closed"), list):
            st["closed"] = []
        if not isinstance(st.get("events"), list):
            st["events"] = []
        try:
            st["cash_usd"] = float(st.get("cash_usd") if st.get("cash_usd") is not None else START_BANKROLL)
        except (TypeError, ValueError):
            st["cash_usd"] = START_BANKROLL
        return st


def save_state(st: dict) -> None:
    with _lock:
        _write_json(STATE_PATH, st)


def load_scores() -> list:
    data = _read_json(SCORES_PATH, [])
    return data if isinstance(data, list) else []


def save_scores(scores: list) -> None:
    _write_json(SCORES_PATH, scores[:MAX_SCORES])


# —— Points, profile, shop (paper cosmetics — no wallets) ——

PROFILE_PATH = DIR / "game_profile.json"
POINTS_FINISH_PROFIT = 80
POINTS_NEW_PEAK = 120
POINTS_BOARD_FIRST = 250
POINTS_EARLY_SELL = -25
POINTS_BLOW_UP = -100
POINTS_DAILY_CAP = 400
EARLY_SELL_SEC = 5 * 60
BLOW_EQUITY_USD = 1.0
PFPS = ("rocket", "star", "flame", "moon")
_MISSING = object()

# Prices match the dopamine v2b shop. Nebula + Plain are owned starters.
SHOP = (
    {"id": "nebula", "name": "Nebula Frame", "price": 0, "tint": "nebula"},
    {"id": "plain", "name": "Plain Booster", "price": 0, "tint": "plain"},
    {"id": "acid", "name": "Acid Trail", "price": 750, "tint": "acid"},
    {"id": "ember", "name": "Ember Skin", "price": 1500, "tint": "ember"},
    {"id": "crown", "name": "Crown Thrusters", "price": 3500, "tint": "crown"},
    {"id": "void", "name": "Void Ghost", "price": 5000, "tint": "void"},
)
_SHOP_BY_ID = {row["id"]: row for row in SHOP}
STARTER_OWNED = ("nebula", "plain")
DEFAULT_EQUIPPED = "nebula"

POINT_RULES = (
    {"id": "finish_profit", "label": "Finish 24h run in profit", "points": POINTS_FINISH_PROFIT},
    {"id": "new_peak", "label": "New personal peak equity", "points": POINTS_NEW_PEAK},
    {"id": "board_first", "label": "Board #1 at settle", "points": POINTS_BOARD_FIRST},
    {"id": "early_sell", "label": "Early sell <5m hold", "points": POINTS_EARLY_SELL},
    {"id": "blow_up", "label": "Blow up (<$1 equity)", "points": POINTS_BLOW_UP},
    {"id": "daily_cap", "label": "Daily earn cap", "points": POINTS_DAILY_CAP, "cap": True},
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def empty_profile() -> dict:
    return {
        "name": "Pilot",
        "pfp": "rocket",
        "points": 0,
        "owned": list(STARTER_OWNED),
        "equipped": DEFAULT_EQUIPPED,
        "personal_peak": START_BANKROLL,
        "earned_today": 0,
        "earned_day": "",
        "settled_sessions": [],
        "ledger": [],
    }


def _clean_profile(raw: dict) -> dict:
    base = empty_profile()
    profile = dict(base)
    if isinstance(raw, dict):
        profile.update({k: raw.get(k) for k in base})
    name = " ".join(str(profile.get("name") or "").split())
    profile["name"] = name[:24] if name else "Pilot"
    pfp = str(profile.get("pfp") or "")
    profile["pfp"] = pfp if pfp in PFPS else "rocket"
    try:
        profile["points"] = int(profile.get("points") or 0)
    except (TypeError, ValueError):
        profile["points"] = 0
    owned = []
    for skin_id in profile.get("owned") or []:
        if skin_id in _SHOP_BY_ID and skin_id not in owned:
            owned.append(skin_id)
    for skin_id in STARTER_OWNED:
        if skin_id not in owned:
            owned.append(skin_id)
    profile["owned"] = owned
    equipped = str(profile.get("equipped") or "")
    profile["equipped"] = equipped if equipped in owned else DEFAULT_EQUIPPED
    try:
        profile["personal_peak"] = float(profile.get("personal_peak") if profile.get("personal_peak") is not None else START_BANKROLL)
    except (TypeError, ValueError):
        profile["personal_peak"] = START_BANKROLL
    try:
        profile["earned_today"] = int(profile.get("earned_today") or 0)
    except (TypeError, ValueError):
        profile["earned_today"] = 0
    profile["earned_day"] = str(profile.get("earned_day") or "")
    settled = [str(s) for s in (profile.get("settled_sessions") or []) if s][-40:]
    profile["settled_sessions"] = settled
    ledger = profile.get("ledger") if isinstance(profile.get("ledger"), list) else []
    profile["ledger"] = [row for row in ledger if isinstance(row, dict)][:30]
    return profile


def load_profile() -> dict:
    with _lock:
        raw = _read_json(PROFILE_PATH, None)
        if not isinstance(raw, dict):
            return empty_profile()
        return _clean_profile(raw)


def save_profile(profile: dict) -> None:
    with _lock:
        _write_json(PROFILE_PATH, _clean_profile(profile))


def _roll_cap(profile: dict, day: str | None = None) -> None:
    day = day or _today()
    if profile.get("earned_day") != day:
        profile["earned_day"] = day
        profile["earned_today"] = 0


def apply_points(profile: dict, amount: int, reason: str, detail: str = "") -> dict | None:
    """Grant or tax points. Positive grants stop at the daily cap. Does not save."""
    _roll_cap(profile)
    try:
        requested = int(amount)
    except (TypeError, ValueError):
        return None
    applied = requested
    capped = False
    if requested > 0:
        room = POINTS_DAILY_CAP - int(profile.get("earned_today") or 0)
        if room < 0:
            room = 0
        if requested > room:
            capped = True
            applied = room
        profile["earned_today"] = int(profile.get("earned_today") or 0) + applied
    if applied == 0:
        return None
    profile["points"] = int(profile.get("points") or 0) + applied
    row = {
        "at": _utc(),
        "reason": reason,
        "requested": requested,
        "applied": applied,
        "capped": capped,
        "balance": profile["points"],
        "detail": detail,
    }
    ledger = list(profile.get("ledger") or [])
    ledger.insert(0, row)
    profile["ledger"] = ledger[:30]
    return row


def _maybe_early_sell_points(pos: dict, reason: str) -> None:
    """Manual close held under 5 minutes. Stop / TP / 24h force are not this tax."""
    if reason != "manual":
        return
    opened = _parse_utc(pos.get("opened_at"))
    if opened is None:
        return
    held = (datetime.now(timezone.utc) - opened).total_seconds()
    if held >= EARLY_SELL_SEC:
        return
    profile = load_profile()
    apply_points(
        profile,
        POINTS_EARLY_SELL,
        "early_sell",
        f"{pos.get('symbol') or 'position'} held {int(max(0, held))}s",
    )
    save_profile(profile)


def settle_run_points(st: dict) -> list[dict]:
    """Award the locked table once when a 24h run ends. Safe to call again."""
    sid = str(st.get("session_id") or "")
    profile = load_profile()
    settled = list(profile.get("settled_sessions") or [])
    if st.get("points_settled") or (sid and sid in settled):
        st["points_settled"] = True
        return []
    events: list[dict] = []
    final = float(st.get("final_cash") if st.get("final_cash") is not None else st.get("cash_usd") or 0)
    start = float(st.get("start_bankroll") or START_BANKROLL)
    if final > start + 1e-9:
        row = apply_points(profile, POINTS_FINISH_PROFIT, "finish_profit", f"Finished ${final:.2f}")
        if row:
            events.append(row)
    try:
        peak = float(st.get("peak_equity") or 0)
    except (TypeError, ValueError):
        peak = 0.0
    best = float(profile.get("personal_peak") or START_BANKROLL)
    if peak > best + 1e-6:
        row = apply_points(profile, POINTS_NEW_PEAK, "new_peak", f"Peak ${peak:.2f}")
        profile["personal_peak"] = peak
        if row:
            events.append(row)
    scores = load_scores()
    if sid and scores and str(scores[0].get("session_id") or "") == sid:
        row = apply_points(profile, POINTS_BOARD_FIRST, "board_first", "Board #1 at settle")
        if row:
            events.append(row)
    if final < BLOW_EQUITY_USD:
        row = apply_points(profile, POINTS_BLOW_UP, "blow_up", f"Equity ${final:.2f}")
        if row:
            events.append(row)
    if sid:
        settled.append(sid)
        profile["settled_sessions"] = settled[-40:]
    save_profile(profile)
    st["points_settled"] = True
    return events


def public_profile(profile: dict | None = None) -> dict:
    profile = _clean_profile(profile) if isinstance(profile, dict) else load_profile()
    bal = int(profile.get("points") or 0)
    owned = set(profile.get("owned") or [])
    equipped = str(profile.get("equipped") or DEFAULT_EQUIPPED)
    shop = []
    for skin in SHOP:
        have = skin["id"] in owned
        price = int(skin["price"])
        shop.append({
            "id": skin["id"],
            "name": skin["name"],
            "price": price,
            "tint": skin["tint"],
            "owned": have,
            "equipped": have and skin["id"] == equipped,
            "locked": (not have) and bal < price,
            "need": 0 if have or bal >= price else price - bal,
        })
    skin = _SHOP_BY_ID.get(equipped) or _SHOP_BY_ID[DEFAULT_EQUIPPED]
    return {
        "name": profile.get("name") or "Pilot",
        "pfp": profile.get("pfp") or "rocket",
        "points": bal,
        "owned": [row["id"] for row in SHOP if row["id"] in owned],
        "equipped": equipped,
        "equipped_name": skin["name"],
        "tint": skin["tint"],
        "personal_peak": profile.get("personal_peak"),
        "earned_today": int(profile.get("earned_today") or 0),
        "daily_cap": POINTS_DAILY_CAP,
        "rules": [dict(row) for row in POINT_RULES],
        "shop": shop,
        "ledger": list(profile.get("ledger") or [])[:12],
        "pfps": list(PFPS),
    }


def update_profile(name=_MISSING, pfp=_MISSING) -> dict:
    if name is _MISSING and pfp is _MISSING:
        return _fail("name or pfp required")
    with _lock:
        profile = load_profile()
        if name is not _MISSING:
            clean = " ".join(str(name or "").split())
            if not clean or len(clean) > 24:
                return _fail("Name must be 1–24 characters", profile=public_profile(profile))
            profile["name"] = clean
        if pfp is not _MISSING:
            if str(pfp or "") not in PFPS:
                return _fail("Pick a picture", profile=public_profile(profile))
            profile["pfp"] = str(pfp)
        save_profile(profile)
        return {"ok": True, "profile": public_profile(profile)}


def buy_cosmetic(skin_id: str) -> dict:
    skin = _SHOP_BY_ID.get(str(skin_id or "").strip())
    if not skin:
        return _fail("Unknown cosmetic")
    with _lock:
        profile = load_profile()
        if skin["id"] in (profile.get("owned") or []):
            return _fail("Already owned", profile=public_profile(profile))
        price = int(skin["price"])
        bal = int(profile.get("points") or 0)
        if bal < price:
            return _fail(
                f"Need {price - bal:,} more pts",
                profile=public_profile(profile),
                need=price - bal,
            )
        profile["points"] = bal - price
        owned = list(profile.get("owned") or [])
        owned.append(skin["id"])
        profile["owned"] = owned
        row = {
            "at": _utc(),
            "reason": "shop_unlock",
            "requested": -price,
            "applied": -price,
            "capped": False,
            "balance": profile["points"],
            "detail": skin["name"],
        }
        ledger = list(profile.get("ledger") or [])
        ledger.insert(0, row)
        profile["ledger"] = ledger[:30]
        save_profile(profile)
        return {"ok": True, "sound": "unlock", "skin": skin["id"], "profile": public_profile(profile)}


def equip_cosmetic(skin_id: str) -> dict:
    skin = _SHOP_BY_ID.get(str(skin_id or "").strip())
    if not skin:
        return _fail("Unknown cosmetic")
    with _lock:
        profile = load_profile()
        if skin["id"] not in (profile.get("owned") or []):
            return _fail("Locked — buy it first", profile=public_profile(profile))
        profile["equipped"] = skin["id"]
        save_profile(profile)
        return {"ok": True, "sound": "equip", "skin": skin["id"], "profile": public_profile(profile)}


def equity(st: dict) -> float:
    eq = float(st.get("cash_usd") or 0)
    for p in st.get("positions") or []:
        px = _f(p.get("last_price_usd")) or _f(p.get("entry_price_usd")) or 0
        qty = float(p.get("qty_tokens") or 0)
        eq += px * qty
    return round(eq, 6)


def _push_event(st: dict, kind: str, msg: str, **extra) -> None:
    ev = {"at": _utc(), "kind": kind, "msg": msg, **extra}
    evs = list(st.get("events") or [])
    evs.insert(0, ev)
    st["events"] = evs[:40]


def _fail(error: str, st: dict | None = None, **extra) -> dict:
    """Error payload. `sound` is a UI hint only — no trade or balance is invented."""
    out: dict = {"ok": False, "error": error, "sound": "fail"}
    if st is not None:
        out["state"] = st
    out.update(extra)
    return out


def _run_expired(st: dict, now: datetime | None = None) -> bool:
    """True when an active 24h clock is done, including a missing clock.

    The panel prints remain as whole seconds, so the last partial second
    already reads 00:00:00. That display is the end of the run.
    """
    if st.get("status") != "active":
        return False
    now = now or datetime.now(timezone.utc)
    ends = _parse_utc(st.get("ends_at"))
    if ends is None:
        return True
    return (ends - now).total_seconds() < 1.0


def _force_close_opens(st: dict) -> None:
    """Close whatever is still open at the last mark. Caller holds _lock."""
    for pos in list(st.get("positions") or []):
        px = _f(pos.get("last_price_usd")) or _f(pos.get("entry_price_usd")) or 0.0
        _close_position(st, pos, float(px), "force_24h")
    st["positions"] = []


def _mark_ended(st: dict) -> None:
    """Flip an active run to ended. Caller holds _lock and has closed opens."""
    if st.get("status") == "ended":
        st["positions"] = []
        return
    st["positions"] = []
    st["status"] = "ended"
    st["final_cash"] = round(float(st.get("cash_usd") or 0), 6)
    st["ended_at"] = st.get("ended_at") or _utc()
    _record_score(st)
    settle_run_points(st)
    _push_event(st, "end", f"24h over — final ${st['final_cash']:.3f}")


def force_end_session() -> dict:
    """Close the active 24h run the same way the clock does.

    Does not start another run. A future ends_at is pulled back so tick_session
    takes the natural force-sell path (live mark when DEX answers, else last mark).
    """
    with _lock:
        st = load_state()
        if st.get("status") != "active":
            return st
        past = datetime.now(timezone.utc) - timedelta(seconds=2)
        st["ends_at"] = past.strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(st)
        return tick_session(st)


def start_session(force: bool = False) -> dict:
    with _lock:
        st = load_state()
        if st.get("status") == "active" and not force:
            st = tick_session(st)
            if st.get("status") == "active":
                return st
        now = datetime.now(timezone.utc)
        ends = now + timedelta(hours=SESSION_HOURS)
        st = {
            "session_id": uuid.uuid4().hex[:12],
            "status": "active",
            "started_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ends_at": ends.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cash_usd": START_BANKROLL,
            "start_bankroll": START_BANKROLL,
            "peak_equity": START_BANKROLL,
            "positions": [],
            "closed": [],
            "events": [],
            "disclaimer": "Paper research only. Not financial advice. No real trades.",
        }
        _push_event(st, "start", f"New 24h run — ${START_BANKROLL:.2f} paper bankroll")
        save_state(st)
        return st


def _close_position(st: dict, pos: dict, price: float, reason: str) -> None:
    qty = float(pos.get("qty_tokens") or 0)
    proceeds = round(price * qty, 6)
    entry = float(pos.get("entry_price_usd") or 0)
    stake = float(pos.get("stake_usd") or 0)
    pnl = round(proceeds - stake, 6)
    pnl_pct = round((price / entry - 1.0) * 100, 2) if entry else 0.0
    st["cash_usd"] = round(float(st.get("cash_usd") or 0) + proceeds, 6)
    closed = {
        **pos,
        "exit_price_usd": price,
        "exit_usd": proceeds,
        "pnl_usd": pnl,
        "pnl_pct": pnl_pct,
        "closed_at": _utc(),
        "close_reason": reason,
    }
    st.setdefault("closed", []).insert(0, closed)
    st["positions"] = [p for p in (st.get("positions") or []) if p.get("id") != pos.get("id")]
    _push_event(
        st,
        reason,
        f"{pos.get('symbol')} {reason} · PnL ${pnl:+.3f} ({pnl_pct:+.1f}%)",
        position_id=pos.get("id"),
        pnl_usd=pnl,
    )
    _maybe_early_sell_points(pos, reason)


def tick_session(st: dict | None = None) -> dict:
    """Refresh open prices, hit SL/TP, force-close at 24h."""
    with _lock:
        if st is None:
            st = load_state()
        if st.get("status") != "active":
            return st

        now = datetime.now(timezone.utc)
        timed_out = _run_expired(st, now)

        mints = list({p.get("mint") for p in (st.get("positions") or []) if p.get("mint")})
        quotes: dict[str, dict] = {}
        if mints:
            futs = {_fetch_pool.submit(fetch_dex_token, m): m for m in mints}
            try:
                for fut in as_completed(futs, timeout=max(6.0, _DEX_TIMEOUT + 1)):
                    m = futs[fut]
                    try:
                        q = fut.result()
                    except Exception:
                        q = None
                    if q and q.get("price_usd"):
                        quotes[m] = q
            except TimeoutError:
                pass

        still = []
        for pos in list(st.get("positions") or []):
            mint = pos.get("mint")
            q = quotes.get(mint) if mint else None
            px = _f(q.get("price_usd")) if q else None
            if px is None or not (q and q.get("quote_live")):
                px = _f(pos.get("last_price_usd")) or _f(pos.get("entry_price_usd"))
            else:
                pos["last_price_usd"] = px
                pos["quote_epoch"] = q.get("quoted_epoch")
                pos["quoted_at"] = q.get("quoted_at")
                if q.get("pair_url"):
                    pos["pair_url"] = q.get("pair_url")
                prog = q.get("mcap_progress")
                if isinstance(prog, dict) and prog.get("now_source") == "dex" and prog.get("now_available"):
                    # Price and NOW are this same pair. A previous print is not kept.
                    pos["mcap_progress"] = dict(prog)
                    pos["market_cap_usd"] = prog.get("market_cap_usd")
                else:
                    pos["mcap_progress"] = hide_live_now(
                        pos.get("mcap_progress") if isinstance(pos.get("mcap_progress"), dict) else None
                    )
                    pos["market_cap_usd"] = None
            if q and q.get("quote_live"):
                if q.get("image_url") and not pos.get("image_url"):
                    pos["image_url"] = q.get("image_url")
                if q.get("name") and (not pos.get("name") or pos.get("name") == "?"):
                    pos["name"] = q.get("name")
                if q.get("symbol") and (not pos.get("symbol") or pos.get("symbol") == "?"):
                    pos["symbol"] = q.get("symbol")
            if not pos.get("image_url") and mint:
                img = image_for_mint(mint)
                if img:
                    pos["image_url"] = img
            entry = float(pos.get("entry_price_usd") or 0)
            if timed_out:
                _close_position(st, pos, float(px or entry or 0), "force_24h")
                continue
            sl = pos.get("sl_pct")
            tp = pos.get("tp_pct")
            if entry and px:
                chg = (px / entry - 1.0) * 100
                if sl is not None and chg <= -abs(float(sl)):
                    _close_position(st, pos, px, "stop")
                    continue
                if tp is not None and chg >= abs(float(tp)):
                    _close_position(st, pos, px, "tp")
                    continue
            still.append(pos)
        st["positions"] = still

        eq = equity(st)
        if eq > float(st.get("peak_equity") or 0):
            st["peak_equity"] = eq
            _push_event(st, "high", f"New equity high ${eq:.3f}")

        if timed_out:
            _force_close_opens(st)
            _mark_ended(st)

        save_state(st)
        return st


def _record_score(st: dict) -> None:
    final = float(st.get("final_cash") if st.get("final_cash") is not None else st.get("cash_usd") or 0)
    start = float(st.get("start_bankroll") or START_BANKROLL)
    pct = round((final / start - 1.0) * 100, 2) if start else 0.0
    row = {
        "session_id": st.get("session_id"),
        "started_at": st.get("started_at"),
        "ended_at": st.get("ended_at") or _utc(),
        "final_cash": round(final, 6),
        "pct_gain": pct,
        "trades": len(st.get("closed") or []),
        "peak_equity": st.get("peak_equity"),
    }
    scores = load_scores()
    scores = [s for s in scores if s.get("session_id") != row["session_id"]]
    scores.append(row)
    scores.sort(key=lambda s: float(s.get("final_cash") or 0), reverse=True)
    save_scores(scores)


def buy(mint: str, stake_usd: float, sl_pct: float | None = None, tp_pct: float | None = None) -> dict:
    with _lock:
        st = tick_session()
        if st.get("status") != "active":
            return _fail("No active session — POST /api/game/start first", st)
        try:
            stake = float(stake_usd)
        except (TypeError, ValueError):
            return _fail("stake_usd required", st)
        if stake <= 0:
            return _fail("stake must be > 0", st)
        cash = float(st.get("cash_usd") or 0)
        if stake > cash + 1e-9:
            return _fail(f"Need ${stake:.3f} but cash is ${cash:.3f}", st)
        resolved, err, src = resolve_mint(mint)
        if not resolved:
            return _fail(err or "mint required", st)
        mint = resolved
        q = fetch_dex_token(mint, bypass_cache=True)
        if not q or not q.get("price_usd"):
            return _fail(
                "No DEX Screener quote for that mint — try again or paste CA from Filter",
                st,
                resolved_mint=mint,
                resolve_source=src,
            )
        px = float(q["price_usd"])
        if px <= 0:
            return _fail("Bad price", st)
        qty = stake / px
        pos = {
            "id": uuid.uuid4().hex[:10],
            "mint": mint,
            "symbol": q.get("symbol") or "?",
            "name": q.get("name") or "?",
            "stake_usd": round(stake, 6),
            "entry_price_usd": px,
            "qty_tokens": qty,
            "sl_pct": float(sl_pct) if sl_pct is not None else None,
            "tp_pct": float(tp_pct) if tp_pct is not None else None,
            "opened_at": _utc(),
            "pair_url": q.get("pair_url"),
            "image_url": image_for_mint(mint, q.get("image_url") or None),
            "last_price_usd": px,
            "quote_epoch": q.get("quoted_epoch"),
            "quoted_at": q.get("quoted_at"),
            "red_flags": flags_for_mint(mint),
            "ca_short": (mint[:4] + "…" + mint[-4:]) if len(mint) > 10 else mint,
            "market_cap_usd": q.get("market_cap_usd"),
            "mcap_progress": q.get("mcap_progress"),
        }
        st["cash_usd"] = round(cash - stake, 6)
        st.setdefault("positions", []).append(pos)
        _push_event(st, "buy", f"Bought {pos['symbol']} ${stake:.3f} @ {px}", position_id=pos["id"])
        save_state(st)
        return {
            "ok": True,
            "position": pos,
            "state": st,
            "sound": "buy",
            "resolved_mint": mint,
            "resolve_source": src,
        }


def sell(position_id: str) -> dict:
    with _lock:
        st = tick_session()
        if st.get("status") != "active":
            return _fail("No active session", st)
        pos = next((p for p in (st.get("positions") or []) if p.get("id") == position_id), None)
        if not pos:
            return _fail("Position not found", st)
        q = fetch_dex_token(pos["mint"])
        px = _f(q.get("price_usd")) if q else None
        if px is None:
            px = _f(pos.get("last_price_usd")) or _f(pos.get("entry_price_usd")) or 0
        _close_position(st, pos, float(px), "manual")
        save_state(st)
        return {"ok": True, "state": st, "sound": "sell"}


def present_open_position(pos: dict, now: float | None = None) -> dict:
    """Public position. Live USD and DEX NOW only while the quote is inside the SLA."""
    pp = dict(pos)
    clock = time.time() if now is None else now
    epoch = _f(pp.get("quote_epoch"))
    age = None if epoch is None else max(0.0, clock - epoch)
    px = _f(pp.get("last_price_usd"))
    fresh = age is not None and age <= QUOTE_FRESH_SLA_S and px is not None and px > 0
    pp["quote_sla_s"] = QUOTE_FRESH_SLA_S
    pp["quote_band"] = QUOTE_BAND
    pp["quote_age_s"] = None if age is None else round(age, 3)
    if not fresh:
        pp["quote_live"] = False
        pp["live_price_usd"] = None
        pp["mcap_progress"] = hide_live_now(pp.get("mcap_progress") if isinstance(pp.get("mcap_progress"), dict) else None)
        pp["market_cap_usd"] = None
        return pp
    pp["quote_live"] = True
    pp["live_price_usd"] = px
    prog = pp.get("mcap_progress")
    if isinstance(prog, dict) and prog.get("now_source") == "dex" and not prog.get("now_available"):
        pp["mcap_progress"] = hide_live_now(prog)
        pp["market_cap_usd"] = None
    return pp


def attach_live_dex_quote(row: dict | None, mint: str, fresh: bool = False) -> dict:
    """Use the Game DEX quote for a Filter/scan row, or keep an honest snapshot.

    NOW labeled DEX Screener is the fresh token-endpoint market cap (same pair
    as the DEX link and the USD price). A stored number outside ±5% of that
    quote is not left under the DEX label. If the quote fails or is past the
    SLA, the row stays a filter snapshot and is not labeled live DEX.
    """
    base = dict(row) if isinstance(row, dict) else {}
    q = fetch_dex_token((mint or "").strip(), bypass_cache=fresh)
    prog = q.get("mcap_progress") if isinstance(q, dict) else None
    px = _f(q.get("price_usd")) if isinstance(q, dict) else None
    quote_mcap = _f(prog.get("market_cap_usd")) if isinstance(prog, dict) else None
    live_px = bool(isinstance(q, dict) and q.get("quote_live") and px is not None and px > 0)
    live_mcap = bool(
        live_px
        and isinstance(prog, dict)
        and prog.get("now_source") == "dex"
        and prog.get("now_available")
        and quote_mcap is not None
    )
    metrics0 = base.get("metrics") if isinstance(base.get("metrics"), dict) else {}
    if not live_px and not live_mcap:
        out = apply_mcap(base, None)
        out["quote_live"] = False
        out["quote_sla_s"] = QUOTE_FRESH_SLA_S
        out["quote_band"] = QUOTE_BAND
        snap = out.get("mcap_progress")
        if isinstance(snap, dict) and snap.get("now_source") == "dex":
            snap = dict(snap)
            snap["now_source"] = "snapshot"
            out["mcap_progress"] = snap
        return out

    out = dict(base)
    metrics = dict(metrics0)
    if live_mcap:
        merged = merge_live_progress(base, prog)
        for key in (
            "market_cap_usd",
            "fdv_usd",
            "bonding_mcap_usd",
            "graduation_mcap_usd",
            "ath_mcap_usd",
            "market_cap_from_fdv",
        ):
            metrics[key] = merged.get(key)
        out["mcap_progress"] = merged
        out["market_cap_usd"] = merged.get("market_cap_usd")
    else:
        snap = apply_mcap(base, None)
        out["mcap_progress"] = snap.get("mcap_progress")
        out["market_cap_usd"] = (out.get("mcap_progress") or {}).get("market_cap_usd")
        metrics["market_cap_usd"] = out["market_cap_usd"]
    if live_px:
        metrics["price_usd"] = px
        out["price_usd"] = px
        out["quote_live"] = True
        out["quoted_at"] = q.get("quoted_at")
        out["quote_epoch"] = q.get("quoted_epoch")
    else:
        out["quote_live"] = False
    out["metrics"] = metrics
    url = q.get("pair_url") if isinstance(q, dict) else None
    if url and live_mcap:
        out["dex_url"] = url
        links = dict(out.get("links") or {}) if isinstance(out.get("links"), dict) else {}
        links["dex"] = url
        out["links"] = links
    out["quote_sla_s"] = QUOTE_FRESH_SLA_S
    out["quote_band"] = QUOTE_BAND
    return out


def public_state(st: dict | None = None) -> dict:
    st = tick_session(st) if st is None else st
    eq = equity(st)
    ends = _parse_utc(st.get("ends_at"))
    now = datetime.now(timezone.utc)
    remain_s = int((ends - now).total_seconds()) if ends and st.get("status") == "active" else 0
    if remain_s < 0:
        remain_s = 0
    out = dict(st)
    out["equity_usd"] = eq
    out["pnl_usd"] = round(eq - float(st.get("start_bankroll") or START_BANKROLL), 6)
    out["pnl_pct"] = round(
        (eq / float(st.get("start_bankroll") or START_BANKROLL) - 1.0) * 100, 2
    ) if float(st.get("start_bankroll") or 0) else 0.0
    out["remain_seconds"] = remain_s
    out["scores_top"] = load_scores()[:10]
    # attach live pnl fields so UI never recomputes wrong
    enriched = []
    for p in list(out.get("positions") or []):
        pp = present_open_position(p, now=now.timestamp())
        entry = _f(pp.get("entry_price_usd")) or 0.0
        last = _f(pp.get("last_price_usd")) or entry
        stake = float(pp.get("stake_usd") or 0)
        mark = stake * (last / entry) if entry else stake
        pnl_usd = mark - stake
        pnl_pct = ((last / entry) - 1.0) * 100 if entry else 0.0
        pp["mark_usd"] = round(mark, 6)
        pp["pnl_usd"] = round(pnl_usd, 6)
        pp["pnl_pct"] = round(pnl_pct, 2)
        if not pp.get("image_url") and pp.get("mint"):
            img = image_for_mint(str(pp["mint"]))
            if img:
                pp["image_url"] = img
        enriched.append(pp)
    out["positions"] = enriched
    out["open_count"] = len(enriched)
    out["server_time"] = _utc()
    out["profile"] = public_profile()
    return out


# Display-only equity step for the Game QA hook. Must match QA_TICK_STEP_USD in
# static/index.html. DexScreener marks often stay inside the client TICK_EPS
# ($0.005) for minutes, so a poll never crosses tick+ / nose-down. The page
# query ?qa_tick=both|up|down sequences these offsets. Nothing here is saved.
QA_TICK_STEP_USD = 0.12


def qa_tick_view(payload: dict | None, mode: str | None) -> dict:
    """Return a copy of a public game state with one optional equity offset.

    `up` adds QA_TICK_STEP_USD, `down` subtracts it, `flat` leaves the mark and
    labels the poll as the scripted loss (the previous poll was `up`). Any
    other mode, including empty, is the live mark with no QA label. Inactive
    sessions are not shifted. The caller's dict is not mutated and nothing is saved.
    """
    out = dict(payload or {})
    token = (mode or "").strip().lower()
    if token not in ("up", "down", "flat"):
        return out
    if out.get("status") != "active":
        out["qa_tick"] = "need-active"
        return out
    base = _f(out.get("equity_usd"))
    if base is None:
        out["qa_tick"] = "need-active"
        return out
    if token == "flat":
        out["qa_tick"] = "down"
        out["qa_tick_delta_usd"] = 0
        return out
    delta = QA_TICK_STEP_USD if token == "up" else -QA_TICK_STEP_USD
    eq = round(base + delta, 6)
    out["equity_usd"] = eq
    pnl = _f(out.get("pnl_usd"))
    if pnl is not None:
        out["pnl_usd"] = round(pnl + delta, 6)
    start = _f(out.get("start_bankroll"))
    if start:
        out["pnl_pct"] = round((eq / start - 1.0) * 100, 2)
    out["qa_tick"] = token
    out["qa_tick_delta_usd"] = delta
    return out


def sell_all() -> dict:
    with _lock:
        st = tick_session()
        if st.get("status") != "active":
            return _fail("No active session", st)
        ids = [p.get("id") for p in list(st.get("positions") or []) if p.get("id")]
        if not ids:
            return _fail("No open positions", st)
        sold = 0
        for pid in ids:
            # re-tick each time so prices stay fresh
            st = tick_session()
            pos = next((p for p in (st.get("positions") or []) if p.get("id") == pid), None)
            if not pos:
                continue
            q = fetch_dex_token(pos["mint"], bypass_cache=True)
            px = _f(q.get("price_usd")) if q else None
            if px is None:
                px = _f(pos.get("last_price_usd")) or _f(pos.get("entry_price_usd")) or 0
            _close_position(st, pos, float(px), "manual")
            sold += 1
        save_state(st)
        return {"ok": True, "sold": sold, "state": st, "sound": "sell"}


def append_paper_trade(
    mint: str,
    stake_usd: float,
    sl_pct: float,
    tp_pct: float,
    user_note: str | None = None,
) -> dict:
    """Append open row to papertrades.csv. Requires stake + SL% + TP%."""
    try:
        stake = float(stake_usd)
        sl = float(sl_pct)
        tp = float(tp_pct)
    except (TypeError, ValueError):
        return {"ok": False, "error": "stake_usd, stop_loss_pct, take_profit_pct all required numbers"}
    if stake <= 0:
        return {"ok": False, "error": "stake_usd must be > 0"}
    mint = (mint or "").strip()
    if len(mint) < 32:
        return {"ok": False, "error": "mint/CA required"}
    q = fetch_dex_token(mint)
    if not q:
        return {"ok": False, "error": "Could not fetch DEX Screener quote for mint"}
    path = _paper_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "id,opened_utc,symbol,name,chain,address,pair_url,"
        "entry_price_usd,entry_mcap,entry_liq,stake_usd,user_note,"
        "status,closed_utc,exit_price_usd\n"
    )
    next_id = 1
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[1:]:
                if not line.strip():
                    continue
                try:
                    next_id = max(next_id, int(line.split(",", 1)[0]) + 1)
                except ValueError:
                    pass
        except OSError:
            pass
    else:
        path.write_text(header, encoding="utf-8")

    note_bits = [f"Panel paper; SL {sl:g}% / TP {tp:g}%"]
    if user_note:
        note_bits.append(str(user_note).strip())
    note = "; ".join(note_bits).replace('"', "'")

    def csv_num(v):
        if v is None:
            return ""
        return str(v)

    import csv
    from io import StringIO

    row = [
        str(next_id),
        _utc(),
        q.get("symbol") or "?",
        q.get("name") or "?",
        "solana",
        mint,
        q.get("pair_url") or "",
        csv_num(q.get("price_usd")),
        csv_num(q.get("market_cap_usd")),
        csv_num(q.get("liquidity_usd")),
        csv_num(stake),
        note,
        "open",
        "",
        "",
    ]
    buf = StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(row)
    with path.open("a", encoding="utf-8") as f:
        f.write(buf.getvalue())
    return {
        "ok": True,
        "id": next_id,
        "path": str(path),
        "symbol": q.get("symbol"),
        "entry_price_usd": q.get("price_usd"),
        "stake_usd": stake,
        "stop_loss_pct": sl,
        "take_profit_pct": tp,
        "status": "open",
        "opened_utc": _utc(),
    }


def quote_payload(mint: str, fresh: bool = False) -> dict:
    resolved, err, src = resolve_mint(mint)
    if not resolved:
        return _fail(err or "No quote")
    q = fetch_dex_token(resolved, bypass_cache=fresh)
    if not q or not q.get("quote_live") or not q.get("price_usd"):
        return _fail(
            "No live USD price on DEX Screener — token may be too new, illiquid, or delisted. Paste CA from Filter Play.",
            resolved_mint=resolved,
            resolve_source=src,
        )
    note = None
    qsym = (q.get("symbol") or "").strip()
    raw_q = (mint or "").strip()
    if src == "dex_search":
        note = f"Resolved via DEX search → {qsym} (best Solana name/ticker match by liq). Paste full CA if wrong coin."
    elif src == "flagged":
        note = f"Resolved from your Filter flags → {qsym}."
    elif src == "mint" and looks_like_mint(raw_q) and qsym:
        note = f"CA quote · {qsym}"
    return {
        "ok": True,
        "quote": q,
        "resolved_mint": resolved,
        "resolve_source": src,
        "resolve_note": note,
        "red_flags": flags_for_mint(resolved),
        "disclaimer": "Paper research only.",
    }
