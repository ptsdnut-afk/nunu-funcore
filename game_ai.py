"""SHIP AB — VECTOR and LEDGER paper books beside the owner's 24h run.

VECTOR is momentum and may buy a checklist SKIP (the receipt says SKIP).
LEDGER buys only at 7/8 or better and never buys SKIP or unknown.

Fills go through game.fillable_tick / fill_price / fill_quote_age_s and
game.build_receipt. The owner price tick is not modified here.

Fairness: Filter list only, a 3s reaction delay, the same 5s quote rule,
and at most 6 entries an hour per AI.

After every closed trade, one small setting nudge is backtested on stored
receipts and feed history (first 2/3 train, last 1/3 validate). It is kept
only when validation P/L beats the current setting and validation drawdown
is 20% or less. Rejected nudges stay in the settings log.

Balances reset with each 24h run. Tuned settings, the settings log, and the
feed history stay in game_state.json.
"""

from __future__ import annotations

import copy
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import checklist_adapter
import game

AI_NAMES = ("VECTOR", "LEDGER")
REACTION_DELAY_S = 3.0
MAX_TRADES_PER_HOUR = 6
STAKE_USD = 1.0
DRAWDOWN_CAP_PCT = 20.0
LEDGER_MIN_PASS = 7
LEDGER_CHECKS = 8
MOMENTUM_M5 = 8.0
MOMENTUM_H1 = 15.0
FEED_CAP = 600
FEED_GAP_S = 20.0
RACE_CAP = 180
LOG_CAP = 80
SETTINGS_LOG_CAP = 80

STOP_MIN, STOP_MAX, STOP_STEP = 8.0, 40.0, 1.0
TP_MIN, TP_MAX, TP_STEP = 10.0, 80.0, 2.0
HOLD_MIN, HOLD_MAX, HOLD_STEP = 120, 3600, 60


def default_settings() -> dict[str, dict[str, Any]]:
    return {
        "VECTOR": {"stop_pct": 18.0, "tp_pct": 40.0, "min_verdict": 0, "hold_s": 600},
        "LEDGER": {"stop_pct": 12.0, "tp_pct": 30.0, "min_verdict": 7, "hold_s": 900},
    }


def fresh_book() -> dict[str, Any]:
    start = float(game.START_BANKROLL)
    return {
        "cash_usd": start,
        "start_bankroll": start,
        "peak_equity": start,
        "max_drawdown_pct": 0.0,
        "positions": [],
        "closed": [],
        "pending": None,
        "trade_epochs": [],
        "equity_usd": start,
        "pnl_usd": 0.0,
        "pnl_pct": 0.0,
    }


def _clamp_field(name: str, field: str, value: float) -> float | int:
    if field == "stop_pct":
        return float(min(STOP_MAX, max(STOP_MIN, round(float(value), 4))))
    if field == "tp_pct":
        return float(min(TP_MAX, max(TP_MIN, round(float(value), 4))))
    if field == "hold_s":
        return int(min(HOLD_MAX, max(HOLD_MIN, int(round(float(value))))))
    if field == "min_verdict":
        lo = LEDGER_MIN_PASS if name == "LEDGER" else 0
        return int(min(8, max(lo, int(round(float(value))))))
    return value


def _clamp_settings(name: str, raw: dict) -> dict[str, Any]:
    base = default_settings()[name]
    out = dict(base)
    for field in ("stop_pct", "tp_pct", "min_verdict", "hold_s"):
        if field in raw and raw[field] is not None:
            try:
                out[field] = _clamp_field(name, field, raw[field])
            except (TypeError, ValueError):
                out[field] = base[field]
    return out


def ensure(st: dict) -> dict:
    """Fill AI keys on a game state. Does not reset a book that is already there."""
    defaults = default_settings()
    stored = st.get("ai_settings") if isinstance(st.get("ai_settings"), dict) else {}
    settings: dict[str, dict] = {}
    for name in AI_NAMES:
        cur = stored.get(name) if isinstance(stored.get(name), dict) else {}
        settings[name] = _clamp_settings(name, cur)
    st["ai_settings"] = settings
    books = st.get("ai_books") if isinstance(st.get("ai_books"), dict) else {}
    for name in AI_NAMES:
        book = books.get(name)
        if not isinstance(book, dict):
            books[name] = fresh_book()
            continue
        blank = fresh_book()
        for key, val in blank.items():
            if key not in book:
                book[key] = copy.deepcopy(val) if isinstance(val, (dict, list)) else val
        if not isinstance(book.get("positions"), list):
            book["positions"] = []
        if not isinstance(book.get("closed"), list):
            book["closed"] = []
        if not isinstance(book.get("trade_epochs"), list):
            book["trade_epochs"] = []
    st["ai_books"] = books
    if not isinstance(st.get("ai_settings_log"), list):
        st["ai_settings_log"] = []
    if not isinstance(st.get("ai_feed"), list):
        st["ai_feed"] = []
    if not isinstance(st.get("ai_log"), list):
        st["ai_log"] = []
    if not isinstance(st.get("ai_race"), list):
        st["ai_race"] = []
    try:
        st["max_drawdown_pct"] = float(st.get("max_drawdown_pct") or 0)
    except (TypeError, ValueError):
        st["max_drawdown_pct"] = 0.0
    return st


def persisted_slice(st: dict) -> dict:
    """Settings, the settings log, and feed history. Balances are not in here."""
    ensure(st)
    return {
        "ai_settings": copy.deepcopy(st["ai_settings"]),
        "ai_settings_log": copy.deepcopy(st["ai_settings_log"]),
        "ai_feed": copy.deepcopy(st["ai_feed"]),
    }


