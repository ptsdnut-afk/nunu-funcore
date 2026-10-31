#!/usr/bin/env python3
"""
24h $5 paper game + DEX quote helpers (research only — no wallets/orders).
"""

from __future__ import annotations

import base64
import contextvars
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
_QUOTE_TTL = 2.0  # seconds — happy-path cache so polls do not refetch every tick
# Labeled live USD may only show a quote fetched inside this window.
# A failed refresh may reuse the cache while it is still inside the SLA.
# Older than the SLA: not a fill price. The panel shows "stale Ns".
QUOTE_FRESH_SLA_S = 5.0
QUOTE_BAND = 0.05  # ±5% vs the same-path DexScreener quote, else not a live DEX print
_DEX_TIMEOUT = 3.5
# SHIP W2 — execution price. Jupiter Price API v3 usdPrice is the mid.
# A SOL→token swap quote is not the tick: impact and fees sit 6–9% above Dex.
# GeckoTerminal is not a fallback. Browsers never call Jupiter.
# Many clients share one fetch per mint for LIVE_TICK_TTL_S (single-flight).
LIVE_TICK_TTL_S = 1.0
_LIVE_TIMEOUT = 2.0
SOL_MINT = "So11111111111111111111111111111111111111112"
JUP_PRICE_URL = "https://lite-api.jup.ag/price/v3?ids={mint}"
# QA only. ?qa_tick=down walks one browser session's copy of the tick.
# The shared Jupiter cache is never written with this price.
QA_WALK_COOKIE = "nunu_qa_walk"
QA_WALK_DROP = 0.27  # 27% so the print is down 25%+ by the end of the span
QA_WALK_SPAN_S = 20.0
_live_cache: dict[str, tuple[float, dict]] = {}
_live_backoff_until: dict[str, float] = {}
_live_backoff_s: dict[str, float] = {}
_live_guard = threading.Lock()
_live_locks: dict[str, threading.Lock] = {}
_qa_walks: dict[str, dict[str, dict]] = {}
_qa_walk_guard = threading.Lock()
_qa_sid: contextvars.ContextVar[str | None] = contextvars.ContextVar("nunu_qa_sid", default=None)
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


def _live_lock(mint: str) -> threading.Lock:
    with _live_guard:
        lk = _live_locks.get(mint)
        if lk is None:
            lk = threading.Lock()
            _live_locks[mint] = lk
        return lk


