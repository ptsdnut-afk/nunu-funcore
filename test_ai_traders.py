"""SHIP AB — owner vs VECTOR and LEDGER, paper only."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import game


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fresh(px: float, source: str = "pool_swap", age: float = 0.4, live: bool = True) -> dict:
    return {
        "price_usd": px,
        "quote_live": live,
        "quote_age_s": age,
        "source": source,
        "stale": (not live) or age > 5,
        "price_ts": "2026-11-02T00:00:00Z",
    }


def _receipt(i: int, path: list[dict], hold_s: float = 5000) -> dict:
    return {
        "id": f"r{i:02d}",
        "verdict": "BUY-WATCH",
        "close_reason": "stop",
        "pnl_pct": path[-1]["pnl_pct"],
        "hold_s": hold_s,
        "closed_at": f"2026-11-02T00:{i:02d}:00Z",
        "path": path,
    }


DECLINE = [
    {"t": 0, "pnl_pct": 0},
    {"t": 5, "pnl_pct": -6},
    {"t": 10, "pnl_pct": -12},
    {"t": 20, "pnl_pct": -25},
]
RALLY = [
    {"t": 0, "pnl_pct": 0},
    {"t": 5, "pnl_pct": -6},
    {"t": 10, "pnl_pct": -12},
    {"t": 40, "pnl_pct": 40},
]
LOSS = [{"t": 0, "pnl_pct": 0}, {"t": 1, "pnl_pct": -40}]
RECOVER = [{"t": 0, "pnl_pct": 0}, {"t": 1, "pnl_pct": -31}, {"t": 50, "pnl_pct": 80}]


class AiTraderTests(unittest.TestCase):
    def setUp(self):
        import ai_traders

        self.ai = ai_traders
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(game, "STATE_PATH", root / "game_state.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
            mock.patch.object(game, "PROFILE_PATH", root / "profile.json"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.ai.reset_for_tests()
        self.addCleanup(self.ai.reset_for_tests)

    def _live_mints(self) -> list[str]:
        rows = self.ai.filter_universe()
        mints = []
        for row in rows:
            mint = str(row.get("mint") or "")
            if mint not in mints:
                mints.append(mint)
        self.assertGreaterEqual(len(mints), 2, "filter list needs two live coins")
        return mints

    def _rows(self, mints: list[str]) -> list[dict]:
        return [
            {
                "mint": mints[0],
                "ticker": "MOMO",
                "name": "Momo",
                "score": 40,
                "metrics": {"price_change_h1": 80, "buy_sell_ratio_h1": 2.4},
            },
            {
                "mint": mints[1],
                "ticker": "LIST",
                "name": "List",
                "score": 99,
                "metrics": {"price_change_h1": 12, "buy_sell_ratio_h1": 1.4},
            },
        ]

    def _holding(self, mint: str) -> bool:
        block = self.ai._block or {}
        for trader in (block.get("traders") or {}).values():
            for pos in (trader.get("session") or {}).get("positions") or []:
                if pos.get("mint") == mint:
                    return True
        return False

    def _quote_factory(self, mode: dict | None = None):
        mode = mode if mode is not None else {"kind": "up"}

        def quote(mint, purpose="mark"):
            kind = mode.get("kind", "up")
            if kind == "jupiter":
                return _fresh(1.0, source="jupiter", age=0.2, live=False)
            if kind == "stale":
                return _fresh(1.0, source="pool_swap", age=9.0, live=False)
            if purpose == "fill" and not self._holding(mint):
                return _fresh(1.0)
            if self._holding(mint) or purpose == "mark":
                return _fresh(3.0 if kind == "up" else 1.0)
            return _fresh(1.0)

        return quote

    def _owner(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "session_id": "owner-book",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 5.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [],
            "closed": [],
            "events": [],
        }

    def test_verdict_is_unknown_until_checklist_code_exists(self):
        self.assertEqual(self.ai.get_verdict("not-a-mint"), "UNKNOWN")
        mint = self._live_mints()[0]
        self.ai._clear_provider_cache()
        self.assertEqual(self.ai.get_verdict(mint), "UNKNOWN")
        mod = types.ModuleType("checklist")
        mod.get_verdict = lambda m: "BUY-WATCH" if m == mint else "nope"
        sys.modules["checklist"] = mod
        self.addCleanup(lambda: sys.modules.pop("checklist", None))
        self.ai._clear_provider_cache()
        self.assertEqual(self.ai.get_verdict(mint), "BUY-WATCH")
        self.assertEqual(self.ai.get_verdict(mint + "x"), "UNKNOWN")

    def test_ledger_never_buys_a_non_buy_watch_coin(self):
        loose = {"min_verdict": "SKIP", "stop_pct": 20, "tp_pct": 40, "hold_min": 20}
        for verdict in ("SKIP", "WATCH", "UNKNOWN", "BUY", "", None):
            self.assertFalse(self.ai.allows_buy("LEDGER", verdict, loose))
        self.assertTrue(self.ai.allows_buy("LEDGER", "BUY-WATCH", loose))
        self.assertTrue(self.ai.allows_buy("LEDGER", "buy watch", loose))

        mints = self._live_mints()
        rows = self._rows(mints)
        rows.append({
            "mint": mints[0],
            "ticker": "DUP",
            "score": 1,
            "metrics": {"price_change_h1": 5},
        })
        # A third real mint, verdicts that LEDGER must refuse, plus one BUY-WATCH.
        extra = None
        for row in self.ai.filter_universe():
            mint = str(row.get("mint") or "")
            if mint not in mints[:2]:
                extra = mint
                break
        self.assertIsNotNone(extra)
        rows = [
            {"mint": mints[0], "ticker": "SKIPX", "score": 90, "metrics": {"price_change_h1": 50}},
            {"mint": mints[1], "ticker": "WATCHX", "score": 80, "metrics": {"price_change_h1": 40}},
            {"mint": extra, "ticker": "UNKX", "score": 70, "metrics": {"price_change_h1": 30}},
        ]
        buy_watch = mints[0]

        def verdict(mint):
            if mint == mints[1]:
                return "WATCH"
            if mint == extra:
                return "UNKNOWN"
            return "SKIP"

        self.ai.set_verdict_fn(verdict)
        quote = self._quote_factory()
        for t in range(0, 12):
            self.ai.step(now=t, quote_fn=quote, universe=rows)
        ledger = self.ai._block["traders"]["LEDGER"]
        self.assertEqual(ledger["session"]["positions"], [])
        self.assertEqual(ledger["receipts"], [])
        self.assertEqual(ledger["buy_times"], [])
        vector = self.ai._block["traders"]["VECTOR"]
        self.assertTrue(vector["session"]["positions"] or vector["receipts"] or vector["session"].get("pending"))
        # Force the saved floor open and still refuse SKIP.
        ledger["settings"]["min_verdict"] = "SKIP"
        self.ai.step(now=20, quote_fn=quote, universe=rows)
        self.assertEqual(self.ai._block["traders"]["LEDGER"]["buy_times"], [])

        def only_good(mint):
            return "BUY-WATCH" if mint == buy_watch else "SKIP"

        self.ai.reset_for_tests()
        game.save_state(self._owner())
        self.ai.set_verdict_fn(only_good)
        for t in (0, 3):
            self.ai.step(now=t, quote_fn=quote, universe=self._rows(mints))
        held = [p["mint"] for p in self.ai._block["traders"]["LEDGER"]["session"]["positions"]]
        self.assertEqual(held, [buy_watch])
        self.assertEqual(self.ai._block["traders"]["LEDGER"]["session"]["positions"][0]["verdict"], "BUY-WATCH")

    def test_both_ais_fill_live_filter_coins_with_delay_and_fresh_quote(self):
        mints = self._live_mints()
        rows = self._rows(mints)
        game.save_state(self._owner())
        game.tick_session()
        checked = {"lock": False}

        def quote(mint, purpose="mark"):
            held = game._lock.acquire(blocking=False)
            if held:
                game._lock.release()
            self.assertTrue(held)
            if not checked["lock"]:
                checked["lock"] = True
                box = {}

                def other():
                    t0 = time.perf_counter()
                    game.tick_session()
                    box["dt"] = time.perf_counter() - t0

                thread = threading.Thread(target=other)
                thread.start()
                thread.join(1.0)
                self.assertFalse(thread.is_alive())
                self.assertLess(box["dt"], 0.25)
            if purpose == "fill" and not self._holding(mint):
                return _fresh(1.0)
            return _fresh(3.0)

        def verdict(mint):
            if mint == mints[0]:
                return "SKIP"
            if mint == mints[1]:
                return "BUY-WATCH"
            return "UNKNOWN"

        self.ai.set_verdict_fn(verdict)
        with self.assertLogs("nunu.ai_traders", level="INFO") as logs:
            for t in range(0, 12):
                self.ai.step(now=float(t), quote_fn=quote, universe=rows)
            self.ai.step(now=30, quote_fn=quote, universe=rows)
            self.ai.step(now=50, quote_fn=quote, universe=rows)
        text = "\n".join(logs.output)
        self.assertIn("delay_ok=True", text)
        self.assertIn("fresh_ok=True", text)
        self.assertIn("source=pool_swap", text)

        vector = self.ai._block["traders"]["VECTOR"]
        ledger = self.ai._block["traders"]["LEDGER"]
        self.assertTrue(vector["receipts"], "VECTOR placed no paper trade")
        self.assertTrue(ledger["receipts"], "LEDGER placed no paper trade")
        v = vector["receipts"][0]
        g = ledger["receipts"][0]
        self.assertEqual(v["mint"], mints[0])
        self.assertEqual(v["verdict"], "SKIP")
        self.assertTrue(v["skip_flag"])
        self.assertEqual(v["flag"], "SKIP")
        self.assertIn("momentum", v["entry_reason"])
        self.assertTrue(v["exit_reason"])
        self.assertIn("SKIP", v["lesson"])
        self.assertGreaterEqual(v["entry_proof"]["delay_s"], 3)
        self.assertTrue(v["entry_proof"]["delay_ok"])
        self.assertTrue(v["entry_proof"]["fresh_ok"])
        self.assertFalse(v["entry_proof"]["jupiter"])
        self.assertEqual(v["entry_proof"]["source"], "pool_swap")
        self.assertLessEqual(v["entry_proof"]["quote_age_s"], 5)
        self.assertLessEqual(v["entry_proof"]["trades_this_hour"], 6)
        self.assertTrue(v["entry_proof"]["cap_ok"])
        self.assertEqual(g["mint"], mints[1])
        self.assertEqual(g["verdict"], "BUY-WATCH")
        self.assertFalse(g["skip_flag"])
        self.assertIn("BUY-WATCH", g["entry_reason"])
        self.assertTrue(g["exit_reason"])
        self.assertTrue(g["lesson"])
        self.assertGreaterEqual(g["entry_proof"]["delay_s"], 3)
        self.assertTrue(g["entry_proof"]["fresh_ok"])

        owner = json.loads(game.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(owner["session_id"], "owner-book")
        self.assertEqual(owner["cash_usd"], 5.0)
        self.assertEqual(owner["positions"], [])
        self.assertIn("ai_traders", owner)
        self.assertIn("VECTOR", owner["ai_traders"]["traders"])
        pub = game.public_state()
        self.assertNotIn("ai_traders", pub)
        self.assertEqual(pub["session_id"], "owner-book")
        self.assertEqual(pub["cash_usd"], 5.0)

        view = self.ai.public_payload()
        ids = [row["id"] for row in view["scoreboard"]]
        self.assertEqual(set(ids), {"YOU", "VECTOR", "LEDGER"})
        pcts = [row["pnl_pct"] for row in view["scoreboard"]]
        self.assertEqual(pcts, sorted(pcts, reverse=True))
        self.assertEqual(view["scoreboard"][0]["id"], "VECTOR")
        for row in view["scoreboard"]:
            self.assertIn("pnl_usd", row)
            self.assertGreaterEqual(row["max_drawdown_pct"], 0)
        self.assertGreaterEqual(len(view["series"]["VECTOR"]), 2)
        self.assertGreaterEqual(len(view["series"]["LEDGER"]), 2)
        self.assertTrue(view["settings_log"])
        for row in view["settings_log"]:
            self.assertIn("train", row)
            self.assertIn("validate", row)
            self.assertIn("current", row["train"])
            self.assertIn("candidate", row["validate"])

        # A fill before the 3s delay does not happen, and a stale or Jupiter quote is refused.
        self.ai.reset_for_tests()
        self.ai.set_verdict_fn(verdict)
        early = self._quote_factory()
        self.ai.step(now=0, quote_fn=early, universe=rows)
        self.ai.step(now=2.9, quote_fn=early, universe=rows)
        self.assertEqual(self.ai._block["traders"]["VECTOR"]["session"]["positions"], [])
        self.ai.step(now=3, quote_fn=early, universe=rows)
        self.assertEqual(len(self.ai._block["traders"]["VECTOR"]["session"]["positions"]), 1)

        self.ai.reset_for_tests()
        self.ai.set_verdict_fn(verdict)
        self.ai.step(now=0, quote_fn=self._quote_factory({"kind": "jupiter"}), universe=rows)
        self.ai.step(now=3, quote_fn=self._quote_factory({"kind": "jupiter"}), universe=rows)
        self.assertEqual(self.ai._block["traders"]["VECTOR"]["session"]["positions"], [])
        refused = self.ai._block["proofs"][0]
        self.assertFalse(refused["accepted"])
        self.assertTrue(refused["jupiter"])
        self.assertFalse(refused["fresh_ok"])
        self.assertGreaterEqual(refused["delay_s"], 3)

        self.ai.reset_for_tests()
        self.ai.set_verdict_fn(verdict)
        self.ai.step(now=0, quote_fn=self._quote_factory({"kind": "stale"}), universe=rows)
        self.ai.step(now=3, quote_fn=self._quote_factory({"kind": "stale"}), universe=rows)
        self.assertEqual(self.ai._block["traders"]["VECTOR"]["session"]["positions"], [])
        stale = self.ai._block["proofs"][0]
        self.assertFalse(stale["accepted"])
        self.assertFalse(stale["fresh_ok"])
        self.assertFalse(stale["jupiter"])
        self.assertGreaterEqual(stale["delay_s"], 3)

    def test_six_buys_per_hour_is_logged_and_blocks_the_seventh(self):
        mints = self._live_mints()
        rows = [self._rows(mints)[0]]

        def verdict(mint):
            return "SKIP" if mint == mints[0] else "UNKNOWN"

        self.ai.set_verdict_fn(verdict)
        quote = self._quote_factory()
        with self.assertLogs("nunu.ai_traders", level="INFO") as logs:
            for t in range(0, 80):
                self.ai.step(now=float(t), quote_fn=quote, universe=rows)
                buys = [
                    p for p in self.ai._block["proofs"]
                    if p.get("ai") == "VECTOR" and p.get("kind") == "buy" and p.get("accepted")
                ]
                refused = [
                    p for p in self.ai._block["proofs"]
                    if p.get("ai") == "VECTOR" and p.get("kind") == "buy" and p.get("reason") == "trade cap"
                ]
                if len(buys) >= 6 and refused:
                    break
            else:
                self.fail("cap was not reached")
        self.assertEqual(len(buys), 6)
        for proof in buys:
            self.assertTrue(proof["fresh_ok"])
            self.assertTrue(proof["delay_ok"])
            self.assertGreaterEqual(proof["delay_s"], 3)
            self.assertLess(proof["trades_this_hour"], 6)
        cap = refused[0]
        self.assertFalse(cap["accepted"])
        self.assertFalse(cap["cap_ok"])
        self.assertEqual(cap["trades_this_hour"], 6)
        self.assertEqual(cap["cap"], 6)
        self.assertTrue(cap["delay_ok"])
        self.assertGreaterEqual(cap["delay_s"], 3)
        blob = "\n".join(logs.output)
        self.assertIn("cap_ok=False", blob)
        self.assertIn("trades_this_hour=6", blob)
        self.assertEqual(len(self.ai._block["traders"]["LEDGER"]["buy_times"]), 0)

    def test_settings_log_keeps_and_rejects_with_backtest_results(self):
        base = {"stop_pct": 20, "tp_pct": 100, "min_verdict": "BUY-WATCH", "hold_min": 20}
        declines = [_receipt(i, DECLINE) for i in range(6)]
        kept = self.ai.judge_change("VECTOR", base, declines, declines[-1])
        again = self.ai.judge_change("VECTOR", base, declines, declines[-1])
        self.assertEqual(kept["applied"], again["applied"])
        self.assertEqual(kept["after"], again["after"])
        self.assertTrue(kept["applied"])
        self.assertEqual(kept["setting"], "stop_pct")
        self.assertEqual(kept["before"], 20)
        self.assertEqual(kept["after"], 5)
        self.assertLessEqual(kept["validate"]["candidate"]["max_drawdown_pct"], 20)
        self.assertGreater(
            kept["validate"]["candidate"]["pnl_usd"],
            kept["validate"]["current"]["pnl_usd"],
        )
        self.assertGreater(
            kept["train"]["candidate"]["pnl_usd"],
            kept["train"]["current"]["pnl_usd"],
        )

        mixed = [_receipt(i, DECLINE) for i in range(4)] + [_receipt(i, RALLY) for i in range(4, 6)]
        rejected = self.ai.judge_change("VECTOR", base, mixed, mixed[-1])
        self.assertFalse(rejected["applied"])
        self.assertIn("validation", rejected["reject_reason"])
        self.assertIsNotNone(rejected["train"]["current"]["pnl_usd"])
        self.assertIsNotNone(rejected["validate"]["candidate"]["pnl_usd"])
        self.assertLess(
            rejected["validate"]["candidate"]["pnl_usd"],
            rejected["validate"]["current"]["pnl_usd"],
        )

        wide = {"stop_pct": 20, "tp_pct": 100, "min_verdict": "BUY-WATCH", "hold_min": 60}
        paths = [_receipt(i, LOSS, hold_s=10) for i in range(4)]
        paths += [_receipt(i, RECOVER, hold_s=10) for i in range(4, 8)]
        paths += [_receipt(i, LOSS, hold_s=10) for i in range(8, 11)]
        paths.append(_receipt(11, RECOVER, hold_s=10))
        deep = self.ai.judge_change("VECTOR", wide, paths, paths[-1])
        self.assertFalse(deep["applied"])
        self.assertIn("drawdown", deep["reject_reason"])
        self.assertGreater(deep["validate"]["candidate"]["pnl_usd"], deep["validate"]["current"]["pnl_usd"])
        self.assertGreater(deep["validate"]["candidate"]["max_drawdown_pct"], 20)
        self.assertLessEqual(deep["train"]["current"]["max_drawdown_pct"], 100)

        short = self.ai.judge_change("LEDGER", base, declines[:2], declines[0])
        self.assertFalse(short["applied"])
        self.assertIn("3", short["reject_reason"])
        self.assertIn("train", short)
        self.assertIn("validate", short)
        self.assertIsNone(short["validate"]["candidate"]["pnl_usd"])

    def test_balances_reset_each_24h_and_settings_persist(self):
        mints = self._live_mints()
        rows = self._rows(mints)

        def verdict(mint):
            return "SKIP" if mint == mints[0] else "BUY-WATCH"

        self.ai.set_verdict_fn(verdict)
        quote = self._quote_factory({"kind": "flat"})
        self.ai.step(now=1000, quote_fn=quote, universe=rows)
        self.ai.step(now=1003, quote_fn=quote, universe=rows)
        trader = self.ai._block["traders"]["VECTOR"]
        self.assertEqual(len(trader["session"]["positions"]), 1)
        self.assertLess(trader["session"]["cash_usd"], 5)
        old = trader["session"]["session_id"]
        trader["settings"]["stop_pct"] = 35
        ends = trader["session"]["ends_epoch"]
        self.ai.step(now=ends + 1, quote_fn=quote, universe=rows)
        trader = self.ai._block["traders"]["VECTOR"]
        self.assertNotEqual(trader["session"]["session_id"], old)
        self.assertEqual(trader["session"]["cash_usd"], 5.0)
        self.assertEqual(trader["session"]["positions"], [])
        self.assertEqual(trader["settings"]["stop_pct"], 35)
        self.assertTrue(trader["receipts"])
        raw = json.loads(game.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["ai_traders"]["traders"]["VECTOR"]["settings"]["stop_pct"], 35)
        # An owner rewrite must not drop the tuned block.
        st = game.load_state()
        st["cash_usd"] = 4.25
        game.save_state(st)
        kept = json.loads(game.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(kept["cash_usd"], 4.25)
        self.assertEqual(kept["ai_traders"]["traders"]["VECTOR"]["settings"]["stop_pct"], 35)

    def test_health_and_ai_route_do_not_trade(self):
        import server

        client = server.app.test_client()
        warm = client.get("/api/health")
        self.assertEqual(warm.get_json()["panel_v"], 103)
        self.assertIn("ai_traders", warm.get_json()["features"])
        with mock.patch.object(self.ai, "step") as stepped:
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            body = client.get("/api/game/ai").get_json()
            game.tick_session()
            self.assertFalse(stepped.called)
        self.assertTrue(body["ok"])
        self.assertEqual(body["reaction_delay_s"], 3)
        self.assertEqual(body["trade_cap_per_hour"], 6)
        ids = [row["id"] for row in body["scoreboard"]]
        self.assertEqual(ids, ["YOU", "VECTOR", "LEDGER"])


if __name__ == "__main__":
    unittest.main()
