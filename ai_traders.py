#!/usr/bin/env python3
"""Paper VECTOR and LEDGER traders (research only — no wallets, no orders).

They each keep a $5 24h book on the Filter list. They never write the owner's
cash, positions, or session. Tuned settings and the receipt ledger live in
game_state.json under ``ai_traders`` and survive a balance reset.

SHIP X owns the checklist. ``get_verdict(mint)`` is the only call site: it
returns that verdict when the checklist code imports, and ``UNKNOWN`` otherwise.
"""

from __future__ import annotations

import copy
import importlib
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import game

logger = logging.getLogger("nunu.ai_traders")

AI_KEY = "ai_traders"
REACTION_DELAY_S = 3.0
MAX_TRADES_PER_HOUR = 6
STAKE_USD = 1.0
MIN_STAKE_USD = 0.25
DRAWDOWN_LIMIT_PCT = 20.0
SERIES_GAP_S = 15.0
SERIES_MAX = 240
RECEIPT_MAX = 90
PROOF_MAX = 60
LOG_MAX = 60
PATH_MAX = 16
TUNE_STEPS = 3

# Loosest → strictest. VECTOR's floor slides on this ladder.
# LEDGER does not: it buys BUY-WATCH and nothing else.
_LADDER = ("UNKNOWN", "SKIP", "WATCH", "BUY-WATCH")
_RANK = {name: i for i, name in enumerate(_LADDER)}
_BOUNDS = {
    "stop_pct": (5, 40, 5),
    "tp_pct": (10, 150, 10),
    "hold_min": (5, 120, 5),
}
_PROVIDER_SPEC = (
    ("checklist", "get_verdict"),
    ("checklist", "verdict"),
    ("coin_checklist", "get_verdict"),
    ("coin_checklist", "verdict"),
    ("ship_x", "get_verdict"),
    ("verdict", "get_verdict"),
    ("filter", "get_verdict"),
)

_start_lock = threading.Lock()
_step_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_block: dict | None = None
_snapshot: dict = {}
_verdict_override: Callable[[str], Any] | None = None
_provider_cache: tuple[float, Any] | None = None
_PROVIDER_TTL_S = 30.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_verdict(raw: Any) -> str:
    text = str(raw or "").strip().upper().replace("_", "-").replace(" ", "-")
    while "--" in text:
        text = text.replace("--", "-")
    if text in _RANK:
        return text
    return "UNKNOWN"


def _clear_provider_cache() -> None:
    global _provider_cache
    _provider_cache = None


def _lookup_provider() -> Callable | None:
    """First checklist callable SHIP X (or a sibling module) exported."""
    global _provider_cache
    now = time.time()
    if _provider_cache is not None and now - _provider_cache[0] < _PROVIDER_TTL_S:
        return _provider_cache[1]
    found = None
    for mod_name, fn_name in _PROVIDER_SPEC:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            found = fn
            break
    _provider_cache = (now, found)
    return found


def get_verdict(mint: str) -> str:
    """Checklist verdict for one mint.

    SHIP X owns the words BUY-WATCH, WATCH, and SKIP. When that code exposes
    ``get_verdict`` or ``verdict`` (see ``_PROVIDER_SPEC``), this returns its
    answer. A missing module, a bad value, or an error is ``UNKNOWN``.
    """
    mint = (mint or "").strip()
    if not mint:
        return "UNKNOWN"
    if _verdict_override is not None:
        try:
            return _normalize_verdict(_verdict_override(mint))
        except Exception:
            logger.exception("verdict override failed")
            return "UNKNOWN"
    fn = _lookup_provider()
    if fn is None:
        return "UNKNOWN"
    try:
        return _normalize_verdict(fn(mint))
    except Exception:
        logger.exception("checklist verdict failed for %s", mint[:8])
        return "UNKNOWN"


def set_verdict_fn(fn: Callable[[str], Any] | None) -> None:
    """Test hook. Production leaves this unset and uses SHIP X when it imports."""
    global _verdict_override
    _verdict_override = fn


def allows_buy(ai_id: str, verdict: str, settings: dict | None) -> bool:
    """True when this AI may open a paper buy at this verdict.

    LEDGER is checklist-strict: only BUY-WATCH. SKIP, WATCH, and UNKNOWN are
    refused even if a saved setting says otherwise. VECTOR uses its min verdict.
    """
    verdict = _normalize_verdict(verdict)
    if ai_id == "LEDGER":
        return verdict == "BUY-WATCH"
    floor = _normalize_verdict((settings or {}).get("min_verdict"))
    if floor not in _RANK:
        floor = "UNKNOWN"
    return _RANK[verdict] >= _RANK[floor]


def _default_settings(ai_id: str) -> dict:
    if ai_id == "LEDGER":
        return {"stop_pct": 15, "tp_pct": 30, "min_verdict": "BUY-WATCH", "hold_min": 30}
    # UNKNOWN floor: momentum may buy before a checklist exists, and may buy SKIP.
    return {"stop_pct": 20, "tp_pct": 40, "min_verdict": "UNKNOWN", "hold_min": 20}


def _clamp_settings(ai_id: str, raw: dict | None) -> dict:
    base = _default_settings(ai_id)
    src = raw if isinstance(raw, dict) else {}
    out = dict(base)
    for key, (lo, hi, step) in _BOUNDS.items():
        try:
            val = float(src.get(key, base[key]))
        except (TypeError, ValueError):
            val = float(base[key])
        snapped = lo + round((val - lo) / step) * step
        snapped = int(max(lo, min(hi, snapped)))
        out[key] = snapped
    if ai_id == "LEDGER":
        out["min_verdict"] = "BUY-WATCH"
    else:
        verdict = _normalize_verdict(src.get("min_verdict", base["min_verdict"]))
        out["min_verdict"] = verdict if verdict in _RANK else "UNKNOWN"
    return out


def _new_session(now: float) -> dict:
    start = game.START_BANKROLL
    return {
        "session_id": uuid.uuid4().hex[:12],
        "status": "active",
        "started_at": _iso(now),
        "ends_at": _iso(now + game.SESSION_HOURS * 3600),
        "started_epoch": now,
        "ends_epoch": now + game.SESSION_HOURS * 3600,
        "cash_usd": start,
        "start_bankroll": start,
        "peak_equity": start,
        "max_drawdown_pct": 0.0,
        "positions": [],
        "closed": [],
        "pending": None,
    }


def _new_trader(ai_id: str, now: float) -> dict:
    style = "momentum" if ai_id == "VECTOR" else "checklist"
    return {
        "id": ai_id,
        "style": style,
        "settings": _default_settings(ai_id),
        "buy_times": [],
        "receipts": [],
        "suppress_buy_until": 0.0,
        "session": _new_session(now),
    }