def _http_json(url: str) -> tuple[int, dict | None]:
    """GET JSON. 429 is returned as a status so the caller can back off."""
    req = Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urlopen(req, timeout=_LIVE_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            code = int(getattr(resp, "status", 200) or 200)
            return code, raw if isinstance(raw, dict) else None
    except HTTPError as exc:
        return int(exc.code or 0), None
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        return 0, None


def _arm_live_backoff(mint: str, now: float) -> None:
    delay = _live_backoff_s.get(mint, 1.0)
    delay = min(20.0, max(2.0, delay * 2))
    _live_backoff_s[mint] = delay
    _live_backoff_until[mint] = now + delay


def _clear_live_backoff(mint: str) -> None:
    _live_backoff_s.pop(mint, None)
    _live_backoff_until.pop(mint, None)


def _jup_row(raw: dict | None, mint: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    row = raw.get(mint)
    return row if isinstance(row, dict) else None


def _parse_jup_price(raw: dict | None, mint: str) -> tuple[float | None, int | None]:
    row = _jup_row(raw, mint)
    if not row:
        return None, None
    block = row.get("blockId")
    try:
        block_i = int(block) if block is not None else None
    except (TypeError, ValueError):
        block_i = None
    return _f(row.get("usdPrice")), block_i


def _jup_decimals(raw: dict | None, mint: str) -> int | None:
    row = _jup_row(raw, mint)
    if not row or row.get("decimals") is None:
        return None
    try:
        dec = int(row.get("decimals"))
    except (TypeError, ValueError):
        return None
    if dec < 0 or dec > 18:
        return None
    return dec


def _jupiter_price(mint: str) -> tuple[float | None, int | None, bool]:
    """Jupiter Price API mid in USD. One request. (price, block, rate_limited).

    usdPrice is the pool mid. A one-way swap quote is not used: its price
    impact and fees print 6–9% above Dexscreener.
    """
    code, raw = _http_json(JUP_PRICE_URL.format(mint=mint))
    if code == 429:
        return None, None, True
    px, block = _parse_jup_price(raw, mint)
    if px is not None and px > 0:
        return px, block, False
    return None, block, code == 429


def _pull_live_price(mint: str) -> tuple[float | None, str, int | None, bool]:
    """Return (price, source, block_id, rate_limited). Jupiter only."""
    px, block, limited = _jupiter_price(mint)
    if px is not None and px > 0:
        return px, "jup", block, False
    return None, "", block, limited


def _decorate_live(payload: dict, fetched_at: float, now: float) -> dict:
    out = dict(payload)
    age = max(0.0, now - fetched_at)
    px = _f(out.get("price_usd"))
    out["quoted_epoch"] = fetched_at
    out["quote_age_s"] = round(age, 3)
    out["quote_sla_s"] = QUOTE_FRESH_SLA_S
    out["price_ts"] = out.get("price_ts") or _utc()
    out["quoted_at"] = out["price_ts"]
    live = px is not None and px > 0 and age <= QUOTE_FRESH_SLA_S
    out["quote_live"] = live
    if not live:
        out["price_usd"] = px if px is not None and px > 0 else None
    return out


def tick_is_stale(age, sla: float | None = None) -> bool:
    """True exactly when age > quote SLA. A missing age is not that comparison."""
    limit = QUOTE_FRESH_SLA_S if sla is None else sla
    try:
        age_f = float(age)
        limit_f = float(limit)
    except (TypeError, ValueError):
        return False
    return age_f > limit_f


def fillable_tick(tick: dict | None) -> bool:
    """True when this tick may be a buy, sell, or stop fill."""
    if not isinstance(tick, dict) or not tick.get("quote_live"):
        return False
    px = _f(tick.get("price_usd"))
    age = _f(tick.get("quote_age_s"))
    if tick_is_stale(age):
        return False
    return px is not None and px > 0 and age is not None and age <= QUOTE_FRESH_SLA_S


def fill_price(tick: dict | None) -> float | None:
    """USD price a fill may use. None when the tick is not fillable.

    Receipts and other callers read the fill through here. How a tick
    becomes fillable stays in fillable_tick / fetch_live_tick.
    """
    if not fillable_tick(tick) or not isinstance(tick, dict):
        return None
    px = _f(tick.get("price_usd"))
    if px is None or px <= 0:
        return None
    return float(px)


def fill_quote_age_s(tick: dict | None) -> float | None:
    """Quote age already stamped on this fill tick. Not recomputed later."""
    if not isinstance(tick, dict) or not fillable_tick(tick):
        return None
    return _f(tick.get("quote_age_s"))


def set_qa_walk_sid(sid: str | None) -> None:
    """Bind the QA price walk to this request. None clears it."""
    _qa_sid.set(sid or None)


def qa_walk_armed(sid: str | None) -> bool:
    if not sid:
        return False
    with _qa_walk_guard:
        return sid in _qa_walks


def _qa_overlay(sid: str | None, tick: dict | None) -> dict | None:
    """Copy of tick, walked down for this QA session. The shared cache is not written."""
    if not sid or not isinstance(tick, dict):
        return tick
    px = _f(tick.get("price_usd"))
    mint = str(tick.get("mint") or "")
    if px is None or px <= 0 or not mint:
        return tick
    now = time.time()
    with _qa_walk_guard:
        book = _qa_walks.setdefault(sid, {})
        row = book.get(mint)
        if row is None:
            row = {"t0": now, "px0": px}
            book[mint] = row
        t0 = float(row["t0"])
        px0 = float(row["px0"])
    if px0 <= 0:
        return tick
    frac = min(1.0, max(0.0, (now - t0) / QA_WALK_SPAN_S))
    walked = px0 * (1.0 - QA_WALK_DROP * frac)
    out = dict(tick)
    out["price_usd"] = walked
    out["source"] = "qa_tick"
    out["qa_tick"] = "down"
    out["qa_walk_drop"] = round(1.0 - (walked / px0), 6)
    out["price_ts"] = _utc()
    out["quoted_at"] = out["price_ts"]
    return out


def fetch_live_tick(mint: str, bypass_cache: bool = False, qa_sid: str | None = None) -> dict | None:
    """Fresh USD price for one mint.

    Cache hits younger than LIVE_TICK_TTL_S do not call upstream. A 429 or a
    transport error backs off and may reuse the last tick only while it is
    still inside QUOTE_FRESH_SLA_S. Older than that, quote_live is false.
    """
    mint = (mint or "").strip()
    if not mint or len(mint) < 32:
        return None
    with _live_lock(mint):
        now = time.time()
        hit = _live_cache.get(mint)
        if now < _live_backoff_until.get(mint, 0):
            if hit:
                return _qa_overlay(qa_sid or _qa_sid.get(), _decorate_live(hit[1], hit[0], now))
            return None
        if not bypass_cache and hit and now - hit[0] < LIVE_TICK_TTL_S:
            return _qa_overlay(qa_sid or _qa_sid.get(), _decorate_live(hit[1], hit[0], now))
        px, source, block, limited = _pull_live_price(mint)
        if px is None or px <= 0:
            _arm_live_backoff(mint, now)
            if hit:
                return _qa_overlay(qa_sid or _qa_sid.get(), _decorate_live(hit[1], hit[0], now))
            return None
        _clear_live_backoff(mint)
        payload = {
            "mint": mint,
            "price_usd": px,
            "price_ts": _utc(),
            "source": source,
            "block_id": block,
            "rate_limited": limited,
        }
        _live_cache[mint] = (now, payload)
        fresh = _decorate_live(payload, now, now)
        return _qa_overlay(qa_sid or _qa_sid.get(), fresh)


def _apply_live_tick(quote: dict, tick: dict) -> dict:
    """Replace any Dex priceUsd with the live tick. The card and the fill match."""
    out = dict(quote)
    px = float(tick["price_usd"])
    out["price_usd"] = px
    out["price_ts"] = tick.get("price_ts")
    out["quoted_at"] = tick.get("price_ts")
    out["quoted_epoch"] = tick.get("quoted_epoch")
    out["quote_age_s"] = tick.get("quote_age_s")
    out["quote_sla_s"] = QUOTE_FRESH_SLA_S
    out["quote_live"] = True
    out["price_source"] = tick.get("source")
    return out


def _position_for_request(st: dict, request_id: str) -> dict | None:
    rid = (request_id or "").strip()
    if not rid:
        return None
    for bucket in (st.get("positions") or [], st.get("closed") or []):
        for pos in bucket:
            if isinstance(pos, dict) and pos.get("client_request_id") == rid:
                return pos
    return None


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
        "receipts": [],
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
        if not isinstance(st.get("receipts"), list):
            st["receipts"] = []
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
CUSTOM_PFP = "custom"
# Upload cap is what the file picker may hand us. Stored cap is the cropped still
# kept on the same paper profile as the name (data URL, not a second account).
PFP_FILE_MAX_BYTES = 8 * 1024 * 1024
PFP_DATA_MAX_CHARS = 120_000
PFP_IMAGE_MAX_BYTES = 90_000
_PFP_MIMES = ("image/png", "image/jpeg", "image/webp")
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


def _image_magic_ok(mime: str, blob: bytes) -> bool:
    if mime == "image/jpeg":
        return blob.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return blob.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return len(blob) >= 12 and blob.startswith(b"RIFF") and blob[8:12] == b"WEBP"
    return False


def normalize_pfp_data(raw) -> tuple[str | None, str | None]:
    """Return (data_url, error). A bad file does not produce a URL."""
    if not isinstance(raw, str):
        return None, "That file is not a valid image. Use a PNG, JPEG, or WebP."
    text = "".join(raw.split())
    if len(text) > PFP_DATA_MAX_CHARS:
        return None, (
            "Picture is too large. Upload up to 8 MB; the saved still must stay under "
            f"{PFP_DATA_MAX_CHARS // 1000} KB."
        )
    if not text.startswith("data:image/") or "," not in text:
        return None, "That file is not a valid image. Use a PNG, JPEG, or WebP."
    header, b64 = text.split(",", 1)
    if not header.endswith(";base64"):
        return None, "That file is not a valid image. Use a PNG, JPEG, or WebP."
    mime = header[5:-7].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in _PFP_MIMES:
        return None, "That file is not a valid image. Use a PNG, JPEG, or WebP."
    try:
        blob = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError):
        return None, "That file is not a valid image. Use a PNG, JPEG, or WebP."
    if not blob:
        return None, "That file is not a valid image. Use a PNG, JPEG, or WebP."
    if len(blob) > PFP_IMAGE_MAX_BYTES:
        return None, (
            "Picture is too large. Upload up to 8 MB; the saved still must stay under "
            f"{PFP_DATA_MAX_CHARS // 1000} KB."
        )
    if not _image_magic_ok(mime, blob):
        return None, "That file is not a valid image. Use a PNG, JPEG, or WebP."
    canon = "data:" + mime + ";base64," + base64.b64encode(blob).decode("ascii")
    if len(canon) > PFP_DATA_MAX_CHARS:
        return None, (
            "Picture is too large. Upload up to 8 MB; the saved still must stay under "
            f"{PFP_DATA_MAX_CHARS // 1000} KB."
        )
    return canon, None


def empty_profile() -> dict:
    return {
        "name": "Pilot",
        "pfp": "rocket",
        "pfp_data": "",
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
    pfp = str(profile.get("pfp") or "").strip()
    raw_pic = profile.get("pfp_data") if isinstance(profile.get("pfp_data"), str) else ""
    if pfp == CUSTOM_PFP:
        cleaned, err = normalize_pfp_data(raw_pic)
        if err:
            profile["pfp"] = "rocket"
            profile["pfp_data"] = ""
        else:
            profile["pfp"] = CUSTOM_PFP
            profile["pfp_data"] = cleaned
    elif pfp in PFPS:
        profile["pfp"] = pfp
        profile["pfp_data"] = ""
    else:
        profile["pfp"] = "rocket"
        profile["pfp_data"] = ""
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
        "pfp_data": profile.get("pfp_data") or "",
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
        "pfp_limits": {
            "max_file_bytes": PFP_FILE_MAX_BYTES,
            "max_stored_chars": PFP_DATA_MAX_CHARS,
            "types": list(_PFP_MIMES),
            "note": "Still image on this paper profile. Not synced across devices.",
        },
    }


def update_profile(name=_MISSING, pfp=_MISSING, pfp_data=_MISSING) -> dict:
    if name is _MISSING and pfp is _MISSING and pfp_data is _MISSING:
        return _fail("name or pfp required")
    with _lock:
        profile = load_profile()
        clean_name = None
        if name is not _MISSING:
            clean_name = " ".join(str(name or "").split())
            if not clean_name or len(clean_name) > 24:
                return _fail("Name must be 1–24 characters", profile=public_profile(profile))
        next_pfp = None
        next_data = None
        if pfp is not _MISSING or pfp_data is not _MISSING:
            # A picture payload with no preset id is a custom still. An explicit
            # preset replaces any custom bytes, even if the body also sent them.
            if pfp is _MISSING:
                choice = CUSTOM_PFP
            else:
                choice = str(pfp or "").strip()
            if choice in PFPS:
                next_pfp = choice
                next_data = ""
            elif choice == CUSTOM_PFP:
                raw = pfp_data if pfp_data is not _MISSING else profile.get("pfp_data")
                cleaned, err = normalize_pfp_data(raw)
                if err:
                    return _fail(err, profile=public_profile(profile))
                next_pfp = CUSTOM_PFP
                next_data = cleaned
            else:
                return _fail("Pick a picture", profile=public_profile(profile))
        if clean_name is not None:
            profile["name"] = clean_name
        if next_pfp is not None:
            profile["pfp"] = next_pfp
            profile["pfp_data"] = next_data
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
        kept = st.get("receipts") if isinstance(st.get("receipts"), list) else []
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
            "receipts": kept,
            "disclaimer": "Paper research only. Not financial advice. No real trades.",
        }
        _push_event(st, "start", f"New 24h run — ${START_BANKROLL:.2f} paper bankroll")
        save_state(st)
        return st


