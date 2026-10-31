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

Jupiter is not on the steady-state path. A watched pool is priced from its
vaults and from logsSubscribe. Jupiter is only a labeled fallback after the
pool has been unreachable for more than 10 seconds, and that tick is stale
so it cannot fill. Any quote mint that is not SOL or USDC/USDT is the pool
ratio times that quote token's own SOL or USDC pool. That pool is resolved,
cached, and subscribed, including when it is not the quote token's busiest
pair. BABA (发发) and an SPCX-quoted coin such as SIS both take this path.

``logsSubscribe`` uses commitment ``confirmed``. On the public mainnet
socket, ``processed`` returns a subscription id and then pushes nothing.
``api.mainnet-beta`` pushes every mentions subscription on one socket, and
answers 429 once a process opens about 10 of those sockets. Priority coins
(open positions, then the coin being viewed) share one logs socket, capped
at 8 subscriptions. A 429 backs off with jitter. Any pool without a live
logs subscription is priced from the 1s vault poll, so it does not go stale.
Vault ``accountSubscribe`` stays on the shared publicnode socket. HTTP
(Jupiter anchor, pool resolve, vault poll) stays on the vault thread.

The mark from a Buy or Sell event is the executed fill: quote the user
paid or received, divided by the base amount filled. The raw vault ratio
(quote balance / base balance) runs about 7% under that fill on current
PumpSwap SOL pools because the AMM quotes against
``quote_vault + virtual_quote_reserves``, not the raw token balance.
``virtual_quote_reserves`` is the signed i128 on the pool account. LP fee
is 20 bps and protocol fee is 5 bps, so the fee schedule is not that gap.
For 5 seconds after a fill the mark stays on that executed price. Between
swaps the mark is the vault ratio plus virtual quote, which is the same
correction. A move through 15% is held until a second independent read
confirms it. That read publishes immediately: within 2%, or within 12%
when one read is the fill and the other is the vault (the ~7% virtual-quote
gap). The hold is at most 3.0 seconds from hold start. A hard timer
publishes the latest on-chain read then, even if no confirm has arrived,
so a real crash cannot be held longer. A one-off bounce is rejected when
a later read disagrees. There is no real baseline when none has been
accepted, or baseline_age is huge. The guard does not publish an override
in that case. It publishes only once a swap price and a vault price agree
within 5%. Until then the tick is stale. Two agreeing on-chain reads
replace a stuck price. Spike rows log hold_start and publish. A baseline
older than 5 seconds does not veto agreeing reads, and an echo of that
old baseline does not refresh it.
Vault polls for every watched pool share one getMultipleAccounts call.
RPC URLs come from SOLANA_WS_URL and SOLANA_RPC_URL. The socket runs on a
daemon thread.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Same window as game.QUOTE_FRESH_SLA_S. Silent means no accepted swap or
# decoded mid for more than this many seconds, which is when the tick falls
# back to Jupiter.
STREAM_SILENCE_S = 5.0
STREAM_OUTLIER = 0.03  # kept for callers that still name the old band
SPIKE_MOVE = 0.15  # one print beyond this waits for a second read
SPIKE_AGREE = 0.02  # two consecutive reads within this override the baseline
SPIKE_CROSS = 0.12  # vault ratio vs the executed fill on the same crash (~7% + noise)
SPIKE_PAIR = 0.05  # swap fill and vault must agree within this when there is no real baseline
SPIKE_HOLD_S = 3.0  # a crash candidate may sit at most this long, from hold start
BASELINE_VETO_S = 5.0  # an older baseline cannot veto agreeing fresh reads
BASELINE_HUGE_S = 1e8  # _baseline_age returns 1e9 when no tick has been accepted
MID_OVER_JUPITER = 0.02  # PumpSwap mid wins when it disagrees with Jupiter by more than this
MAX_SUBS = 48
WATCH_TTL_S = 300.0  # a viewed coin keeps its subscriptions for 5 minutes
SWAP_MARK_S = 5.0  # a fresh executed fill is the mark; vault ratio waits
LOGS_COMMITMENT = "confirmed"  # processed acks on publicnode and never notifies
SUB_ACK_S = 2.0  # a subscribe with no ack is dropped so the next sync retries
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
# Raydium CLMM. A quote token's SOL or USDC pool is often this, not PumpSwap.
RAYDIUM_CLMM_OWNER = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
CLMM_DISC = bytes([247, 237, 227, 245, 215, 195, 222, 70])
# Meteora DLMM. SPCX/SOL 3h9Gh… is this program.
METEORA_DLMM_OWNER = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
DLMM_DISC = bytes([33, 11, 49, 98, 181, 101, 177, 13])
DLMM_ACTIVE_ID = 76
DLMM_BIN_STEP = 80
DLMM_MINT_X = 88
DLMM_MINT_Y = 120
# api.mainnet-beta answers 429 once a process opens about 10 logs sockets.
# One socket carries the priority logsSubscribe set. The rest use the 1s vault poll.
MAX_LOG_SOCKETS = 1
MAX_LOG_SUBS = 8
CLMM_MINT_0 = 73
CLMM_MINT_1 = 105
CLMM_VAULT_0 = 137
CLMM_VAULT_1 = 169
CLMM_DEC_0 = 233
CLMM_DEC_1 = 234
CLMM_SQRT = 253

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
# Body offsets (after the 8-byte disc). Buy and Sell share them:
# base amount filled at 8, pre-trade vault reserves at 40/48, user quote at 104.
EV_BASE_AMT = 8
EV_BASE_RES = 40
EV_QUOTE_RES = 48
EV_USER_QUOTE = 104
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
# publicnode answers several logsSubscribe calls and then pushes notifications
# for only one of them. api.mainnet-beta pushes every mentions subscription.
_LOGS_WSS = "wss://api.mainnet-beta.solana.com"

_guard = threading.Lock()
_views: dict[str, float] = {}
_seen: dict[str, float] = {}
_last_px: dict[str, float] = {}
_anchor: dict[str, tuple[float, float]] = {}
_jupiter: dict[str, tuple[float, float]] = {}
_mids: dict[str, tuple[float, float]] = {}
_decimals: dict[str, int] = {SOL_MINT: 9, USDC_MINT: 6, USDT_MINT: 6}
_rejects: list[dict] = []
_spike_rejects: list[dict] = []
_pending_spikes: dict[str, dict] = {}
_pairs: dict[str, dict[str, dict]] = {}
_hold_timers: dict[str, threading.Timer] = {}
_hold_gen: dict[str, int] = {}
_awaiting_pair: set[str] = set()
_swap_at: dict[str, float] = {}
_native: dict[str, float] = {}
_pools: dict[str, dict] = {}
_pair_seq = 0
_thread: threading.Thread | None = None
_vault_thread: threading.Thread | None = None
_logs_thread: threading.Thread | None = None
_log_threads: dict[str, threading.Thread] = {}
_log_stop: dict[str, threading.Event] = {}
_stop = threading.Event()
_no_pool: dict[str, float] = {}
_pool_miss_at: dict[str, float] = {}
_quote_px: dict[str, tuple[float, float]] = {}
_quote_pools: dict[str, dict] = {}
_swap_counts: dict[str, int] = {}
_rate_limits = 0
POOL_UNREACHABLE_S = 10.0
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


def _helius_urls(key: str) -> tuple[str, str]:
    return (
        f"wss://mainnet.helius-rpc.com/?api-key={key}",
        f"https://mainnet.helius-rpc.com/?api-key={key}",
    )


def logs_endpoint() -> str:
    """Websocket for the one multiplexed logs socket.

    The default stays on mainnet-beta: publicnode acks several logsSubscribe
    calls and then notifies only one of them. SOLANA_WS_URL, SOLANA_LOGS_WSS,
    or HELIUS_API_KEY points that socket at a paid RPC. The free host 429s
    around 10 connections, so this process opens one.
    """
    explicit = (os.environ.get("SOLANA_LOGS_WSS") or "").strip()
    if explicit:
        return explicit
    custom = (os.environ.get("SOLANA_WS_URL") or "").strip()
    if custom:
        return custom
    key = (os.environ.get("HELIUS_API_KEY") or "").strip()
    if key:
        return _helius_urls(key)[0]
    return _LOGS_WSS


def endpoints() -> tuple[str, str, str]:
    """Return (wss, http, name).

    SOLANA_WS_URL and SOLANA_RPC_URL override the defaults. The defaults are
    publicnode for account subscribe and HTTP. Helius is used when
    HELIUS_API_KEY is set and those two variables are not.
    """
    http = (os.environ.get("SOLANA_RPC_URL") or os.environ.get("SOLANA_RPC_HTTP") or "").strip()
    wss = (os.environ.get("SOLANA_WS_URL") or os.environ.get("SOLANA_RPC_WSS") or "").strip()
    key = (os.environ.get("HELIUS_API_KEY") or "").strip()
    if key and not http and not wss:
        helius_wss, helius_http = _helius_urls(key)
        return helius_wss, helius_http, "helius"
    if not wss and http.startswith("https://"):
        wss = "wss://" + http[len("https://"):]
    elif not wss and http.startswith("http://"):
        wss = "ws://" + http[len("http://"):]
    if not http and wss.startswith("wss://"):
        http = "https://" + wss[len("wss://"):]
    elif not http and wss.startswith("ws://"):
        http = "http://" + wss[len("ws://"):]
    name = "env" if (http or wss) else "public"
    return wss or _PUBLIC_WSS, http or _PUBLIC_HTTP, name


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