def attach_fresh_run(st: dict, persisted: dict | None) -> dict:
    """New 24h balances. Tuned settings and the log stay."""
    persisted = persisted or {}
    st["ai_settings"] = copy.deepcopy(persisted.get("ai_settings") or default_settings())
    st["ai_settings_log"] = copy.deepcopy(persisted.get("ai_settings_log") or [])
    st["ai_feed"] = copy.deepcopy(persisted.get("ai_feed") or [])
    st["ai_books"] = {name: fresh_book() for name in AI_NAMES}
    st["ai_log"] = []
    st["ai_race"] = []
    st["max_drawdown_pct"] = 0.0
    return ensure(st)


def ledger_allows(card: dict | None, min_verdict: int | float | None = None) -> bool:
    """True only at 7/8 or better. SKIP and unknown never pass."""
    if not isinstance(card, dict):
        return False
    verdict = card.get("verdict")
    if verdict in (None, "", "unknown", "SKIP"):
        return False
    passes = card.get("pass_count")
    if not isinstance(passes, int):
        return False
    try:
        checks = int(card.get("check_count") or LEDGER_CHECKS)
    except (TypeError, ValueError):
        checks = LEDGER_CHECKS
    if checks <= 0:
        return False
    try:
        need = max(LEDGER_MIN_PASS, int(min_verdict if min_verdict is not None else LEDGER_MIN_PASS))
    except (TypeError, ValueError):
        need = LEDGER_MIN_PASS
    if passes < need:
        return False
    if passes * LEDGER_CHECKS < LEDGER_MIN_PASS * checks:
        return False
    return True


def _vector_allows(card: dict | None, min_verdict) -> bool:
    """VECTOR may buy SKIP. A raised min verdict still requires that many passes."""
    try:
        need = int(min_verdict or 0)
    except (TypeError, ValueError):
        need = 0
    if need <= 0:
        return True
    if not isinstance(card, dict):
        return False
    if card.get("verdict") in (None, "", "unknown"):
        return False
    passes = card.get("pass_count")
    return isinstance(passes, int) and passes >= need


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _metrics(row: dict) -> dict:
    metrics = row.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _row_num(row: dict, key: str) -> float | None:
    metrics = _metrics(row)
    val = game._f(metrics.get(key))
    if val is None:
        val = game._f(row.get(key))
    return val


def _row_price(row: dict) -> float | None:
    px = _row_num(row, "price_usd")
    if px is not None and px > 0:
        return px
    return None


def _symbol(row: dict) -> str:
    return str(row.get("ticker") or row.get("symbol") or "?").strip() or "?"


def filter_rows() -> list[dict]:
    raw = game._read_json(game.FLAGGED_PATH, [])
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        mint = str(row.get("mint") or "").strip()
        if len(mint) < 32:
            continue
        out.append(row)
    return out


def momentum_signal(row: dict) -> tuple[float | None, str]:
    """Deterministic momentum. 5m first, else 1h when 5m was not printed."""
    m5 = _row_num(row, "price_change_m5")
    h1 = _row_num(row, "price_change_h1")
    if m5 is not None and m5 >= MOMENTUM_M5:
        return float(m5), f"momentum m5 {m5:+.1f}%"
    if m5 is None and h1 is not None and h1 >= MOMENTUM_H1:
        return float(h1), f"momentum h1 {h1:+.1f}%"
    return None, ""


def _log(st: dict, row: dict) -> None:
    row = dict(row)
    row.setdefault("at", _iso(time.time()))
    logs = st.get("ai_log")
    if not isinstance(logs, list):
        logs = []
        st["ai_log"] = logs
    logs.insert(0, row)
    if len(logs) > LOG_CAP:
        del logs[LOG_CAP:]


def _trades_in_hour(book: dict, now: float) -> int:
    kept = []
    for raw in book.get("trade_epochs") or []:
        try:
            ts = float(raw)
        except (TypeError, ValueError):
            continue
        if now - ts < 3600:
            kept.append(ts)
    book["trade_epochs"] = kept
    return len(kept)


def book_equity(book: dict) -> float:
    eq = float(book.get("cash_usd") or 0)
    for pos in book.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        px = game._f(pos.get("last_price_usd")) or game._f(pos.get("entry_price_usd")) or 0
        eq += float(px or 0) * float(pos.get("qty_tokens") or 0)
    return eq


def refresh_book(book: dict) -> float:
    eq = book_equity(book)
    start = float(book.get("start_bankroll") or game.START_BANKROLL)
    book["equity_usd"] = round(eq, 6)
    book["pnl_usd"] = round(eq - start, 6)
    book["pnl_pct"] = round((eq / start - 1.0) * 100, 2) if start else 0.0
    peak = max(float(book.get("peak_equity") or eq), eq)
    book["peak_equity"] = peak
    dd = ((peak - eq) / peak * 100.0) if peak else 0.0
    book["max_drawdown_pct"] = round(max(float(book.get("max_drawdown_pct") or 0), dd), 4)
    return eq


def lesson_text(
    symbol: str,
    exit_reason: str,
    verdict: str,
    passes,
    checks,
    entry,
    pnl,
    pnl_pct,
) -> str:
    """Templated receipt summary. The decision itself stays a rule, not this text."""
    if isinstance(passes, int):
        score = f"{passes}/{int(checks or LEDGER_CHECKS)}"
    else:
        score = "n/a"
    try:
        px = float(entry)
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        px_bit = "n/a"
    elif px >= 1:
        px_bit = f"${px:.4f}"
    else:
        px_bit = f"${px:.8f}".rstrip("0").rstrip(".")
        if px_bit.endswith("."):
            px_bit = px_bit[:-1]
        if not px_bit.startswith("$"):
            px_bit = "$" + px_bit
    try:
        pnl_v = float(pnl or 0)
    except (TypeError, ValueError):
        pnl_v = 0.0
    try:
        pct_v = float(pnl_pct or 0)
    except (TypeError, ValueError):
        pct_v = 0.0
    return (
        f"{symbol or '?'} {exit_reason or 'close'}. Verdict {verdict or 'unknown'} ({score}). "
        f"Entry {px_bit}. P/L ${pnl_v:+.2f} ({pct_v:+.1f}%)."
    )