def _close_position(st: dict, pos: dict, price: float, reason: str, price_ts: str | None = None) -> None:
    qty = float(pos.get("qty_tokens") or 0)
    proceeds = round(price * qty, 6)
    entry = float(pos.get("entry_price_usd") or 0)
    stake = float(pos.get("stake_usd") or 0)
    pnl = round(proceeds - stake, 6)
    pnl_pct = round((price / entry - 1.0) * 100, 2) if entry else 0.0
    ts = price_ts or pos.get("price_ts") or _utc()
    st["cash_usd"] = round(float(st.get("cash_usd") or 0) + proceeds, 6)
    closed = {
        **pos,
        "exit_price_usd": price,
        "exit_price_ts": ts,
        "price_ts": ts,
        "exit_usd": proceeds,
        "pnl_usd": pnl,
        "pnl_pct": pnl_pct,
        "closed_at": _utc(),
        "close_reason": reason,
    }
    prior = float(pos.get("realized_pnl_usd") or 0)
    prior_stake = float(pos.get("realized_stake_usd") or 0)
    prior_proceeds = float(pos.get("realized_proceeds_usd") or 0)
    if prior or prior_stake:
        closed["pnl_usd"] = round(pnl + prior, 6)
        closed["stake_usd"] = round(stake + prior_stake, 6)
        base = float(closed["stake_usd"] or 0)
        closed["pnl_pct"] = round((closed["pnl_usd"] / base) * 100.0, 2) if base else pnl_pct
        closed["exit_usd"] = round(proceeds + prior_proceeds, 6)
    _note_receipt_sell(st, pos, price, reason, ts, qty, stake, pnl, pnl_pct, True)
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
        ticks: dict[str, dict] = {}
        qa_sid = _qa_sid.get()
        if mints:
            futs = {_fetch_pool.submit(fetch_live_tick, m, False, qa_sid): m for m in mints}
            try:
                for fut in as_completed(futs, timeout=max(4.0, _LIVE_TIMEOUT + 1)):
                    m = futs[fut]
                    try:
                        tick = fut.result()
                    except Exception:
                        tick = None
                    if isinstance(tick, dict):
                        ticks[m] = tick
            except TimeoutError:
                pass

        still = []
        for pos in list(st.get("positions") or []):
            mint = pos.get("mint")
            tick = ticks.get(mint) if mint else None
            fresh = fillable_tick(tick)
            px = _f(tick.get("price_usd")) if fresh and tick else None
            entry = float(pos.get("entry_price_usd") or 0)
            if fresh and px is not None:
                prev = _f(pos.get("last_price_usd"))
                prev_mcap = _f(pos.get("market_cap_usd"))
                if prev and prev > 0 and prev_mcap and prev_mcap > 0:
                    ratio = px / prev
                    if 0.05 <= ratio <= 20:
                        pos["market_cap_usd"] = prev_mcap * ratio
                pos["last_price_usd"] = px
                pos["quote_epoch"] = tick.get("quoted_epoch")
                pos["quoted_at"] = tick.get("price_ts")
                pos["price_ts"] = tick.get("price_ts")
                pos["price_source"] = tick.get("source")
            else:
                # Do not fire a stop on a stale print, and do not zero the mark.
                px = _f(pos.get("last_price_usd")) or (entry if entry else None)
            if not pos.get("image_url") and mint:
                img = image_for_mint(mint)
                if img:
                    pos["image_url"] = img
            if timed_out:
                _close_position(
                    st,
                    pos,
                    float(px or entry or 0),
                    "force_24h",
                    price_ts=(tick.get("price_ts") if fresh and tick else None),
                )
                continue
            sl = pos.get("sl_pct")
            tp = pos.get("tp_pct")
            # Stops and targets fill at this tick's price, and only when that
            # tick is still inside the 5s window. A frozen cache must not gap
            # the fill past the mark the player was shown.
            if fresh and entry and px:
                chg = (px / entry - 1.0) * 100
                if sl is not None and chg <= -abs(float(sl)):
                    _close_position(st, pos, px, "stop", price_ts=tick.get("price_ts") if tick else None)
                    continue
                if tp is not None and chg >= abs(float(tp)):
                    _close_position(st, pos, px, "tp", price_ts=tick.get("price_ts") if tick else None)
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


# SHIP Z — paper buy receipts. Points and the fill live in game_state.json.
# The picture is drawn in the browser from those points. panel_v is not bumped.
RECEIPT_POINTS = 60
MAX_RECEIPTS = 80
VERDICT_SKIP_BELOW = 10.0


def format_et(when: datetime | None = None) -> str:
    """Wall clock in US Eastern, labeled ET. The receipt freezes this at the fill."""
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        et = when.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        et = when.astimezone(timezone(timedelta(hours=-5)))
    hour = et.hour % 12 or 12
    ampm = "AM" if et.hour < 12 else "PM"
    return f"{et.strftime('%b')} {et.day}, {et.year} {hour}:{et.minute:02d} {ampm} ET"


def _score_12_of(row: dict) -> float | None:
    s12 = _f(row.get("score_12"))
    if s12 is not None:
        return s12
    score = _f(row.get("score"))
    if score is None:
        return None
    return round((score / 100.0) * 12.0, 1)


def verdict_for_row(row: dict | None) -> str:
    """Filter verdict frozen at the buy. No row and no score is unknown."""
    if not isinstance(row, dict):
        return "unknown"
    scar = row.get("gold_scar")
    if isinstance(scar, dict) and scar.get("gold") is True:
        return "GOLD"
    s12 = _score_12_of(row)
    if s12 is None:
        return "unknown"
    if row.get("trench_band") is False or s12 < VERDICT_SKIP_BELOW:
        return "SKIP"
    return "PLAY"


def verdict_for_mint(mint: str) -> str:
    return verdict_for_row(flagged_row(mint))


def _level_px(entry: float, pct, kind: str) -> float | None:
    p = _f(pct)
    if p is None or not entry or entry <= 0:
        return None
    mag = abs(float(p))
    if kind == "stop":
        return entry * (1.0 - mag / 100.0)
    return entry * (1.0 + mag / 100.0)