def mark_pool_miss(mint: str, now: float | None = None) -> None:
    """Remember the first moment a known pool had no on-chain price."""
    if not mint:
        return
    stamped = time.time() if now is None else now
    with _guard:
        _pool_miss_at.setdefault(mint, stamped)


def clear_pool_miss(mint: str) -> None:
    with _guard:
        _pool_miss_at.pop(mint, None)


def pool_unreachable(mint: str, now: float | None = None) -> bool:
    """True once a pool has had no on-chain price for POOL_UNREACHABLE_S."""
    stamped = time.time() if now is None else now
    with _guard:
        started = _pool_miss_at.get(mint)
    if started is None:
        return False
    return (stamped - started) > POOL_UNREACHABLE_S


def recent_rejects() -> list[dict]:
    with _guard:
        return list(_rejects)


def recent_spikes() -> list[dict]:
    """Spikes held back from the tick cache. /api/health prints this list."""
    with _guard:
        return list(_spike_rejects)


def reset_for_tests() -> None:
    global _rate_limits
    with _guard:
        for mint in list(_hold_gen):
            _hold_gen[mint] = _hold_gen.get(mint, 0) + 1
        timers = list(_hold_timers.values())
        _hold_timers.clear()
        _views.clear()
        _seen.clear()
        _last_px.clear()
        _anchor.clear()
        _jupiter.clear()
        _mids.clear()
        _decimals.clear()
        _decimals.update({SOL_MINT: 9, USDC_MINT: 6, USDT_MINT: 6})
        _rejects.clear()
        _spike_rejects.clear()
        _pending_spikes.clear()
        _pairs.clear()
        _awaiting_pair.clear()
        _swap_at.clear()
        _native.clear()
        _pools.clear()
        _no_pool.clear()
        _pool_miss_at.clear()
        _quote_px.clear()
        _quote_pools.clear()
        _swap_counts.clear()
        _rate_limits = 0
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
    for timer in timers:
        timer.cancel()


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


def _remember_spike(row: dict) -> None:
    age = row.get("baseline_age")
    extra = ""
    if age is not None:
        extra += f" baseline_age={age}"
    if row.get("agree") is not None:
        extra += f" agree={row.get('agree')}"
    if row.get("hold_start"):
        extra += f" hold_start={row.get('hold_start')}"
    if row.get("publish"):
        extra += f" publish={row.get('publish')}"
    if row.get("hold_s") is not None:
        extra += f" hold_s={row.get('hold_s')}"
    if row.get("reason"):
        extra += f" reason={row.get('reason')}"
    print(
        "[trade-stream] spike {kind} {source} mint={mint} px={px} baseline={baseline} move={move}{extra}".format(
            kind=row.get("kind") or "spike",
            source=row.get("source"),
            mint=row.get("mint"),
            px=row.get("px"),
            baseline=row.get("baseline"),
            move=row.get("move"),
            extra=extra,
        ),
        flush=True,
    )
    with _guard:
        _spike_rejects.append(row)
        if len(_spike_rejects) > 50:
            del _spike_rejects[:-50]


def _same_side_move(baseline: float, a: float, b: float) -> bool:
    """Both prints are more than SPIKE_MOVE from baseline, on the same side."""
    if baseline <= 0 or a <= 0 or b <= 0:
        return False
    da = a - baseline
    db = b - baseline
    if da * db <= 0:
        return False
    return abs(da) / baseline > SPIKE_MOVE and abs(db) / baseline > SPIKE_MOVE


def _move_shrank(baseline: float, held: float, px: float) -> bool:
    """A later print gave the spike back. That one-off is not the crash."""
    if baseline <= 0 or held <= 0 or px <= 0:
        return False
    held_move = abs(held - baseline) / baseline
    new_move = abs(px - baseline) / baseline
    gap = abs(px - held) / held
    return gap > SPIKE_CROSS and new_move < held_move * 0.5


def _remember_pair(mint: str, px: float, source: str, signature: str) -> float | None:
    """Store the latest swap or vault read. Return agreement with the previous same-source print."""
    src = public_src(source)
    if src not in ("pool_swap", "pool_vault"):
        return None
    prev_agree = None
    with _guard:
        bucket = _pairs.setdefault(mint, {})
        prev = bucket.get(src)
        if prev and float(prev.get("px") or 0) > 0:
            prev_agree = abs(px - float(prev["px"])) / float(prev["px"])
        bucket[src] = {"px": px, "sig": signature or "", "at": time.time()}
    return prev_agree


def _pair_agreement(mint: str) -> tuple[float, float] | None:
    """Swap and vault within 5%, from two observations. None otherwise."""
    with _guard:
        bucket = _pairs.get(mint) or {}
        swap = dict(bucket["pool_swap"]) if bucket.get("pool_swap") else None
        vault = dict(bucket["pool_vault"]) if bucket.get("pool_vault") else None
    if not swap or not vault:
        return None
    if swap.get("sig") and swap.get("sig") == vault.get("sig"):
        return None
    try:
        a = float(swap.get("px") or 0)
        b = float(vault.get("px") or 0)
    except (TypeError, ValueError):
        return None
    if a <= 0 or b <= 0:
        return None
    agree = abs(a - b) / min(a, b)
    if agree > SPIKE_PAIR:
        return None
    px = a if float(swap.get("at") or 0) >= float(vault.get("at") or 0) else b
    return px, agree


def _real_baseline(mint: str, now: float | None = None) -> tuple[float | None, float]:
    """Accepted price and its age. (None, 1e9) when no tick has been published."""
    age = _baseline_age(mint, now)
    with _guard:
        px = _last_px.get(mint)
    if not px or float(px) <= 0 or age >= BASELINE_HUGE_S:
        return None, age
    return float(px), age


def awaiting_baseline(mint: str) -> bool:
    """True while a pumpswap price is waiting for swap and vault to agree."""
    with _guard:
        return mint in _awaiting_pair


def _cancel_hold_timer(mint: str) -> None:
    with _guard:
        _hold_gen[mint] = _hold_gen.get(mint, 0) + 1
        timer = _hold_timers.pop(mint, None)
    if timer is not None:
        timer.cancel()


def _arm_hold_timer(mint: str) -> None:
    """Publish the held read at 3.0s from hold start, even with no confirm."""
    with _guard:
        _hold_gen[mint] = _hold_gen.get(mint, 0) + 1
        gen = _hold_gen[mint]
        old = _hold_timers.pop(mint, None)
    if old is not None:
        old.cancel()
    timer = threading.Timer(SPIKE_HOLD_S, _release_hold, args=(mint, gen))
    timer.daemon = True
    with _guard:
        if _hold_gen.get(mint) != gen:
            return
        _hold_timers[mint] = timer
    timer.start()


def _release_hold(mint: str, gen: int) -> None:
    """Hard cap. The latest on-chain read publishes at 3.0s from hold start."""
    with _guard:
        if _hold_gen.get(mint) != gen:
            return
        pend = _pending_spikes.get(mint)
        if not pend or pend.get("rejected"):
            _hold_timers.pop(mint, None)
            return
        px = float(pend.get("px") or 0)
        source = str(pend.get("source") or "pool_swap")
        baseline = float(pend.get("baseline") or 0)
        hold_start = float(pend.get("hold_start") or pend.get("at") or 0)
        sig = str(pend.get("sig") or "")
        sol = pend.get("sol")
        _pending_spikes.pop(mint, None)
        _hold_timers.pop(mint, None)
    if px <= 0 or hold_start <= 0:
        return
    publish_at = time.time()
    move = abs(px - baseline) / baseline if baseline > 0 else 0.0
    _remember_spike({
        "kind": "override",
        "mint": mint,
        "px": px,
        "baseline": baseline,
        "baseline_age": round(_baseline_age(mint, publish_at), 3),
        "move": round(move, 6),
        "source": source,
        "at": _iso_ms(publish_at),
        "hold_start": _iso_ms(hold_start),
        "publish": _iso_ms(publish_at),
        "hold_s": round(publish_at - hold_start, 3),
        "reason": "hold-cap",
    })
    _commit_price(
        mint,
        px,
        source,
        sol if isinstance(sol, (int, float)) else None,
        sig,
        None,
        None,
    )
    note_mid(mint, px)