def _tick_for(mint: str) -> dict | None:
    try:
        tick = game.fetch_live_tick(mint)
    except Exception:
        return None
    return tick if isinstance(tick, dict) else None


def _held_mints(book: dict) -> set[str]:
    held = set()
    pending = book.get("pending")
    if isinstance(pending, dict) and pending.get("mint"):
        held.add(str(pending["mint"]))
    for pos in book.get("positions") or []:
        if isinstance(pos, dict) and pos.get("mint"):
            held.add(str(pos["mint"]))
    return held


def _decorate_receipt(rec: dict, name: str, pos: dict, card: dict, reason: str) -> None:
    rec["trader"] = name
    rec["entry_reason"] = reason
    rec["exit_reason"] = None
    rec["checklist_verdict"] = card.get("verdict") or "unknown"
    rec["pass_count"] = card.get("pass_count") if isinstance(card.get("pass_count"), int) else None
    rec["check_count"] = card.get("check_count") or LEDGER_CHECKS
    rec["filter_verdict"] = rec.get("verdict")
    rec["lesson"] = ""
    rec["momentum"] = pos.get("momentum")
    verdict = card.get("verdict") or "unknown"
    if verdict == "SKIP":
        rec["verdict"] = "SKIP"
        rec["skip_flag"] = True
    elif verdict in ("WATCH", "BUY-WATCH"):
        rec["verdict"] = verdict
    else:
        rec["checklist_verdict"] = "unknown"


def _open_position(st: dict, name: str, book: dict, settings: dict, pending: dict, tick: dict, now: float) -> None:
    px = game.fill_price(tick)
    age = game.fill_quote_age_s(tick)
    if px is None or age is None:
        return
    card = pending.get("checklist") if isinstance(pending.get("checklist"), dict) else {}
    if name == "LEDGER" and not ledger_allows(card, settings.get("min_verdict")):
        _log(st, {
            "at": _iso(now),
            "ai": name,
            "kind": "skip",
            "mint": pending.get("mint"),
            "symbol": pending.get("symbol"),
            "verdict": card.get("verdict") or "unknown",
            "pass_count": card.get("pass_count"),
            "msg": f"{name} did not buy {pending.get('symbol')} — under 7/8, SKIP, or unknown",
        })
        book["pending"] = None
        return
    if name == "VECTOR" and not _vector_allows(card, settings.get("min_verdict")):
        book["pending"] = None
        return
    if float(book.get("cash_usd") or 0) + 1e-9 < STAKE_USD:
        book["pending"] = None
        return
    delay = float(now) - float(pending.get("decided_epoch") or now)
    trades = _trades_in_hour(book, now) + 1
    mint = str(pending.get("mint") or "")
    reason = str(pending.get("entry_reason") or "")
    pos = {
        "id": uuid.uuid4().hex[:10],
        "mint": mint,
        "symbol": pending.get("symbol") or "?",
        "name": pending.get("name") or pending.get("symbol") or "?",
        "stake_usd": STAKE_USD,
        "entry_price_usd": px,
        "entry_price_ts": tick.get("price_ts"),
        "price_ts": tick.get("price_ts"),
        "qty_tokens": STAKE_USD / px,
        "sl_pct": float(settings["stop_pct"]),
        "tp_pct": float(settings["tp_pct"]),
        "hold_s": int(settings["hold_s"]),
        "opened_at": _iso(now),
        "opened_epoch": float(now),
        "last_price_usd": px,
        "quote_epoch": tick.get("quoted_epoch"),
        "trader": name,
        "entry_reason": reason,
        "checklist_verdict": card.get("verdict") or "unknown",
        "pass_count": card.get("pass_count") if isinstance(card.get("pass_count"), int) else None,
        "check_count": card.get("check_count") or LEDGER_CHECKS,
        "momentum": pending.get("momentum"),
        "client_request_id": None,
    }
    points = pending.get("points")
    rec = game.build_receipt(st, pos, tick, points=points)
    _decorate_receipt(rec, name, pos, card, reason)
    pos["receipt_id"] = rec["id"]
    game._push_receipt(st, rec)
    book["cash_usd"] = round(float(book.get("cash_usd") or 0) - STAKE_USD, 6)
    book.setdefault("positions", []).append(pos)
    book.setdefault("trade_epochs", []).append(float(now))
    book["pending"] = None
    refresh_book(book)
    _log(st, {
        "at": _iso(now),
        "ai": name,
        "kind": "fill",
        "mint": mint,
        "symbol": pos["symbol"],
        "quote_age_s": age,
        "delay_s": round(delay, 3),
        "trades_this_hour": trades,
        "max_trades_per_hour": MAX_TRADES_PER_HOUR,
        "quote_sla_s": game.QUOTE_FRESH_SLA_S,
        "reaction_delay_s": REACTION_DELAY_S,
        "verdict": rec.get("verdict"),
        "receipt_id": rec.get("id"),
        "msg": (
            f"{name} filled {pos['symbol']} quote {age:.3f}s (5s rule) "
            f"after {delay:.1f}s delay, trade {trades}/{MAX_TRADES_PER_HOUR} this hour"
        ),
    })