def _clean_points(raw, limit: int = RECEIPT_POINTS) -> list[float]:
    if not isinstance(raw, list):
        return []
    src = raw[-200:] if len(raw) > 200 else raw
    out: list[float] = []
    for item in src:
        if isinstance(item, dict):
            p = _f(item.get("p") if item.get("p") is not None else item.get("price"))
        else:
            p = _f(item)
        if p is not None and p > 0:
            out.append(float(p))
    if len(out) > limit:
        out = out[-limit:]
    return out


def _receipts(st: dict) -> list:
    rows = st.get("receipts")
    if not isinstance(rows, list):
        rows = []
        st["receipts"] = rows
    return rows


def _receipt_for(st: dict, position_id: str | None) -> dict | None:
    pid = str(position_id or "")
    if not pid:
        return None
    for rec in _receipts(st):
        if isinstance(rec, dict) and str(rec.get("position_id") or "") == pid:
            return rec
    return None


def _held_parts(opened_at, closed_at) -> tuple[int | None, str]:
    a = _parse_utc(opened_at) if isinstance(opened_at, str) else None
    b = _parse_utc(closed_at) if isinstance(closed_at, str) else None
    if a is None or b is None:
        return None, ""
    sec = int((b - a).total_seconds())
    if sec < 0:
        sec = 0
    if sec < 60:
        label = f"{sec}s"
    elif sec < 3600:
        label = f"{sec // 60}m {sec % 60}s"
    else:
        label = f"{sec // 3600}h {(sec % 3600) // 60}m"
    return sec, label


def _sol_usd_now() -> float | None:
    """SOL/USD from the same live tick a fill uses. None when that tick is not fillable."""
    return fill_price(fetch_live_tick(SOL_MINT))


def _push_receipt(st: dict, rec: dict) -> None:
    rows = _receipts(st)
    rows.insert(0, rec)
    if len(rows) > MAX_RECEIPTS:
        del rows[MAX_RECEIPTS:]


def build_receipt(st: dict, pos: dict, tick: dict | None, points=None) -> dict:
    """Frozen buy snapshot. The fill price and quote age come from the fill tick."""
    px = fill_price(tick)
    if px is None:
        px = float(_f(pos.get("entry_price_usd")) or 0)
    age = fill_quote_age_s(tick)
    sol = _sol_usd_now()
    entry_sol = (px / sol) if (sol and px) else None
    sl = pos.get("sl_pct")
    tp = pos.get("tp_pct")
    opened = pos.get("opened_at") or _utc()
    opened_dt = _parse_utc(opened) if isinstance(opened, str) else None
    stake = float(pos.get("stake_usd") or 0)
    qty = float(pos.get("qty_tokens") or 0)
    price_ts = tick.get("price_ts") if isinstance(tick, dict) else pos.get("price_ts")
    return {
        "id": uuid.uuid4().hex[:10],
        "position_id": pos.get("id"),
        "session_id": st.get("session_id"),
        "mint": pos.get("mint"),
        "symbol": pos.get("symbol") or "?",
        "name": pos.get("name") or "?",
        "status": "open",
        "opened_at": opened,
        "time_et": format_et(opened_dt),
        "entry_price_usd": px,
        "entry_price_sol": entry_sol,
        "sol_usd": sol,
        "size_usd": round(stake, 6),
        "remaining_usd": round(stake, 6),
        "qty_tokens": qty,
        "remaining_qty": qty,
        "sl_pct": sl,
        "tp_pct": tp,
        "stop_price_usd": _level_px(px, sl, "stop"),
        "tp_price_usd": _level_px(px, tp, "tp"),
        "quote_age_s": age,
        "quote_sla_s": QUOTE_FRESH_SLA_S,
        "stale": tick_is_stale(age),
        "verdict": verdict_for_mint(str(pos.get("mint") or "")),
        "points": _clean_points(points),
        "fill_point": {"p": px, "at": price_ts},
        "sells": [],
    }


def _note_receipt_sell(
    st: dict,
    pos: dict,
    price: float,
    reason: str,
    price_ts: str | None,
    sold_qty: float,
    sold_stake: float,
    slice_pnl: float,
    slice_pct: float,
    closes: bool,
) -> None:
    rec = _receipt_for(st, pos.get("id"))
    if not rec or rec.get("status") == "closed":
        return
    at = _utc()
    held_s, held = _held_parts(rec.get("opened_at") or pos.get("opened_at"), at)
    sells = rec.get("sells")
    if not isinstance(sells, list):
        sells = []
        rec["sells"] = sells
    sells.append({
        "at": at,
        "size_usd": round(float(sold_stake), 6),
        "qty_tokens": float(sold_qty),
        "exit_price_usd": float(price),
        "exit_price_ts": price_ts,
        "pnl_usd": round(float(slice_pnl), 6),
        "pnl_pct": round(float(slice_pct), 2) if slice_pct is not None else None,
        "held_s": held_s,
        "held": held,
        "reason": reason,
        "exit_reason": reason,
    })
    if closes:
        rec["remaining_usd"] = 0
        rec["remaining_qty"] = 0
        rec["status"] = "closed"
        rec["closed_at"] = at
    else:
        rec["remaining_usd"] = round(float(pos.get("stake_usd") or 0), 6)
        rec["remaining_qty"] = float(pos.get("qty_tokens") or 0)
        rec["status"] = "open"


def _sell_fraction(st: dict, pos: dict, price: float, reason: str, price_ts: str | None, fraction: float) -> None:
    """Sell a share of the original size. Whatever is left stays open. A full share closes."""
    fraction = float(fraction)
    if fraction <= 0:
        return
    if fraction >= 0.999:
        _close_position(st, pos, price, reason, price_ts=price_ts)
        return
    rem_qty = float(pos.get("qty_tokens") or 0)
    rem_stake = float(pos.get("stake_usd") or 0)
    if rem_qty <= 0 or rem_stake <= 0:
        _close_position(st, pos, price, reason, price_ts=price_ts)
        return
    rec = _receipt_for(st, pos.get("id"))
    orig_qty = _f(pos.get("original_qty_tokens"))
    if orig_qty is None and isinstance(rec, dict):
        orig_qty = _f(rec.get("qty_tokens"))
    if orig_qty is None or orig_qty <= 0:
        orig_qty = rem_qty
    orig_stake = _f(pos.get("original_stake_usd"))
    if orig_stake is None and isinstance(rec, dict):
        orig_stake = _f(rec.get("size_usd"))
    if orig_stake is None or orig_stake <= 0:
        orig_stake = rem_stake
    want_qty = orig_qty * fraction
    if want_qty >= rem_qty * 0.999:
        _close_position(st, pos, price, reason, price_ts=price_ts)
        return
    sold_qty = want_qty
    sold_stake = rem_stake * (sold_qty / rem_qty) if rem_qty else 0.0
    entry = float(pos.get("entry_price_usd") or 0)
    proceeds = round(price * sold_qty, 6)
    pnl = round(proceeds - sold_stake, 6)
    pnl_pct = round((price / entry - 1.0) * 100, 2) if entry else 0.0
    pos["qty_tokens"] = rem_qty - sold_qty
    pos["stake_usd"] = round(rem_stake - sold_stake, 6)
    pos["original_qty_tokens"] = orig_qty
    pos["original_stake_usd"] = orig_stake
    pos["realized_pnl_usd"] = round(float(pos.get("realized_pnl_usd") or 0) + pnl, 6)
    pos["realized_stake_usd"] = round(float(pos.get("realized_stake_usd") or 0) + sold_stake, 6)
    pos["realized_proceeds_usd"] = round(float(pos.get("realized_proceeds_usd") or 0) + proceeds, 6)
    pos["last_price_usd"] = price
    if price_ts:
        pos["price_ts"] = price_ts
    st["cash_usd"] = round(float(st.get("cash_usd") or 0) + proceeds, 6)
    _note_receipt_sell(st, pos, price, reason, price_ts, sold_qty, sold_stake, pnl, pnl_pct, False)
    _push_event(
        st,
        reason,
        f"{pos.get('symbol')} sell {fraction * 100:.0f}% · PnL ${pnl:+.3f} ({pnl_pct:+.1f}%) · left ${pos['stake_usd']:.3f}",
        position_id=pos.get("id"),
        pnl_usd=pnl,
    )


