#!/usr/bin/env python3
"""SHIP W3 — watched-mint swap stream into the shared tick cache.

PumpPortal ``subscribeTokenTrade`` is not the source. Since 2026-05-01 that
method answers with an error unless the socket was opened with an API key
whose wallet holds at least 0.02 SOL, and each 10k events bills that wallet.
That is a paid key. This process does not open it.

The no-key source is Solana RPC ``accountSubscribe`` (commitment processed)
on the token's main pool. Each notification is a pool account write from a
real swap. The post-swap price is decoded from that account:

- Orca Whirlpool: sqrt price at byte 65, mint A at 101, mint B at 181.
- pump.fun bonding curve: virtual SOL and token reserves.

``logsSubscribe`` is not used. The log lines are not a price, and calling
``getTransaction`` for every signature would block the stream and trip public
rate limits. ``api.mainnet-beta.solana.com`` accepts ``accountSubscribe`` and
then often pushes nothing. ``wss://solana-rpc.publicnode.com`` does push.
When ``HELIUS_API_KEY`` is set, that same subscribe runs on Helius instead.

Jupiter Price API v3 ``usdPrice`` stays the anchor and the fallback. A swap
more than 3% from the anchor is logged and dropped. If the stream is silent
for more than 5 seconds, ``fetch_live_tick`` uses Jupiter again. The request
path only reads the cache. The socket, the reconnect backoff, and the anchor
poll run on a daemon thread.
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

# Same window as game.QUOTE_FRESH_SLA_S. Silent means no accepted swap for
# more than this many seconds, which is when the tick falls back to Jupiter.
STREAM_SILENCE_S = 5.0
STREAM_OUTLIER = 0.03  # reject when abs(swap - anchor) / anchor is more than this
MAX_SUBS = 16
WATCH_TTL_S = 20.0
_BACKOFF_CAP_S = 20.0

SOURCE_WHY = (
    "PumpPortal subscribeTokenTrade requires an API key funded with at least "
    "0.02 SOL (metered). The stream uses public Solana RPC accountSubscribe "
    "on the token's main pool (processed). Helius is used only when "
    "HELIUS_API_KEY is set. logsSubscribe is not a price."
)

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
_STABLES = {USDC_MINT, USDT_MINT}

WHIRLPOOL_OWNER = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
PUMP_OWNER = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_PUBLIC_WSS = "wss://solana-rpc.publicnode.com"
_PUBLIC_HTTP = "https://solana-rpc.publicnode.com"

_guard = threading.Lock()
_views: dict[str, float] = {}
_seen: dict[str, float] = {}
_last_px: dict[str, float] = {}
_anchor: dict[str, tuple[float, float]] = {}
_decimals: dict[str, int] = {SOL_MINT: 9, USDC_MINT: 6, USDT_MINT: 6}
_rejects: list[dict] = []
_pools: dict[str, dict] = {}
_thread: threading.Thread | None = None
_stop = threading.Event()


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


def select_watched(positions: list[str], views: list[str], *, limit: int = MAX_SUBS) -> list[str]:
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


def note_anchor(mint: str, price: float, now: float | None = None, decimals: int | None = None) -> None:
    if not mint or price is None or price <= 0:
        return
    stamped = time.time() if now is None else now
    with _guard:
        _anchor[mint] = (stamped, float(price))
        if decimals is not None and 0 <= int(decimals) <= 18:
            _decimals[mint] = int(decimals)


def anchor_price(mint: str, max_age: float = 30.0) -> float | None:
    with _guard:
        row = _anchor.get(mint)
    if not row:
        return None
    if time.time() - row[0] > max_age:
        return None
    return row[1]


def stream_is_fresh(mint: str, now: float | None = None) -> bool:
    """True when an accepted swap landed within STREAM_SILENCE_S (not more than 5s)."""
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
        _decimals.clear()
        _decimals.update({SOL_MINT: 9, USDC_MINT: 6, USDT_MINT: 6})
        _rejects.clear()
        _pools.clear()


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
) -> str:
    """Publish one real swap into the shared tick cache.

    Returns ok, same, outlier, no-anchor, or bad. Outliers and missing
    anchors are not published, so a silent stretch still falls back to Jupiter.
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
        _remember_reject({"kind": "no-anchor", "mint": mint, "swap": px, "anchor": None, "gap": None})
        return "no-anchor"
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
        "source": "stream",
        "signature": signature or "",
        "slot": slot,
    }
    with game._live_lock(mint):
        game._live_cache[mint] = (now, payload)
        game._clear_live_backoff(mint)
    with _guard:
        _seen[mint] = now
        _last_px[mint] = px
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
    # Token decimals on the curve are 6. SOL is 9.
    sol_ui = virtual_sol / 1e9
    token_ui = virtual_token / 1e6
    if token_ui <= 0:
        return None
    return (sol_ui / token_ui) * sol_usd


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


