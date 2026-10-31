#!/usr/bin/env python3
"""SHIP W4 — last swap on the main pool, into the shared tick cache.

PumpPortal ``subscribeTokenTrade`` is not the source. Since 2026-05-01 that
method answers with an error unless the socket was opened with an API key
whose wallet holds at least 0.02 SOL, and each 10k events bills that wallet.
That is a paid key. This process does not open it.

The displayed price is the last real swap on the coin's main pool:

- Orca Whirlpool: ``accountSubscribe`` on the pool. Sqrt price at byte 65.
- PumpSwap (``pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA``): ``logsSubscribe``
  mentions the pool and parses Buy/Sell ``Program data:`` (base64 Anchor
  event). The event carries the base and quote amounts and the pool reserves
  after the swap. Vault ``accountSubscribe`` is the second source. The mid is
  quote/base with mint decimals, times the Jupiter wSOL price. Pool layout is
  the pump_amm IDL ``Pool`` account (8-byte disc, then bump, index, creator,
  base_mint, quote_mint, lp_mint, pool_base_token_account,
  pool_quote_token_account). ``virtual_quote_reserves`` (i128) is added to the
  quote vault. It is 0 on non-boost pools.
- pump.fun bonding curve: ``logsSubscribe`` mentions the curve and parses
  TradeEvent virtual reserves. ``accountSubscribe`` on the curve stays.

No ``getTransaction``. ``api.mainnet-beta.solana.com`` accepts subscribe and
then often pushes nothing. ``wss://solana-rpc.publicnode.com`` does push.
When ``HELIUS_API_KEY`` is set, the same subscribe runs on Helius.

Jupiter is the fallback when no trade and no decoded mid have landed within
5 seconds (``source=jupiter_fallback``). For pump coins the 3% outlier check
is against the decoded pool mid, not Jupiter. A PumpSwap mid more than 2%
from Jupiter wins. The socket runs on a daemon thread.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

# Same window as game.QUOTE_FRESH_SLA_S. Silent means no accepted swap or
# decoded mid for more than this many seconds, which is when the tick falls
# back to Jupiter.
STREAM_SILENCE_S = 5.0
STREAM_OUTLIER = 0.03  # reject when abs(swap - anchor) / anchor is more than this
MID_OVER_JUPITER = 0.02  # PumpSwap mid wins when it disagrees with Jupiter by more than this
MAX_SUBS = 48
WATCH_TTL_S = 60.0
_BACKOFF_CAP_S = 20.0

SOURCE_WHY = (
    "PumpPortal subscribeTokenTrade requires an API key funded with at least "
    "0.02 SOL (metered). PumpSwap and pump.fun curves use logsSubscribe "
    "(mentions the pool) and parse Buy/Sell Program data. Vault "
    "accountSubscribe is the second source. Orca keeps accountSubscribe. "
    "Helius is used only when HELIUS_API_KEY is set. No getTransaction."
)

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
_STABLES = {USDC_MINT, USDT_MINT}

WHIRLPOOL_OWNER = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
PUMP_OWNER = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_OWNER = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

# pump_amm IDL Pool discriminator and field offsets, including the 8-byte disc.
# pool_bump u8, index u16, creator, base_mint, quote_mint, lp_mint,
# pool_base_token_account, pool_quote_token_account, lp_supply u64,
# coin_creator, is_mayhem_mode bool, is_cashback_coin bool,
# virtual_quote_reserves i128.
PUMPSWAP_DISC = bytes([241, 154, 109, 4, 17, 177, 109, 188])
PS_BASE_MINT = 43
PS_QUOTE_MINT = 75
PS_BASE_VAULT = 139
PS_QUOTE_VAULT = 171
PS_VIRTUAL_QUOTE = 245

# BuyEvent / SellEvent discriminators from the same IDL. Reserves sit at the
# same payload offsets on both (after the 8-byte event disc).
BUY_EVENT_DISC = bytes([103, 244, 82, 31, 44, 245, 119, 119])
SELL_EVENT_DISC = bytes([62, 47, 55, 10, 165, 3, 220, 42])
EV_BASE_RES = 40
EV_QUOTE_RES = 48
EV_POOL = 112

# pump.json TradeEvent. virtual reserves follow mint, amounts, the is_buy
# bool, user, and timestamp.
TRADE_EVENT_DISC = bytes([189, 219, 127, 211, 78, 230, 97, 238])
TR_MINT = 0
TR_VIRT_SOL = 89
TR_VIRT_TOKEN = 97

# SPL token account amount.
TOKEN_AMOUNT = 64

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_PUBLIC_WSS = "wss://solana-rpc.publicnode.com"
_PUBLIC_HTTP = "https://solana-rpc.publicnode.com"

_guard = threading.Lock()
_views: dict[str, float] = {}
_seen: dict[str, float] = {}
_last_px: dict[str, float] = {}
_anchor: dict[str, tuple[float, float]] = {}
_jupiter: dict[str, tuple[float, float]] = {}
_mids: dict[str, tuple[float, float]] = {}
_decimals: dict[str, int] = {SOL_MINT: 9, USDC_MINT: 6, USDT_MINT: 6}
_rejects: list[dict] = []
_pools: dict[str, dict] = {}
_thread: threading.Thread | None = None
_vault_thread: threading.Thread | None = None
_stop = threading.Event()
_no_pool: dict[str, float] = {}
_ws: dict = {
    "state": "down",
    "error": "",
    "errors": [],
    "connected_at": None,
    "subscriptions": {},
    "last_event": {},
    "vault_poll_at": None,
    "vault_poll_ok": None,
    "transport": "down",
    "why": "not started",
}
POOL_SOURCES = {"stream", "pool_swap", "pool_vault"}


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + _B58.index(ch)
    pad = 0
    for ch in text:
        if ch != "1":
            break
        pad += 1
    if n == 0:
        body = b""
    else:
        body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * pad + body


def b58encode(raw: bytes) -> str:
    pad = 0
    for byte in raw:
        if byte != 0:
            break
        pad += 1
    n = int.from_bytes(raw, "big") if raw else 0
    chars: list[str] = []
    while n:
        n, rem = divmod(n, 58)
        chars.append(_B58[rem])
    if not chars:
        chars = ["1"]
    return ("1" * pad) + "".join(reversed(chars))


def next_backoff(prev: float) -> float:
    """Reconnect delay. Starts at 1s and doubles, capped at 20s."""
    try:
        cur = float(prev)
    except (TypeError, ValueError):
        cur = 0.0
    if cur < 1.0:
        return 1.0
    return min(_BACKOFF_CAP_S, cur * 2.0)


def endpoints() -> tuple[str, str, str]:
    """Return (wss, http, name). Helius only when HELIUS_API_KEY is set."""
    key = (os.environ.get("HELIUS_API_KEY") or "").strip()
    if key:
        return (
            f"wss://mainnet.helius-rpc.com/?api-key={key}",
            f"https://mainnet.helius-rpc.com/?api-key={key}",
            "helius",
        )
    wss = (os.environ.get("SOLANA_RPC_WSS") or "").strip() or _PUBLIC_WSS
    http = (os.environ.get("SOLANA_RPC_HTTP") or "").strip() or _PUBLIC_HTTP
    return wss, http, "public"


def select_watched(positions: list[str], views: list[str], *, limit: int = 16) -> list[str]:
    """Positions first, then open views, capped. Order is kept, duplicates drop."""
    out: list[str] = []
    for mint in list(positions) + list(views):
        if not mint or mint in out:
            continue
        out.append(mint)
        if len(out) >= limit:
            break
    return out


def note_view(mint: str) -> None:
    """Mark a Game or drawer view. Never blocks and never opens a socket."""
    mint = (mint or "").strip()
    if len(mint) < 32:
        return
    with _guard:
        _views[mint] = time.monotonic()


def note_anchor(mint: str, price: float, now: float | None = None, decimals: int | None = None, force: bool = False) -> None:
    """Remember an outlier anchor.

    Jupiter quotes for a pump mint are stored aside. They must not become the
    anchor, because Jupiter itself is wrong on those pools. ``force`` writes
    the decoded mid.
    """
    if not mint or price is None or price <= 0:
        return
    stamped = time.time() if now is None else now
    with _guard:
        if decimals is not None and 0 <= int(decimals) <= 18:
            _decimals[mint] = int(decimals)
        kind = (_pools.get(mint) or {}).get("kind")
        if kind in ("pumpswap", "pump") and not force:
            _jupiter[mint] = (stamped, float(price))
            return
        _anchor[mint] = (stamped, float(price))


def note_mid(mint: str, price: float, now: float | None = None) -> None:
    """Remember the decoded pool mid and use it as the pump outlier anchor."""
    if not mint or price is None or price <= 0:
        return
    stamped = time.time() if now is None else now
    with _guard:
        _mids[mint] = (stamped, float(price))
    note_anchor(mint, price, stamped, force=True)


def mid_price(mint: str, max_age: float = STREAM_SILENCE_S) -> float | None:
    with _guard:
        row = _mids.get(mint)
    if not row:
        return None
    if time.time() - row[0] > max_age:
        return None
    return row[1]


def pool_kind(mint: str) -> str:
    with _guard:
        return str((_pools.get(mint) or {}).get("kind") or "")


def mark_pool_kind(mint: str, kind: str) -> None:
    """Test hook. Production pools come from the chain account owner."""
    with _guard:
        row = dict(_pools.get(mint) or {})
        row["kind"] = kind
        row["mint"] = mint
        _pools[mint] = row


def anchor_price(mint: str, max_age: float = 30.0) -> float | None:
    with _guard:
        row = _anchor.get(mint)
    if not row:
        return None
    if time.time() - row[0] > max_age:
        return None
    return row[1]


def jupiter_price(mint: str, max_age: float = 30.0) -> float | None:
    with _guard:
        row = _jupiter.get(mint)
    if not row:
        return None
    if time.time() - row[0] > max_age:
        return None
    return row[1]


def is_pool_source(src: str | None) -> bool:
    return str(src or "") in POOL_SOURCES


def public_src(src: str | None) -> str:
    raw = str(src or "")
    if raw in ("stream", "pool_swap"):
        return "pool_swap"
    if raw == "pool_vault":
        return "pool_vault"
    if raw == "jupiter_fallback":
        return "jupiter_fallback"
    if raw == "qa_tick":
        return "qa_tick"
    return "jupiter"


def stream_is_fresh(mint: str, now: float | None = None) -> bool:
    """True when an accepted swap or mid landed within STREAM_SILENCE_S."""
    stamped = time.time() if now is None else now
    with _guard:
        seen = _seen.get(mint)
    if seen is None:
        return False
    return (stamped - seen) <= STREAM_SILENCE_S


def recent_rejects() -> list[dict]:
    with _guard:
        return list(_rejects)


def reset_for_tests() -> None:
    with _guard:
        _views.clear()
        _seen.clear()
        _last_px.clear()
        _anchor.clear()
        _jupiter.clear()
        _mids.clear()
        _decimals.clear()
        _decimals.update({SOL_MINT: 9, USDC_MINT: 6, USDT_MINT: 6})
        _rejects.clear()
        _pools.clear()
        _no_pool.clear()
        _ws.update({
            "state": "down",
            "error": "",
            "errors": [],
            "connected_at": None,
            "subscriptions": {},
            "last_event": {},
            "vault_poll_at": None,
            "vault_poll_ok": None,
            "transport": "down",
            "why": "not started",
        })


def _remember_reject(row: dict) -> None:
    print(
        "[trade-stream] reject {kind} mint={mint} swap={swap} anchor={anchor} gap={gap}".format(
            kind=row.get("kind"),
            mint=row.get("mint"),
            swap=row.get("swap"),
            anchor=row.get("anchor"),
            gap=row.get("gap"),
        ),
        flush=True,
    )
    with _guard:
        _rejects.append(row)
        if len(_rejects) > 50:
            del _rejects[:-50]


def ingest_swap(
    mint: str,
    price_usd: float,
    *,
    anchor: float | None = None,
    trade_epoch: float | None = None,
    signature: str = "",
    slot: int | None = None,
    seed_ok: bool = False,
    source: str = "pool_swap",
) -> str:
    """Publish one real swap or pool mid into the shared tick cache.

    Returns ok, same, outlier, no-anchor, or bad. Outliers are not published.
    ``seed_ok`` accepts the first pump print when no decoded mid exists yet,
    so a wrong Jupiter quote cannot block it.
    """
    import game

    mint = (mint or "").strip()
    try:
        px = float(price_usd)
    except (TypeError, ValueError):
        return "bad"
    if not mint or px <= 0:
        return "bad"
    anchor_px = anchor
    if anchor_px is None:
        anchor_px = anchor_price(mint)
    if anchor_px is None or anchor_px <= 0:
        if not seed_ok:
            _remember_reject({"kind": "no-anchor", "mint": mint, "swap": px, "anchor": None, "gap": None})
            return "no-anchor"
        note_anchor(mint, px, force=True)
        anchor_px = px
    gap = abs(px - float(anchor_px)) / float(anchor_px)
    if gap > STREAM_OUTLIER:
        _remember_reject({
            "kind": "outlier",
            "mint": mint,
            "swap": px,
            "anchor": float(anchor_px),
            "gap": round(gap, 6),
        })
        return "outlier"
    now = time.time()
    epoch = now if trade_epoch is None else float(trade_epoch)
    with _guard:
        prev = _last_px.get(mint)
    same = prev is not None and prev > 0 and abs(px - prev) / prev < 1e-12
    payload = {
        "mint": mint,
        "price_usd": px,
        "price_ts": game._utc(),
        "trade_ts": _iso_ms(epoch),
        "trade_epoch": epoch,
        "fetched_at": _iso_ms(now),
        "source": public_src(source),
        "src": public_src(source),
        "signature": signature or "",
        "slot": slot,
    }
    with game._live_lock(mint):
        game._live_cache[mint] = (now, payload)
        game._clear_live_backoff(mint)
    with _guard:
        _seen[mint] = now
        _last_px[mint] = px
        _ws["last_event"][mint] = _iso_ms(now)
    return "same" if same else "ok"


def _iso_ms(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def whirlpool_swap_usd(raw: bytes, watched: str, sol_usd: float, decimals: dict[str, int] | None = None) -> float | None:
    """Post-swap USD from an Orca Whirlpool account. None when it is not that pool."""
    if not raw or len(raw) < 213 or not sol_usd or sol_usd <= 0:
        return None
    decs = decimals or {}
    try:
        sqrt_price = int.from_bytes(raw[65:81], "little")
        mint_a = b58encode(raw[101:133])
        mint_b = b58encode(raw[181:213])
    except Exception:
        return None
    if sqrt_price <= 0:
        return None
    dec_a = decs.get(mint_a)
    dec_b = decs.get(mint_b)
    if mint_a == SOL_MINT:
        dec_a = 9
    if mint_b == SOL_MINT:
        dec_b = 9
    if mint_a in _STABLES:
        dec_a = 6
    if mint_b in _STABLES:
        dec_b = 6
    if dec_a is None or dec_b is None:
        return None
    b_per_a = (sqrt_price / 2**64) ** 2 * (10 ** (dec_a - dec_b))
    if b_per_a <= 0:
        return None
    return _sided_usd(mint_a, mint_b, b_per_a, watched, sol_usd)


def pump_curve_swap_usd(raw: bytes, sol_usd: float) -> float | None:
    """USD from a pump.fun bonding-curve account (virtual reserves)."""
    if not raw or len(raw) < 24 or not sol_usd or sol_usd <= 0:
        return None
    virtual_token = struct.unpack_from("<Q", raw, 8)[0]
    virtual_sol = struct.unpack_from("<Q", raw, 16)[0]
    if virtual_token <= 0 or virtual_sol <= 0:
        return None
    sol_ui = virtual_sol / 1e9
    token_ui = virtual_token / 1e6
    if token_ui <= 0:
        return None
    return (sol_ui / token_ui) * sol_usd


def pumpswap_pool_view(raw: bytes) -> dict | None:
    """Vaults and mints from a PumpSwap Pool account. None if the disc mismatches.

    Offsets are the pump_amm IDL ``Pool`` layout (disc + bump + index + creator
    + three mints + two vaults). ``virtual_quote`` is the i128 at byte 245.
    """
    if not raw or len(raw) < PS_QUOTE_VAULT + 32 or raw[:8] != PUMPSWAP_DISC:
        return None
    try:
        view = {
            "base_mint": b58encode(raw[PS_BASE_MINT:PS_BASE_MINT + 32]),
            "quote_mint": b58encode(raw[PS_QUOTE_MINT:PS_QUOTE_MINT + 32]),
            "base_vault": b58encode(raw[PS_BASE_VAULT:PS_BASE_VAULT + 32]),
            "quote_vault": b58encode(raw[PS_QUOTE_VAULT:PS_QUOTE_VAULT + 32]),
            "virtual_quote": 0,
        }
    except Exception:
        return None
    if len(raw) >= PS_VIRTUAL_QUOTE + 16:
        view["virtual_quote"] = int.from_bytes(raw[PS_VIRTUAL_QUOTE:PS_VIRTUAL_QUOTE + 16], "little", signed=True)
    return view


def pumpswap_mid_usd(
    base_raw: int,
    base_dec: int,
    quote_raw: int,
    quote_dec: int,
    base_mint: str,
    quote_mint: str,
    watched: str,
    sol_usd: float,
    virtual_quote: int = 0,
) -> float | None:
    """USD mid = (quote vault + virtual quote) / base vault, scaled by mint decimals.

    Quote SOL is converted with the Jupiter wSOL price. Quote USDC/USDT is $1.
    Raw amounts without decimals, or the inverted base/quote side, are not a price.
    """
    try:
        base_raw_i = int(base_raw)
        quote_raw_i = int(quote_raw) + int(virtual_quote or 0)
        base_dec_i = int(base_dec)
        quote_dec_i = int(quote_dec)
    except (TypeError, ValueError):
        return None
    if base_raw_i <= 0 or quote_raw_i <= 0:
        return None
    if base_dec_i < 0 or quote_dec_i < 0 or base_dec_i > 18 or quote_dec_i > 18:
        return None
    base_ui = base_raw_i / (10 ** base_dec_i)
    quote_ui = quote_raw_i / (10 ** quote_dec_i)
    if base_ui <= 0 or quote_ui <= 0:
        return None
    return _sided_usd(base_mint, quote_mint, quote_ui / base_ui, watched, sol_usd)


def pumpswap_event_reserves(raw: bytes) -> tuple[int, int, str | None] | None:
    """Post-swap base and quote reserves from a BuyEvent or SellEvent. None otherwise."""
    if not raw or len(raw) < 8 + EV_QUOTE_RES + 8:
        return None
    if raw[:8] not in (BUY_EVENT_DISC, SELL_EVENT_DISC):
        return None
    body = raw[8:]
    base = struct.unpack_from("<Q", body, EV_BASE_RES)[0]
    quote = struct.unpack_from("<Q", body, EV_QUOTE_RES)[0]
    pool = None
    if len(body) >= EV_POOL + 32:
        try:
            pool = b58encode(body[EV_POOL:EV_POOL + 32])
        except Exception:
            pool = None
    if base <= 0 or quote <= 0:
        return None
    return base, quote, pool


def pump_trade_reserves(raw: bytes) -> tuple[str, int, int] | None:
    """(mint, virtual_sol, virtual_token) from a pump.fun TradeEvent."""
    if not raw or len(raw) < 8 + TR_VIRT_TOKEN + 8 or raw[:8] != TRADE_EVENT_DISC:
        return None
    body = raw[8:]
    try:
        mint = b58encode(body[TR_MINT:TR_MINT + 32])
    except Exception:
        return None
    virtual_sol = struct.unpack_from("<Q", body, TR_VIRT_SOL)[0]
    virtual_token = struct.unpack_from("<Q", body, TR_VIRT_TOKEN)[0]
    if virtual_sol <= 0 or virtual_token <= 0:
        return None
    return mint, virtual_sol, virtual_token


def price_from_program_logs(logs: list, pool: dict, sol_usd: float) -> float | None:
    """Per-trade USD from Program data lines. Ignores every other log line."""
    if not isinstance(logs, list) or not pool or not sol_usd or sol_usd <= 0:
        return None
    kind = pool.get("kind")
    watched = str(pool.get("mint") or "")
    found = None
    for line in logs:
        if not isinstance(line, str) or not line.startswith("Program data:"):
            continue
        b64 = line.split("Program data:", 1)[1].strip()
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        if kind == "pumpswap":
            ev = pumpswap_event_reserves(raw)
            if not ev:
                continue
            base, quote, pool_pk = ev
            if pool_pk and pool.get("address") and pool_pk != pool.get("address"):
                continue
            px = pumpswap_mid_usd(
                base,
                int(pool.get("base_dec") if pool.get("base_dec") is not None else 6),
                quote,
                int(pool.get("quote_dec") if pool.get("quote_dec") is not None else 9),
                str(pool.get("base_mint") or ""),
                str(pool.get("quote_mint") or ""),
                watched,
                sol_usd,
                int(pool.get("virtual_quote") or 0),
            )
            if px and px > 0:
                found = px
        elif kind == "pump":
            ev = pump_trade_reserves(raw)
            if not ev:
                continue
            mint, virtual_sol, virtual_token = ev
            if mint != watched:
                continue
            packed = b"\x00" * 8 + struct.pack("<QQ", virtual_token, virtual_sol)
            px = pump_curve_swap_usd(packed, sol_usd)
            if px and px > 0:
                found = px
    return found


def _sided_usd(mint_a: str, mint_b: str, b_per_a: float, watched: str, sol_usd: float) -> float | None:
    """b_per_a is how many B you get for one A. Return the watched mint's USD."""
    def quote_usd(mint: str) -> float | None:
        if mint == SOL_MINT:
            return sol_usd
        if mint in _STABLES:
            return 1.0
        return None

    qa = quote_usd(mint_a)
    qb = quote_usd(mint_b)
    if qa is not None and qb is None and mint_b == watched:
        return qa / b_per_a
    if qb is not None and qa is None and mint_a == watched:
        return b_per_a * qb
    return None