def buy(
    mint: str,
    stake_usd: float,
    sl_pct: float | None = None,
    tp_pct: float | None = None,
    client_request_id: str | None = None,
    points=None,
) -> dict:
    with _lock:
        st = tick_session()
        if st.get("status") != "active":
            return _fail("No active session — POST /api/game/start first", st)
        rid = (client_request_id or "").strip()[:80]
        if rid:
            prev = _position_for_request(st, rid)
            if prev:
                return {
                    "ok": True,
                    "position": prev,
                    "state": st,
                    "sound": "buy",
                    "deduped": True,
                    "resolved_mint": prev.get("mint"),
                    "receipt": _receipt_for(st, prev.get("id")),
                }
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
        q = fetch_dex_token(mint, bypass_cache=True) or {}
        tick = fetch_live_tick(mint)
        if not fillable_tick(tick):
            return _fail(
                "No fresh price within 5s — not buying at a stale quote. Try again.",
                st,
                resolved_mint=mint,
                resolve_source=src,
            )
        if q:
            q = _apply_live_tick(q, tick)
        px = fill_price(tick)
        if px is None or px <= 0:
            return _fail("Bad price", st)
        qty = stake / px
        price_ts = tick.get("price_ts")
        pos = {
            "id": uuid.uuid4().hex[:10],
            "mint": mint,
            "symbol": (q or {}).get("symbol") or "?",
            "name": (q or {}).get("name") or "?",
            "stake_usd": round(stake, 6),
            "entry_price_usd": px,
            "entry_price_ts": price_ts,
            "price_ts": price_ts,
            "qty_tokens": qty,
            "sl_pct": float(sl_pct) if sl_pct is not None else None,
            "tp_pct": float(tp_pct) if tp_pct is not None else None,
            "opened_at": _utc(),
            "pair_url": (q or {}).get("pair_url"),
            "image_url": image_for_mint(mint, (q or {}).get("image_url") or None),
            "last_price_usd": px,
            "quote_epoch": tick.get("quoted_epoch"),
            "quoted_at": price_ts,
            "price_source": tick.get("source"),
            "client_request_id": rid or None,
            "red_flags": flags_for_mint(mint),
            "ca_short": (mint[:4] + "…" + mint[-4:]) if len(mint) > 10 else mint,
            "market_cap_usd": (q or {}).get("market_cap_usd"),
            "mcap_progress": (q or {}).get("mcap_progress"),
            "original_stake_usd": round(stake, 6),
            "original_qty_tokens": qty,
        }
        rec = build_receipt(st, pos, tick, points=points)
        pos["receipt_id"] = rec["id"]
        _push_receipt(st, rec)
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
            "receipt": rec,
        }


def sell(position_id: str, fraction: float | None = None) -> dict:
    with _lock:
        st = load_state()
        if st.get("status") != "active":
            return _fail("No active session", st)
        pos = next((p for p in (st.get("positions") or []) if p.get("id") == position_id), None)
        if not pos:
            return _fail("Position not found", st)
        frac = None
        if fraction is not None:
            try:
                frac = float(fraction)
            except (TypeError, ValueError):
                return _fail("fraction must be a number", st)
            if frac <= 0 or frac > 1:
                return _fail("fraction must be between 0 and 1", st)
        tick = fetch_live_tick(str(pos.get("mint") or ""))
        if not fillable_tick(tick):
            return _fail("Price is older than 5s — not selling at a stale quote", st)
        px = fill_price(tick)
        if px is None:
            return _fail("Price is older than 5s — not selling at a stale quote", st)
        ts = tick.get("price_ts")
        # Same tick the stop would use. If it already crossed, the fill is that price.
        st = tick_session(st)
        pos = next((p for p in (st.get("positions") or []) if p.get("id") == position_id), None)
        if not pos:
            save_state(st)
            return {
                "ok": True,
                "state": st,
                "sound": "sell",
                "closed_by": "stop",
                "price_usd": px,
                "price_ts": ts,
                "receipt": _receipt_for(st, position_id),
            }
        if frac is None or frac >= 0.999:
            _close_position(st, pos, px, "manual", price_ts=ts)
        else:
            _sell_fraction(st, pos, px, "manual", ts, frac)
        save_state(st)
        return {
            "ok": True,
            "state": st,
            "sound": "sell",
            "price_usd": px,
            "price_ts": ts,
            "receipt": _receipt_for(st, position_id),
        }


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


# SHIP P — post-game review of a finished 24h paper run.
# Filled from this session's closed tape and equity. Not mid-run coaching.
# Not a forecast. Pump.fun base rates stay in the footer on purpose.
REVIEW_DISCLAIMER = "Paper research review. Not financial advice."
REVIEW_HUMILITY = (
    "Pump.fun odds are ugly: about 1 in 58 graduate, a few in 10,000 get near $1M+, "
    "and most die the same day. A winner pattern is not a promise. "
    "Fed and CPI days dump more often — this recap does not know the calendar, and it is not a call."
)
_GRADE_TONE = {
    "A": "Green book",
    "B+": "Solid grind",
    "B": "Small paper edge",
    "C+": "Scratch",
    "C": "Leaky book",
    "D": "Heavy paper leak",
    "F": "Down hard",
}
_CLOSE_WHY = {
    "tp": "take-profit",
    "stop": "stop",
    "manual": "manual close",
    "force_24h": "clock force-sell",
}


def review_grade(pnl_pct: float) -> str:
    """Letter label for this run's paper P&L. It is not a forecast."""
    try:
        p = float(pnl_pct)
    except (TypeError, ValueError):
        p = 0.0
    if p >= 50:
        return "A"
    if p >= 20:
        return "B+"
    if p >= 5:
        return "B"
    if p >= -5:
        return "C+"
    if p >= -20:
        return "C"
    if p >= -40:
        return "D"
    return "F"


def _money(n: float, signed: bool = False) -> str:
    v = float(n or 0)
    if not signed:
        return f"${v:.2f}"
    if v > 0:
        return f"+${v:.2f}"
    if v < 0:
        return f"−${abs(v):.2f}"
    return "$0.00"


def _pct_text(n: float) -> str:
    v = float(n or 0)
    if v > 0:
        return f"+{v:.1f}%"
    if v < 0:
        return f"−{abs(v):.1f}%"
    return "0.0%"


def _why_closed(reason: str) -> str:
    key = str(reason or "").strip()
    return _CLOSE_WHY.get(key, key or "close")


def _closed_rows(st: dict) -> list[dict]:
    rows = []
    for i, raw in enumerate(st.get("closed") or []):
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip() or "?"
        rows.append({
            "symbol": symbol,
            "pnl_usd": _f(raw.get("pnl_usd")),
            "pnl_pct": _f(raw.get("pnl_pct")),
            "close_reason": str(raw.get("close_reason") or "").strip(),
            "opened_at": raw.get("opened_at"),
            "closed_at": raw.get("closed_at"),
            "id": raw.get("id"),
            "_i": i,
        })
    return rows


