"""RPC scheduling for a 16-coin watch.

Vault polls share getMultipleAccounts, quiet coins wait, and -32005 / 429
back off per endpoint inside a global budget. A paid RPC is optional.
"""

import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import game
import trade_stream


def _pk(n: int) -> str:
    return trade_stream.b58encode(int(n).to_bytes(32, "big"))


def _pool(i: int, *, quote_vault: str | None = None) -> dict:
    mint = _pk(1000 + i)
    return {
        "mint": mint,
        "kind": "pumpswap",
        "address": _pk(2000 + i),
        "base_mint": mint,
        "quote_mint": trade_stream.SOL_MINT,
        "base_vault": _pk(3000 + i),
        "quote_vault": quote_vault or _pk(4000 + i),
        "base_dec": 6,
        "quote_dec": 9,
        "virtual_quote": 0,
    }


def _clear_rpc_env():
    saved = {}
    for key in list(os.environ):
        if key.startswith("SOLANA_") or key in ("HELIUS_API_KEY", "NUNU_RPC_SCHEDULER", "NUNU_RPC_GAP"):
            saved[key] = os.environ.pop(key)
    return saved


class RpcScheduleTests(unittest.TestCase):
    def setUp(self):
        game._live_cache.clear()
        trade_stream.reset_for_tests()
        self._env = _clear_rpc_env()

    def tearDown(self):
        trade_stream._sched_hold = False
        for key in list(os.environ):
            if key.startswith("SOLANA_") or key in ("HELIUS_API_KEY", "NUNU_RPC_SCHEDULER", "NUNU_RPC_GAP"):
                os.environ.pop(key, None)
        os.environ.update(self._env)
        trade_stream.reset_for_tests()
        game._live_cache.clear()

    def _arm(self, n: int, *, quote_vault: str | None = None) -> dict:
        pool = _pool(n, quote_vault=quote_vault)
        trade_stream._pools[pool["mint"]] = pool
        trade_stream.note_view(pool["mint"])
        trade_stream._decimals[pool["mint"]] = 6
        return pool

    def test_sixteen_vaults_split_and_a_shared_account_is_once(self):
        shared = _pk(4999)
        pools = [self._arm(i, quote_vault=shared if i < 2 else None) for i in range(16)]
        held = pools[0]["mint"]
        trade_stream.note_view(pools[1]["mint"])
        calls = []

        def post(http, body, timeout):
            payload = json.loads(body.decode())
            calls.append(payload)
            vals = []
            for _addr in payload["params"][0]:
                vals.append({"data": ["", "base64"]})
            return {"result": {"value": vals}}

        state = Path("/tmp") / "rpc-schedule-state.json"
        with mock.patch.object(game, "STATE_PATH", state), \
             mock.patch.object(trade_stream, "_post_rpc", side_effect=post), \
             mock.patch.object(trade_stream, "mint_decimals", return_value=6):
            game.save_state({
                "status": "active",
                "positions": [{"mint": held}],
                "closed": [],
                "events": [],
            })
            trade_stream._refresh_pump_mids("http://rpc")
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(row["method"] == "getMultipleAccounts" for row in calls))
        self.assertTrue(all(len(row["params"][0]) <= trade_stream._PUBLIC_ACCOUNT_CHUNK for row in calls))
        first = calls[0]["params"][0]
        self.assertIn(pools[0]["base_vault"], first)
        self.assertIn(pools[1]["base_vault"], first)
        joined = [addr for row in calls for addr in row["params"][0]]
        # Shared quote vault is not repeated inside one call.
        for row in calls:
            self.assertEqual(len(row["params"][0]), len(set(row["params"][0])))
        self.assertGreaterEqual(joined.count(shared), 1)
        self.assertEqual(len({p["base_vault"] for p in pools} & set(joined)), 16)

    def test_six_vaults_stay_inside_the_public_account_cap(self):
        pools = [self._arm(i) for i in range(6)]
        calls = []

        def post(http, body, timeout):
            payload = json.loads(body.decode())
            calls.append(payload)
            return {"result": {"value": []}}

        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._refresh_pump_mids("http://rpc")
        self.assertTrue(calls)
        self.assertTrue(all(row["method"] == "getMultipleAccounts" for row in calls))
        self.assertTrue(all(len(row["params"][0]) <= trade_stream._PUBLIC_ACCOUNT_CHUNK for row in calls))
        self.assertEqual(sum(len(row["params"][0]) for row in calls), 12)
        self.assertLess(len(calls), 6)
        self.assertEqual(len({p["address"] for p in pools}), 6)

    def test_rate_limit_does_not_fan_out(self):
        for i in range(5):
            self._arm(i)
        calls = []

        def post(http, body, timeout):
            calls.append(json.loads(body.decode()))
            return {"error": {"code": -32005, "message": "rate limit"}}

        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post), \
             mock.patch.object(trade_stream, "_read_vault_mid", side_effect=AssertionError("fanout")):
            trade_stream._refresh_pump_mids("http://rpc")
        self.assertEqual(len(calls), 1)

    def test_quiet_coin_waits_and_a_position_does_not(self):
        quiet = self._arm(1)
        held = self._arm(2)
        now = time.time()
        for pool in (quiet, held):
            trade_stream._seen[pool["mint"]] = now
            trade_stream._last_px[pool["mint"]] = 1.0
            trade_stream._poll_at[pool["address"]] = now - 1.2
        state = Path("/tmp/rpc-schedule-quiet.json")
        calls = []

        def post(http, body, timeout):
            calls.append(json.loads(body.decode())["params"][0])
            return {"result": {"value": []}}

        with mock.patch.object(game, "STATE_PATH", state), \
             mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            game.save_state({
                "status": "active",
                "positions": [{"mint": held["mint"]}],
                "closed": [],
                "events": [],
            })
            trade_stream._refresh_pump_mids("http://rpc")
        self.assertEqual(len(calls), 1)
        self.assertIn(held["base_vault"], calls[0])
        self.assertNotIn(quiet["base_vault"], calls[0])

    def test_viewed_coin_is_ahead_of_a_quiet_one(self):
        quiet = self._arm(3)
        viewed = self._arm(4)
        now = time.time()
        for pool in (quiet, viewed):
            trade_stream._seen[pool["mint"]] = now
            trade_stream._last_px[pool["mint"]] = 1.0
            trade_stream._poll_at[pool["address"]] = now - 1.2
        trade_stream.note_view(viewed["mint"])
        due = trade_stream._due_pump_snaps()
        mints = [row["mint"] for row in due]
        self.assertIn(viewed["mint"], mints)
        self.assertNotIn(quiet["mint"], mints)
        self.assertEqual(trade_stream._mint_tier(viewed["mint"]), 0)
        self.assertEqual(trade_stream._mint_tier(quiet["mint"]), 2)

    def test_backoff_doubles_with_jitter_per_endpoint(self):
        with mock.patch("random.uniform", return_value=1.0):
            trade_stream._note_endpoint_limit("https://rpc.example/a")
            first = trade_stream._endpoint_delay("https://rpc.example/a")
            trade_stream._note_endpoint_limit("https://rpc.example/a")
            second = trade_stream._endpoint_delay("https://rpc.example/a")
        self.assertAlmostEqual(first, 0.5)
        self.assertAlmostEqual(second, 1.0)
        self.assertGreater(trade_stream._endpoint_wait("https://rpc.example/a"), 0)
        self.assertEqual(trade_stream._endpoint_wait("https://other.example/a"), 0)
        calls = []

        def post(http, body, timeout):
            calls.append(http)
            return {"result": {"value": None}}

        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._account_info("https://rpc.example/a", _pk(9))
        self.assertEqual(calls, [])
        trade_stream._note_endpoint_ok("https://rpc.example/a")
        self.assertEqual(trade_stream._endpoint_wait("https://rpc.example/a"), 0)
        self.assertLess(trade_stream._endpoint_delay("https://rpc.example/a"), second)
        # 429 uses the same backoff as -32005. A different host starts over.
        with mock.patch("random.uniform", return_value=1.0):
            trade_stream._note_endpoint_limit("https://other-rpc.example/b")
        self.assertAlmostEqual(trade_stream._endpoint_delay("https://other-rpc.example/b"), 0.5)

    def test_budget_skips_the_extra_call(self):
        trade_stream._budget_override = (0.0, 1.0)
        trade_stream._tokens = 0.0
        trade_stream._tokens_at = 0.0
        calls = []

        def post(http, body, timeout):
            calls.append(1)
            return {"result": {"value": None}}

        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._account_info("http://rpc", _pk(8))
            trade_stream._account_info("http://rpc", _pk(9))
        self.assertEqual(len(calls), 1)

    def test_paid_rpc_falls_back_to_mainnet_beta(self):
        os.environ["HELIUS_API_KEY"] = "test-key"
        os.environ["NUNU_RPC_SCHEDULER"] = "0"
        hosts = []

        def post(http, body, timeout):
            hosts.append(http)
            if "helius" in http:
                return {"error": {"code": -32005, "message": "rate limit"}}
            if "429" in http:
                return None
            return {"result": {"value": None}}

        primary = trade_stream.poll_http_primary()
        self.assertIn("helius", primary)
        self.assertEqual(
            trade_stream._http_candidates(primary)[-1],
            trade_stream._MAINNET_BETA_HTTP,
        )
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._fetch_transaction(primary, "sig")
        self.assertGreaterEqual(len(hosts), 2)
        self.assertIn("helius", hosts[0])
        self.assertIn("mainnet-beta.solana.com", hosts[1])

    def test_no_key_uses_only_the_public_host(self):
        hosts = []

        def post(http, body, timeout):
            hosts.append(http)
            return {"result": {"context": {"slot": 1}, "value": None}}

        public = "https://solana-rpc.publicnode.com"
        self.assertFalse(trade_stream.paid_rpc_configured())
        self.assertEqual(trade_stream._http_candidates(public), [public])
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._account_info(public, _pk(7))
            trade_stream._fetch_transaction(public, "sig")
        self.assertEqual(hosts, [public, public])

    def test_scheduler_serves_a_hot_call_before_quiet_ones(self):
        os.environ["NUNU_RPC_SCHEDULER"] = "1"
        trade_stream._budget_override = (100.0, 8.0)
        trade_stream._sched_hold = True
        order = []

        def post(http, body, timeout):
            payload = json.loads(body.decode())
            order.append(payload.get("id"))
            return {"result": {"value": None}}

        def submit(priority, ident):
            body = json.dumps({
                "jsonrpc": "2.0",
                "id": ident,
                "method": "getAccountInfo",
                "params": [_pk(1), {"encoding": "base64"}],
            }).encode()
            trade_stream._sched_submit(priority, "https://solana-rpc.publicnode.com", body, 2.0)

        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            threads = [
                threading.Thread(target=submit, args=args)
                for args in ((2, "quiet-a"), (2, "quiet-b"), (0, "position"))
            ]
            for thread in threads:
                thread.start()
            time.sleep(0.25)
            trade_stream._sched_hold = False
            with trade_stream._sched_cv:
                trade_stream._sched_cv.notify_all()
            for thread in threads:
                thread.join(4)
        self.assertTrue(order, order)
        self.assertEqual(order[0], "position")

    def test_chunk_gap_is_zero_in_tests_and_spreads_live(self):
        self.assertEqual(trade_stream._chunk_gap(), 0.0)
        with mock.patch.object(trade_stream, "_under_test", return_value=False):
            self.assertGreaterEqual(trade_stream._chunk_gap(), 0.1)

    def test_known_mint_decimals_do_not_hit_rpc_again(self):
        mint = _pk(42)
        calls = []

        def post(http, body, timeout):
            calls.append(json.loads(body.decode())["method"])
            raw = bytearray(50)
            raw[44] = 6
            import base64
            return {"result": {"value": {"data": [base64.b64encode(bytes(raw)).decode(), "base64"]}}}

        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            self.assertEqual(trade_stream.mint_decimals("http://rpc", mint), 6)
            self.assertEqual(trade_stream.mint_decimals("http://rpc", mint), 6)
        self.assertEqual(calls, ["getAccountInfo"])


if __name__ == "__main__":
    unittest.main()