def _position_mints() -> list[str]:
    import game

    try:
        st = game.load_state()
    except Exception:
        return []
    if st.get("status") != "active":
        return []
    out: list[str] = []
    for pos in st.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        mint = str(pos.get("mint") or "")
        if game.looks_like_mint(mint):
            out.append(mint)
    return out


def view_mints(now: float | None = None) -> list[str]:
    """Open views only. Does not load the game book (that lock may already be held)."""
    stamped = time.monotonic() if now is None else now
    with _guard:
        return [mint for mint, seen in _views.items() if stamped - seen <= WATCH_TTL_S]


def watched_mints(now: float | None = None) -> list[str]:
    stamped = time.monotonic() if now is None else now
    with _guard:
        views = [mint for mint, seen in _views.items() if stamped - seen <= WATCH_TTL_S]
    return select_watched(_position_mints(), views)


def ensure_started() -> None:
    """Start the daemon if it is not already running. No-op under tests."""
    global _thread
    if (os.environ.get("NUNU_TRADE_STREAM") or "1").strip() == "0":
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    argv = " ".join(sys.argv)
    if "unittest" in argv or "pytest" in argv:
        return
    with _guard:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run, name="trade-stream", daemon=True)
        _thread.start()


def _record_ws(state: str, error: str = "") -> None:
    """Remember why the websocket is or isn't attached. Also printed."""
    line = f"[trade-stream] ws {state}" + (f" ({error})" if error else "")
    print(line, flush=True)
    with _guard:
        _ws["state"] = state
        _ws["error"] = error or ""
        if state == "open":
            _ws["connected_at"] = _iso_ms(time.time())
            _ws["transport"] = "ws+http_vault"
        elif state == "down":
            if _ws.get("transport") != "http_vault":
                _ws["transport"] = "http_vault" if _ws.get("vault_poll_ok") else "down"
        if error:
            errs = list(_ws.get("errors") or [])
            errs.append(error)
            _ws["errors"] = errs[-8:]
        _ws["why"] = _why_text()