def _spike_confirmed(
    mint: str,
    px: float,
    source: str,
    baseline: float,
    signature: str = "",
    sol: float | None = None,
) -> tuple[str, float, bool]:
    """Return (confirm|spike|reject, hold_start, arm_timer).

    Two reads within 2% agree, including when both are far from the baseline.
    A vault read and a swap on the same crash also agree within 12%, because
    the raw vault sits about 7% under the fill. The hold clock does not
    restart while the crash is still printing. A later read that gives the
    move back rejects the one-off so the 3s timer cannot publish it. The
    same signature twice is one observation.
    """
    now = time.time()
    arm = False
    with _guard:
        pend = _pending_spikes.get(mint)
        if pend and pend.get("rejected"):
            pend = None
        fresh = {
            "px": px,
            "source": source,
            "baseline": baseline,
            "at": now,
            "hold_start": now,
            "sig": signature or "",
            "rejected": False,
            "sol": sol,
        }
        if not pend:
            _pending_spikes[mint] = fresh
            return "spike", now, True
        sig = signature or ""
        hold_start = float(pend.get("hold_start") or pend.get("at") or now)
        if sig and sig == str(pend.get("sig") or ""):
            return "spike", hold_start, False
        held = float(pend.get("px") or 0)
        # Tests move ``at`` to age a hold without waiting. The timer uses
        # wall-clock hold_start; this age is the backdatable clock.
        age = now - float(pend.get("at") or hold_start)
        base = float(pend.get("baseline") or baseline or 0)
        close = held > 0 and abs(px - held) / held <= SPIKE_AGREE
        cross = (
            held > 0
            and abs(px - held) / held <= SPIKE_CROSS
            and _same_side_move(base, held, px)
            and str(pend.get("source") or "") != str(source or "")
        )
        if close or cross or (age >= SPIKE_HOLD_S and _same_side_move(base, held, px)):
            _pending_spikes.pop(mint, None)
            return "confirm", hold_start, False
        if _move_shrank(base, held, px):
            pend["rejected"] = True
            _pending_spikes[mint] = pend
            return "reject", hold_start, False
        if _same_side_move(base, held, px) and age < SPIKE_HOLD_S:
            # Keep the original clock. Slide to the latest crash print so the
            # next read can agree with it without adding another 3s.
            pend["px"] = px
            pend["source"] = source
            pend["sig"] = sig
            if sol is not None:
                pend["sol"] = sol
            _pending_spikes[mint] = pend
            return "spike", hold_start, False
        if age >= SPIKE_HOLD_S:
            _pending_spikes[mint] = fresh
            arm = True
            hold_start = now
        return "spike", hold_start, arm


def _baseline_age(mint: str, now: float | None = None) -> float:
    stamped = time.time() if now is None else now
    with _guard:
        seen = _seen.get(mint)
    if seen is None:
        return 1e9
    return max(0.0, stamped - seen)


def _recent_swap(mint: str, now: float | None = None) -> bool:
    """True when an executed fill is still the mark."""
    stamped = time.time() if now is None else now
    with _guard:
        at = _swap_at.get(mint)
    return at is not None and (stamped - at) < SWAP_MARK_S


def _driver_for(mint: str, px: float, sol: float | None) -> str:
    """sol_usd when the reserve ratio is unchanged and only the SOL price moved."""
    native = None
    try:
        if sol and float(sol) > 0 and px > 0:
            native = px / float(sol)
    except (TypeError, ValueError):
        native = None
    with _guard:
        prev = _native.get(mint)
        if native is not None:
            _native[mint] = native
    if native is not None and prev is not None and prev > 0 and abs(native - prev) / prev < 1e-9:
        return "sol_usd"
    return "swap"


def _commit_price(
    mint: str,
    px: float,
    source: str,
    sol: float | None,
    signature: str,
    slot: int | None,
    trade_epoch: float | None,
) -> str:
    """Write one accepted price into the shared tick cache."""
    import game

    _cancel_hold_timer(mint)
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
        "driver": _driver_for(mint, px, sol),
        "signature": signature or "",
        "slot": slot,
    }
    with game._live_lock(mint):
        game._live_cache[mint] = (now, payload)
        game._clear_live_backoff(mint)
    clear_pool_miss(mint)
    with _guard:
        _seen[mint] = now
        _last_px[mint] = px
        _awaiting_pair.discard(mint)
        _pending_spikes.pop(mint, None)
        _ws["last_event"][mint] = _iso_ms(now)
    return "same" if same else "ok"


def _spike_row(
    mint: str,
    px: float,
    baseline: float,
    baseline_age: float,
    move: float,
    source: str,
    now_mono: float,
    *,
    kind: str,
    agree: float | None = None,
    hold_start: float | None = None,
    reason: str = "",
) -> dict:
    row = {
        "kind": kind,
        "mint": mint,
        "px": px,
        "baseline": baseline,
        "baseline_age": round(baseline_age, 3),
        "move": round(move, 6),
        "source": source,
        "at": _iso_ms(now_mono),
    }
    if agree is not None:
        row["agree"] = round(agree, 6)
    if hold_start:
        row["hold_start"] = _iso_ms(hold_start)
    if reason:
        row["reason"] = reason
    if kind == "override" and hold_start:
        row["publish"] = _iso_ms(now_mono)
        row["hold_s"] = round(max(0.0, now_mono - hold_start), 3)
    return row


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
    sol: float | None = None,
) -> str:
    """Publish one real swap or pool mid into the shared tick cache.

    Returns ok, same, spike, no-anchor, or bad. One print beyond 15% stays
    out of the cache until a second independent read confirms it. That read
    publishes immediately. The hold is at most 3.0s from hold start: a timer
    publishes the latest on-chain read then. A baseline older than 5s does
    not get refreshed by an echo of itself while that pair is still forming.
    With no real baseline (none accepted, or baseline_age huge) an override
    is refused. The price publishes only once a swap and a vault agree
    within 5%, and until then the tick is stale. ``seed_ok`` still accepts
    an explicit first print so a test can establish a baseline.
    """
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
        if seed_ok:
            note_anchor(mint, px, force=True)
            anchor_px = px
        elif public_src(source) not in ("pool_swap", "pool_vault"):
            _remember_reject({"kind": "no-anchor", "mint": mint, "swap": px, "anchor": None, "gap": None})
            return "no-anchor"
    now_mono = time.time()
    real_px, baseline_age = _real_baseline(mint, now_mono)
    same_agree = _remember_pair(mint, px, source, signature)
    agreed = _pair_agreement(mint)

    if real_px is None:
        # No accepted tick. Do not publish the guard's own override.
        if agreed is not None:
            px, agree = agreed
            _remember_spike(_spike_row(
                mint, px, 0.0, baseline_age, 0.0, source, now_mono,
                kind="pair", agree=agree, reason="swap-vault",
            ))
            return _commit_price(mint, px, source, sol, signature, slot, trade_epoch)
        if seed_ok:
            return _commit_price(mint, px, source, sol, signature, slot, trade_epoch)
        with _guard:
            _awaiting_pair.add(mint)
        baseline_for_log = float(anchor_px) if anchor_px else 0.0
        move = 0.0
        if baseline_for_log > 0:
            move = abs(px - baseline_for_log) / baseline_for_log
        _remember_spike(_spike_row(
            mint, px, baseline_for_log, baseline_age, move, source, now_mono,
            kind="spike", agree=same_agree, reason="no-baseline",
        ))
        return "spike"

    baseline = float(real_px)
    move = abs(px - baseline) / baseline if baseline > 0 else 0.0
    # Two on-chain reads that agree replace a stuck price even if an echo
    # of that price arrived between them.
    if agreed is not None and baseline > 0 and abs(agreed[0] - baseline) / baseline > SPIKE_MOVE:
        px, agree = agreed
        with _guard:
            hold_start = float((_pending_spikes.get(mint) or {}).get("hold_start") or 0)
        _remember_spike(_spike_row(
            mint, px, baseline, baseline_age, abs(px - baseline) / baseline, source, now_mono,
            kind="override", agree=agree, hold_start=hold_start or None, reason="pair",
        ))
        return _commit_price(mint, px, source, sol, signature, slot, trade_epoch)

    if move > SPIKE_MOVE:
        with _guard:
            pend_px = (_pending_spikes.get(mint) or {}).get("px")
        status, hold_start, arm = _spike_confirmed(mint, px, source, baseline, signature, sol)
        if status == "confirm":
            _cancel_hold_timer(mint)
            agree = None
            try:
                if pend_px and float(pend_px) > 0:
                    agree = abs(px - float(pend_px)) / float(pend_px)
            except (TypeError, ValueError):
                agree = None
            _remember_spike(_spike_row(
                mint, px, baseline, baseline_age, move, source, time.time(),
                kind="override", agree=agree, hold_start=hold_start, reason="confirm",
            ))
            return _commit_price(mint, px, source, sol, signature, slot, trade_epoch)
        if status == "reject":
            _cancel_hold_timer(mint)
        elif arm:
            _arm_hold_timer(mint)
        _remember_spike(_spike_row(
            mint, px, baseline, baseline_age, move, source, now_mono,
            kind="spike", hold_start=hold_start, reason="reject" if status == "reject" else "hold",
        ))
        return "spike"

    echo = baseline > 0 and move <= SPIKE_AGREE
    with _guard:
        pend = _pending_spikes.get(mint)
    # A baseline older than 5s must not refresh itself by echoing, or it
    # vetoes the fresh reads that already agree with each other.
    if echo and pend and baseline_age > BASELINE_VETO_S:
        return "spike"
    _cancel_hold_timer(mint)
    with _guard:
        _pending_spikes.pop(mint, None)
    return _commit_price(mint, px, source, sol, signature, slot, trade_epoch)


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
    extra_usd: dict | None = None,
) -> float | None:
    """USD mid = (quote vault + virtual quote) / base vault, scaled by mint decimals.

    Quote SOL is converted with the Jupiter wSOL price. Quote USDC/USDT is $1.
    A quote token such as BABA uses ``extra_usd`` (that token's own SOL-pool
    price). Raw amounts without decimals, or the inverted base/quote side,
    are not a price.
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
    return _sided_usd(base_mint, quote_mint, quote_ui / base_ui, watched, sol_usd, extra_usd)


def _event_body(raw: bytes) -> bytes | None:
    if not raw or len(raw) < 8 or raw[:8] not in (BUY_EVENT_DISC, SELL_EVENT_DISC):
        return None
    return raw[8:]


def _event_pool(body: bytes) -> str | None:
    if len(body) < EV_POOL + 32:
        return None
    try:
        return b58encode(body[EV_POOL:EV_POOL + 32])
    except Exception:
        return None


def pumpswap_event_reserves(raw: bytes) -> tuple[int, int, str | None] | None:
    """Pre-trade base and quote vault balances from a BuyEvent or SellEvent.

    These are the raw token balances, not the fill and not the virtual-quote
    spot. None when the disc mismatches.
    """
    body = _event_body(raw)
    if body is None or len(body) < EV_QUOTE_RES + 8:
        return None
    base = struct.unpack_from("<Q", body, EV_BASE_RES)[0]
    quote = struct.unpack_from("<Q", body, EV_QUOTE_RES)[0]
    if base <= 0 or quote <= 0:
        return None
    return base, quote, _event_pool(body)


def pumpswap_event_fill(raw: bytes) -> tuple[int, int, str | None] | None:
    """Executed (base amount, user quote amount, pool) from a Buy or Sell.

    Buy is base received and quote the user paid. Sell is base sold and quote
    the user received. The raw vault ratio is not this number.
    """
    body = _event_body(raw)
    if body is None or len(body) < EV_USER_QUOTE + 8:
        return None
    base = struct.unpack_from("<Q", body, EV_BASE_AMT)[0]
    quote = struct.unpack_from("<Q", body, EV_USER_QUOTE)[0]
    if base <= 0 or quote <= 0:
        return None
    return base, quote, _event_pool(body)


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


def price_from_program_logs(
    logs: list,
    pool: dict,
    sol_usd: float,
    extra_usd: dict | None = None,
) -> float | None:
    """Per-trade USD from Program data lines. Ignores every other log line.

    The mark is the executed fill (user quote / base amount). Buy and Sell
    store those amounts at the same body offsets. The pre-trade vault
    reserves are the fallback when the fill fields are absent, and that
    fallback adds ``virtual_quote`` the same way a quiet vault read does.
    """
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
            fill = pumpswap_event_fill(raw)
            reserves = pumpswap_event_reserves(raw)
            if fill:
                base, quote, pool_pk = fill
                virtual = 0
            elif reserves:
                base, quote, pool_pk = reserves
                virtual = int(pool.get("virtual_quote") or 0)
            else:
                continue
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
                virtual,
                extra_usd,
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


def _sided_usd(
    mint_a: str,
    mint_b: str,
    b_per_a: float,
    watched: str,
    sol_usd: float,
    extra_usd: dict | None = None,
) -> float | None:
    """b_per_a is how many B you get for one A. Return the watched mint's USD."""
    def quote_usd(mint: str) -> float | None:
        if mint == SOL_MINT:
            return sol_usd
        if mint in _STABLES:
            return 1.0
        if extra_usd and mint in extra_usd:
            try:
                px = float(extra_usd[mint])
            except (TypeError, ValueError):
                return None
            return px if px > 0 else None
        return None

    qa = quote_usd(mint_a)
    qb = quote_usd(mint_b)
    if qa is not None and qb is None and mint_b == watched:
        return qa / b_per_a
    if qb is not None and qa is None and mint_a == watched:
        return b_per_a * qb
    return None