def _run() -> None:
    backoff = 0.0
    while not _stop.is_set():
        try:
            if not watched_mints():
                time.sleep(0.5)
                continue
            _serve_socket()
            backoff = 0.0
        except Exception as exc:
            backoff = next_backoff(backoff)
            print(f"[trade-stream] reconnect in {backoff:.0f}s ({exc})", flush=True)
            time.sleep(backoff)


def _serve_socket() -> None:
    import websocket

    wss, http, name = endpoints()
    ws = websocket.WebSocket()
    ws.settimeout(5)
    ws.connect(wss, timeout=8)
    print(f"[trade-stream] connected via {name}", flush=True)
    subs: dict[str, int] = {}
    pending: dict[int, str] = {}
    rpc_id = 1
    try:
        while not _stop.is_set():
            _refresh_anchors(watched_mints())
            subs, pending, rpc_id = _sync_subs(ws, subs, pending, rpc_id, http)
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
                pool = pending.pop(msg["id"])
                try:
                    subs[pool] = int(msg["result"])
                except (TypeError, ValueError):
                    subs.pop(pool, None)
                continue
            if msg.get("method") == "accountNotification":
                _on_account(msg, subs)
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _sync_subs(ws, subs, pending, rpc_id, http):
    want = {}
    for mint in watched_mints():
        pool = _pool_for(mint, http)
        if pool:
            want[pool["address"]] = pool
    for pool_addr in list(subs):
        if pool_addr not in want:
            sub_id = subs.pop(pool_addr, None)
            if sub_id is not None:
                rpc_id += 1
                _send(ws, {"jsonrpc": "2.0", "id": rpc_id, "method": "accountUnsubscribe", "params": [sub_id]})
    for pool_addr, pool in want.items():
        if pool_addr in subs or pool_addr in pending.values():
            continue
        rpc_id += 1
        pending[rpc_id] = pool_addr
        _send(ws, {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "accountSubscribe",
            "params": [pool_addr, {"encoding": "base64", "commitment": "processed"}],
        })
    if len(subs) + len(pending) > MAX_SUBS:
        print(f"[trade-stream] subscription cap {MAX_SUBS}", flush=True)
    return subs, pending, rpc_id


def _send(ws, payload: dict) -> None:
    ws.send(json.dumps(payload))


def _on_account(msg: dict, subs: dict[str, int]) -> None:
    """Decode the one pool this subscription id names. Other pools are ignored."""
    params = msg.get("params") or {}
    result = params.get("result") or {}
    value = result.get("value") or {}
    data = value.get("data")
    if not isinstance(data, list) or not data:
        return
    try:
        sub_id = int(params.get("subscription"))
    except (TypeError, ValueError):
        return
    pool = None
    with _guard:
        pools = list(_pools.values())
        decs = dict(_decimals)
    for item in pools:
        addr = item.get("address")
        if addr and subs.get(addr) == sub_id:
            pool = item
            break
    if not pool or not pool.get("mint"):
        return
    try:
        raw = base64.b64decode(data[0])
    except Exception:
        return
    owner = str(value.get("owner") or pool.get("owner") or "")
    slot = None
    ctx = result.get("context") or {}
    try:
        slot = int(ctx.get("slot")) if ctx.get("slot") is not None else None
    except (TypeError, ValueError):
        slot = None
    sol = anchor_price(SOL_MINT, max_age=60.0)
    if sol is None or sol <= 0:
        return
    mint = str(pool.get("mint"))
    px = None
    if owner == WHIRLPOOL_OWNER:
        px = whirlpool_swap_usd(raw, mint, sol, decs)
    elif owner == PUMP_OWNER:
        px = pump_curve_swap_usd(raw, sol)
    if px is None or px <= 0:
        return
    ingest_swap(mint, px, trade_epoch=time.time(), slot=slot)


def _pool_for(mint: str, http: str) -> dict | None:
    with _guard:
        cached = _pools.get(mint)
    if cached:
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
        if owner not in (WHIRLPOOL_OWNER, PUMP_OWNER):
            continue
        return {"mint": mint, "address": addr, "owner": owner}
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