def _why_text() -> str:
    state = _ws.get("state") or "down"
    err = _ws.get("error") or ""
    nsub = len(_ws.get("subscriptions") or {})
    if state == "open":
        return f"websocket open, {nsub} subscriptions, vault poll every 1s"
    if _ws.get("vault_poll_ok"):
        extra = f" Last ws error: {err}." if err else ""
        return "websocket down; HTTP getMultipleAccounts vault poll every 1s." + extra
    if err:
        return f"websocket down ({err}); vault poll has not succeeded"
    return "stream not started"


def stream_health() -> dict:
    """Snapshot for /api/health. Watched mints, socket errors, subs, last event."""
    with _guard:
        snap = {
            "attached": _ws.get("state") == "open" or bool(_ws.get("vault_poll_ok")),
            "transport": _ws.get("transport") or "down",
            "ws_state": _ws.get("state") or "down",
            "ws_error": _ws.get("error") or "",
            "ws_errors": list(_ws.get("errors") or []),
            "ws_connected_at": _ws.get("connected_at"),
            "subscriptions": dict(_ws.get("subscriptions") or {}),
            "last_event": dict(_ws.get("last_event") or {}),
            "vault_poll_at": _ws.get("vault_poll_at"),
            "watched": [],
            "watch_ttl_s": WATCH_TTL_S,
            "why": _ws.get("why") or "not started",
        }
    snap["watched"] = watched_mints()
    return snap