def _best_worst(rows: list[dict]) -> tuple[dict | None, dict | None]:
    scored = [r for r in rows if r.get("pnl_usd") is not None]
    if not scored:
        return None, None
    best = max(scored, key=lambda r: (float(r["pnl_usd"]), -r["_i"]))
    worst = min(scored, key=lambda r: (float(r["pnl_usd"]), r["_i"]))
    if best["_i"] == worst["_i"]:
        pnl = float(best["pnl_usd"])
        if pnl > 0.005:
            return best, None
        if pnl < -0.005:
            return None, best
        return None, None
    return best, worst


def _print_line(row: dict, role: str) -> str:
    why = _why_closed(row.get("close_reason") or "")
    pnl = row.get("pnl_usd")
    pct = row.get("pnl_pct")
    if pnl is None:
        bit = f"{row['symbol']} {why} — P&L was not on this tape"
    else:
        pct_bit = f", {_pct_text(pct)}" if pct is not None else ""
        bit = f"{row['symbol']} {why} ({_money(pnl, True)}{pct_bit})"
    if role == "best":
        return bit + " — best closed print on this tape."
    if role == "worst":
        return bit + " — worst closed print on this tape."
    return bit + "."


def _cluster_pair(rows: list[dict]) -> tuple[dict, dict] | None:
    parsed = []
    for row in rows:
        opened = _parse_utc(row.get("opened_at"))
        if opened is not None:
            parsed.append((opened, row))
    parsed.sort(key=lambda item: item[0])
    best = None
    for (ta, a), (tb, b) in zip(parsed, parsed[1:]):
        gap = (tb - ta).total_seconds()
        if gap <= 20 * 60 and (best is None or gap < best[0]):
            best = (gap, a, b)
    if not best:
        return None
    return best[1], best[2]


def _held_under_5m(row: dict) -> bool:
    opened = _parse_utc(row.get("opened_at"))
    closed = _parse_utc(row.get("closed_at"))
    if opened is None or closed is None:
        return False
    held = (closed - opened).total_seconds()
    return 0 <= held < 300


def _timing_text(rows: list[dict], cluster: tuple[dict, dict] | None) -> str:
    bits = []
    if cluster:
        a, b = cluster
        if a["symbol"] == b["symbol"]:
            bits.append(f"{a['symbol']} was opened twice inside the same 20m window.")
        else:
            bits.append(f"{a['symbol']} and {b['symbol']} both opened inside the same 20m window.")
    early = [r for r in rows if _held_under_5m(r)]
    if early:
        shown = early[:2]
        names = ", ".join(f"{r['symbol']} {_why_closed(r.get('close_reason') or '')}" for r in shown)
        bits.append(f"{names} cut in under 5 minutes.")
    forced = [r for r in rows if r.get("close_reason") == "force_24h"]
    if forced:
        shown = forced[:2]
        parts = []
        for r in shown:
            if r.get("pnl_usd") is None:
                parts.append(r["symbol"])
            else:
                parts.append(f"{r['symbol']} ({_money(r['pnl_usd'], True)})")
        bits.append("Clock force-sold " + " and ".join(parts) + ".")
    return " ".join(bits)


def _note_text(rows: list[dict], cluster: tuple[dict, dict] | None) -> str:
    if not rows:
        return "An empty tape has nothing to copy. Most coins die the same day anyway."
    if cluster:
        a, b = cluster
        if a["symbol"] == b["symbol"]:
            who = a["symbol"]
        else:
            who = f"{a['symbol']} and {b['symbol']}"
        return f"{who} opened close together. Spacing is a note from this tape, not a reason the next name works."
    pnls = [float(r["pnl_usd"]) for r in rows if r.get("pnl_usd") is not None]
    if pnls and all(p <= 0.005 for p in pnls):
        return "Every close was flat or red. Fewer names is a note from this tape, not a call."
    best = max(rows, key=lambda r: float(r["pnl_usd"]) if r.get("pnl_usd") is not None else -1e18)
    if best.get("pnl_usd") is not None and float(best["pnl_usd"]) > 0.005:
        return f"{best['symbol']} did most of the green print. That describes this run. It is not a pattern to trust."
    return "The grade only labels this paper book. It is not a reason to size up."


def _px_text(n: float) -> str:
    v = float(n)
    if v >= 1:
        body = f"{v:.4f}".rstrip("0").rstrip(".")
        return "$" + (body or "0")
    body = f"{v:.8f}".rstrip("0").rstrip(".")
    return "$" + (body or "0")


def _session_receipts(st: dict) -> list[dict]:
    sid = str(st.get("session_id") or "")
    out = []
    for raw in st.get("receipts") or []:
        if isinstance(raw, dict) and (not sid or str(raw.get("session_id") or "") == sid):
            out.append(raw)
    return out


def _receipt_review_text(st: dict) -> str:
    """One or two lines the review can cite. Empty when this run has no receipts."""
    bits = []
    for rec in _session_receipts(st)[:2]:
        sym = str(rec.get("symbol") or "?").strip() or "?"
        entry = _f(rec.get("entry_price_usd"))
        verdict = str(rec.get("verdict") or "unknown")
        age = _f(rec.get("quote_age_s"))
        entry_bit = _px_text(entry) if entry is not None else "entry not on the receipt"
        age_bit = "quote age unknown" if age is None else f"quote age {age:g}s"
        sells = [s for s in (rec.get("sells") or []) if isinstance(s, dict)]
        if sells:
            parts = []
            for s in sells[:4]:
                pnl = _f(s.get("pnl_usd"))
                why = _why_closed(str(s.get("reason") or s.get("exit_reason") or ""))
                size = _f(s.get("size_usd"))
                size_bit = f" size ${size:.2f}" if size is not None else ""
                pnl_bit = _money(pnl, True) if pnl is not None else "P/L n/a"
                parts.append(f"{why}{size_bit} {pnl_bit}")
            sell_bit = " Sells: " + "; ".join(parts) + "."
        else:
            sell_bit = " No sells on this receipt."
        bits.append(f"{sym} receipt entry {entry_bit}, verdict {verdict}, {age_bit}.{sell_bit}")
    return " ".join(bits)


def _review_receipts(st: dict) -> list[dict]:
    rows = []
    for rec in _session_receipts(st):
        rows.append({
            "id": rec.get("id"),
            "position_id": rec.get("position_id"),
            "symbol": rec.get("symbol"),
            "entry_price_usd": rec.get("entry_price_usd"),
            "entry_price_sol": rec.get("entry_price_sol"),
            "quote_age_s": rec.get("quote_age_s"),
            "verdict": rec.get("verdict") or "unknown",
            "status": rec.get("status"),
            "size_usd": rec.get("size_usd"),
            "remaining_usd": rec.get("remaining_usd"),
            "time_et": rec.get("time_et"),
            "sells": rec.get("sells") or [],
        })
    return rows


def _session_mark(st: dict, equity_usd: float) -> tuple[float, float, float, float, float]:
    """Match public_state P&L so the review cannot disagree with the panel."""
    eq = float(equity_usd)
    start = float(st.get("start_bankroll") or START_BANKROLL)
    pnl = round(eq - start, 6)
    pnl_pct = round((eq / start - 1.0) * 100, 2) if float(st.get("start_bankroll") or 0) else 0.0
    try:
        peak = float(st.get("peak_equity")) if st.get("peak_equity") is not None else eq
    except (TypeError, ValueError):
        peak = eq
    return eq, start, pnl, pnl_pct, peak


