#!/usr/bin/env python3
"""Live RPC check for a watched set. Prints first-price, stale, Jupiter, and rate limits.

Uses the public RPC unless SOLANA_RPC_URL or HELIUS_API_KEY is set.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import game
import trade_stream


def _boost_mints() -> list[str]:
    out = []
    headers = {"User-Agent": "nunu-filter-paper/1.0", "Accept": "application/json"}
    for url in (
        "https://api.dexscreener.com/token-boosts/top/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1",
    ):
        req = Request(url, headers=headers)
        with urlopen(req, timeout=20) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        for row in rows if isinstance(rows, list) else []:
            if str(row.get("chainId") or "") != "solana":
                continue
            mint = str(row.get("tokenAddress") or "").strip()
            if len(mint) >= 32 and mint not in out:
                out.append(mint)
    return out


def discover(n: int) -> list[str]:
    """Solana mints whose main pool is PumpSwap or a pump.fun curve.

    A boost-list name with no such pool cannot be priced from vaults, so it
    is not part of the 16-coin watch.
    """
    http = trade_stream.poll_http_primary()
    kept = []
    for mint in _boost_mints():
        pool = trade_stream._resolve_pool(mint, http)
        kind = (pool or {}).get("kind")
        if kind not in ("pumpswap", "pump"):
            continue
        # Discover is not the timed watch. Drop the cached row so the clock
        # includes the real resolve.
        with trade_stream._guard:
            trade_stream._pools.pop(mint, None)
        kept.append(mint)
        print(f"[live] candidate {mint} {kind}", flush=True)
        if len(kept) >= n:
            break
    if len(kept) < n:
        raise SystemExit(f"only found {len(kept)} pumpswap/pump mints, need {n}")
    return kept


def _moved(prices: list[float]) -> bool:
    if len(prices) < 2:
        return False
    lo, hi = min(prices), max(prices)
    if lo <= 0:
        return False
    return (hi - lo) / lo > 0.003


def run(mints: list[str], seconds: int) -> dict:
    # Discover already talked to RPC. Start the clock from a clean budget.
    trade_stream._reset_rpc_state()
    with trade_stream._guard:
        trade_stream._rate_limits = 0
    rate0 = int(trade_stream.stream_health().get("rate_limits") or 0)
    t0 = time.time()
    stream_first: dict[str, float] = {}
    stop = threading.Event()

    def watch_publishes() -> None:
        """First pool price as the stream publishes it, not when the tick loop notices."""
        while not stop.is_set():
            now = time.time() - t0
            for mint in mints:
                if mint in stream_first:
                    continue
                try:
                    px = float(trade_stream._last_px.get(mint) or 0)
                except (TypeError, ValueError):
                    px = 0.0
                if px > 0 and trade_stream._seen.get(mint):
                    stream_first[mint] = now
            stop.wait(0.2)

    watcher = threading.Thread(target=watch_publishes, name="rpc-watch", daemon=True)
    watcher.start()
    for mint in mints:
        trade_stream.note_view(mint)
    trade_stream.ensure_started()
    first: dict[str, float] = {}
    fresh: dict[str, float] = {}
    prices: dict[str, list[float]] = {mint: [] for mint in mints}
    samples = []
    next_log = 30.0
    while True:
        now = time.time()
        elapsed = now - t0
        if elapsed >= seconds:
            break
        for mint in mints:
            tick = game.fetch_live_tick(mint, apply_qa=False) or {}
            px = tick.get("price_usd")
            src = str(tick.get("source") or "")
            live = bool(tick.get("quote_live"))
            try:
                px_f = float(px) if px is not None else 0.0
            except (TypeError, ValueError):
                px_f = 0.0
            at = time.time() - t0
            if px_f > 0 and src.startswith("pool") and mint not in first:
                first[mint] = at
            if px_f > 0 and live and mint not in fresh:
                fresh[mint] = at
            if px_f > 0:
                prices[mint].append(px_f)
            samples.append({
                "mint": mint,
                "t": round(at, 3),
                "src": src,
                "stale": bool(tick.get("stale")) or not live,
                "live": live,
                "age": tick.get("quote_age_s"),
                "px": px_f if px_f > 0 else None,
            })
        if time.time() - t0 >= next_log:
            health = trade_stream.stream_health()
            print(
                f"[live] t={int(time.time() - t0)}s stream={len(stream_first)}/{len(mints)} "
                f"tick={len(first)}/{len(mints)} rate_limits={health.get('rate_limits')} "
                f"paid={trade_stream.paid_rpc_configured()}",
                flush=True,
            )
            next_log += 30.0
        remain = 1.0 - ((time.time() - t0) - elapsed)
        if remain > 0 and time.time() - t0 < seconds:
            time.sleep(remain)
    stop.set()
    watcher.join(1.0)
    health = trade_stream.stream_health()
    rate1 = int(health.get("rate_limits") or 0)
    swaps = health.get("pool_swaps") or {}
    elapsed = max(1.0, time.time() - t0)
    active = []
    for mint in mints:
        if int(swaps.get(mint) or 0) > 0 or _moved(prices[mint]):
            active.append(mint)
    def share(pred, rows):
        if not rows:
            return None
        return sum(1 for row in rows if pred(row)) / len(rows)

    active_rows = [
        row for row in samples
        if row["mint"] in active and row["mint"] in first and row["t"] >= first[row["mint"]]
    ]
    priced_rows = [
        row for row in samples
        if row["mint"] in first and row["t"] >= first[row["mint"]]
    ]
    within_5 = [mint for mint, at in stream_first.items() if at <= 5.0]
    return {
        "coins": len(mints),
        "seconds": round(elapsed, 1),
        "paid_rpc": trade_stream.paid_rpc_configured(),
        "http": trade_stream.poll_http_primary(),
        "priced": len(stream_first),
        "first_price_s": {mint: round(at, 2) for mint, at in sorted(stream_first.items(), key=lambda kv: kv[1])},
        "tick_first_price_s": {mint: round(at, 2) for mint, at in sorted(first.items(), key=lambda kv: kv[1])},
        "first_fresh_s": {mint: round(at, 2) for mint, at in fresh.items()},
        "within_5s": len(within_5),
        "within_5s_pct": round(100.0 * len(within_5) / len(mints), 1),
        "active_coins": len(active),
        "stale_share_active": share(lambda row: row["stale"], active_rows),
        "stale_share_priced": share(lambda row: row["stale"], priced_rows),
        "jupiter_share": share(lambda row: row["src"] == "jupiter_fallback", samples),
        "jupiter_share_priced": share(lambda row: row["src"] == "jupiter_fallback", priced_rows),
        "rate_limits_start": rate0,
        "rate_limits_end": rate1,
        "rate_limits_delta": rate1 - rate0,
        "rate_limits_per_s": round((rate1 - rate0) / elapsed, 3),
        "panel94_rate_per_s": 6.5,
        "mints": mints,
    }


def main() -> None:
    coins = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    mints = discover(coins)
    print(f"[live] watching {coins} for {seconds}s", flush=True)
    for mint in mints:
        print(f"[live] mint {mint}", flush=True)
    report = run(mints, seconds)
    text = json.dumps(report, indent=2)
    print(text, flush=True)
    path = f"/tmp/rpc-live-{coins}.json"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"[live] wrote {path}", flush=True)


if __name__ == "__main__":
    main()