def _remember_subs(subs: dict) -> None:
    with _guard:
        _ws["subscriptions"] = {str(k): int(v) for k, v in subs.items() if v is not None}


def _under_test() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if os.environ.get("NUNU_COLD_VAULT") == "1":
        return False
    argv = " ".join(__import__("sys").argv)
    return "unittest" in argv or "pytest" in argv


def cold_vault_price(mint: str) -> bool:
    """Resolve the pool and read it now. Registers the mint for the 1s poll.

    Returns True when a pool price was published. Under pytest this is a no-op
    unless NUNU_COLD_VAULT=1, so the suite does not hit RPC. No pool means the
    caller uses Jupiter.
    """
    if _under_test() and os.environ.get("NUNU_COLD_VAULT") != "1":
        return False
    mint = (mint or "").strip()
    if len(mint) < 32:
        return False
    note_view(mint)
    ensure_started()
    if stream_is_fresh(mint):
        return True
    now = time.time()
    with _guard:
        missed = _no_pool.get(mint)
    if missed and now - missed < 30.0:
        return False
    http = endpoints()[1]
    _refresh_anchors([mint])
    pool = _pool_for(mint, http)
    if not pool:
        with _guard:
            _no_pool[mint] = time.time()
        return False
    kind = pool.get("kind")
    if kind == "pumpswap":
        return _read_vault_mid(http, pool) is not None
    info = _account_info(http, str(pool.get("address") or ""))
    raw = _account_bytes(info) if info else None
    sol = anchor_price(SOL_MINT, max_age=60.0)
    if not raw or sol is None or sol <= 0:
        return False
    px = None
    if kind == "whirlpool":
        with _guard:
            decs = dict(_decimals)
        px = whirlpool_swap_usd(raw, mint, sol, decs)
    elif kind == "pump":
        px = pump_curve_swap_usd(raw, sol)
    if px is None or px <= 0:
        return False
    _publish(mint, px, compare_mid=False, source="pool_swap")
    return True