def raydium_clmm_view(raw: bytes) -> dict | None:
    """Mints and sqrt price from a Raydium CLMM pool. None if the disc mismatches."""
    if not raw or len(raw) < CLMM_SQRT + 16 or raw[:8] != CLMM_DISC:
        return None
    try:
        return {
            "mint_0": b58encode(raw[CLMM_MINT_0:CLMM_MINT_0 + 32]),
            "mint_1": b58encode(raw[CLMM_MINT_1:CLMM_MINT_1 + 32]),
            "vault_0": b58encode(raw[CLMM_VAULT_0:CLMM_VAULT_0 + 32]),
            "vault_1": b58encode(raw[CLMM_VAULT_1:CLMM_VAULT_1 + 32]),
            "dec_0": raw[CLMM_DEC_0],
            "dec_1": raw[CLMM_DEC_1],
            "sqrt_price": int.from_bytes(raw[CLMM_SQRT:CLMM_SQRT + 16], "little"),
        }
    except Exception:
        return None


def meteora_dlmm_view(raw: bytes) -> dict | None:
    """Active bin and mints from a Meteora DLMM LbPair. None if the disc mismatches."""
    if not raw or len(raw) < DLMM_MINT_Y + 32 or raw[:8] != DLMM_DISC:
        return None
    try:
        return {
            "active_id": struct.unpack_from("<i", raw, DLMM_ACTIVE_ID)[0],
            "bin_step": struct.unpack_from("<H", raw, DLMM_BIN_STEP)[0],
            "mint_x": b58encode(raw[DLMM_MINT_X:DLMM_MINT_X + 32]),
            "mint_y": b58encode(raw[DLMM_MINT_Y:DLMM_MINT_Y + 32]),
        }
    except Exception:
        return None


def meteora_dlmm_usd(
    raw: bytes,
    watched: str,
    sol_usd: float,
    decimals: dict[str, int] | None = None,
    extra_usd: dict | None = None,
) -> float | None:
    """USD from the active bin. Price of X in Y is (1 + bin_step/10000) ** active_id."""
    view = meteora_dlmm_view(raw)
    if not view or not sol_usd or sol_usd <= 0 or view["bin_step"] <= 0:
        return None
    decs = decimals or {}
    try:
        dec_x = int(decs[view["mint_x"]])
        dec_y = int(decs[view["mint_y"]])
    except (KeyError, TypeError, ValueError):
        return None
    y_per_x = (1 + view["bin_step"] / 10000) ** int(view["active_id"])
    y_per_x *= 10 ** (dec_x - dec_y)
    if y_per_x <= 0:
        return None
    return _sided_usd(view["mint_x"], view["mint_y"], y_per_x, watched, sol_usd, extra_usd)


def raydium_clmm_usd(raw: bytes, watched: str, sol_usd: float, extra_usd: dict | None = None) -> float | None:
    """USD from sqrt_price_x64. token1 per token0, same convention as the Dex price."""
    view = raydium_clmm_view(raw)
    if not view or not sol_usd or sol_usd <= 0:
        return None
    sqrt_price = int(view["sqrt_price"])
    if sqrt_price <= 0:
        return None
    dec_0 = int(view["dec_0"])
    dec_1 = int(view["dec_1"])
    if dec_0 > 18 or dec_1 > 18:
        return None
    b_per_a = (sqrt_price / 2**64) ** 2 * (10 ** (dec_0 - dec_1))
    if b_per_a <= 0:
        return None
    return _sided_usd(view["mint_0"], view["mint_1"], b_per_a, watched, sol_usd, extra_usd)


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
            "spike_rejects": list(_spike_rejects),
            "rate_limits": int(_rate_limits),
            "pool_swaps": dict(_swap_counts),
        }
    snap["watched"] = watched_mints()
    return snap


def _remember_subs(subs: dict) -> None:
    """Publish the account-socket map without dropping per-pool logs ids."""
    with _guard:
        current = dict(_ws.get("subscriptions") or {})
        for key in list(current):
            if str(key).startswith("logs:"):
                continue
            if key not in subs:
                current.pop(key, None)
        for key, val in subs.items():
            if val is None or str(key).startswith("logs:"):
                continue
            current[str(key)] = int(val)
        _ws["subscriptions"] = current


def _set_log_sub(key: str, sub_id: int | None, *, clear_id: int | None = None) -> None:
    """Record one pool's logs subscription. Clearing only removes that id."""
    with _guard:
        current = dict(_ws.get("subscriptions") or {})
        if sub_id is None:
            if clear_id is None or current.get(key) == clear_id:
                current.pop(key, None)
        else:
            current[key] = int(sub_id)
        _ws["subscriptions"] = current


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
    _publish(mint, px, compare_mid=False, source="pool_swap", sol=sol)
    return True


def _vault_sub_covered(pool: dict) -> bool:
    """True when both PumpSwap vaults have a live accountSubscribe id."""
    base = str(pool.get("base_vault") or "")
    quote = str(pool.get("quote_vault") or "")
    if not base or not quote:
        return False
    with _guard:
        subs = _ws.get("subscriptions") or {}
    return f"vault:{base}" in subs and f"vault:{quote}" in subs


def _http_vault_loop() -> None:
    """Poll pool vaults over HTTP every 1s. Runs even when the websocket is down.

    Also resolves pools so the socket thread can subscribe without doing HTTP.
    A pool with no vault subscription is read on every pass. A covered pool
    is read when its mid is older than a second.
    """
    last_log = 0.0
    while not _stop.is_set():
        started = time.monotonic()
        mints = watched_mints()
        if not mints:
            with _guard:
                _ws["why"] = "idle: no watched mints"
            time.sleep(0.5)
            continue
        http = endpoints()[1]
        try:
            _resolve_watched_pools(http, mints)
            _ensure_quote_pools(http)
            # A missing SOL anchor has to land before the first vault math.
            # Once it is cached, refresh it after the mids so a slow Jupiter
            # call cannot stall every coin's vault read.
            if anchor_price(SOL_MINT, max_age=60.0) is None:
                _refresh_anchors(mints)
            _refresh_pump_mids(http)
            _refresh_account_mids(http, mints)
            _refresh_anchors(mints)
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
        spent = time.monotonic() - started
        time.sleep(max(0.0, 1.0 - spent))