def _close_position(st: dict, name: str, book: dict, pos: dict, price: float, reason: str, price_ts: str | None, now: float) -> None:
    qty = float(pos.get("qty_tokens") or 0)
    stake = float(pos.get("stake_usd") or 0)
    entry = float(pos.get("entry_price_usd") or 0)
    proceeds = price * qty
    pnl = round(proceeds - stake, 6)
    pnl_pct = round((price / entry - 1.0) * 100, 2) if entry else 0.0
    book["cash_usd"] = round(float(book.get("cash_usd") or 0) + proceeds, 6)
    game._note_receipt_sell(st, pos, price, reason, price_ts, qty, stake, pnl, pnl_pct, True)
    card_verdict = pos.get("checklist_verdict") or "unknown"
    passes = pos.get("pass_count")
    checks = pos.get("check_count") or LEDGER_CHECKS
    lesson = lesson_text(str(pos.get("symbol") or "?"), reason, str(card_verdict), passes, checks, entry, pnl, pnl_pct)
    rec = game._receipt_for(st, pos.get("id"))
    if rec:
        rec["lesson"] = lesson
        rec["exit_reason"] = reason
        rec["entry_reason"] = rec.get("entry_reason") or pos.get("entry_reason")
    closed = {
        **pos,
        "exit_price_usd": price,
        "exit_price_ts": price_ts,
        "pnl_usd": pnl,
        "pnl_pct": pnl_pct,
        "closed_at": _iso(now),
        "close_reason": reason,
        "exit_reason": reason,
        "entry_reason": pos.get("entry_reason") or "",
        "verdict": (rec or {}).get("verdict") or card_verdict,
        "lesson": lesson,
    }
    book.setdefault("closed", []).insert(0, closed)
    book["positions"] = [p for p in (book.get("positions") or []) if p.get("id") != pos.get("id")]
    refresh_book(book)
    _log(st, {
        "at": _iso(now),
        "ai": name,
        "kind": "close",
        "symbol": pos.get("symbol"),
        "exit_reason": reason,
        "verdict": closed["verdict"],
        "lesson": lesson,
        "pnl_usd": pnl,
        "msg": f"{name} closed {pos.get('symbol')} {reason} · {lesson}",
    })
    apply_learning(st, name, closed)


def _exit_reason(pos: dict, price: float, now: float) -> str | None:
    entry = float(pos.get("entry_price_usd") or 0)
    if not entry or not price:
        return None
    chg = (price / entry - 1.0) * 100.0
    sl = pos.get("sl_pct")
    tp = pos.get("tp_pct")
    if sl is not None and chg <= -abs(float(sl)):
        return "stop"
    if tp is not None and chg >= abs(float(tp)):
        return "tp"
    opened = pos.get("opened_epoch")
    try:
        opened_f = float(opened) if opened is not None else None
    except (TypeError, ValueError):
        opened_f = None
    if opened_f is None:
        parsed = game._parse_utc(pos.get("opened_at")) if isinstance(pos.get("opened_at"), str) else None
        opened_f = parsed.timestamp() if parsed else None
    hold = pos.get("hold_s")
    if opened_f is not None and hold is not None and (now - opened_f) >= float(hold):
        return "hold"
    return None


def _mark_positions(st: dict, name: str, book: dict, now: float, session_over: bool) -> None:
    still = []
    for pos in list(book.get("positions") or []):
        if not isinstance(pos, dict):
            continue
        tick = _tick_for(str(pos.get("mint") or ""))
        fresh = game.fillable_tick(tick)
        if fresh and tick:
            px = game.fill_price(tick)
            if px is None:
                fresh = False
        else:
            px = None
        if fresh and px is not None and tick is not None:
            pos["last_price_usd"] = px
            pos["price_ts"] = tick.get("price_ts")
            why = _exit_reason(pos, px, now)
            if why:
                _close_position(st, name, book, pos, px, why, tick.get("price_ts"), now)
                continue
            still.append(pos)
            continue
        if session_over:
            last = game._f(pos.get("last_price_usd")) or game._f(pos.get("entry_price_usd")) or 0.0
            ts = tick.get("price_ts") if isinstance(tick, dict) else pos.get("price_ts")
            _close_position(st, name, book, pos, float(last), "force_24h", ts, now)
            continue
        still.append(pos)
    book["positions"] = still
    refresh_book(book)


def _maybe_fill(st: dict, name: str, book: dict, settings: dict, universe: set[str], now: float) -> None:
    pending = book.get("pending")
    if not isinstance(pending, dict):
        return
    fire = float(pending.get("fire_epoch") or 0)
    if now + 1e-9 < fire:
        return
    delay = now - float(pending.get("decided_epoch") or now)
    if delay + 1e-6 < REACTION_DELAY_S:
        _log(st, {
            "at": _iso(now),
            "ai": name,
            "kind": "delay",
            "delay_s": round(delay, 3),
            "msg": f"{name} still inside the 3s reaction delay",
        })
        return
    mint = str(pending.get("mint") or "")
    if mint not in universe:
        book["pending"] = None
        _log(st, {
            "at": _iso(now),
            "ai": name,
            "kind": "universe",
            "mint": mint,
            "msg": f"{name} dropped {pending.get('symbol')} — not on the Filter list",
        })
        return
    hour = _trades_in_hour(book, now)
    if hour >= MAX_TRADES_PER_HOUR:
        book["pending"] = None
        _log(st, {
            "at": _iso(now),
            "ai": name,
            "kind": "rate",
            "trades_this_hour": hour,
            "max_trades_per_hour": MAX_TRADES_PER_HOUR,
            "msg": f"{name} skipped a fill — {hour}/{MAX_TRADES_PER_HOUR} trades this hour",
        })
        return
    tick = _tick_for(mint)
    if not game.fillable_tick(tick):
        age = None
        if isinstance(tick, dict):
            age = game._f(tick.get("quote_age_s"))
        book["pending"] = None
        _log(st, {
            "at": _iso(now),
            "ai": name,
            "kind": "stale",
            "mint": mint,
            "symbol": pending.get("symbol"),
            "quote_age_s": age,
            "quote_sla_s": game.QUOTE_FRESH_SLA_S,
            "delay_s": round(delay, 3),
            "msg": f"{name} did not fill {pending.get('symbol')} — quote older than 5s",
        })
        return
    _open_position(st, name, book, settings, pending, tick, now)


