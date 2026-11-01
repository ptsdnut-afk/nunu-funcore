"""Lock order: game._lock must not nest with the stream resolve/poll locks.

panel_v 96 (PR #44) deadlocked once watched coins grew. The stream called
load_state while holding _resolve_lock or _poll_lock, and buy/tick called
cold_vault_price while holding game._lock. /api/health took the same path.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import game
import trade_stream

MINT = "3KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
OTHER = "B" * 44


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _book(positions: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "session_id": "lock-order",
        "status": "active",
        "started_at": _utc(now - timedelta(hours=1)),
        "ends_at": _utc(now + timedelta(hours=20)),
        "cash_usd": 4.0,
        "start_bankroll": 5.0,
        "peak_equity": 5.0,
        "positions": positions,
        "closed": [],
        "events": [],
    }


def _pos(mint: str, pid: str = "pos") -> dict:
    return {
        "id": pid,
        "mint": mint,
        "symbol": "T",
        "stake_usd": 1.0,
        "entry_price_usd": 1.0,
        "qty_tokens": 1.0,
        "last_price_usd": 1.0,
        "opened_at": _utc(datetime.now(timezone.utc) - timedelta(minutes=5)),
        "sl_pct": 20,
        "tp_pct": 100,
    }


def _lock_is_free(lock: threading.Lock, timeout: float = 0.3) -> bool:
    """True when some other thread can take the lock. Same-thread RLock does not count."""
    box: dict[str, bool] = {}

    def probe() -> None:
        got = lock.acquire(timeout=timeout)
        box["ok"] = got
        if got:
            lock.release()

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(timeout + 0.5)
    return bool(box.get("ok"))


class LockOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._env = {
            "NUNU_COLD_VAULT": os.environ.get("NUNU_COLD_VAULT"),
            "NUNU_TRADE_STREAM": os.environ.get("NUNU_TRADE_STREAM"),
        }
        os.environ["NUNU_TRADE_STREAM"] = "0"
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()
        self._state = mock.patch.object(game, "STATE_PATH", Path(self.tmp.name) / "state.json")
        self._state.start()
        self.addCleanup(self._state.stop)

    def tearDown(self):
        for key, val in self._env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        game._live_cache.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def test_open_close_updates_the_lock_free_set(self):
        game.save_state(_book([_pos(MINT)]))
        calls = []

        def boom():
            calls.append("load_state")
            raise AssertionError("load_state")

        with mock.patch.object(game, "load_state", side_effect=boom):
            self.assertEqual(trade_stream._position_mints(), [MINT])
            self.assertIn(MINT, trade_stream.watched_mints())
            self.assertEqual(trade_stream._mint_tier(MINT), 0)
            ordered = trade_stream._order_mints([OTHER, MINT])
            self.assertEqual(ordered[0], MINT)
            health = trade_stream.stream_health()
        self.assertEqual(calls, [])
        self.assertIn(MINT, health["watched"])

        game.save_state(_book([]))
        self.assertEqual(trade_stream._position_mints(), [])
        game.save_state({"status": "idle", "positions": [_pos(MINT)], "closed": [], "events": []})
        self.assertEqual(trade_stream._position_mints(), [])

    def test_health_does_not_wait_on_game_lock(self):
        game.save_state(_book([_pos(MINT)]))
        release = threading.Event()
        ready = threading.Event()

        def hold() -> None:
            with game._lock:
                ready.set()
                release.wait(2)

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        self.assertTrue(ready.wait(1))
        # Release even if health blocks, so this test cannot hang the suite.
        threading.Thread(target=lambda: (time.sleep(0.35), release.set()), daemon=True).start()
        started = time.monotonic()
        snap = trade_stream.stream_health()
        elapsed = time.monotonic() - started
        release.set()
        holder.join(1)
        self.assertLess(elapsed, 0.25)
        self.assertIn(MINT, snap["watched"])

    def test_repro_interleaving_finishes(self):
        """The attached ABBA repro: resolve lock then game lock, and the reverse."""
        game.save_state(_book([_pos(MINT)]))
        self._assert_cross(
            trade_stream._resolve_lock,
            lambda: trade_stream._order_mints([MINT]),
            lambda: trade_stream._resolve_watched_pools("http://x", [MINT]),
        )
        self._assert_cross(
            trade_stream._poll_lock,
            lambda: (trade_stream._due_pump_snaps(), trade_stream._mint_tier(MINT)),
            lambda: trade_stream._refresh_pump_mids("http://x"),
        )

    def _assert_cross(self, stream_lock, under_stream, under_game) -> None:
        got_stream = threading.Event()
        got_game = threading.Event()
        done: dict[str, bool] = {}
        errors: list[BaseException] = []

        def stream_side() -> None:
            try:
                with stream_lock:
                    got_stream.set()
                    self.assertTrue(got_game.wait(2))
                    under_stream()
                done["stream"] = True
            except BaseException as exc:
                errors.append(exc)
                done["stream"] = False

        def game_side() -> None:
            try:
                with game._lock:
                    got_game.set()
                    self.assertTrue(got_stream.wait(2))
                    with mock.patch.object(trade_stream, "_resolve_watched_pools_locked", return_value=None), \
                         mock.patch.object(trade_stream, "_refresh_pump_mids_locked", return_value=None), \
                         mock.patch.object(trade_stream, "_post_rpc", return_value={"result": {"value": []}}):
                        under_game()
                done["game"] = True
            except BaseException as exc:
                errors.append(exc)
                done["game"] = False

        left = threading.Thread(target=stream_side, daemon=True)
        right = threading.Thread(target=game_side, daemon=True)
        left.start()
        right.start()
        left.join(5)
        right.join(5)
        self.assertFalse(left.is_alive())
        self.assertFalse(right.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(done.get("stream"))
        self.assertTrue(done.get("game"))

    def test_tick_and_buy_fetch_outside_game_lock(self):
        os.environ["NUNU_COLD_VAULT"] = "1"
        game.save_state(_book([_pos(MINT, "held")]))
        seen: list[str] = []
        blocked: list[str] = []

        def watch(name):
            def wrapped(*_args, **_kwargs):
                seen.append(name)
                if not _lock_is_free(game._lock):
                    blocked.append(name)

            return wrapped

        dex = {"symbol": "NVD", "name": "Nvidia", "price_usd": 1.0, "pair_url": "https://dexscreener.com/solana/x"}
        with mock.patch.object(trade_stream, "_resolve_watched_pools_locked", side_effect=watch("resolve")), \
             mock.patch.object(trade_stream, "_refresh_pump_mids_locked", side_effect=watch("poll")), \
             mock.patch.object(trade_stream, "_refresh_anchors", return_value=None), \
             mock.patch.object(trade_stream, "_kick_chain_baselines", return_value=None), \
             mock.patch.object(trade_stream, "_warm_decimals", return_value=None), \
             mock.patch.object(game, "fetch_dex_token", return_value=dex), \
             mock.patch.object(game, "_http_json", return_value=(200, {OTHER: {"usdPrice": 1.0, "blockId": 1}})):
            game.tick_session()
            game.buy(OTHER, 0.4, sl_pct=20, tp_pct=100)
        self.assertIn("resolve", seen)
        self.assertIn("poll", seen)
        self.assertEqual(blocked, [])

    def test_twelve_mints_buy_tick_and_health_finish(self):
        os.environ["NUNU_COLD_VAULT"] = "1"
        mints = [trade_stream.b58encode((i + 1).to_bytes(32, "big")) for i in range(12)]
        self.assertGreaterEqual(len(mints), 12)
        for mint in mints:
            self.assertTrue(game.looks_like_mint(mint))
            trade_stream._pools[mint] = {
                "mint": mint,
                "kind": "pumpswap",
                "address": trade_stream.b58encode((1000 + mints.index(mint)).to_bytes(32, "big")),
                "base_mint": mint,
                "quote_mint": trade_stream.SOL_MINT,
                "base_vault": trade_stream.b58encode((2000 + mints.index(mint)).to_bytes(32, "big")),
                "quote_vault": trade_stream.b58encode((3000 + mints.index(mint)).to_bytes(32, "big")),
                "base_dec": 6,
                "quote_dec": 9,
                "virtual_quote": 0,
            }
            trade_stream.note_view(mint)
        game.start_session(force=True)
        game.save_state(_book([_pos(mint, f"p{i}") for i, mint in enumerate(mints[:6])]))
        # Keep the session cash so buys of the other mints can be attempted.
        st = game.load_state()
        st["cash_usd"] = 5.0
        game.save_state(st)

        errors: list[str] = []
        finished = threading.Event()

        def post(_http, _body, _timeout):
            time.sleep(0.004)
            return {"result": {"value": []}}

        def worker(kind: int) -> None:
            try:
                import server
                for _ in range(3):
                    if kind == 0:
                        game.tick_session()
                    elif kind == 1:
                        game.buy(mints[6 + (kind % 6)], 0.05, sl_pct=20, tp_pct=40)
                    else:
                        with server.app.app_context():
                            body = server.api_health().get_json()
                        if body.get("panel_v") != 98:
                            errors.append(f"panel {body.get('panel_v')}")
                            return
                        if len(body.get("stream", {}).get("watched") or []) < 12:
                            errors.append("watched")
                            return
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        def stream() -> None:
            try:
                for _ in range(6):
                    trade_stream._resolve_watched_pools("http://rpc", mints)
                    trade_stream._refresh_pump_mids("http://rpc")
                    trade_stream._order_mints(mints)
                    trade_stream._mint_tier(mints[0])
            except Exception as exc:
                errors.append(f"stream {type(exc).__name__}: {exc}")

        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post), \
             mock.patch.object(trade_stream, "_dex_pairs", return_value=[]), \
             mock.patch.object(trade_stream, "_refresh_anchors", return_value=None), \
             mock.patch.object(game, "fetch_dex_token", return_value={"symbol": "T", "name": "T", "price_usd": 1.0}), \
             mock.patch.object(game, "_http_json", return_value=(200, {})):
            threads = [threading.Thread(target=stream, daemon=True) for _ in range(2)]
            threads += [threading.Thread(target=worker, args=(i % 3,), daemon=True) for i in range(6)]
            started = time.monotonic()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(8)
            finished.set()
        alive = [thread.name for thread in threads if thread.is_alive()]
        self.assertEqual(alive, [])
        self.assertEqual(errors, [])
        self.assertLess(time.monotonic() - started, 8)
        self.assertGreaterEqual(len(trade_stream.watched_mints()), 12)


if __name__ == "__main__":
    unittest.main()