def _refresh_account_mids(http: str, mints: list[str]) -> None:
    """HTTP read of whirlpool, bonding-curve, and Raydium CLMM accounts when the socket is quiet."""
    sol = anchor_price(SOL_MINT, max_age=60.0)
    if sol is None or sol <= 0:
        return
    snaps = []
    seen_addr: set[str] = set()

    def add(pool: dict | None) -> None:
        if not pool or pool.get("kind") not in ("whirlpool", "pump", "raydium", "meteora"):
            return
        addr = str(pool.get("address") or "")
        mint = str(pool.get("mint") or "")
        if not addr or not mint or addr in seen_addr:
            return
        with _guard:
            seen = _seen.get(mint)
        if seen and time.time() - seen < 1.0:
            return
        seen_addr.add(addr)
        snaps.append(dict(pool))

    for mint in mints:
        with _guard:
            pool = _pools.get(mint)
            snap = dict(pool) if pool else None
        add(snap)
    with _guard:
        quotes = [dict(row) for row in _quote_pools.values()]
    for row in quotes:
        add(row)
    for snap in snaps:
        mint = str(snap.get("mint") or "")
        info = _account_info(http, str(snap.get("address") or ""))
        raw = _account_bytes(info) if info else None
        if not raw:
            continue
        kind = snap.get("kind")
        if kind == "whirlpool":
            with _guard:
                decs = dict(_decimals)
            px = whirlpool_swap_usd(raw, mint, sol, decs)
        elif kind == "raydium":
            px = raydium_clmm_usd(raw, mint, sol)
        elif kind == "meteora":
            decs = {
                str(snap.get("base_mint") or ""): snap.get("base_dec"),
                str(snap.get("quote_mint") or ""): snap.get("quote_dec"),
            }
            with _guard:
                decs.update(_decimals)
            px = meteora_dlmm_usd(raw, mint, sol, decs)
        else:
            px = pump_curve_swap_usd(raw, sol)
        if px and px > 0:
            _publish(mint, px, compare_mid=False, source="pool_swap", sol=sol)


def _run() -> None:
    global _vault_thread, _logs_thread
    with _guard:
        start_vault = _vault_thread is None or not _vault_thread.is_alive()
        if start_vault:
            _vault_thread = threading.Thread(target=_http_vault_loop, name="trade-vault", daemon=True)
            _vault_thread.start()
        start_logs = _logs_thread is None or not _logs_thread.is_alive()
        if start_logs:
            _logs_thread = threading.Thread(target=_logs_supervisor, name="trade-logs", daemon=True)
            _logs_thread.start()
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
    # Short read timeout so a quiet socket still resubscribes a new watch.
    # HTTP stays off this thread: a vault poll between messages was dropping
    # both acks and logsNotification bursts.
    ws.settimeout(1.0)
    ws.connect(wss, timeout=8)
    _record_ws("open", "")
    print(f"[trade-stream] connected via {name}", flush=True)
    subs: dict[str, int] = {}
    meta: dict[int, dict] = {}
    pending: dict[int, dict] = {}
    rpc_id = 1
    try:
        while not _stop.is_set():
            subs, meta, pending, rpc_id = _sync_subs(
                ws, subs, meta, pending, rpc_id, http, include_logs=False,
            )
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
            if _take_sub_ack(msg, subs, meta, pending):
                _remember_subs(subs)
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


def _note_sub_error(op: dict | None, err) -> None:
    key = str((op or {}).get("key") or "")
    text = f"subscribe {key} {err}"[:200]
    print(f"[trade-stream] {text}", flush=True)
    with _guard:
        errs = list(_ws.get("errors") or [])
        errs.append(text)
        _ws["errors"] = errs[-8:]


def _pending_pop(pending: dict, msg_id):
    """Match a JSON-RPC id. Some sockets echo it as a string."""
    if msg_id in pending:
        return pending.pop(msg_id)
    try:
        alt = int(msg_id)
    except (TypeError, ValueError):
        return None
    if alt in pending:
        return pending.pop(alt)
    return None


def _take_sub_ack(msg: dict, subs: dict, meta: dict, pending: dict) -> bool:
    """Store one subscribe ack. An error or a bad result is dropped so we retry.

    Returns True when this message was a subscribe response (stored or not).
    """
    if msg.get("method") or "id" not in msg:
        return False
    op = _pending_pop(pending, msg.get("id"))
    if not isinstance(op, dict):
        return False
    err = msg.get("error")
    if err or "result" not in msg:
        _note_sub_error(op, err or "no result")
        return True
    key = str(op.get("key") or "")
    try:
        sub_id = int(msg["result"])
    except (TypeError, ValueError):
        _note_sub_error(op, msg.get("result"))
        return True
    subs[key] = sub_id
    meta[sub_id] = dict(op, key=key)
    return True


def prioritize_subscriptions(pools: list[dict], limit: int = MAX_SUBS) -> dict[str, dict]:
    """logsSubscribe for every PumpSwap and pump pool, then whatever vaults fit.

    A busy watch list used to fill the cap with vault accounts and leave a
    pool with no logs subscription, so it never printed pool_swap.
    """
    logs: list[tuple[str, dict]] = []
    rest: list[tuple[str, dict]] = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        for key, op in _ops_for(pool).items():
            if op.get("method") == "logsSubscribe":
                logs.append((key, op))
            else:
                rest.append((key, op))
    want: dict[str, dict] = {}
    for key, op in logs + rest:
        if key in want:
            continue
        if len(want) >= limit:
            break
        want[key] = op
    return want


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
            "params": [{"mentions": [addr]}, {"commitment": LOGS_COMMITMENT}],
            "role": "logs",
        }
    elif kind in ("raydium", "meteora"):
        ops[f"acct:{addr}"] = {"method": "accountSubscribe", "params": [addr, acct], "role": "pool"}
    elif kind == "pumpswap":
        ops[f"logs:{addr}"] = {
            "method": "logsSubscribe",
            "params": [{"mentions": [addr]}, {"commitment": LOGS_COMMITMENT}],
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


def _cached_watched_pools() -> list[dict]:
    """Pools already resolved. The socket thread must not hit HTTP.

    Quote-token SOL/USDC pools are included so they get the same per-pool
    logs socket and vault subscribe as a watched coin.
    """
    out = []
    seen: set[str] = set()

    def add(pool: dict | None) -> None:
        if not pool or not pool.get("address"):
            return
        addr = str(pool.get("address") or "")
        if not addr or addr in seen:
            return
        seen.add(addr)
        out.append(dict(pool))

    for mint in watched_mints():
        with _guard:
            pool = _pools.get(mint)
            snap = dict(pool) if pool else None
        add(snap)
    with _guard:
        quotes = [dict(row) for row in _quote_pools.values()]
    for row in quotes:
        add(row)
    return out


def _sync_subs(ws, subs, meta, pending, rpc_id, http, include_logs: bool = True):
    del http  # pool resolve lives on the vault thread
    now = time.monotonic()
    for rpc, op in list(pending.items()):
        sent = float(op.get("sent") or 0)
        if sent and now - sent > SUB_ACK_S:
            pending.pop(rpc, None)
            _note_sub_error(op, "ack timeout")
    pools = _cached_watched_pools()
    want = prioritize_subscriptions(pools, MAX_SUBS)
    if not include_logs:
        # Logs each have their own socket. This socket is vaults and pool accounts.
        want = {k: v for k, v in want.items() if not str(k).startswith("logs:")}
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
        pending[rpc_id] = dict(op, key=key, sent=now)
        _send(ws, {"jsonrpc": "2.0", "id": rpc_id, "method": op["method"], "params": op["params"]})
    for key, op in want.items():
        sub_id = subs.get(key)
        if sub_id is not None:
            meta[sub_id] = dict(op, key=key)
    return subs, meta, pending, rpc_id


def _wanted_log_ops() -> dict[str, dict]:
    """logsSubscribe for every watched PumpSwap or pump pool.

    The socket that is actually opened is the priority subset from
    ``_priority_log_ops``. This map is the full plan.
    """
    ops: dict[str, dict] = {}
    for pool in _cached_watched_pools():
        addr = str(pool.get("address") or "")
        if pool.get("kind") not in ("pumpswap", "pump") or not addr:
            continue
        key = f"logs:{addr}"
        ops[key] = _log_op(pool, key)
    return ops


def _log_op(pool: dict, key: str) -> dict:
    addr = str(pool.get("address") or "")
    return {
        "method": "logsSubscribe",
        "params": [{"mentions": [addr]}, {"commitment": LOGS_COMMITMENT}],
        "role": "logs",
        "mint": pool.get("mint"),
        "pool": addr,
        "key": key,
    }


def _priority_mints() -> list[str]:
    """Open positions first, then the most recently viewed coin."""
    try:
        positions = _position_mints()
    except Exception:
        positions = []
    with _guard:
        views = [mint for mint, _seen_at in sorted(_views.items(), key=lambda kv: -kv[1])]
    out: list[str] = []
    for mint in list(positions) + views:
        if mint and mint not in out:
            out.append(mint)
    return out


def _priority_log_ops(limit: int = MAX_LOG_SUBS) -> dict[str, dict]:
    """The logsSubscribe set that fits on the one mainnet-beta socket.

    Positions come first, then the coin being viewed. Anything past the cap
    stays on the 1s vault poll.
    """
    all_ops = _wanted_log_ops()
    by_mint: dict[str, list[tuple[str, dict]]] = {}
    for key, op in all_ops.items():
        by_mint.setdefault(str(op.get("mint") or ""), []).append((key, op))
    chosen: dict[str, dict] = {}

    def take(mint: str) -> None:
        for key, op in by_mint.get(mint, []):
            if len(chosen) >= limit:
                return
            chosen[key] = op

    for mint in _priority_mints():
        take(mint)
        if len(chosen) >= limit:
            return chosen
    for key, op in all_ops.items():
        if key in chosen:
            continue
        if len(chosen) >= limit:
            break
        chosen[key] = op
    return chosen


def _jittered(seconds: float) -> float:
    import random
    return max(0.3, float(seconds) * random.uniform(0.5, 1.5))


def _is_rate_limit(text: str) -> bool:
    return "429" in text or "Too Many Requests" in text


def logs_sub_live(address: str) -> bool:
    """True when this pool's logsSubscribe id is on the live socket."""
    if not address:
        return False
    with _guard:
        subs = _ws.get("subscriptions") or {}
    return f"logs:{address}" in subs


def _logs_supervisor() -> None:
    """One logs socket for the priority pools. 429s back off with jitter."""
    backoff = 0.0
    while not _stop.is_set():
        if not _priority_log_ops():
            time.sleep(0.4)
            continue
        try:
            _serve_log_socket()
            backoff = 0.0
        except Exception as exc:
            text = f"{exc.__class__.__name__}: {exc}"
            limited = _is_rate_limit(text)
            if limited:
                _note_rate_limit("logs")
                backoff = min(_BACKOFF_CAP_S, max(2.0, (backoff or 1.0) * 2.0))
            else:
                routine = "1006" in text or "Connection to remote host was lost" in text
                backoff = 0.4 if routine else min(_BACKOFF_CAP_S, max(1.0, (backoff or 1.0) * 2.0))
                if not routine:
                    _note_sub_error({"key": "logs"}, text)
            time.sleep(_jittered(backoff))


def _serve_log_socket() -> None:
    """One websocket, many logsSubscribe calls, for the priority pools only."""
    import websocket

    ws = websocket.WebSocket()
    ws.settimeout(5)
    ws.connect(logs_endpoint(), timeout=8)
    subs: dict[str, int] = {}
    meta: dict[int, dict] = {}
    pending: dict[int, dict] = {}
    rpc = 1
    last_note = time.time()
    try:
        while not _stop.is_set():
            want = _priority_log_ops()
            for key in list(subs):
                if key in want:
                    continue
                sub_id = subs.pop(key, None)
                meta.pop(sub_id, None)
                _set_log_sub(key, None, clear_id=sub_id)
                rpc += 1
                try:
                    ws.send(json.dumps({
                        "jsonrpc": "2.0",
                        "id": rpc,
                        "method": "logsUnsubscribe",
                        "params": [sub_id],
                    }))
                except Exception:
                    raise
            pending_keys = {str(op.get("key") or "") for op in pending.values()}
            for key, op in want.items():
                if key in subs or key in pending_keys:
                    continue
                rpc += 1
                pending[rpc] = dict(op, key=key, sent=time.monotonic())
                ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": rpc,
                    "method": "logsSubscribe",
                    "params": op["params"],
                }))
            try:
                raw = ws.recv()
            except Exception as exc:
                if exc.__class__.__name__ == "WebSocketTimeoutException":
                    now = time.monotonic()
                    for rid, op in list(pending.items()):
                        if now - float(op.get("sent") or 0) > 8:
                            pending.pop(rid, None)
                            _note_sub_error(op, "ack timeout")
                    if time.time() - last_note > 45 and subs:
                        raise RuntimeError("logs silent")
                    try:
                        ws.ping()
                    except Exception:
                        raise
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
            err = msg.get("error")
            if err and _is_rate_limit(str(err)):
                raise RuntimeError(f"429 {err}")
            if not msg.get("method") and "id" in msg:
                op = _pending_pop(pending, msg.get("id"))
                if not isinstance(op, dict):
                    continue
                if msg.get("error") or "result" not in msg:
                    _note_sub_error(op, msg.get("error") or "no result")
                    if _is_rate_limit(str(msg.get("error") or "")):
                        raise RuntimeError(f"429 {msg.get('error')}")
                    continue
                try:
                    sub_id = int(msg["result"])
                except (TypeError, ValueError):
                    _note_sub_error(op, msg.get("result"))
                    continue
                key = str(op.get("key") or "")
                subs[key] = sub_id
                meta[sub_id] = dict(op, key=key)
                _set_log_sub(key, sub_id)
                continue
            if msg.get("method") == "logsNotification":
                last_note = time.time()
                _on_logs(msg, meta)
    finally:
        for key, sub_id in list(subs.items()):
            _set_log_sub(key, None, clear_id=sub_id)
        try:
            ws.close()
        except Exception:
            pass


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