def _http_vault_loop() -> None:
    """Poll pool vaults over HTTP every 1s. Runs even when the websocket is down."""
    last_log = 0.0
    while not _stop.is_set():
        mints = watched_mints()
        if not mints:
            with _guard:
                _ws["why"] = "idle: no watched mints"
            time.sleep(0.5)
            continue
        http = endpoints()[1]
        try:
            _refresh_anchors(mints)
            _refresh_pump_mids(http)
            _refresh_account_mids(http, mints)
            with _guard:
                _ws["vault_poll_at"] = _iso_ms(time.time())
                _ws["vault_poll_ok"] = True
                if _ws.get("state") != "open":
                    _ws["transport"] = "http_vault"
                _ws["why"] = _why_text()
            now = time.time()
            if now - last_log >= 30.0:
                last_log = now
                with _guard:
                    state = _ws.get("state")
                    err = _ws.get("error") or ""
                print(
                    f"[trade-stream] vault poll n={len(mints)} ws={state} err={err or '-'}",
                    flush=True,
                )
        except Exception as exc:
            print(f"[trade-stream] vault poll failed ({exc})", flush=True)
            with _guard:
                _ws["vault_poll_ok"] = False
                errs = list(_ws.get("errors") or [])
                errs.append(f"vault poll {exc}")
                _ws["errors"] = errs[-8:]
                _ws["why"] = _why_text()
        time.sleep(1.0)