def _empty_block(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    return {
        "version": 1,
        "traders": {
            "VECTOR": _new_trader("VECTOR", now),
            "LEDGER": _new_trader("LEDGER", now),
        },
        "series": {"YOU": [], "VECTOR": [], "LEDGER": []},
        "owner_session_id": None,
        "owner_max_dd": 0.0,
        "proofs": [],
        "settings_log": [],
    }


def _normalize_block(raw: Any, now: float) -> dict:
    block = _empty_block(now)
    if not isinstance(raw, dict):
        return block
    for ai_id in ("VECTOR", "LEDGER"):
        src = raw.get("traders", {}).get(ai_id) if isinstance(raw.get("traders"), dict) else None
        if not isinstance(src, dict):
            continue
        trader = block["traders"][ai_id]
        trader["settings"] = _clamp_settings(ai_id, src.get("settings"))
        times = []
        for item in src.get("buy_times") or []:
            try:
                times.append(float(item))
            except (TypeError, ValueError):
                continue
        trader["buy_times"] = times[-12:]
        receipts = [r for r in (src.get("receipts") or []) if isinstance(r, dict)]
        trader["receipts"] = receipts[-RECEIPT_MAX:]
        try:
            trader["suppress_buy_until"] = float(src.get("suppress_buy_until") or 0)
        except (TypeError, ValueError):
            trader["suppress_buy_until"] = 0.0
        session = src.get("session") if isinstance(src.get("session"), dict) else None
        if session and session.get("status") == "active":
            trader["session"] = _clean_session(session, now)
    series = raw.get("series") if isinstance(raw.get("series"), dict) else {}
    for name in ("YOU", "VECTOR", "LEDGER"):
        pts = []
        for pt in series.get(name) or []:
            if not isinstance(pt, dict):
                continue
            try:
                pts.append({"t": float(pt.get("t")), "pnl_pct": float(pt.get("pnl_pct") or 0)})
            except (TypeError, ValueError):
                continue
        block["series"][name] = pts[-SERIES_MAX:]
    block["owner_session_id"] = raw.get("owner_session_id")
    try:
        block["owner_max_dd"] = float(raw.get("owner_max_dd") or 0)
    except (TypeError, ValueError):
        block["owner_max_dd"] = 0.0
    block["proofs"] = [p for p in (raw.get("proofs") or []) if isinstance(p, dict)][:PROOF_MAX]
    block["settings_log"] = [p for p in (raw.get("settings_log") or []) if isinstance(p, dict)][:LOG_MAX]
    return block


def _clean_session(session: dict, now: float) -> dict:
    base = _new_session(now)
    base["session_id"] = str(session.get("session_id") or base["session_id"])
    for key in ("started_at", "ends_at"):
        if session.get(key):
            base[key] = session[key]
    for key in ("started_epoch", "ends_epoch", "cash_usd", "start_bankroll", "peak_equity", "max_drawdown_pct"):
        try:
            if session.get(key) is not None:
                base[key] = float(session[key])
        except (TypeError, ValueError):
            pass
    base["positions"] = [p for p in (session.get("positions") or []) if isinstance(p, dict)]
    closed = [p for p in (session.get("closed") or []) if isinstance(p, dict)]
    base["closed"] = closed[:40]
    pending = session.get("pending")
    base["pending"] = pending if isinstance(pending, dict) else None
    base["status"] = "active"
    return base


def _read_disk() -> tuple[dict | None, bool]:
    if not game._lock.acquire(timeout=game.LOCK_WAIT_S):
        return None, False
    try:
        raw = game._read_json(game.STATE_PATH, None)
    finally:
        game._lock.release()
    if isinstance(raw, dict) and isinstance(raw.get(AI_KEY), dict):
        return raw[AI_KEY], True
    return {}, True


def _persist(block: dict) -> bool:
    if not game._lock.acquire(timeout=game.LOCK_WAIT_S):
        return False
    try:
        raw = game._read_json(game.STATE_PATH, None)
        if not isinstance(raw, dict):
            raw = game.empty_state()
        else:
            raw = dict(raw)
        raw[AI_KEY] = block
        game._write_json(game.STATE_PATH, raw)
        return True
    finally:
        game._lock.release()


def _load_block(now: float) -> dict | None:
    global _block
    if _block is not None:
        return _block
    disk, ok = _read_disk()
    if not ok:
        return None
    _block = _normalize_block(disk, now)
    return _block


def reset_for_tests() -> None:
    """Drop the in-memory book so a test can point STATE_PATH at a temp file."""
    global _block, _snapshot
    _block = None
    _snapshot = {}
    set_verdict_fn(None)
    _clear_provider_cache()
    if game._lock.acquire(timeout=game.LOCK_WAIT_S):
        try:
            raw = game._read_json(game.STATE_PATH, None)
            if isinstance(raw, dict) and AI_KEY in raw:
                raw = dict(raw)
                raw.pop(AI_KEY, None)
                game._write_json(game.STATE_PATH, raw)
        finally:
            game._lock.release()


def filter_universe() -> list[dict]:
    """Coins on the Filter list the owner sees. QA fixture rows are not tradable."""
    raw = game._read_json(game.FLAGGED_PATH, [])
    if not isinstance(raw, list):
        return []
    out = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        if row.get("qa_max_fixture") or row.get("qa_gold_fixture") or row.get("fixture"):
            continue
        if row.get("flagged") is False:
            continue
        mint = str(row.get("mint") or "").strip()
        if mint in seen or not game.looks_like_mint(mint):
            continue
        seen.add(mint)
        out.append(row)
    return out


def _h1(row: dict) -> float:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for src in (metrics, row):
        val = src.get("price_change_h1")
        try:
            if val is not None and val != "":
                return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def _score(row: dict) -> float:
    try:
        return float(row.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _symbol(row: dict, mint: str) -> str:
    for key in ("ticker", "symbol"):
        val = str(row.get(key) or "").strip()
        if val:
            return val[:16]
    return mint[:4]


def _live_quote(mint: str, purpose: str = "mark") -> dict | None:
    """Same fresh-pool rule as the owner's fill. QA price walks are not used."""
    del purpose
    try:
        return game.fetch_live_tick(mint, apply_qa=False)
    except Exception:
        logger.exception("quote failed")
        return None


def _buys_in_hour(trader: dict, now: float) -> int:
    cutoff = now - 3600.0
    times = [t for t in trader.get("buy_times") or [] if t > cutoff]
    trader["buy_times"] = times
    return len(times)


def _equity(session: dict) -> float:
    eq = float(session.get("cash_usd") or 0)
    for pos in session.get("positions") or []:
        px = pos.get("last_price_usd")
        if px is None:
            px = pos.get("entry_price_usd") or 0
        try:
            eq += float(px) * float(pos.get("qty_tokens") or 0)
        except (TypeError, ValueError):
            continue
    return eq


def _note_equity(session: dict) -> tuple[float, float, float]:
    eq = _equity(session)
    start = float(session.get("start_bankroll") or game.START_BANKROLL) or game.START_BANKROLL
    peak = max(float(session.get("peak_equity") or eq), eq)
    session["peak_equity"] = round(peak, 6)
    dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
    if dd < 0:
        dd = 0.0
    session["max_drawdown_pct"] = round(max(float(session.get("max_drawdown_pct") or 0), dd), 4)
    pnl = eq - start
    pnl_pct = (eq / start - 1.0) * 100.0 if start else 0.0
    return eq, pnl, pnl_pct


def _append_series(block: dict, name: str, now: float, pnl_pct: float) -> None:
    row = block.setdefault("series", {}).setdefault(name, [])
    if row and now - float(row[-1].get("t") or 0) < SERIES_GAP_S:
        return
    row.append({"t": now, "pnl_pct": round(pnl_pct, 4)})
    if len(row) > SERIES_MAX:
        del row[:-SERIES_MAX]


def _sample_owner(block: dict, now: float) -> dict:
    """Owner mark from the saved book. Does not tick, quote, or trade."""
    if not game._lock.acquire(timeout=game.LOCK_WAIT_S):
        return {
            "id": "YOU",
            "label": "You",
            "pnl_pct": 0.0,
            "pnl_usd": 0.0,
            "max_drawdown_pct": round(float(block.get("owner_max_dd") or 0), 4),
            "equity_usd": game.START_BANKROLL,
            "session_id": block.get("owner_session_id"),
        }
    try:
        st = game._read_json(game.STATE_PATH, None)
    finally:
        game._lock.release()
    if not isinstance(st, dict):
        st = game.empty_state()
    else:
        st = dict(st)
        st.pop(AI_KEY, None)
    for key, val in game.empty_state().items():
        st.setdefault(key, val)
    eq = game.equity(st)
    start = float(st.get("start_bankroll") or game.START_BANKROLL) or game.START_BANKROLL
    pnl = eq - start
    pnl_pct = (eq / start - 1.0) * 100.0 if start else 0.0
    sid = st.get("session_id")
    if sid != block.get("owner_session_id"):
        block["owner_session_id"] = sid
        block["owner_max_dd"] = 0.0
        block.setdefault("series", {})["YOU"] = []
    try:
        peak = float(st.get("peak_equity") or eq)
    except (TypeError, ValueError):
        peak = eq
    dd_now = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
    if dd_now < 0:
        dd_now = 0.0
    block["owner_max_dd"] = round(max(float(block.get("owner_max_dd") or 0), dd_now), 4)
    _append_series(block, "YOU", now, pnl_pct)
    return {
        "id": "YOU",
        "label": "You",
        "pnl_pct": round(pnl_pct, 4),
        "pnl_usd": round(pnl, 6),
        "max_drawdown_pct": block["owner_max_dd"],
        "equity_usd": round(eq, 6),
        "session_id": sid,
    }


def _rank(rows: list[dict]) -> list[dict]:
    order = {"YOU": 0, "VECTOR": 1, "LEDGER": 2}
    ranked = sorted(rows, key=lambda r: (-float(r.get("pnl_pct") or 0), order.get(r.get("id"), 9)))
    out = []
    for i, row in enumerate(ranked, start=1):
        item = dict(row)
        item["rank"] = i
        out.append(item)
    return out


def _study_trade(row: dict, status: str) -> dict:
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol") or "?",
        "mint": row.get("mint") or "",
        "verdict": row.get("verdict") or "UNKNOWN",
        "skip_flag": bool(row.get("skip_flag") or row.get("verdict") == "SKIP"),
        "flag": "SKIP" if (row.get("skip_flag") or row.get("verdict") == "SKIP") else "",
        "entry_reason": row.get("entry_reason") or "",
        "exit_reason": row.get("exit_reason") or ("still open" if status == "open" else ""),
        "lesson": row.get("lesson") or ("Still open. No exit yet." if status == "open" else ""),
        "pnl_usd": row.get("pnl_usd"),
        "pnl_pct": row.get("pnl_pct"),
        "opened_at": row.get("opened_at"),
        "closed_at": row.get("closed_at"),
        "status": status,
    }


def _view(block: dict, owner: dict) -> dict:
    rows = [owner]
    traders_out = {}
    for ai_id in ("VECTOR", "LEDGER"):
        trader = block["traders"][ai_id]
        session = trader["session"]
        trades = []
        for pos in session.get("positions") or []:
            entry = float(pos.get("entry_price_usd") or 0)
            last = float(pos.get("last_price_usd") or entry or 0)
            stake = float(pos.get("stake_usd") or 0)
            pnl_u = (last - entry) * float(pos.get("qty_tokens") or 0) if entry else 0.0
            pnl_p = (last / entry - 1.0) * 100.0 if entry else 0.0
            shown = dict(pos)
            shown["pnl_usd"] = round(pnl_u, 6)
            shown["pnl_pct"] = round(pnl_p, 4)
            shown["exit_reason"] = "still open"
            shown["lesson"] = "Still open. No exit yet."
            trades.append(_study_trade(shown, "open"))
        for closed in session.get("closed") or []:
            trades.append(_study_trade(closed, "closed"))
        _eq, _pnl, pnl_pct = _note_equity(session)
        rows.append({
            "id": ai_id,
            "label": ai_id,
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(_pnl, 6),
            "max_drawdown_pct": round(float(session.get("max_drawdown_pct") or 0), 4),
            "equity_usd": round(_eq, 6),
            "session_id": session.get("session_id"),
        })
        traders_out[ai_id] = {
            "id": ai_id,
            "style": trader.get("style"),
            "session_id": session.get("session_id"),
            "status": session.get("status"),
            "started_at": session.get("started_at"),
            "ends_at": session.get("ends_at"),
            "cash_usd": session.get("cash_usd"),
            "equity_usd": round(_eq, 6),
            "pnl_usd": round(_pnl, 6),
            "pnl_pct": round(pnl_pct, 4),
            "max_drawdown_pct": round(float(session.get("max_drawdown_pct") or 0), 4),
            "settings": dict(trader.get("settings") or {}),
            "trades": trades,
            "open_count": len(session.get("positions") or []),
        }
    # Rebuild AI series points with the caller's clock. _view is wrapped by step.
    return {
        "ok": True,
        "feature": "ai_traders",
        "disclaimer": "Paper research only. Not financial advice. No real trades.",
        "reaction_delay_s": REACTION_DELAY_S,
        "trade_cap_per_hour": MAX_TRADES_PER_HOUR,
        "scoreboard": _rank(rows),
        "series": {name: [dict(pt) for pt in (block.get("series") or {}).get(name) or []] for name in ("YOU", "VECTOR", "LEDGER")},
        "traders": traders_out,
        "settings_log": [dict(row) for row in (block.get("settings_log") or [])],
        "proofs": [dict(row) for row in (block.get("proofs") or [])],
    }


def _publish(block: dict, owner: dict, now: float) -> dict:
    global _snapshot
    for ai_id in ("VECTOR", "LEDGER"):
        _eq, _pnl, pnl_pct = _note_equity(block["traders"][ai_id]["session"])
        _append_series(block, ai_id, now, pnl_pct)
    view = _view(block, owner)
    view["series"] = {
        name: [dict(pt) for pt in (block.get("series") or {}).get(name) or []]
        for name in ("YOU", "VECTOR", "LEDGER")
    }
    _snapshot = view
    return view


def public_payload() -> dict:
    """Scoreboard and study data. No quote, no trade, no wait beyond 0.25s."""
    if _snapshot:
        return copy.deepcopy(_snapshot)
    disk, ok = _read_disk()
    now = time.time()
    block = _normalize_block(disk if ok else {}, now)
    owner = _sample_owner(block, now) if ok else {
        "id": "YOU",
        "label": "You",
        "pnl_pct": 0.0,
        "pnl_usd": 0.0,
        "max_drawdown_pct": 0.0,
        "equity_usd": game.START_BANKROLL,
        "session_id": None,
    }
    return _view(block, owner)


def _log_proof(block: dict, proof: dict) -> None:
    block.setdefault("proofs", []).insert(0, proof)
    del block["proofs"][PROOF_MAX:]
    logger.info(
        "proof ai=%s kind=%s accepted=%s delay_s=%s delay_ok=%s fresh_ok=%s source=%s quote_age_s=%s trades_this_hour=%s cap=%s cap_ok=%s verdict=%s reason=%s",
        proof.get("ai"),
        proof.get("kind"),
        proof.get("accepted"),
        proof.get("delay_s"),
        proof.get("delay_ok"),
        proof.get("fresh_ok"),
        proof.get("source"),
        proof.get("quote_age_s"),
        proof.get("trades_this_hour"),
        proof.get("cap"),
        proof.get("cap_ok"),
        proof.get("verdict"),
        proof.get("reason"),
    )


def _proof(
    *,
    ai_id: str,
    kind: str,
    mint: str,
    symbol: str,
    verdict: str,
    signal_at: float,
    now: float,
    tick: dict | None,
    trades_this_hour: int,
    accepted: bool,
    reason: str,
    cap_applies: bool,
) -> dict:
    age = None
    source = ""
    quote_live = False
    jupiter = False
    if isinstance(tick, dict):
        source = str(tick.get("source") or tick.get("src") or "")
        quote_live = bool(tick.get("quote_live"))
        jupiter = game.jupiter_priced(tick)
        try:
            if tick.get("quote_age_s") is not None:
                age = round(float(tick.get("quote_age_s")), 3)
        except (TypeError, ValueError):
            age = None
    fresh = bool(game.fillable_tick(tick))
    delay = round(now - signal_at, 3)
    cap_ok = (trades_this_hour < MAX_TRADES_PER_HOUR) if cap_applies else True
    return {
        "at": _iso(now),
        "ai": ai_id,
        "kind": kind,
        "mint": mint,
        "symbol": symbol,
        "verdict": verdict,
        "skip_flag": verdict == "SKIP",
        "flag": "SKIP" if verdict == "SKIP" else "",
        "signal_at": _iso(signal_at),
        "acted_at": _iso(now),
        "delay_s": delay,
        "delay_ok": delay + 1e-9 >= REACTION_DELAY_S,
        "source": source,
        "quote_age_s": age,
        "quote_live": quote_live,
        "stale": (not fresh),
        "jupiter": jupiter,
        "fresh_ok": fresh,
        "trades_this_hour": trades_this_hour,
        "cap": MAX_TRADES_PER_HOUR,
        "cap_ok": cap_ok,
        "cap_applies": cap_applies,
        "accepted": accepted,
        "reason": reason,
    }


def _entry_reason(ai_id: str, row: dict, verdict: str) -> str:
    if ai_id == "LEDGER":
        return f"checklist BUY-WATCH, score {_score(row):g}"
    return f"momentum h1 {_h1(row):+.1f}% on the Filter list"


def _exit_reason_text(code: str, settings: dict) -> str:
    if code == "stop":
        return f"stop {settings['stop_pct']}%"
    if code == "tp":
        return f"take-profit {settings['tp_pct']}%"
    if code == "hold":
        return f"hold {settings['hold_min']}m"
    if code == "force_24h":
        return "24h force-sell"
    return code or "close"


def _lesson(verdict: str, pnl_pct: float, code: str) -> str:
    pnl = f"{pnl_pct:+.1f}%"
    if verdict == "SKIP" and pnl_pct < 0:
        return f"SKIP closed {pnl} on a {code or 'close'}. The receipt stays flagged SKIP."
    if verdict == "SKIP":
        return f"SKIP closed {pnl}. The checklist would have left this name; the receipt is flagged SKIP."
    if verdict == "UNKNOWN":
        return f"UNKNOWN closed {pnl} on a {code or 'close'}. No checklist verdict was on file."
    if code == "stop":
        return f"{verdict} stopped at {pnl}. The stop ended it."
    if code == "tp":
        return f"{verdict} hit the target at {pnl}."
    if code == "hold":
        return f"{verdict} held out the clock and closed {pnl}."
    if code == "force_24h":
        return f"{verdict} was force-sold at the 24h reset, {pnl}."
    return f"{verdict} closed {pnl}."


def _mark_position(pos: dict, tick: dict | None, now: float) -> bool:
    if not game.fillable_tick(tick):
        return False
    try:
        px = float(tick["price_usd"])
        entry = float(pos.get("entry_price_usd") or 0)
    except (TypeError, ValueError, KeyError):
        return False
    if px <= 0 or entry <= 0:
        return False
    pos["mark_fresh"] = True
    pos["last_price_usd"] = px
    pos["price_ts"] = tick.get("price_ts")
    pos["price_source"] = tick.get("source")
    pnl = (px / entry - 1.0) * 100.0
    t = max(0.0, now - float(pos.get("opened_epoch") or now))
    path = pos.setdefault("path", [{"t": 0.0, "pnl_pct": 0.0}])
    last = path[-1] if path else {"t": -999.0, "pnl_pct": 0.0}
    if t - float(last.get("t") or 0) >= 10 or abs(pnl - float(last.get("pnl_pct") or 0)) >= 1:
        path.append({"t": round(t, 3), "pnl_pct": round(pnl, 4)})
    if len(path) > PATH_MAX:
        pos["path"] = [path[0]] + path[-(PATH_MAX - 1):]
    return True


def _pnl_pct(pos: dict) -> float:
    try:
        entry = float(pos.get("entry_price_usd") or 0)
        last = float(pos.get("last_price_usd") or entry or 0)
    except (TypeError, ValueError):
        return 0.0
    if entry <= 0:
        return 0.0
    return (last / entry - 1.0) * 100.0


def _exit_code(pos: dict, settings: dict, now: float, fresh: bool) -> str | None:
    if not fresh:
        return None
    pnl = _pnl_pct(pos)
    if pnl <= -abs(float(settings["stop_pct"])):
        return "stop"
    if pnl >= abs(float(settings["tp_pct"])):
        return "tp"
    held = now - float(pos.get("opened_epoch") or now)
    if held >= float(settings["hold_min"]) * 60.0:
        return "hold"
    return None


def _find_row(rows: list[dict], mint: str) -> dict | None:
    for row in rows:
        if str(row.get("mint") or "") == mint:
            return row
    return None


def _open_mints(session: dict) -> set[str]:
    return {str(p.get("mint") or "") for p in session.get("positions") or []}


def _pick_candidate(ai_id: str, trader: dict, rows: list[dict], session: dict) -> dict | None:
    held = _open_mints(session)
    settings = trader["settings"]
    cands = []
    for row in rows:
        mint = str(row.get("mint") or "")
        if not mint or mint in held:
            continue
        verdict = get_verdict(mint)
        if not allows_buy(ai_id, verdict, settings):
            continue
        h1 = _h1(row)
        if ai_id == "VECTOR" and h1 <= 0:
            continue
        rank_key = h1 if ai_id == "VECTOR" else _score(row)
        cands.append((rank_key, mint, row, verdict))
    if not cands:
        return None
    cands.sort(key=lambda item: (-item[0], item[1]))
    _rank_key, mint, row, verdict = cands[0]
    return {
        "mint": mint,
        "symbol": _symbol(row, mint),
        "name": str(row.get("name") or _symbol(row, mint)),
        "verdict": verdict,
        "entry_reason": _entry_reason(ai_id, row, verdict),
        "row": row,
    }


def _close_position(
    block: dict,
    trader: dict,
    pos: dict,
    price: float,
    code: str,
    now: float,
    proof: dict | None,
) -> dict:
    session = trader["session"]
    settings = trader["settings"]
    try:
        qty = float(pos.get("qty_tokens") or 0)
        entry = float(pos.get("entry_price_usd") or 0)
        stake = float(pos.get("stake_usd") or 0)
    except (TypeError, ValueError):
        qty, entry, stake = 0.0, 0.0, 0.0
    proceeds = price * qty
    pnl = proceeds - stake
    pnl_pct = (price / entry - 1.0) * 100.0 if entry else 0.0
    hold_s = max(0.0, now - float(pos.get("opened_epoch") or now))
    path = list(pos.get("path") or [])
    path.append({"t": round(hold_s, 3), "pnl_pct": round(pnl_pct, 4)})
    verdict = str(pos.get("verdict") or "UNKNOWN")
    receipt = {
        "id": pos.get("id"),
        "mint": pos.get("mint"),
        "symbol": pos.get("symbol") or "?",
        "name": pos.get("name") or "",
        "verdict": verdict,
        "skip_flag": bool(pos.get("skip_flag") or verdict == "SKIP"),
        "flag": "SKIP" if (pos.get("skip_flag") or verdict == "SKIP") else "",
        "entry_reason": pos.get("entry_reason") or "",
        "exit_reason": _exit_reason_text(code, settings),
        "close_reason": code,
        "lesson": _lesson(verdict, pnl_pct, code),
        "stake_usd": round(stake, 6),
        "entry_price_usd": entry,
        "exit_price_usd": price,
        "pnl_usd": round(pnl, 6),
        "pnl_pct": round(pnl_pct, 4),
        "opened_at": pos.get("opened_at"),
        "closed_at": _iso(now),
        "hold_s": round(hold_s, 3),
        "path": path[-PATH_MAX:],
        "entry_proof": pos.get("entry_proof"),
        "exit_proof": proof,
        "session_id": session.get("session_id"),
    }
    session["cash_usd"] = round(float(session.get("cash_usd") or 0) + proceeds, 6)
    session["positions"] = [p for p in session.get("positions") or [] if p.get("id") != pos.get("id")]
    session.setdefault("closed", []).insert(0, receipt)
    del session["closed"][40:]
    trader.setdefault("receipts", []).append(receipt)
    del trader["receipts"][:-RECEIPT_MAX]
    _note_equity(session)
    record = judge_change(trader["id"], trader["settings"], trader["receipts"], receipt)
    if record.get("applied"):
        trader["settings"] = _clamp_settings(
            trader["id"],
            {**trader["settings"], record["setting"]: record["after"]},
        )
    if not record.get("skipped"):
        block.setdefault("settings_log", []).insert(0, record)
        del block["settings_log"][LOG_MAX:]
        _log_settings(record)
    return receipt


def _log_settings(record: dict) -> None:
    train = record.get("train") or {}
    valid = record.get("validate") or {}
    logger.info(
        "settings ai=%s applied=%s setting=%s before=%s after=%s reason=%s reject=%s train_current=%s train_candidate=%s validate_current=%s validate_candidate=%s",
        record.get("ai"),
        record.get("applied"),
        record.get("setting"),
        record.get("before"),
        record.get("after"),
        record.get("reason"),
        record.get("reject_reason"),
        (train.get("current") or {}).get("pnl_usd"),
        (train.get("candidate") or {}).get("pnl_usd"),
        (valid.get("current") or {}).get("pnl_usd"),
        (valid.get("candidate") or {}).get("pnl_usd"),
    )


def _empty_stats(n: int) -> dict:
    return {
        "n": n,
        "trades": 0,
        "pnl_usd": None,
        "pnl_pct": None,
        "max_drawdown_pct": None,
        "end_equity": None,
    }


def _path_of(receipt: dict) -> list[dict]:
    path = receipt.get("path")
    out = []
    if isinstance(path, list):
        for pt in path:
            if not isinstance(pt, dict):
                continue
            try:
                out.append({"t": float(pt.get("t") or 0), "pnl_pct": float(pt.get("pnl_pct") or 0)})
            except (TypeError, ValueError):
                continue
    if out:
        out.sort(key=lambda pt: pt["t"])
        return out
    try:
        pnl = float(receipt.get("pnl_pct") or 0)
    except (TypeError, ValueError):
        pnl = 0.0
    try:
        hold = float(receipt.get("hold_s") or 0)
    except (TypeError, ValueError):
        hold = 0.0
    return [{"t": 0.0, "pnl_pct": 0.0}, {"t": max(0.0, hold), "pnl_pct": pnl}]


def replay_pnl_pct(receipt: dict, settings: dict, ai_id: str) -> float | None:
    """Exit percent of one stored receipt under a settings dict. None if not taken."""
    if not allows_buy(ai_id, str(receipt.get("verdict") or "UNKNOWN"), settings):
        return None
    stop = abs(float(settings["stop_pct"]))
    tp = abs(float(settings["tp_pct"]))
    hold_s = float(settings["hold_min"]) * 60.0
    last = 0.0
    for pt in _path_of(receipt):
        pnl = pt["pnl_pct"]
        if pnl <= -stop:
            return -stop
        if pnl >= tp:
            return tp
        if pt["t"] >= hold_s:
            return pnl
        last = pnl
    return last


def simulate(receipts: list[dict], settings: dict, ai_id: str) -> dict:
    """Replay receipts in order on a $5 book, $1 stake, for one settings dict."""
    eq = float(game.START_BANKROLL)
    peak = eq
    max_dd = 0.0
    pnl_sum = 0.0
    taken = 0
    for receipt in receipts:
        ret = replay_pnl_pct(receipt, settings, ai_id)
        if ret is None:
            continue
        stake = min(1.0, eq)
        if stake <= 0:
            break
        delta = stake * float(ret) / 100.0
        eq += delta
        pnl_sum += delta
        taken += 1
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    start = float(game.START_BANKROLL)
    return {
        "n": len(receipts),
        "trades": taken,
        "pnl_usd": round(pnl_sum, 6),
        "pnl_pct": round((eq / start - 1.0) * 100.0, 4) if start else 0.0,
        "max_drawdown_pct": round(max_dd, 4),
        "end_equity": round(eq, 6),
    }


def _step_value(key: str, current: Any, direction: str, ai_id: str) -> Any:
    if key == "min_verdict":
        if ai_id == "LEDGER":
            return "BUY-WATCH"
        cur = _normalize_verdict(current)
        idx = _RANK.get(cur, 0)
        if direction == "tighter":
            idx = min(len(_LADDER) - 1, idx + 1)
        elif direction == "looser":
            idx = max(0, idx - 1)
        return _LADDER[idx]
    lo, hi, step = _BOUNDS[key]
    cur = float(current)
    nxt = cur + step if direction == "up" else cur - step
    snapped = lo + round((nxt - lo) / step) * step
    return int(max(lo, min(hi, snapped)))


def _distance(key: str, current: Any, cand: Any) -> float:
    if key == "min_verdict":
        return abs(_RANK.get(_normalize_verdict(cand), 0) - _RANK.get(_normalize_verdict(current), 0))
    return abs(float(cand) - float(current))


def propose_nudge(ai_id: str, settings: dict, trade: dict) -> dict | None:
    """One deterministic knob and direction from the trade that just closed."""
    settings = _clamp_settings(ai_id, settings)
    try:
        pnl = float(trade.get("pnl_pct") or 0)
    except (TypeError, ValueError):
        pnl = 0.0
    try:
        hold_s = float(trade.get("hold_s") or 0)
    except (TypeError, ValueError):
        hold_s = 0.0
    code = str(trade.get("close_reason") or "")
    verdict = _normalize_verdict(trade.get("verdict"))
    hold_lim = float(settings["hold_min"]) * 60.0
    early = hold_lim > 0 and hold_s < hold_lim * 0.25
    options: list[tuple[str, str, str]] = []
    if ai_id != "LEDGER" and verdict == "SKIP" and pnl < 0:
        options.append(("min_verdict", "tighter", "A SKIP coin lost; raise the verdict floor one step."))
    if code == "stop" and early:
        options.append(("stop_pct", "up", "Stop fired in the first quarter of the hold; test a wider stop."))
    if code == "stop":
        options.append(("stop_pct", "down", "A stop closed the trade; test a tighter stop."))
    if code == "tp":
        options.append(("tp_pct", "up", "The target hit; test a higher target."))
    if code == "hold" and pnl < 0:
        options.append(("hold_min", "down", "The time stop was red; test a shorter hold."))
    if code == "hold" and pnl >= 0:
        options.append(("hold_min", "up", "The time stop was green; test a longer hold."))
    if ai_id != "LEDGER" and pnl > 0:
        options.append(("min_verdict", "looser", "A green close; test one looser verdict floor."))
    if pnl < 0:
        options.append(("hold_min", "down", "The close was red; test a shorter hold."))
    else:
        options.append(("hold_min", "up", "The close was green; test a longer hold."))
    for key, direction, reason in options:
        if _step_value(key, settings[key], direction, ai_id) != settings[key]:
            return {"key": key, "direction": direction, "reason": reason}
    return None


def _split(receipts: list[dict]) -> tuple[list[dict], list[dict]] | None:
    ordered = sorted(receipts, key=lambda r: (str(r.get("closed_at") or ""), str(r.get("id") or "")))
    n = len(ordered)
    if n < 3:
        return None
    cut = (n * 2) // 3
    if cut < 1:
        cut = 1
    if cut >= n:
        cut = n - 1
    return ordered[:cut], ordered[cut:]


def _tune(train: list[dict], settings: dict, proposal: dict, ai_id: str) -> tuple[Any, dict, dict]:
    key = proposal["key"]
    direction = proposal["direction"]
    current = settings[key]
    candidates = []
    val = current
    for _ in range(TUNE_STEPS):
        nxt = _step_value(key, val, direction, ai_id)
        if nxt == val:
            break
        candidates.append(nxt)
        val = nxt
    current_stats = simulate(train, settings, ai_id)
    best_val = candidates[0] if candidates else current
    best_stats = current_stats
    best_rank = None
    for cand in candidates:
        trial = dict(settings)
        trial[key] = cand
        stats = simulate(train, trial, ai_id)
        rank = (
            round(float(stats["pnl_usd"]), 6),
            -float(stats["max_drawdown_pct"]),
            -_distance(key, current, cand),
        )
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_val = cand
            best_stats = stats
    return best_val, best_stats, current_stats


def judge_change(ai_id: str, settings: dict, receipts: list[dict], trade: dict) -> dict:
    """Backtest one proposed nudge. Tune on the first 2/3, accept on the last 1/3."""
    settings = _clamp_settings(ai_id, settings)
    proposal = propose_nudge(ai_id, settings, trade)
    now = _iso(time.time())
    base = {
        "at": now,
        "ai": ai_id,
        "trade_id": trade.get("id"),
        "applied": False,
        "skipped": False,
        "reject_reason": "",
        "reason": "",
        "setting": None,
        "before": None,
        "after": None,
        "direction": None,
        "train": {"current": _empty_stats(len(receipts)), "candidate": _empty_stats(len(receipts))},
        "validate": {"current": _empty_stats(0), "candidate": _empty_stats(0)},
    }
    if not proposal:
        base["skipped"] = True
        base["reject_reason"] = "no bounded step left"
        return base
    key = proposal["key"]
    before = settings[key]
    base["setting"] = key
    base["before"] = before
    base["direction"] = proposal["direction"]
    base["reason"] = proposal["reason"]
    parts = _split(receipts)
    if parts is None:
        after = _step_value(key, before, proposal["direction"], ai_id)
        base["after"] = after
        base["reject_reason"] = "need at least 3 stored receipts to split train and validate"
        base["train"] = {"current": _empty_stats(len(receipts)), "candidate": _empty_stats(len(receipts))}
        base["validate"] = {"current": _empty_stats(0), "candidate": _empty_stats(0)}
        return base
    train, valid = parts
    best, train_cand, train_cur = _tune(train, settings, proposal, ai_id)
    trial = dict(settings)
    trial[key] = best
    valid_cur = simulate(valid, settings, ai_id)
    valid_cand = simulate(valid, trial, ai_id)
    base["after"] = best
    base["train"] = {"current": train_cur, "candidate": train_cand}
    base["validate"] = {"current": valid_cur, "candidate": valid_cand}
    train_beats = round(float(train_cand["pnl_usd"]), 6) > round(float(train_cur["pnl_usd"]), 6)
    valid_beats = round(float(valid_cand["pnl_usd"]), 6) > round(float(valid_cur["pnl_usd"]), 6)
    dd_ok = float(valid_cand["max_drawdown_pct"]) <= DRAWDOWN_LIMIT_PCT
    if not train_beats:
        base["reject_reason"] = "train split did not beat the current setting"
    elif not valid_beats and not dd_ok:
        base["reject_reason"] = "validation did not beat the current setting and drawdown is over 20%"
    elif not valid_beats:
        base["reject_reason"] = "validation did not beat the current setting"
    elif not dd_ok:
        base["reject_reason"] = "validation drawdown is over 20%"
    else:
        base["applied"] = True
    return base


def _ready(pending: dict, now: float) -> bool:
    try:
        return now + 1e-9 >= float(pending.get("ready_at"))
    except (TypeError, ValueError):
        return False


def _arm(kind: str, now: float, **fields) -> dict:
    return {
        "kind": kind,
        "signal_at": now,
        "ready_at": now + REACTION_DELAY_S,
        **fields,
    }


def _fill_buy(block, trader, rows, now, quote_fn) -> None:
    session = trader["session"]
    pending = session.get("pending") or {}
    mint = str(pending.get("mint") or "")
    symbol = str(pending.get("symbol") or "")
    signal_at = float(now if pending.get("signal_at") is None else pending.get("signal_at"))
    n = _buys_in_hour(trader, now)
    ai_id = trader["id"]
    if n >= MAX_TRADES_PER_HOUR:
        proof = _proof(
            ai_id=ai_id, kind="buy", mint=mint, symbol=symbol,
            verdict=str(pending.get("verdict") or "UNKNOWN"),
            signal_at=signal_at, now=now, tick=None, trades_this_hour=n,
            accepted=False, reason="trade cap", cap_applies=True,
        )
        _log_proof(block, proof)
        session["pending"] = None
        trader["suppress_buy_until"] = now + 30.0
        return
    row = _find_row(rows, mint)
    verdict = get_verdict(mint)
    if row is None:
        proof = _proof(
            ai_id=ai_id, kind="buy", mint=mint, symbol=symbol, verdict=verdict,
            signal_at=signal_at, now=now, tick=None, trades_this_hour=n,
            accepted=False, reason="left the filter list", cap_applies=True,
        )
        _log_proof(block, proof)
        session["pending"] = None
        return
    if not allows_buy(ai_id, verdict, trader["settings"]):
        proof = _proof(
            ai_id=ai_id, kind="buy", mint=mint, symbol=symbol, verdict=verdict,
            signal_at=signal_at, now=now, tick=None, trades_this_hour=n,
            accepted=False, reason="verdict", cap_applies=True,
        )
        _log_proof(block, proof)
        session["pending"] = None
        return
    if ai_id == "VECTOR" and _h1(row) <= 0:
        proof = _proof(
            ai_id=ai_id, kind="buy", mint=mint, symbol=symbol, verdict=verdict,
            signal_at=signal_at, now=now, tick=None, trades_this_hour=n,
            accepted=False, reason="momentum", cap_applies=True,
        )
        _log_proof(block, proof)
        session["pending"] = None
        return
    tick = _call_quote(quote_fn, mint, "fill")
    fresh = game.fillable_tick(tick)
    if not fresh:
        why = "jupiter" if game.jupiter_priced(tick) else "stale quote"
        proof = _proof(
            ai_id=ai_id, kind="buy", mint=mint, symbol=symbol, verdict=verdict,
            signal_at=signal_at, now=now, tick=tick if isinstance(tick, dict) else None,
            trades_this_hour=n, accepted=False, reason=why, cap_applies=True,
        )
        _log_proof(block, proof)
        return
    cash = float(session.get("cash_usd") or 0)
    stake = min(STAKE_USD, cash)
    if stake < MIN_STAKE_USD:
        proof = _proof(
            ai_id=ai_id, kind="buy", mint=mint, symbol=symbol, verdict=verdict,
            signal_at=signal_at, now=now, tick=tick, trades_this_hour=n,
            accepted=False, reason="no cash", cap_applies=True,
        )
        _log_proof(block, proof)
        session["pending"] = None
        return
    px = float(tick["price_usd"])
    proof = _proof(
        ai_id=ai_id, kind="buy", mint=mint, symbol=_symbol(row, mint), verdict=verdict,
        signal_at=signal_at, now=now, tick=tick, trades_this_hour=n,
        accepted=True, reason="filled", cap_applies=True,
    )
    pos = {
        "id": uuid.uuid4().hex[:10],
        "mint": mint,
        "symbol": _symbol(row, mint),
        "name": str(row.get("name") or _symbol(row, mint)),
        "stake_usd": round(stake, 6),
        "entry_price_usd": px,
        "qty_tokens": stake / px,
        "opened_at": _iso(now),
        "opened_epoch": now,
        "last_price_usd": px,
        "price_source": tick.get("source"),
        "verdict": verdict,
        "skip_flag": verdict == "SKIP",
        "flag": "SKIP" if verdict == "SKIP" else "",
        "entry_reason": pending.get("entry_reason") or _entry_reason(ai_id, row, verdict),
        "path": [{"t": 0.0, "pnl_pct": 0.0}],
        "entry_proof": proof,
    }
    session["cash_usd"] = round(cash - stake, 6)
    session.setdefault("positions", []).append(pos)
    trader.setdefault("buy_times", []).append(now)
    session["pending"] = None
    _log_proof(block, proof)
    _note_equity(session)


def _fill_sell(block, trader, now, quote_fn) -> None:
    session = trader["session"]
    pending = session.get("pending") or {}
    pid = pending.get("position_id")
    pos = next((p for p in session.get("positions") or [] if p.get("id") == pid), None)
    signal_at = float(now if pending.get("signal_at") is None else pending.get("signal_at"))
    n = _buys_in_hour(trader, now)
    ai_id = trader["id"]
    if pos is None:
        session["pending"] = None
        return
    mint = str(pos.get("mint") or "")
    symbol = str(pos.get("symbol") or "")
    verdict = str(pos.get("verdict") or "UNKNOWN")
    tick = _call_quote(quote_fn, mint, "fill")
    fresh = game.fillable_tick(tick)
    if not fresh:
        why = "jupiter" if game.jupiter_priced(tick) else "stale quote"
        proof = _proof(
            ai_id=ai_id, kind="sell", mint=mint, symbol=symbol, verdict=verdict,
            signal_at=signal_at, now=now, tick=tick if isinstance(tick, dict) else None,
            trades_this_hour=n, accepted=False, reason=why, cap_applies=False,
        )
        _log_proof(block, proof)
        return
    px = float(tick["price_usd"])
    pos["last_price_usd"] = px
    code = _exit_code(pos, trader["settings"], now, True)
    if code is None:
        proof = _proof(
            ai_id=ai_id, kind="sell", mint=mint, symbol=symbol, verdict=verdict,
            signal_at=signal_at, now=now, tick=tick, trades_this_hour=n,
            accepted=False, reason="exit no longer true", cap_applies=False,
        )
        _log_proof(block, proof)
        session["pending"] = None
        return
    proof = _proof(
        ai_id=ai_id, kind="sell", mint=mint, symbol=symbol, verdict=verdict,
        signal_at=signal_at, now=now, tick=tick, trades_this_hour=n,
        accepted=True, reason=code, cap_applies=False,
    )
    _log_proof(block, proof)
    session["pending"] = None
    _close_position(block, trader, pos, px, code, now, proof)


def _force_flat(block, trader, now, quote_fn) -> None:
    session = trader["session"]
    for pos in list(session.get("positions") or []):
        mint = str(pos.get("mint") or "")
        tick = _call_quote(quote_fn, mint, "fill")
        if game.fillable_tick(tick):
            px = float(tick["price_usd"])
        else:
            try:
                px = float(pos.get("last_price_usd") or pos.get("entry_price_usd") or 0)
            except (TypeError, ValueError):
                px = 0.0
        n = _buys_in_hour(trader, now)
        proof = _proof(
            ai_id=trader["id"], kind="sell", mint=mint, symbol=str(pos.get("symbol") or ""),
            verdict=str(pos.get("verdict") or "UNKNOWN"),
            signal_at=now, now=now,
            tick=tick if isinstance(tick, dict) else None,
            trades_this_hour=n, accepted=True, reason="24h reset", cap_applies=False,
        )
        _log_proof(block, proof)
        _close_position(block, trader, pos, px, "force_24h", now, proof)
    session["positions"] = []
    session["pending"] = None


def _trade_one(block, trader, rows, now, quote_fn) -> None:
    session = trader["session"]
    if now >= float(session.get("ends_epoch") or 0):
        _force_flat(block, trader, now, quote_fn)
        settings = trader["settings"]
        buy_times = list(trader.get("buy_times") or [])
        receipts = list(trader.get("receipts") or [])
        suppress = float(trader.get("suppress_buy_until") or 0)
        fresh = _new_trader(trader["id"], now)
        fresh["settings"] = settings
        fresh["buy_times"] = buy_times
        fresh["receipts"] = receipts
        fresh["suppress_buy_until"] = suppress
        block["traders"][trader["id"]] = fresh
        block["series"][trader["id"]] = []
        return
    for pos in list(session.get("positions") or []):
        pos["mark_fresh"] = False
        tick = _call_quote(quote_fn, str(pos.get("mint") or ""), "mark")
        _mark_position(pos, tick if isinstance(tick, dict) else None, now)
    pending = session.get("pending")
    if isinstance(pending, dict) and pending.get("kind") == "buy" and not _ready(pending, now):
        for pos in session.get("positions") or []:
            code = _exit_code(pos, trader["settings"], now, bool(pos.get("mark_fresh")))
            if code:
                session["pending"] = _arm(
                    "sell", now,
                    position_id=pos.get("id"),
                    mint=pos.get("mint"),
                    symbol=pos.get("symbol"),
                    verdict=pos.get("verdict"),
                    exit_code=code,
                )
                _note_equity(session)
                return
        pending = session.get("pending")
    if isinstance(pending, dict) and _ready(pending, now):
        if pending.get("kind") == "sell":
            _fill_sell(block, trader, now, quote_fn)
        else:
            _fill_buy(block, trader, rows, now, quote_fn)
        trader = block["traders"][trader["id"]]
        session = trader["session"]
    if session.get("pending"):
        _note_equity(session)
        return
    for pos in session.get("positions") or []:
        code = _exit_code(pos, trader["settings"], now, bool(pos.get("mark_fresh")))
        if code:
            session["pending"] = _arm(
                "sell", now,
                position_id=pos.get("id"),
                mint=pos.get("mint"),
                symbol=pos.get("symbol"),
                verdict=pos.get("verdict"),
                exit_code=code,
            )
            _note_equity(session)
            return
    if now < float(trader.get("suppress_buy_until") or 0):
        _note_equity(session)
        return
    cash = float(session.get("cash_usd") or 0)
    if cash < MIN_STAKE_USD:
        _note_equity(session)
        return
    cand = _pick_candidate(trader["id"], trader, rows, session)
    if not cand:
        _note_equity(session)
        return
    session["pending"] = _arm(
        "buy", now,
        mint=cand["mint"],
        symbol=cand["symbol"],
        verdict=cand["verdict"],
        entry_reason=cand["entry_reason"],
    )
    _note_equity(session)


def _call_quote(quote_fn: Callable, mint: str, purpose: str):
    try:
        return quote_fn(mint, purpose)
    except TypeError:
        return quote_fn(mint)


def _step_inner(now: float, quote_fn: Callable, universe: list[dict] | None) -> dict:
    global _block
    block = _load_block(now)
    if block is None:
        return public_payload()
    rows = filter_universe() if universe is None else list(universe)
    for ai_id in ("VECTOR", "LEDGER"):
        _trade_one(block, block["traders"][ai_id], rows, now, quote_fn)
    owner = _sample_owner(block, now)
    view = _publish(block, owner, now)
    _persist(block)
    return view


def step(
    now: float | None = None,
    quote_fn: Callable | None = None,
    universe: list[dict] | None = None,
) -> dict:
    """One background beat. Quotes run here, never on tick, health, or buy."""
    if not _step_lock.acquire(blocking=False):
        return public_payload()
    try:
        clock = time.time() if now is None else float(now)
        fn = quote_fn or _live_quote
        return _step_inner(clock, fn, universe)
    finally:
        _step_lock.release()


def _loop() -> None:
    while not _stop.is_set():
        try:
            step()
        except Exception:
            logger.exception("ai traders step failed")
        _stop.wait(1.0)


def ensure_started() -> None:
    """Start the background trader once. Tests do not call this."""
    global _thread
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="ai-traders", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()