def _is_usd_leg(mint: str) -> bool:
    return mint == SOL_MINT or mint in _STABLES


def _pool_rows() -> list[dict]:
    rows = []
    for bucket in (_pools, _quote_pools):
        rows.extend(bucket.values())
    return rows


def _lookup_pool(mint: str = "", address: str = "", vault: str = "") -> dict | None:
    """Copy the pool row for an address, a vault, or a mint.

    Quote-token SOL/USDC pools live in ``_quote_pools`` and can share a mint
    with a different trading pool, so the address wins.
    """
    with _guard:
        if address:
            for row in _pool_rows():
                if str(row.get("address") or "") == address:
                    return dict(row)
        if vault:
            for row in _pool_rows():
                if vault in (str(row.get("base_vault") or ""), str(row.get("quote_vault") or "")):
                    return dict(row)
        if mint:
            row = _pools.get(mint) or _quote_pools.get(mint)
            return dict(row) if row else None
    return None


def _live_row_unlocked(mint: str, address: str = "", vault: str = "") -> dict | None:
    """Mutable pool dict. Caller holds ``_guard``."""
    if address:
        for bucket in (_pools, _quote_pools):
            for row in bucket.values():
                if str(row.get("address") or "") == address:
                    return row
    if vault:
        for bucket in (_pools, _quote_pools):
            for row in bucket.values():
                if vault in (str(row.get("base_vault") or ""), str(row.get("quote_vault") or "")):
                    return row
    if mint in _pools:
        return _pools[mint]
    if mint in _quote_pools:
        return _quote_pools[mint]
    return None


def _note_quote_mark(mint: str, px: float) -> None:
    """Remember a quote token's USD so the watched coin does not ask Jupiter."""
    if not mint or px <= 0:
        return
    now = time.time()
    with _guard:
        if mint in _quote_pools:
            _quote_px[mint] = (now, float(px))
            return
        for row in _pools.values():
            sides = (str(row.get("base_mint") or ""), str(row.get("quote_mint") or ""))
            if mint in sides and mint != str(row.get("mint") or ""):
                _quote_px[mint] = (now, float(px))
                return


def _note_rate_limit(where: str = "") -> None:
    global _rate_limits
    with _guard:
        _rate_limits += 1
    if where:
        print(f"[trade-stream] rpc 429 {where}", flush=True)


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
    pool = _lookup_pool(mint, address=str(op.get("pool") or ""), vault=str(op.get("vault") or ""))
    with _guard:
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
    elif owner == RAYDIUM_CLMM_OWNER or pool.get("kind") == "raydium":
        px = raydium_clmm_usd(raw, mint, sol)
    elif owner == METEORA_DLMM_OWNER or pool.get("kind") == "meteora":
        decs = {
            str(pool.get("base_mint") or ""): pool.get("base_dec"),
            str(pool.get("quote_mint") or ""): pool.get("quote_dec"),
        }
        with _guard:
            decs.update(_decimals)
        px = meteora_dlmm_usd(raw, mint, sol, decs)
    if px is None or px <= 0:
        return
    _publish(mint, px, compare_mid=compare_mid, slot=slot, sol=sol)


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
    pool = _lookup_pool(mint, address=str(op.get("pool") or ""))
    if not pool:
        return
    sol = anchor_price(SOL_MINT, max_age=60.0)
    if sol is None or sol <= 0:
        return
    extra = _extra_for_pool(pool, sol, endpoints()[1])
    px = price_from_program_logs(logs, pool, sol, extra)
    if px is None or px <= 0:
        return
    sig = str(value.get("signature") or "")
    sol_driver = sol if _quote_is_sol_or_stable(pool) else None
    _publish(
        mint,
        px,
        compare_mid=True,
        slot=_slot_of(result),
        signature=sig,
        sol=sol_driver,
        executed=True,
    )


def _touch_live(mint: str) -> None:
    """Refresh quote age without changing the price. The fill stays the mark."""
    if not mint:
        return
    now = time.time()
    with _guard:
        if mint not in _seen:
            return
        _seen[mint] = now
    import game

    with game._live_lock(mint):
        hit = game._live_cache.get(mint)
        if not hit:
            return
        payload = dict(hit[1])
        payload["fetched_at"] = _iso_ms(now)
        game._live_cache[mint] = (now, payload)


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
    sol: float | None = None,
    executed: bool = False,
) -> str:
    """Publish against the previous mid. A spike does not move that mid.

    An executed fill becomes the mark for SWAP_MARK_S. Vault ratios wait.
    """
    if source == "pool_vault" and _recent_swap(mint):
        # The fill stays the mark. The vault read still happened, so the
        # quote age must not sit on the 5s line until the hold expires.
        _touch_live(mint)
        return "held"
    had = mid_price(mint, max_age=120.0)
    # PumpSwap has a swap fill and a vault. With no accepted baseline the
    # first print waits until those two agree. A single-source pool still
    # seeds, because it has nothing else to agree with.
    status = ingest_swap(
        mint,
        px,
        anchor=had,
        seed_ok=pool_kind(mint) != "pumpswap",
        slot=slot,
        signature=signature,
        source=source,
        sol=sol,
    )
    if status in ("ok", "same"):
        note_mid(mint, px)
        _note_quote_mark(mint, px)
        with _guard:
            if public_src(source) == "pool_swap":
                _swap_counts[mint] = _swap_counts.get(mint, 0) + 1
            if executed:
                _swap_at[mint] = time.time()
    return status