def _pick_vector(rows: list[dict], cards: dict[str, dict], settings: dict, held: set[str]) -> tuple[dict, dict, float, str] | None:
    best = None
    for row in rows:
        mint = str(row.get("mint") or "")
        if mint in held:
            continue
        strength, reason = momentum_signal(row)
        if strength is None:
            continue
        card = cards.get(mint) or {"verdict": "unknown", "pass_count": None, "check_count": 8}
        if not _vector_allows(card, settings.get("min_verdict")):
            continue
        rank = (strength, mint)
        if best is None or rank > best[0]:
            best = (rank, row, card, strength, reason)
    if not best:
        return None
    return best[1], best[2], best[3], best[4]


def _pick_ledger(rows: list[dict], cards: dict[str, dict], settings: dict, held: set[str]) -> tuple[dict, dict, str] | None:
    best = None
    need = settings.get("min_verdict")
    for row in rows:
        mint = str(row.get("mint") or "")
        if mint in held:
            continue
        card = cards.get(mint) or {"verdict": "unknown", "pass_count": None, "check_count": 8}
        if not ledger_allows(card, need):
            continue
        passes = int(card.get("pass_count") or 0)
        strength, _reason = momentum_signal(row)
        rank = (passes, strength or 0.0, mint)
        if best is None or rank > best[0]:
            best = (rank, row, card)
    if not best:
        return None
    row, card = best[1], best[2]
    passes = card.get("pass_count")
    checks = card.get("check_count") or LEDGER_CHECKS
    reason = f"checklist {passes}/{checks} {card.get('verdict')}"
    return row, card, reason


def _maybe_arm(st: dict, name: str, book: dict, settings: dict, rows: list[dict], cards: dict[str, dict], now: float) -> None:
    if isinstance(book.get("pending"), dict):
        return
    if float(book.get("cash_usd") or 0) + 1e-9 < STAKE_USD:
        return
    hour = _trades_in_hour(book, now)
    if hour >= MAX_TRADES_PER_HOUR:
        _log(st, {
            "at": _iso(now),
            "ai": name,
            "kind": "rate",
            "trades_this_hour": hour,
            "max_trades_per_hour": MAX_TRADES_PER_HOUR,
            "msg": f"{name} did not arm — {hour}/{MAX_TRADES_PER_HOUR} trades this hour",
        })
        return
    held = _held_mints(book)
    picked = None
    momentum = None
    if name == "VECTOR":
        found = _pick_vector(rows, cards, settings, held)
        if found:
            row, card, momentum, reason = found
            picked = (row, card, reason)
    elif name == "LEDGER":
        found_l = _pick_ledger(rows, cards, settings, held)
        if found_l:
            row, card, reason = found_l
            picked = (row, card, reason)
    if not picked:
        return
    row, card, reason = picked
    mint = str(row.get("mint") or "")
    book["pending"] = {
        "mint": mint,
        "symbol": _symbol(row),
        "name": row.get("name") or _symbol(row),
        "decided_epoch": float(now),
        "fire_epoch": float(now) + REACTION_DELAY_S,
        "entry_reason": reason,
        "checklist": {
            "verdict": card.get("verdict") or "unknown",
            "pass_count": card.get("pass_count") if isinstance(card.get("pass_count"), int) else None,
            "check_count": card.get("check_count") or LEDGER_CHECKS,
        },
        "momentum": momentum,
        "points": _feed_points(st, mint),
    }
    _log(st, {
        "at": _iso(now),
        "ai": name,
        "kind": "delay",
        "mint": mint,
        "symbol": book["pending"]["symbol"],
        "delay_s": 0,
        "reaction_delay_s": REACTION_DELAY_S,
        "verdict": card.get("verdict") or "unknown",
        "msg": f"{name} saw {book['pending']['symbol']} — fill waits {REACTION_DELAY_S:.0f}s ({reason})",
    })


def _feed_points(st: dict, mint: str) -> list[float]:
    pts = []
    for row in st.get("ai_feed") or []:
        if isinstance(row, dict) and str(row.get("mint") or "") == mint:
            px = game._f(row.get("price"))
            if px and px > 0:
                pts.append(float(px))
    return pts[-game.RECEIPT_POINTS:]


def record_feed(st: dict, rows: list[dict], cards: dict[str, dict], now: float) -> None:
    feed = st.get("ai_feed")
    if not isinstance(feed, list):
        feed = []
        st["ai_feed"] = feed
    last_at: dict[str, float] = {}
    for row in feed:
        if not isinstance(row, dict):
            continue
        mint = str(row.get("mint") or "")
        try:
            last_at[mint] = float(row.get("epoch") or 0)
        except (TypeError, ValueError):
            continue
    for row in rows:
        mint = str(row.get("mint") or "")
        price = _row_price(row)
        if price is None:
            continue
        if now - last_at.get(mint, 0) < FEED_GAP_S:
            continue
        card = cards.get(mint) or {}
        m5 = _row_num(row, "price_change_m5")
        strength, _reason = momentum_signal(row)
        signal = strength is not None or ledger_allows(card, LEDGER_MIN_PASS)
        feed.append({
            "epoch": float(now),
            "t": _iso(now),
            "mint": mint,
            "symbol": _symbol(row),
            "price": price,
            "m5": m5 if m5 is not None else (strength if strength is not None else None),
            "h1": _row_num(row, "price_change_h1"),
            "verdict": card.get("verdict") or "unknown",
            "pass_count": card.get("pass_count") if isinstance(card.get("pass_count"), int) else None,
            "check_count": card.get("check_count") or LEDGER_CHECKS,
            "signal": bool(signal),
        })
        last_at[mint] = float(now)
    if len(feed) > FEED_CAP:
        del feed[:-FEED_CAP]