def build_run_review(st: dict | None, equity_usd: float | None = None) -> dict:
    """Chess-style recap of one finished paper run.

    Active and idle sessions get status ``none`` — no mid-run tips.
    Ended sessions cite only this run's closed rows and equity marks.
    """
    st = st if isinstance(st, dict) else {}
    sid = st.get("session_id")
    base = {
        "feature": "game_ai_review",
        "status": "none",
        "session_id": sid,
        "advice": False,
    }
    if st.get("status") != "ended":
        return base
    if equity_usd is None:
        equity_usd = equity(st)
    eq, start, pnl, pnl_pct, peak = _session_mark(st, equity_usd)
    grade = review_grade(pnl_pct)
    tone = _GRADE_TONE[grade]
    rows = _closed_rows(st)
    best, worst = _best_worst(rows)
    cluster = _cluster_pair(rows)
    arc = (
        f"Started {_money(start)}, peaked {_money(peak)}, finished {_money(eq)} "
        f"({_money(pnl, True)}, {_pct_text(pnl_pct)})."
    )
    flat = bool(rows) and all(r.get("pnl_usd") is not None and abs(float(r["pnl_usd"])) <= 0.005 for r in rows)
    if not rows:
        headline = "Quiet book — no closed trades"
        summary = f"No closed trades on this run. Grade {grade} labels this paper book only."
    elif best and worst and float(best["pnl_usd"]) > 0.005 and float(worst["pnl_usd"]) < -0.005:
        headline = f"{tone} — {best['symbol']} carried, {worst['symbol']} leaked"
        summary = (
            f"{best['symbol']} closed {_money(best['pnl_usd'], True)} and "
            f"{worst['symbol']} closed {_money(worst['pnl_usd'], True)}. "
            f"Grade {grade} labels this paper result only."
        )
    elif best and float(best["pnl_usd"]) > 0.005:
        headline = f"{tone} — {best['symbol']} led the book"
        summary = (
            f"{best['symbol']} closed {_money(best['pnl_usd'], True)}. "
            f"Grade {grade} labels this paper result only."
        )
    elif worst and float(worst["pnl_usd"]) < -0.005:
        headline = f"{tone} — {worst['symbol']} was the leak"
        summary = (
            f"{worst['symbol']} closed {_money(worst['pnl_usd'], True)}. "
            f"Grade {grade} labels this paper result only."
        )
    elif len(rows) == 1:
        headline = f"{tone} — {rows[0]['symbol']} closed flat"
        summary = f"{rows[0]['symbol']} closed flat. Grade {grade} labels this paper result only."
    else:
        headline = f"{tone} — closes were flat"
        summary = f"Closed trades were flat. Grade {grade} labels this paper result only."

    bullets: list[dict] = []
    if not rows:
        bullets.append({"tag": "TAPE", "text": "No closed trades on this run."})
    if best and float(best["pnl_usd"]) > 0.005:
        bullets.append({"tag": "BEST", "text": _print_line(best, "best")})
    if worst and float(worst["pnl_usd"]) < -0.005 and (best is None or worst["_i"] != best["_i"]):
        bullets.append({"tag": "WORST", "text": _print_line(worst, "worst")})
    if flat and not bullets:
        shown = rows[0]
        why = _why_closed(shown.get("close_reason") or "")
        bullets.append({
            "tag": "TAPE",
            "text": f"{shown['symbol']} {why} ({_money(shown.get('pnl_usd') or 0, True)}) — flat on this tape.",
        })
    timing = _timing_text(rows, cluster)
    if timing:
        bullets.append({"tag": "TIMING", "text": timing})
    giveback = round(peak - eq, 2)
    if giveback >= 0.01:
        bullets.append({
            "tag": "ARC",
            "text": f"Gave back {_money(giveback)} from the {_money(peak)} peak. Finish was {_money(eq)}.",
        })
    note = _note_text(rows, cluster)
    receipt_text = _receipt_review_text(st)
    if receipt_text:
        bullets.insert(0, {"tag": "RECEIPT", "text": receipt_text})
    core = [b for b in bullets if b["tag"] != "ARC"]
    arc_bits = [b for b in bullets if b["tag"] == "ARC"]
    # Keep the recap to a handful of lines. Drop the give-back line before the note.
    if len(core) >= 4:
        arc_bits = []
    bullets = (core + arc_bits)[:4]
    bullets.append({"tag": "NOTE", "text": note})

    takeaways = [arc]
    if best and worst and float(best["pnl_usd"]) > 0.005 and float(worst["pnl_usd"]) < -0.005:
        takeaways.append(
            f"{best['symbol']} {_money(best['pnl_usd'], True)} and "
            f"{worst['symbol']} {_money(worst['pnl_usd'], True)} are the tape, not a forecast."
        )
    elif rows and bullets:
        takeaways.append(bullets[0]["text"])
    if note not in takeaways:
        takeaways.append(note)
    takeaways = takeaways[:3]

    cited = []
    by_pos = {str(rec.get("position_id") or ""): rec for rec in _session_receipts(st)}
    for r in rows:
        item = {
            "symbol": r["symbol"],
            "pnl_usd": r["pnl_usd"],
            "pnl_pct": r["pnl_pct"],
            "close_reason": r["close_reason"],
            "opened_at": r["opened_at"],
            "closed_at": r["closed_at"],
            "id": r["id"],
        }
        rec = by_pos.get(str(r["id"] or ""))
        if rec:
            item["verdict"] = rec.get("verdict") or "unknown"
            item["entry_price_usd"] = rec.get("entry_price_usd")
            item["quote_age_s"] = rec.get("quote_age_s")
            item["receipt_id"] = rec.get("id")
        cited.append(item)
    return {
        "feature": "game_ai_review",
        "status": "ready",
        "session_id": sid,
        "advice": False,
        "grounded": True,
        "label": "Paper run review",
        "grade": grade,
        "grade_note": "Labels this paper P&L only — not a forecast.",
        "headline": headline,
        "arc": arc,
        "summary": summary,
        "bullets": bullets,
        "takeaways": takeaways,
        "disclaimer": REVIEW_DISCLAIMER,
        "humility": REVIEW_HUMILITY,
        "pnl_usd": pnl,
        "pnl_pct": pnl_pct,
        "final_equity": round(eq, 6),
        "start_bankroll": start,
        "peak_equity": round(peak, 6),
        "trade_count": len(rows),
        "symbols": [r["symbol"] for r in rows],
        "cited": cited,
        "receipts": _review_receipts(st),
    }