def _apply_vault_amount(pool: dict, vault: str, amount: int, sol: float, slot: int | None) -> None:
    mint = str(pool.get("mint") or "")
    if not mint or not vault:
        return
    with _guard:
        live = _live_row_unlocked(mint, address=str(pool.get("address") or ""), vault=vault)
        if not live:
            return
        if vault == live.get("base_vault"):
            live["base_raw"] = int(amount)
            live["base_slot"] = slot
        elif vault == live.get("quote_vault"):
            live["quote_raw"] = int(amount)
            live["quote_slot"] = slot
        else:
            return
        base_raw = live.get("base_raw")
        quote_raw = live.get("quote_raw")
        paired = live.get("base_slot") is not None and live.get("base_slot") == live.get("quote_slot")
        snap = dict(live)
    # One vault notification is mid-trade. Wait until both sides share a slot,
    # or until the paired HTTP read replaces them. A hot coin's one-sided
    # update is several percent off Dex; a thin coin's is not.
    if base_raw is None or quote_raw is None or not paired:
        return
    extra = _extra_for_pool(snap, sol, endpoints()[1])
    if extra is None and not _quote_is_sol_or_stable(snap):
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
        extra,
    )
    if px and px > 0:
        sol_driver = sol if _quote_is_sol_or_stable(snap) else None
        _publish(mint, px, compare_mid=False, slot=slot, source="pool_vault", sol=sol_driver)


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


def _pair_other(pair: dict, mint: str) -> str:
    base = str((pair.get("baseToken") or {}).get("address") or "")
    quote = str((pair.get("quoteToken") or {}).get("address") or "")
    if base == mint:
        return quote
    if quote == mint:
        return base
    return ""


def _dex_pairs(mint: str) -> list[dict]:
    """Dex pairs for mint, highest liquidity first."""
    try:
        req = Request(
            f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}",
            headers={"User-Agent": "nunu-filter-paper/1.0", "Accept": "application/json"},
        )
        with urlopen(req, timeout=4) as resp:
            pairs = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    if not isinstance(pairs, list):
        return []
    ranked = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        addr = str(pair.get("pairAddress") or "")
        if len(addr) < 32:
            continue
        try:
            liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        ranked.append((liq, pair))
    ranked.sort(key=lambda row: -row[0])
    return [pair for _liq, pair in ranked]


def _decode_pool_account(mint: str, addr: str, http: str) -> dict | None:
    info = _account_info(http, addr)
    if not info:
        return None
    owner = str(info.get("owner") or "")
    raw = _account_bytes(info)
    if owner == WHIRLPOOL_OWNER:
        return {"mint": mint, "address": addr, "owner": owner, "kind": "whirlpool"}
    if owner == PUMP_OWNER:
        return {"mint": mint, "address": addr, "owner": owner, "kind": "pump"}
    if owner == METEORA_DLMM_OWNER and raw:
        view = meteora_dlmm_view(raw)
        if not view or mint not in (view["mint_x"], view["mint_y"]):
            return None
        dec_x = mint_decimals(http, view["mint_x"])
        dec_y = mint_decimals(http, view["mint_y"])
        return {
            "mint": mint,
            "address": addr,
            "owner": owner,
            "kind": "meteora",
            "base_mint": view["mint_x"],
            "quote_mint": view["mint_y"],
            "base_dec": dec_x if dec_x is not None else 9,
            "quote_dec": dec_y if dec_y is not None else 9,
        }
    if owner == RAYDIUM_CLMM_OWNER and raw:
        view = raydium_clmm_view(raw)
        if not view or mint not in (view["mint_0"], view["mint_1"]):
            return None
        return {
            "mint": mint,
            "address": addr,
            "owner": owner,
            "kind": "raydium",
            "base_mint": view["mint_0"],
            "quote_mint": view["mint_1"],
            "base_vault": view["vault_0"],
            "quote_vault": view["vault_1"],
            "base_dec": view["dec_0"],
            "quote_dec": view["dec_1"],
        }
    if owner == PUMPSWAP_OWNER and raw:
        view = pumpswap_pool_view(raw)
        if not view:
            return None
        if mint not in (view["base_mint"], view["quote_mint"]):
            return None
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


def _resolve_pool(mint: str, http: str, usd_only: bool = False) -> dict | None:
    """Highest-liquidity Dex pair whose account we can decode.

    ``usd_only`` keeps pairs whose other side is SOL or USDC/USDT. A quote
    token's busiest pair can be another coin (SIS/SPCX). That pair cannot
    price the quote. The SOL or USDC pool can.
    """
    picked = []
    for pair in _dex_pairs(mint):
        if usd_only and not _is_usd_leg(_pair_other(pair, mint)):
            continue
        addr = str(pair.get("pairAddress") or "")
        if len(addr) < 32:
            continue
        picked.append(addr)
        if len(picked) >= 4:
            break
    for addr in picked:
        pool = _decode_pool_account(mint, addr, http)
        if pool:
            return pool
    return None


def _resolve_usd_pool(mint: str, http: str) -> dict | None:
    """SOL or USDC/USDT pool for a quote mint. Not necessarily its busiest pair."""
    return _resolve_pool(mint, http, usd_only=True)


def _pool_has_usd_leg(pool: dict, mint: str) -> bool:
    sides = {str(pool.get("base_mint") or ""), str(pool.get("quote_mint") or "")}
    if pool.get("kind") == "pump":
        return True
    return any(_is_usd_leg(side) for side in sides if side and side != mint)


def _quote_pool_for(mint: str, http: str) -> dict | None:
    """Cached SOL or USDC pool used to price ``mint``. Subscribed with the watch set."""
    with _guard:
        cached = _quote_pools.get(mint)
    if cached and cached.get("address"):
        return cached
    with _guard:
        main = dict(_pools[mint]) if mint in _pools else None
    if main and main.get("address") and _pool_has_usd_leg(main, mint):
        pool = main
    else:
        pool = _resolve_usd_pool(mint, http)
    if not pool:
        return None
    pool = dict(pool)
    pool["mint"] = mint
    with _guard:
        _quote_pools[mint] = pool
    return pool


def _resolve_watched_pools(http: str, mints: list[str]) -> None:
    missing = []
    for mint in mints:
        with _guard:
            pool = _pools.get(mint)
        if not (pool and pool.get("address")):
            missing.append(mint)
    if len(missing) <= 1:
        for mint in missing:
            _pool_for(mint, http)
        return
    with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
        list(pool.map(lambda mint: _pool_for(mint, http), missing))


def _ensure_quote_pools(http: str) -> None:
    """Resolve a SOL or USDC pool for every non-stable quote leg."""
    sides: list[str] = []
    with _guard:
        rows = [dict(row) for row in _pools.values()]
    for pool in rows:
        watched = str(pool.get("mint") or "")
        for side in (str(pool.get("base_mint") or ""), str(pool.get("quote_mint") or "")):
            if side and side != watched and not _is_usd_leg(side) and side not in sides:
                sides.append(side)
    if len(sides) <= 1:
        for side in sides:
            _quote_pool_for(side, http)
        return
    with ThreadPoolExecutor(max_workers=min(8, len(sides))) as pool:
        list(pool.map(lambda side: _quote_pool_for(side, http), sides))


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


def _post_rpc(http: str, body: bytes, timeout: float) -> dict | None:
    try:
        req = Request(http, data=body, headers={"Content-Type": "application/json", "User-Agent": "nunu-filter-paper/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if getattr(exc, "code", None) == 429:
            _note_rate_limit(http)
        return None
    except URLError as exc:
        if "429" in str(exc):
            _note_rate_limit(http)
        return None
    except Exception as exc:
        if "429" in str(exc):
            _note_rate_limit(http)
        return None
    return payload if isinstance(payload, dict) else None


def _account_info(http: str, addr: str) -> dict | None:
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [addr, {"encoding": "base64"}],
    }).encode("utf-8")
    payload = _post_rpc(http, body, 4)
    if not payload:
        return None
    value = ((payload.get("result") or {}).get("value")) if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else None


_anchor_at = 0.0


def _due_pump_snaps() -> list[dict]:
    """PumpSwap pools whose vaults should be read on this pass.

    Watched coins and the SOL/USDC pools that price their quote tokens share
    one batch. A covered pool can wait on the socket until the mid is a
    second old. A pool with no vault subscription is read every pass.
    """
    now = time.time()
    snaps = []
    seen: set[str] = set()

    def consider(pool: dict | None) -> None:
        if not pool or pool.get("kind") != "pumpswap":
            return
        addr = str(pool.get("address") or "")
        if not addr or addr in seen:
            return
        mint = str(pool.get("mint") or "")
        with _guard:
            seen_mid = _mids.get(mint)
        # A live logs socket can carry the price. Without one, the 1s vault
        # poll is the price, even when an accountSubscribe id is already up.
        if logs_sub_live(addr) and _vault_sub_covered(pool) and seen_mid and now - seen_mid[0] < 1.0:
            return
        seen.add(addr)
        snaps.append(dict(pool))

    for mint in watched_mints():
        with _guard:
            pool = _pools.get(mint)
            snap = dict(pool) if pool else None
        consider(snap)
    with _guard:
        quotes = [dict(row) for row in _quote_pools.values()]
    for row in quotes:
        consider(row)
    return snaps