def _mark_owner(st: dict) -> None:
    eq = game.equity(st)
    try:
        peak = float(st.get("peak_equity") if st.get("peak_equity") is not None else eq)
    except (TypeError, ValueError):
        peak = eq
    if peak < eq:
        peak = eq
    dd = ((peak - eq) / peak * 100.0) if peak else 0.0
    st["max_drawdown_pct"] = round(max(float(st.get("max_drawdown_pct") or 0), dd), 4)


def _append_race(st: dict, now: float) -> None:
    race = st.get("ai_race")
    if not isinstance(race, list):
        race = []
        st["ai_race"] = race
    if race:
        try:
            prev = float(race[-1].get("epoch") or 0)
        except (TypeError, ValueError, AttributeError):
            prev = 0
        if now - prev < 0.4:
            return
    start = float(st.get("start_bankroll") or game.START_BANKROLL)
    eq = game.equity(st)
    you = round((eq / start - 1.0) * 100, 2) if start else 0.0
    point = {"t": _iso(now), "epoch": float(now), "you": you}
    for name in AI_NAMES:
        refresh_book(st["ai_books"][name])
        point[name] = round(float(st["ai_books"][name].get("pnl_pct") or 0), 2)
    race.append(point)
    if len(race) > RACE_CAP:
        del race[:-RACE_CAP]


def step(st: dict, now: float | None = None, session_over: bool = False) -> dict:
    """One AI pass. Caller holds the game lock. Does not rewrite the owner tick."""
    ensure(st)
    if st.get("status") != "active":
        return st
    clock = time.time() if now is None else float(now)
    rows = filter_rows()
    cards: dict[str, dict] = {}
    for row in rows:
        mint = str(row.get("mint") or "")
        cards[mint] = checklist_adapter.checklist_for_mint(mint)
    record_feed(st, rows, cards, clock)
    universe = {str(row.get("mint") or "") for row in rows}
    for name in AI_NAMES:
        book = st["ai_books"][name]
        settings = st["ai_settings"][name]
        _mark_positions(st, name, book, clock, session_over)
        if session_over:
            continue
        _maybe_fill(st, name, book, settings, universe, clock)
        _maybe_arm(st, name, book, settings, rows, cards, clock)
    _mark_owner(st)
    _append_race(st, clock)
    return st


def _nudge(name: str, settings: dict, field: str, delta: float, reason: str) -> dict | None:
    before = settings[field]
    after = _clamp_field(name, field, float(before) + delta)
    flipped = False
    if after == before:
        after = _clamp_field(name, field, float(before) - delta)
        flipped = True
    if after == before:
        return None
    if flipped:
        if field == "stop_pct":
            reason = "stop is at a bound — nudge stop 1pp the other way"
        elif field == "tp_pct":
            reason = "take-profit is at a bound — nudge it 2pp the other way"
        elif field == "hold_s":
            reason = "hold time is at a bound — nudge it 60s the other way"
        elif field == "min_verdict":
            reason = "min verdict is at a bound — nudge it by 1 the other way"
    return {"field": field, "before": before, "after": after, "reason": reason}


def propose_nudge(name: str, trade: dict, settings: dict) -> dict | None:
    """One small deterministic nudge from the closed trade."""
    exit_reason = str(trade.get("exit_reason") or trade.get("close_reason") or "")
    try:
        pnl = float(trade.get("pnl_usd") or 0)
    except (TypeError, ValueError):
        pnl = 0.0
    verdict = str(trade.get("verdict") or trade.get("checklist_verdict") or "unknown")
    if exit_reason == "stop":
        return _nudge(name, settings, "stop_pct", STOP_STEP, "stopped out — widen stop 1pp")
    if exit_reason == "tp":
        return _nudge(name, settings, "tp_pct", TP_STEP, "target hit — raise take-profit 2pp")
    if exit_reason in ("hold", "force_24h"):
        if pnl < 0:
            return _nudge(name, settings, "hold_s", -HOLD_STEP, "time exit was red — shorten hold 60s")
        return _nudge(name, settings, "hold_s", HOLD_STEP, "time exit was green — lengthen hold 60s")
    if name == "VECTOR" and verdict == "SKIP" and pnl < 0:
        return _nudge(name, settings, "min_verdict", 1, "SKIP lost — raise min verdict by 1")
    if pnl < 0:
        return _nudge(name, settings, "stop_pct", STOP_STEP, "loss — widen stop 1pp")
    return _nudge(name, settings, "tp_pct", TP_STEP, "win — raise take-profit 2pp")


def _sample_from_feed(row: dict) -> dict | None:
    if not isinstance(row, dict):
        return None
    price = game._f(row.get("price"))
    if price is None or price <= 0:
        return None
    epoch = game._f(row.get("epoch"))
    if epoch is None and isinstance(row.get("t"), str):
        parsed = game._parse_utc(row.get("t"))
        epoch = parsed.timestamp() if parsed else None
    if epoch is None:
        return None
    passes = row.get("pass_count")
    m5 = game._f(row.get("m5"))
    return {
        "epoch": float(epoch),
        "mint": str(row.get("mint") or ""),
        "price": float(price),
        "m5": m5,
        "verdict": row.get("verdict") or "unknown",
        "pass_count": passes if isinstance(passes, int) else None,
        "check_count": int(row.get("check_count") or LEDGER_CHECKS),
        "symbol": row.get("symbol") or "?",
        "signal": bool(row.get("signal")),
    }