def _refresh_account_mids(http: str, mints: list[str]) -> None:
    """HTTP read of whirlpool and bonding-curve accounts when the socket is quiet."""
    sol = anchor_price(SOL_MINT, max_age=60.0)
    if sol is None or sol <= 0:
        return
    for mint in mints:
        with _guard:
            pool = _pools.get(mint)
            snap = dict(pool) if pool else None
            seen = _seen.get(mint)
        if not snap or snap.get("kind") not in ("whirlpool", "pump"):
            continue
        if seen and time.time() - seen < 1.0:
            continue
        info = _account_info(http, str(snap.get("address") or ""))
        raw = _account_bytes(info) if info else None
        if not raw:
            continue
        if snap.get("kind") == "whirlpool":
            with _guard:
                decs = dict(_decimals)
            px = whirlpool_swap_usd(raw, mint, sol, decs)
        else:
            px = pump_curve_swap_usd(raw, sol)
        if px and px > 0:
            _publish(mint, px, compare_mid=False, source="pool_swap")


def _run() -> None:
    global _vault_thread
    with _guard:
        start_vault = _vault_thread is None or not _vault_thread.is_alive()
        if start_vault:
            _vault_thread = threading.Thread(target=_http_vault_loop, name="trade-vault", daemon=True)
            _vault_thread.start()
    backoff = 0.0
    while not _stop.is_set():
        try:
            if not watched_mints():
                time.sleep(0.5)
                continue
            _record_ws("connecting", "")
            _serve_socket()
            backoff = 0.0
            _record_ws("down", "socket closed")
        except Exception as exc:
            backoff = next_backoff(backoff)
            _record_ws("down", f"{exc.__class__.__name__}: {exc}")
            time.sleep(backoff)


def _serve_socket() -> None:
    import websocket

    wss, http, name = endpoints()
    ws = websocket.WebSocket()
    ws.settimeout(5)
    ws.connect(wss, timeout=8)
    _record_ws("open", "")
    print(f"[trade-stream] connected via {name}", flush=True)
    subs: dict[str, int] = {}
    meta: dict[int, dict] = {}
    pending: dict[int, dict] = {}
    rpc_id = 1
    try:
        while not _stop.is_set():
            _refresh_anchors(watched_mints())
            _refresh_pump_mids(http)
            subs, meta, pending, rpc_id = _sync_subs(ws, subs, meta, pending, rpc_id, http)
            _remember_subs(subs)
            try:
                raw = ws.recv()
            except Exception as exc:
                if exc.__class__.__name__ == "WebSocketTimeoutException":
                    continue
                raise
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("id") in pending and "result" in msg and not msg.get("method"):
                op = pending.pop(msg["id"])
                key = str(op.get("key") or "")
                try:
                    sub_id = int(msg["result"])
                except (TypeError, ValueError):
                    subs.pop(key, None)
                    continue
                subs[key] = sub_id
                meta[sub_id] = op
                continue
            if msg.get("method") == "accountNotification":
                _on_account(msg, meta)
            elif msg.get("method") == "logsNotification":
                _on_logs(msg, meta)
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _ops_for(pool: dict) -> dict[str, dict]:
    addr = str(pool.get("address") or "")
    kind = pool.get("kind")
    ops: dict[str, dict] = {}
    if not addr:
        return ops
    acct = {"encoding": "base64", "commitment": "processed"}
    if kind == "whirlpool":
        ops[f"acct:{addr}"] = {"method": "accountSubscribe", "params": [addr, acct], "role": "pool"}
    elif kind == "pump":
        ops[f"acct:{addr}"] = {"method": "accountSubscribe", "params": [addr, acct], "role": "curve"}
        ops[f"logs:{addr}"] = {
            "method": "logsSubscribe",
            "params": [{"mentions": [addr]}, {"commitment": "processed"}],
            "role": "logs",
        }
    elif kind == "pumpswap":
        ops[f"logs:{addr}"] = {
            "method": "logsSubscribe",
            "params": [{"mentions": [addr]}, {"commitment": "processed"}],
            "role": "logs",
        }
        base_vault = str(pool.get("base_vault") or "")
        quote_vault = str(pool.get("quote_vault") or "")
        if base_vault:
            ops[f"vault:{base_vault}"] = {
                "method": "accountSubscribe",
                "params": [base_vault, acct],
                "role": "vault",
                "vault": base_vault,
            }
        if quote_vault:
            ops[f"vault:{quote_vault}"] = {
                "method": "accountSubscribe",
                "params": [quote_vault, acct],
                "role": "vault",
                "vault": quote_vault,
            }
    for op in ops.values():
        op["mint"] = pool.get("mint")
        op["pool"] = addr
    return ops