# SHIP AD — profile paper P/L. Every number is summed from Z receipts
# (_receipts for lifetime, _session_receipts for this run). panel_v is not bumped.
# A flat print is the same dust the run review ignores.
PNL_FLAT_USD = 0.005
_PNL_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _eastern(when: datetime) -> datetime:
    """US Eastern wall time, same clock a receipt's ET label uses."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return when.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return when.astimezone(timezone(timedelta(hours=-5)))


def _pnl_receipts(st: dict, scope: str) -> list[dict]:
    """Lifetime is every stored receipt. This run is Z's session filter.

    An idle book with no session id is not a run, so it does not inherit
    the lifetime list (Z returns every receipt when the id is blank).
    """
    if scope == "run":
        if not str(st.get("session_id") or ""):
            return []
        return [rec for rec in _session_receipts(st) if isinstance(rec, dict)]
    return [rec for rec in _receipts(st) if isinstance(rec, dict)]


def _sell_rows(rec: dict) -> list[dict]:
    return [row for row in (rec.get("sells") or []) if isinstance(row, dict)]


def _sell_pnls(rec: dict) -> list[float]:
    out: list[float] = []
    for sell in _sell_rows(rec):
        pnl = _f(sell.get("pnl_usd"))
        if pnl is not None:
            out.append(float(pnl))
    return out


def _trade_pnl(rec: dict) -> float | None:
    parts = _sell_pnls(rec)
    if not parts:
        return None
    return round(sum(parts), 6)


def _pnl_sign(pnl: float) -> int:
    if pnl > PNL_FLAT_USD:
        return 1
    if pnl < -PNL_FLAT_USD:
        return -1
    return 0


def _as_hold_s(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return None


def _closed_stamp(rec: dict) -> datetime:
    raw = rec.get("closed_at")
    if not isinstance(raw, str) or not raw:
        sells = _sell_rows(rec)
        raw = sells[-1].get("at") if sells else None
    parsed = _parse_utc(raw) if isinstance(raw, str) else None
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed


def _trade_hold_s(rec: dict) -> int | None:
    """Hold of a closed trade: the last sell's held_s, else open-to-close."""
    sells = _sell_rows(rec)
    if sells:
        held = _as_hold_s(sells[-1].get("held_s"))
        if held is not None:
            return held
        closed = rec.get("closed_at") or sells[-1].get("at")
    else:
        closed = rec.get("closed_at")
    sec, _label = _held_parts(rec.get("opened_at"), closed)
    return sec


def _hold_label(seconds: int) -> str:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=max(0, int(seconds)))
    _sec, label = _held_parts(
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return label


def _day_window(now: datetime) -> list[tuple[str, str]]:
    end = _eastern(now).date()
    out = []
    for offset in range(6, -1, -1):
        day = end - timedelta(days=offset)
        out.append((day.isoformat(), _PNL_WEEKDAYS[day.weekday()]))
    return out


def profile_pnl_stats(st: dict | None, scope: str = "lifetime", now: datetime | None = None) -> dict:
    """Paper P/L for the profile card.

    Total P/L and the 7-day chart sum every sell on the receipts in scope,
    including a partial that is still open. Trades, win rate, hold, streak,
    and best/worst count only receipts Z has closed. Nothing is read from
    the closed-trade book or from live equity.
    """
    st = st if isinstance(st, dict) else {}
    scope = "run" if scope == "run" else "lifetime"
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = _pnl_receipts(st, scope)
    day_keys = _day_window(now)
    day_sum = {iso: 0.0 for iso, _label in day_keys}
    realized = 0.0
    closed: list[tuple[float, dict, float]] = []
    for rec in rows:
        for sell in _sell_rows(rec):
            pnl = _f(sell.get("pnl_usd"))
            if pnl is None:
                continue
            realized += float(pnl)
            raw_at = sell.get("at")
            parsed = _parse_utc(raw_at) if isinstance(raw_at, str) else None
            if parsed is None:
                continue
            iso = _eastern(parsed).date().isoformat()
            if iso in day_sum:
                day_sum[iso] += float(pnl)
        if rec.get("status") != "closed":
            continue
        pnl = _trade_pnl(rec)
        if pnl is None:
            continue
        closed.append((pnl, rec, _closed_stamp(rec).timestamp()))

    wins = sum(1 for pnl, _rec, _ts in closed if _pnl_sign(pnl) > 0)
    losses = sum(1 for pnl, _rec, _ts in closed if _pnl_sign(pnl) < 0)
    flats = len(closed) - wins - losses
    trades = len(closed)
    holds = []
    for _pnl, rec, _ts in closed:
        held = _trade_hold_s(rec)
        if held is not None:
            holds.append(held)
    avg_s = int(round(sum(holds) / len(holds))) if holds else None
    ordered = sorted(closed, key=lambda row: row[2], reverse=True)
    streak = 0
    for pnl, _rec, _ts in ordered:
        sign = _pnl_sign(pnl)
        if sign == 0:
            break
        if streak == 0:
            streak = sign
        elif (streak > 0) == (sign > 0):
            streak += sign
        else:
            break

    def _extreme(row: tuple[float, dict, float] | None) -> dict | None:
        if row is None:
            return None
        pnl, rec, _ts = row
        return {
            "receipt_id": rec.get("id"),
            "position_id": rec.get("position_id"),
            "symbol": rec.get("symbol") or "?",
            "pnl_usd": round(pnl, 6),
        }

    best = _extreme(max(closed, key=lambda row: (row[0], row[2])) if closed else None)
    worst = _extreme(min(closed, key=lambda row: (row[0], -row[2])) if closed else None)
    if streak > 0:
        streak_label = "1 win" if streak == 1 else f"{streak} wins"
    elif streak < 0:
        n = abs(streak)
        streak_label = "1 loss" if n == 1 else f"{n} losses"
    else:
        streak_label = ""
    return {
        "scope": scope,
        "pnl_usd": round(realized, 6),
        "win_rate": round(wins / trades, 4) if trades else None,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "trades": trades,
        "avg_hold_s": avg_s,
        "avg_hold": _hold_label(avg_s) if avg_s is not None else "",
        "best": best,
        "worst": worst,
        "streak": streak,
        "streak_label": streak_label,
        "days": [
            {"date": iso, "label": label, "pnl_usd": round(day_sum[iso], 6)}
            for iso, label in day_keys
        ],
    }


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
    out["review"] = build_run_review(st, equity_usd=eq)
    out["pnl_stats"] = {
        "lifetime": profile_pnl_stats(st, "lifetime", now=now),
        "run": profile_pnl_stats(st, "run", now=now),
    }
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
    """Close every open position in one pass. The saved book is the count."""
    with _lock:
        st = load_state()
        if st.get("status") != "active":
            return _fail("No active session", st)
        st = tick_session(st)
        positions = [p for p in list(st.get("positions") or []) if p.get("id")]
        if not positions:
            return _fail("No open positions", st)
        wanted = [p.get("id") for p in positions]
        ticks: dict[str, dict] = {}
        for pos in positions:
            mint = str(pos.get("mint") or "")
            if mint and mint not in ticks:
                tick = fetch_live_tick(mint)
                if isinstance(tick, dict):
                    ticks[mint] = tick
        for pos in positions:
            if pos.get("id") not in {p.get("id") for p in (st.get("positions") or [])}:
                continue
            mint = str(pos.get("mint") or "")
            tick = ticks.get(mint)
            if not fillable_tick(tick):
                continue
            _close_position(st, pos, float(tick["price_usd"]), "manual", price_ts=tick.get("price_ts"))
        save_state(st)
        still = {p.get("id") for p in (st.get("positions") or []) if p.get("id")}
        sold = len([pid for pid in wanted if pid not in still])
        if sold == 0 and still:
            return _fail("Price is older than 5s — not selling at a stale quote", st)
        return {
            "ok": True,
            "sold": sold,
            "open_count": len(still),
            "state": st,
            "sound": "sell",
        }


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
    tick = fetch_live_tick(resolved, bypass_cache=fresh)
    if not q or not fillable_tick(tick):
        return _fail(
            "No fresh price within 5s — not quoting a stale feed. Try again or paste the CA from Filter.",
            resolved_mint=resolved,
            resolve_source=src,
            quote_age_s=(tick or {}).get("quote_age_s") if isinstance(tick, dict) else None,
        )
    q = _apply_live_tick(q, tick)
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