def _samples_from_receipt(rec: dict) -> list[dict]:
    if not isinstance(rec, dict):
        return []
    prices: list[float] = []
    entry = game._f(rec.get("entry_price_usd"))
    if entry and entry > 0:
        prices.append(float(entry))
    for px in game._clean_points(rec.get("points")):
        if not prices or abs(px - prices[-1]) > 0:
            prices.append(float(px))
    for sell in rec.get("sells") or []:
        if isinstance(sell, dict):
            px = game._f(sell.get("exit_price_usd"))
            if px and px > 0:
                prices.append(float(px))
    if len(prices) < 2:
        return []
    opened = game._parse_utc(rec.get("opened_at")) if isinstance(rec.get("opened_at"), str) else None
    epoch0 = opened.timestamp() if opened else 0.0
    verdict = rec.get("checklist_verdict") or rec.get("verdict") or "unknown"
    passes = rec.get("pass_count") if isinstance(rec.get("pass_count"), int) else None
    momentum = game._f(rec.get("momentum"))
    out = []
    for i, px in enumerate(prices):
        out.append({
            "epoch": epoch0 + i * 30.0,
            "mint": str(rec.get("mint") or ""),
            "price": px,
            "m5": momentum if i == 0 else None,
            "verdict": verdict,
            "pass_count": passes,
            "check_count": int(rec.get("check_count") or LEDGER_CHECKS),
            "symbol": rec.get("symbol") or "?",
            "signal": i == 0,
        })
    return out


def build_tape(st: dict) -> list[dict]:
    """Feed history first. Receipts fill mints the feed never saw."""
    samples = []
    seen_mints = set()
    for row in st.get("ai_feed") or []:
        sample = _sample_from_feed(row)
        if not sample or not sample["mint"]:
            continue
        samples.append(sample)
        seen_mints.add(sample["mint"])
    for rec in st.get("receipts") or []:
        if not isinstance(rec, dict):
            continue
        mint = str(rec.get("mint") or "")
        if not mint or mint in seen_mints:
            continue
        extra = _samples_from_receipt(rec)
        if extra:
            samples.extend(extra)
            seen_mints.add(mint)
    samples.sort(key=lambda s: (s["epoch"], s["mint"]))
    return samples


def split_tape(samples: list[dict]) -> tuple[list[dict], list[dict]]:
    """First 2/3 train, last 1/3 validate. Both sides empty when there is no tape."""
    n = len(samples)
    if n < 3:
        return list(samples), []
    cut = (n * 2) // 3
    if cut < 1:
        cut = 1
    if cut >= n:
        cut = n - 1
    return list(samples[:cut]), list(samples[cut:])


def _sim_signal(name: str, settings: dict, sample: dict) -> bool:
    if not sample.get("signal"):
        return False
    card = {
        "verdict": sample.get("verdict"),
        "pass_count": sample.get("pass_count"),
        "check_count": sample.get("check_count") or LEDGER_CHECKS,
    }
    if name == "LEDGER":
        return ledger_allows(card, settings.get("min_verdict"))
    if name == "VECTOR":
        m5 = sample.get("m5")
        if m5 is None or float(m5) < MOMENTUM_M5:
            return False
        return _vector_allows(card, settings.get("min_verdict"))
    return False


def simulate(name: str, settings: dict, samples: list[dict]) -> dict[str, float | int]:
    """Replay one settings dict. Stake matches the live $1 paper clip."""
    cash = float(game.START_BANKROLL)
    peak = cash
    max_dd = 0.0
    open_pos: dict[str, dict] = {}

    def note() -> float:
        nonlocal peak, max_dd
        eq = cash
        for pos in open_pos.values():
            eq += float(pos["qty"]) * float(pos["last"])
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        return eq

    for sample in samples:
        mint = sample["mint"]
        price = float(sample["price"])
        epoch = float(sample["epoch"])
        if mint in open_pos:
            pos = open_pos[mint]
            pos["last"] = price
            chg = (price / float(pos["entry"]) - 1.0) * 100.0
            held = epoch - float(pos["epoch"])
            stop = abs(float(settings["stop_pct"]))
            tp = abs(float(settings["tp_pct"]))
            hold = float(settings["hold_s"])
            if chg <= -stop or chg >= tp or held >= hold:
                cash += float(pos["qty"]) * price
                del open_pos[mint]
            note()
            continue
        if mint in open_pos or cash + 1e-9 < STAKE_USD:
            note()
            continue
        if not _sim_signal(name, settings, sample):
            note()
            continue
        cash -= STAKE_USD
        open_pos[mint] = {
            "entry": price,
            "last": price,
            "qty": STAKE_USD / price,
            "epoch": epoch,
        }
        note()
    final = note()
    start = float(game.START_BANKROLL)
    pnl_pct = ((final / start) - 1.0) * 100.0 if start else 0.0
    return {
        "n": len(samples),
        "pnl_pct": round(pnl_pct, 4),
        "dd_pct": round(max_dd, 4),
        "equity": round(final, 6),
    }


def apply_learning(st: dict, name: str, trade: dict) -> dict | None:
    """Backtest one nudge. Keep it only when validation wins with dd <= 20%."""
    ensure(st)
    settings = dict(st["ai_settings"][name])
    proposal = propose_nudge(name, trade, settings)
    if proposal is None:
        return None
    field = proposal["field"]
    before = proposal["before"]
    after = proposal["after"]
    proposed = dict(settings)
    proposed[field] = after
    tape = build_tape(st)
    train, validate = split_tape(tape)
    train_cur = simulate(name, settings, train)
    train_pro = simulate(name, proposed, train)
    val_cur = simulate(name, settings, validate)
    val_pro = simulate(name, proposed, validate)
    enough = len(train) > 0 and len(validate) > 0
    beats = False
    note = ""
    if not enough:
        note = "not enough history to validate"
    elif float(val_pro["dd_pct"]) > DRAWDOWN_CAP_PCT:
        note = f"validate drawdown {float(val_pro['dd_pct']):.1f}% over {DRAWDOWN_CAP_PCT:.0f}%"
    elif float(val_pro["pnl_pct"]) > float(val_cur["pnl_pct"]):
        beats = True
        note = "validate beat the current setting"
    else:
        note = "validate did not beat the current setting"
    if beats:
        st["ai_settings"][name][field] = after
    row = {
        "at": trade.get("closed_at") or game._utc(),
        "ai": name,
        "field": field,
        "before": before,
        "after": after,
        "applied": bool(beats),
        "reason": proposal["reason"],
        "lesson": trade.get("lesson") or "",
        "backtest": {
            "split": "2/3 train, 1/3 validate",
            "train": {
                "n": train_cur["n"],
                "current_pnl_pct": train_cur["pnl_pct"],
                "proposed_pnl_pct": train_pro["pnl_pct"],
                "current_dd_pct": train_cur["dd_pct"],
                "proposed_dd_pct": train_pro["dd_pct"],
            },
            "validate": {
                "n": val_cur["n"],
                "current_pnl_pct": val_cur["pnl_pct"],
                "proposed_pnl_pct": val_pro["pnl_pct"],
                "current_dd_pct": val_cur["dd_pct"],
                "proposed_dd_pct": val_pro["dd_pct"],
            },
            "kept": bool(beats),
            "drawdown_cap_pct": DRAWDOWN_CAP_PCT,
            "note": note,
        },
    }
    log = st.get("ai_settings_log")
    if not isinstance(log, list):
        log = []
        st["ai_settings_log"] = log
    log.insert(0, row)
    if len(log) > SETTINGS_LOG_CAP:
        del log[SETTINGS_LOG_CAP:]
    return row