def _sync_subs(ws, subs, meta, pending, rpc_id, http):
    want: dict[str, dict] = {}
    for mint in watched_mints():
        pool = _pool_for(mint, http)
        if pool:
            want.update(_ops_for(pool))
        if len(want) >= MAX_SUBS:
            break
    for key in list(subs):
        if key in want:
            continue
        sub_id = subs.pop(key, None)
        if sub_id is None:
            continue
        meta.pop(sub_id, None)
        rpc_id += 1
        method = "logsUnsubscribe" if key.startswith("logs:") else "accountUnsubscribe"
        _send(ws, {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": [sub_id]})
    pending_keys = {str(op.get("key") or "") for op in pending.values()}
    for key, op in want.items():
        if key in subs or key in pending_keys:
            continue
        if len(subs) + len(pending) >= MAX_SUBS:
            break
        rpc_id += 1
        pending[rpc_id] = dict(op, key=key)
        _send(ws, {"jsonrpc": "2.0", "id": rpc_id, "method": op["method"], "params": op["params"]})
    for key, op in want.items():
        sub_id = subs.get(key)
        if sub_id is not None:
            meta[sub_id] = dict(op, key=key)
    return subs, meta, pending, rpc_id


def _send(ws, payload: dict) -> None:
    ws.send(json.dumps(payload))


def _sub_op(msg: dict, meta: dict[int, dict]) -> dict | None:
    params = msg.get("params") or {}
    try:
        sub_id = int(params.get("subscription"))
    except (TypeError, ValueError):
        return None
    op = meta.get(sub_id)
    return op if isinstance(op, dict) else None


def _on_account(msg: dict, meta: dict[int, dict]) -> None:
    op = _sub_op(msg, meta)
    if not op or not op.get("mint"):
        return
    params = msg.get("params") or {}
    result = params.get("result") or {}
    value = result.get("value") or {}
    data = value.get("data")
    if not isinstance(data, list) or not data:
        return
    try:
        raw = base64.b64decode(data[0])
    except Exception:
        return
    slot = _slot_of(result)
    mint = str(op.get("mint"))
    with _guard:
        pool = dict(_pools.get(mint) or {})
        decs = dict(_decimals)
    if not pool:
        return
    sol = anchor_price(SOL_MINT, max_age=60.0)
    role = op.get("role")
    if role == "vault":
        if len(raw) < TOKEN_AMOUNT + 8 or sol is None:
            return
        amount = struct.unpack_from("<Q", raw, TOKEN_AMOUNT)[0]
        _apply_vault_amount(pool, str(op.get("vault") or ""), amount, sol, slot)
        return
    if sol is None or sol <= 0:
        return
    owner = str(value.get("owner") or pool.get("owner") or "")
    px = None
    compare_mid = False
    if owner == WHIRLPOOL_OWNER or pool.get("kind") == "whirlpool":
        px = whirlpool_swap_usd(raw, mint, sol, decs)
    elif owner == PUMP_OWNER or pool.get("kind") == "pump":
        px = pump_curve_swap_usd(raw, sol)
        compare_mid = True
    if px is None or px <= 0:
        return
    _publish(mint, px, compare_mid=compare_mid, slot=slot)


def _on_logs(msg: dict, meta: dict[int, dict]) -> None:
    op = _sub_op(msg, meta)
    if not op or not op.get("mint"):
        return
    params = msg.get("params") or {}
    result = params.get("result") or {}
    value = result.get("value") or {}
    if value.get("err"):
        return
    logs = value.get("logs") or []
    mint = str(op.get("mint"))
    with _guard:
        pool = dict(_pools.get(mint) or {})
    if not pool:
        return
    sol = anchor_price(SOL_MINT, max_age=60.0)
    if sol is None or sol <= 0:
        return
    px = price_from_program_logs(logs, pool, sol)
    if px is None or px <= 0:
        return
    sig = str(value.get("signature") or "")
    _publish(mint, px, compare_mid=True, slot=_slot_of(result), signature=sig)


def _slot_of(result: dict) -> int | None:
    ctx = result.get("context") or {}
    try:
        return int(ctx.get("slot")) if ctx.get("slot") is not None else None
    except (TypeError, ValueError):
        return None


def _publish(
    mint: str,
    px: float,
    *,
    compare_mid: bool,
    slot: int | None = None,
    signature: str = "",
    source: str = "pool_swap",
) -> str:
    """Vault mids set the anchor. Swap events are checked against it."""
    if compare_mid:
        had = mid_price(mint, max_age=120.0)
        status = ingest_swap(
            mint, px, anchor=had, seed_ok=True, slot=slot, signature=signature, source=source,
        )
        if status in ("ok", "same") and had is None:
            note_mid(mint, px)
        return status
    note_mid(mint, px)
    return ingest_swap(mint, px, anchor=px, slot=slot, signature=signature, source=source)


def _apply_vault_amount(pool: dict, vault: str, amount: int, sol: float, slot: int | None) -> None:
    mint = str(pool.get("mint") or "")
    if not mint or not vault:
        return
    with _guard:
        live = _pools.get(mint)
        if not live:
            return
        if vault == live.get("base_vault"):
            live["base_raw"] = int(amount)
        elif vault == live.get("quote_vault"):
            live["quote_raw"] = int(amount)
        else:
            return
        base_raw = live.get("base_raw")
        quote_raw = live.get("quote_raw")
        snap = dict(live)
    if base_raw is None or quote_raw is None:
        return
    px = pumpswap_mid_usd(
        int(base_raw),
        int(snap.get("base_dec") if snap.get("base_dec") is not None else 6),
        int(quote_raw),
        int(snap.get("quote_dec") if snap.get("quote_dec") is not None else 9),
        str(snap.get("base_mint") or ""),
        str(snap.get("quote_mint") or ""),
        mint,
        sol,
        int(snap.get("virtual_quote") or 0),
    )
    if px and px > 0:
        _publish(mint, px, compare_mid=False, slot=slot, source="pool_vault")


def _pool_for(mint: str, http: str) -> dict | None:
    with _guard:
        cached = _pools.get(mint)
    if cached and cached.get("address"):
        return cached
    pool = _resolve_pool(mint, http)
    if not pool:
        return None
    with _guard:
        _pools[mint] = pool
    return pool


def _resolve_pool(mint: str, http: str) -> dict | None:
    """Highest-liquidity Dex pair whose account we can decode. None if not."""
    try:
        req = Request(
            f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}",
            headers={"User-Agent": "nunu-filter-paper/1.0", "Accept": "application/json"},
        )
        with urlopen(req, timeout=4) as resp:
            pairs = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(pairs, list):
        return None
    ranked = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        addr = str(pair.get("pairAddress") or "")
        if len(addr) < 32:
            continue
        liq = 0.0
        try:
            liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        ranked.append((liq, addr))
    ranked.sort(key=lambda row: -row[0])
    for _liq, addr in ranked[:4]:
        info = _account_info(http, addr)
        if not info:
            continue
        owner = str(info.get("owner") or "")
        raw = _account_bytes(info)
        if owner == WHIRLPOOL_OWNER:
            return {"mint": mint, "address": addr, "owner": owner, "kind": "whirlpool"}
        if owner == PUMP_OWNER:
            return {"mint": mint, "address": addr, "owner": owner, "kind": "pump"}
        if owner == PUMPSWAP_OWNER and raw:
            view = pumpswap_pool_view(raw)
            if not view:
                continue
            if mint not in (view["base_mint"], view["quote_mint"]):
                continue
            pool = {
                "mint": mint,
                "address": addr,
                "owner": owner,
                "kind": "pumpswap",
                **view,
            }
            _fill_decimals(pool)
            return pool
    return None


def _fill_decimals(pool: dict) -> None:
    def one(mint: str) -> int:
        if mint == SOL_MINT:
            return 9
        if mint in _STABLES:
            return 6
        with _guard:
            known = _decimals.get(mint)
        return known if known is not None else 6

    pool["base_dec"] = one(str(pool.get("base_mint") or ""))
    pool["quote_dec"] = one(str(pool.get("quote_mint") or ""))


def _account_bytes(info: dict) -> bytes | None:
    data = info.get("data")
    if isinstance(data, list) and data:
        try:
            return base64.b64decode(data[0])
        except Exception:
            return None
    return None


def _account_info(http: str, addr: str) -> dict | None:
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [addr, {"encoding": "base64"}],
    }).encode("utf-8")
    try:
        req = Request(http, data=body, headers={"Content-Type": "application/json", "User-Agent": "nunu-filter-paper/1.0"})
        with urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    value = ((payload.get("result") or {}).get("value")) if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else None