def _refresh_pump_mids(http: str) -> None:
    """Re-read PumpSwap vaults so a quiet coin still shows the post-swap mid.

    Two or more pools share one getMultipleAccounts call. Per-pool HTTP was
    serial, so six watched coins went stale in the same seconds.
    """
    snaps = _due_pump_snaps()
    if not snaps:
        return
    if len(snaps) == 1:
        _read_vault_mid(http, snaps[0])
        return
    _read_vault_mids_batch(http, snaps)


def _quote_is_sol_or_stable(pool: dict) -> bool:
    sides = {str(pool.get("base_mint") or ""), str(pool.get("quote_mint") or "")}
    return bool(sides & ({SOL_MINT} | _STABLES))


def _token_amount(row: dict | None) -> int | None:
    raw = _account_bytes(row) if isinstance(row, dict) else None
    if not raw or len(raw) < TOKEN_AMOUNT + 8:
        return None
    return struct.unpack_from("<Q", raw, TOKEN_AMOUNT)[0]


def _multiple_amounts(http: str, addrs: list[str]) -> dict[str, int] | None:
    """One getMultipleAccounts for every vault. None when the RPC call fails."""
    if not addrs:
        return {}
    out: dict[str, int] = {}
    for start in range(0, len(addrs), 100):
        chunk = addrs[start:start + 100]
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getMultipleAccounts",
            "params": [chunk, {"encoding": "base64", "commitment": "processed"}],
        }).encode("utf-8")
        payload = _post_rpc(http, body, 2.5)
        if not payload:
            return None
        vals = ((payload.get("result") or {}).get("value")) if isinstance(payload, dict) else None
        if not isinstance(vals, list) or len(vals) < len(chunk):
            return None
        for addr, row in zip(chunk, vals):
            amt = _token_amount(row if isinstance(row, dict) else None)
            if amt is None:
                continue
            out[addr] = amt
    return out


def _vault_amounts(http: str, base_vault: str, quote_vault: str) -> tuple[int, int] | None:
    """Raw token amounts. base64, not jsonParsed: public RPC answers 403 to jsonParsed."""
    if not base_vault or not quote_vault:
        return None
    got = _multiple_amounts(http, [base_vault, quote_vault])
    if not got or base_vault not in got or quote_vault not in got:
        return None
    return got[base_vault], got[quote_vault]


def mint_decimals(http: str, mint: str) -> int | None:
    """SPL mint decimals at byte 44. SOL is 9 and USDC/USDT are 6."""
    if mint == SOL_MINT:
        return 9
    if mint in _STABLES:
        return 6
    if not mint:
        return None
    with _guard:
        known = _decimals.get(mint)
    info = _account_info(http, mint)
    raw = _account_bytes(info) if info else None
    if raw and len(raw) > 44:
        dec = raw[44]
        if 0 <= dec <= 18:
            with _guard:
                _decimals[mint] = dec
            return dec
    return known if known is not None else None


def quote_token_usd(mint: str, sol_usd: float, http: str, depth: int = 0) -> float | None:
    """USD for a quote token from its own SOL or USDC pool. Not Jupiter.

    The pool is cached on ``_quote_pools`` and joined to the watch set, so
    steady state is the vault subscribe and the per-pool logs socket.
    """
    if not mint or not sol_usd or sol_usd <= 0:
        return None
    if mint == SOL_MINT:
        return float(sol_usd)
    if mint in _STABLES:
        return 1.0
    now = time.time()
    with _guard:
        cached = _quote_px.get(mint)
    if cached and now - cached[0] < STREAM_SILENCE_S and cached[1] > 0:
        return cached[1]
    if depth >= 2:
        return None
    pool = _quote_pool_for(mint, http)
    if not pool:
        return None
    if pool.get("kind") == "whirlpool":
        info = _account_info(http, str(pool.get("address") or ""))
        raw = _account_bytes(info) if info else None
        if not raw or len(raw) < 213:
            return None
        decs = {}
        for off in (101, 181):
            side = b58encode(raw[off:off + 32])
            dec = mint_decimals(http, side)
            if dec is not None:
                decs[side] = dec
        px = whirlpool_swap_usd(raw, mint, sol_usd, decs)
        if px and px > 0:
            with _guard:
                _quote_px[mint] = (time.time(), px)
            return px
        return None
    if pool.get("kind") == "meteora":
        info = _account_info(http, str(pool.get("address") or ""))
        raw = _account_bytes(info) if info else None
        decs = {
            str(pool.get("base_mint") or ""): pool.get("base_dec"),
            str(pool.get("quote_mint") or ""): pool.get("quote_dec"),
        }
        px = meteora_dlmm_usd(raw, mint, sol_usd, decs) if raw else None
        if px and px > 0:
            with _guard:
                _quote_px[mint] = (time.time(), px)
            return px
        return None
    if pool.get("kind") == "raydium":
        info = _account_info(http, str(pool.get("address") or ""))
        raw = _account_bytes(info) if info else None
        px = raydium_clmm_usd(raw, mint, sol_usd) if raw else None
        if px and px > 0:
            with _guard:
                _quote_px[mint] = (time.time(), px)
            return px
        return None
    if pool.get("kind") == "pump":
        info = _account_info(http, str(pool.get("address") or ""))
        raw = _account_bytes(info) if info else None
        px = pump_curve_swap_usd(raw, sol_usd) if raw else None
        if px and px > 0:
            with _guard:
                _quote_px[mint] = (time.time(), px)
            return px
        return None
    if pool.get("kind") != "pumpswap":
        return None
    px = _read_vault_mid(http, pool)
    if not px or px <= 0:
        return None
    with _guard:
        _quote_px[mint] = (time.time(), px)
    return px


def _extra_for_pool(pool: dict, sol_usd: float, http: str) -> dict | None:
    """USD map for a non-SOL quote (or base) so the watched mint can be priced."""
    watched = str(pool.get("mint") or "")
    extra: dict[str, float] = {}
    for side in (str(pool.get("base_mint") or ""), str(pool.get("quote_mint") or "")):
        if not side or side == watched or side == SOL_MINT or side in _STABLES:
            continue
        px = quote_token_usd(side, sol_usd, http)
        if not px:
            return None
        extra[side] = px
    return extra or None


def _price_vault_pair(http: str, pool: dict, base_raw: int, quote_raw: int) -> float | None:
    """Turn one vault pair into a published USD mid. Shared by the batch poll."""
    global _pair_seq
    mint = str(pool.get("mint") or "")
    base_mint = str(pool.get("base_mint") or "")
    quote_mint = str(pool.get("quote_mint") or "")
    base_dec = mint_decimals(http, base_mint)
    quote_dec = mint_decimals(http, quote_mint)
    if base_dec is None or quote_dec is None:
        return None
    with _guard:
        live = _live_row_unlocked(mint, address=str(pool.get("address") or ""))
        if live:
            _pair_seq -= 1
            live["base_raw"] = base_raw
            live["quote_raw"] = quote_raw
            live["base_dec"] = base_dec
            live["quote_dec"] = quote_dec
            live["base_slot"] = _pair_seq
            live["quote_slot"] = _pair_seq
            _decimals[base_mint] = base_dec
            _decimals[quote_mint] = quote_dec
            snap = dict(live)
        else:
            snap = dict(pool)
            snap["base_dec"] = base_dec
            snap["quote_dec"] = quote_dec
    sol = anchor_price(SOL_MINT, max_age=60.0)
    if sol is None or sol <= 0:
        return None
    extra = _extra_for_pool(snap, sol, http)
    if extra is None and not _quote_is_sol_or_stable(snap):
        return None
    px = pumpswap_mid_usd(
        base_raw,
        base_dec,
        quote_raw,
        quote_dec,
        base_mint,
        quote_mint,
        mint,
        sol,
        int(snap.get("virtual_quote") or 0),
        extra,
    )
    if px and px > 0:
        sol_driver = sol if _quote_is_sol_or_stable(snap) else None
        _publish(mint, px, compare_mid=False, source="pool_vault", sol=sol_driver)
        clear_pool_miss(mint)
        _note_quote_mark(mint, px)
    return px


def _read_vault_mid(http: str, pool: dict) -> float | None:
    base_vault = str(pool.get("base_vault") or "")
    quote_vault = str(pool.get("quote_vault") or "")
    amounts = _vault_amounts(http, base_vault, quote_vault)
    if not amounts:
        return None
    return _price_vault_pair(http, pool, amounts[0], amounts[1])


def _read_vault_mids_batch(http: str, snaps: list[dict]) -> None:
    """Price every due pool from one account fetch."""
    addrs: list[str] = []
    for snap in snaps:
        for key in ("base_vault", "quote_vault"):
            addr = str(snap.get(key) or "")
            if addr and addr not in addrs:
                addrs.append(addr)
    got = _multiple_amounts(http, addrs)
    if got is None:
        with ThreadPoolExecutor(max_workers=min(8, len(snaps))) as pool:
            list(pool.map(lambda snap: _read_vault_mid(http, snap), snaps))
        return
    for snap in snaps:
        base = got.get(str(snap.get("base_vault") or ""))
        quote = got.get(str(snap.get("quote_vault") or ""))
        if base is None or quote is None:
            continue
        _price_vault_pair(http, snap, base, quote)


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