def scoreboard(st: dict, owner_pnl_pct: float, owner_pnl_usd: float) -> dict:
    ensure(st)
    try:
        you_dd = float(st.get("max_drawdown_pct") or 0)
    except (TypeError, ValueError):
        you_dd = 0.0
    rows = [{
        "id": "you",
        "name": "You",
        "pnl_pct": round(float(owner_pnl_pct or 0), 2),
        "pnl_usd": round(float(owner_pnl_usd or 0), 6),
        "max_drawdown_pct": round(you_dd, 2),
    }]
    for name in AI_NAMES:
        book = st["ai_books"][name]
        refresh_book(book)
        rows.append({
            "id": name,
            "name": name,
            "pnl_pct": round(float(book.get("pnl_pct") or 0), 2),
            "pnl_usd": round(float(book.get("pnl_usd") or 0), 6),
            "max_drawdown_pct": round(float(book.get("max_drawdown_pct") or 0), 2),
        })
    rows.sort(key=lambda r: (-float(r["pnl_pct"]), -float(r["pnl_usd"]), r["name"]))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    race = []
    for point in st.get("ai_race") or []:
        if not isinstance(point, dict):
            continue
        race.append({
            "t": point.get("t"),
            "you": point.get("you"),
            "VECTOR": point.get("VECTOR"),
            "LEDGER": point.get("LEDGER"),
        })
    return {"rank_by": "pnl_pct", "rows": rows, "race": race}


def _view_trade(symbol, entry_reason, exit_reason, verdict, lesson, pnl, passes) -> dict:
    return {
        "symbol": symbol or "?",
        "entry_reason": entry_reason or "",
        "exit_reason": exit_reason or "",
        "verdict": verdict or "unknown",
        "lesson": lesson or "",
        "pnl_usd": pnl,
        "pass_count": passes if isinstance(passes, int) else None,
    }


def _you_trades(st: dict) -> list[dict]:
    by_pos = {}
    for rec in game._session_receipts(st):
        by_pos[str(rec.get("position_id") or "")] = rec
    rows = []
    for pos in st.get("closed") or []:
        if not isinstance(pos, dict):
            continue
        if pos.get("trader") in AI_NAMES:
            continue
        rec = by_pos.get(str(pos.get("id") or ""))
        verdict = "unknown"
        passes = None
        entry_reason = "You bought."
        lesson = ""
        if isinstance(rec, dict):
            verdict = rec.get("checklist_verdict") or rec.get("verdict") or "unknown"
            passes = rec.get("pass_count") if isinstance(rec.get("pass_count"), int) else None
            entry_reason = rec.get("entry_reason") or "You bought."
            lesson = rec.get("lesson") or ""
        exit_reason = str(pos.get("close_reason") or (rec or {}).get("exit_reason") or "")
        if not lesson:
            lesson = lesson_text(
                str(pos.get("symbol") or "?"),
                exit_reason,
                str(verdict),
                passes,
                LEDGER_CHECKS,
                pos.get("entry_price_usd"),
                pos.get("pnl_usd"),
                pos.get("pnl_pct"),
            )
        rows.append(_view_trade(
            pos.get("symbol"),
            entry_reason,
            exit_reason,
            str(verdict),
            lesson,
            pos.get("pnl_usd"),
            passes,
        ))
    return rows


def _ai_trades(st: dict, name: str) -> list[dict]:
    book = (st.get("ai_books") or {}).get(name) or {}
    rows = []
    for pos in book.get("closed") or []:
        if not isinstance(pos, dict):
            continue
        verdict = pos.get("verdict") or pos.get("checklist_verdict") or "unknown"
        rows.append(_view_trade(
            pos.get("symbol"),
            pos.get("entry_reason"),
            pos.get("exit_reason") or pos.get("close_reason"),
            str(verdict),
            pos.get("lesson"),
            pos.get("pnl_usd"),
            pos.get("pass_count") if isinstance(pos.get("pass_count"), int) else None,
        ))
    for pos in book.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        rows.append(_view_trade(
            pos.get("symbol"),
            pos.get("entry_reason"),
            "open",
            str(pos.get("checklist_verdict") or "unknown"),
            "",
            None,
            pos.get("pass_count") if isinstance(pos.get("pass_count"), int) else None,
        ))
    return rows


def study(st: dict) -> dict:
    ensure(st)
    return {
        "tabs": ["you", "VECTOR", "LEDGER"],
        "you": {"label": "You", "trades": _you_trades(st)},
        "VECTOR": {"label": "VECTOR", "trades": _ai_trades(st, "VECTOR")},
        "LEDGER": {"label": "LEDGER", "trades": _ai_trades(st, "LEDGER")},
    }