_anchor_at = 0.0


def _refresh_pump_mids(http: str) -> None:
    """Re-read PumpSwap vaults so a quiet coin still shows the post-swap mid."""
    now = time.time()
    mints = []
    for mint in watched_mints():
        with _guard:
            pool = _pools.get(mint)
            if not pool or pool.get("kind") != "pumpswap":
                continue
            seen = _mids.get(mint)
            if seen and now - seen[0] < 1.0:
                continue
        mints.append(mint)
    for mint in mints:
        with _guard:
            pool = _pools.get(mint)
            snap = dict(pool) if pool else None
        if not snap:
            continue
        _read_vault_mid(http, snap)


def _read_vault_mid(http: str, pool: dict) -> float | None:
    base_vault = str(pool.get("base_vault") or "")
    quote_vault = str(pool.get("quote_vault") or "")
    if not base_vault or not quote_vault:
        return None
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMultipleAccounts",
        "params": [[base_vault, quote_vault], {"encoding": "jsonParsed", "commitment": "processed"}],
    }).encode("utf-8")
    try:
        req = Request(http, data=body, headers={"Content-Type": "application/json", "User-Agent": "nunu-filter-paper/1.0"})
        with urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    vals = ((payload.get("result") or {}).get("value")) if isinstance(payload, dict) else None
    if not isinstance(vals, list) or len(vals) < 2:
        return None
    parsed = [_parsed_token_amount(row) for row in vals[:2]]
    if parsed[0] is None or parsed[1] is None:
        return None
    base_raw, base_dec = parsed[0]
    quote_raw, quote_dec = parsed[1]
    mint = str(pool.get("mint") or "")
    with _guard:
        live = _pools.get(mint)
        if live:
            live["base_raw"] = base_raw
            live["quote_raw"] = quote_raw
            live["base_dec"] = base_dec
            live["quote_dec"] = quote_dec
            _decimals[str(live.get("base_mint") or "")] = base_dec
            _decimals[str(live.get("quote_mint") or "")] = quote_dec
            snap = dict(live)
        else:
            snap = dict(pool)
            snap["base_dec"] = base_dec
            snap["quote_dec"] = quote_dec
    sol = anchor_price(SOL_MINT, max_age=60.0)
    if sol is None or sol <= 0:
        return None
    px = pumpswap_mid_usd(
        base_raw,
        base_dec,
        quote_raw,
        quote_dec,
        str(snap.get("base_mint") or ""),
        str(snap.get("quote_mint") or ""),
        mint,
        sol,
        int(snap.get("virtual_quote") or 0),
    )
    if px and px > 0:
        _publish(mint, px, compare_mid=False, source="pool_vault")
    return px


def _parsed_token_amount(row: dict | None) -> tuple[int, int] | None:
    if not isinstance(row, dict):
        return None
    data = row.get("data")
    if isinstance(data, dict):
        info = (data.get("parsed") or {}).get("info") or {}
        token = info.get("tokenAmount") or {}
        try:
            return int(token.get("amount")), int(token.get("decimals"))
        except (TypeError, ValueError):
            return None
    if isinstance(data, list) and data:
        try:
            raw = base64.b64decode(data[0])
        except Exception:
            return None
        if len(raw) < TOKEN_AMOUNT + 8:
            return None
        return struct.unpack_from("<Q", raw, TOKEN_AMOUNT)[0], 0
    return None


def _refresh_anchors(mints: list[str]) -> None:
    """One Jupiter anchor fetch per second. A hot account stream must not 429 ticks."""
    global _anchor_at
    import game

    now = time.time()
    if now - _anchor_at < 1.0:
        return
    _anchor_at = now
    ids = []
    for mint in list(mints) + [SOL_MINT]:
        if mint and mint not in ids:
            ids.append(mint)
    if not ids:
        return
    url = game.JUP_PRICE_URL.format(mint=",".join(ids))
    code, raw = game._http_json(url)
    if code != 200 or not isinstance(raw, dict):
        return
    now = time.time()
    for mint, row in raw.items():
        if not isinstance(row, dict):
            continue
        try:
            px = float(row.get("usdPrice"))
        except (TypeError, ValueError):
            continue
        dec = row.get("decimals")
        try:
            dec_i = int(dec) if dec is not None else None
        except (TypeError, ValueError):
            dec_i = None
        if px > 0:
            note_anchor(str(mint), px, now, dec_i)


def prefer_mid_over_jupiter(mint: str, jupiter_px: float | None) -> float | None:
    """Decoded pump mid when it is fresh and more than 2% from Jupiter."""
    if pool_kind(mint) not in ("pumpswap", "pump"):
        return None
    mid = mid_price(mint, max_age=STREAM_SILENCE_S)
    if mid is None or mid <= 0:
        return None
    try:
        jup = float(jupiter_px) if jupiter_px is not None else 0.0
    except (TypeError, ValueError):
        jup = 0.0
    if jup <= 0:
        return mid
    if abs(mid - jup) / jup > MID_OVER_JUPITER:
        return mid
    return None
