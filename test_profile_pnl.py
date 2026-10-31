"""SHIP AD — profile paper P/L from Z receipts (AC-AD01…AD03).

panel_v is not bumped. Numbers come from receipt sells, not the closed book
and not the live equity mark.
"""

from __future__ import annotations

import json
import subprocess
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import game


MINT = "3KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
NOW = datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tick(mint: str, price: float, age: float) -> dict:
    return {
        "mint": mint,
        "price_usd": price,
        "quote_age_s": age,
        "quote_live": True,
        "price_ts": "2026-10-31T16:00:00Z",
        "quoted_epoch": time.time() - age,
        "source": "jup",
    }


def _manual_pnl(receipts: list[dict]) -> float:
    total = 0.0
    for rec in receipts:
        for sell in rec.get("sells") or []:
            if isinstance(sell, dict) and sell.get("pnl_usd") is not None:
                total += float(sell["pnl_usd"])
    return round(total, 6)


def _manual_trade_pnl(rec: dict) -> float:
    return round(sum(float(s["pnl_usd"]) for s in rec.get("sells") or [] if isinstance(s, dict)), 6)


class ProfilePnlBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for patch in (
            mock.patch.object(game, "PROFILE_PATH", root / "profile.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
            mock.patch.object(game, "STATE_PATH", root / "state.json"),
            mock.patch.object(game, "FLAGGED_PATH", root / "flagged.json"),
            mock.patch.object(game, "fetch_dex_token", return_value={
                "symbol": "NVD",
                "name": "Nvidia",
                "price_usd": 1.0,
                "pair_url": "https://dexscreener.com/solana/nvdia-pair",
            }),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.prices = {MINT: (1.0, 0.2), game.SOL_MINT: (150.0, 0.4)}

        def fake_tick(mint, bypass_cache=False, qa_sid=None):
            px, age = self.prices.get(mint, (None, None))
            if px is None:
                return None
            return _tick(mint, px, age)

        tick_patch = mock.patch.object(game, "fetch_live_tick", side_effect=fake_tick)
        tick_patch.start()
        self.addCleanup(tick_patch.stop)

    def _buy_sell(self, exit_px: float, entry: float = 1.0, stake: float = 1.0, fraction=None):
        self.prices[MINT] = (entry, 0.2)
        bought = game.buy(MINT, stake)
        self.assertTrue(bought["ok"], bought)
        pid = bought["position"]["id"]
        self.prices[MINT] = (exit_px, 0.2)
        if fraction is None:
            sold = game.sell(pid)
        else:
            sold = game.sell(pid, fraction=fraction)
        self.assertTrue(sold["ok"], sold)
        return bought, sold

    def _five_closed_and_one_open(self):
        game.start_session(force=True)
        self._buy_sell(1.0)          # flat 0
        self._buy_sell(0.6)          # loss -0.4
        self._buy_sell(1.5)          # win +0.5
        bought, first = self._buy_sell(1.4, fraction=0.5)
        self.prices[MINT] = (0.8, 0.2)
        second = game.sell(bought["position"]["id"], fraction=0.5)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["receipt"]["status"], "closed")
        self._buy_sell(2.0)          # win +1.0
        self._buy_sell(1.2, fraction=0.5)  # open partial +0.1
        return game.load_state()

    def _stamp(self, st: dict) -> None:
        """Pin hold and calendar days so the manual sum is a constant."""
        plan = {
            ("closed", 0.0): ("2026-10-29T12:00:00Z", "2026-10-31T16:00:00Z", 30),
            ("closed", -0.4): ("2026-10-30T16:00:00Z", "2026-10-30T16:00:00Z", 90),
            ("closed", 0.5): ("2026-10-31T16:01:00Z", "2026-10-31T16:00:00Z", 120),
            ("closed", 0.1): ("2026-10-31T16:02:00Z", "2026-10-31T16:00:00Z", 600),
            ("closed", 1.0): ("2026-10-31T16:03:00Z", "2026-10-31T16:00:00Z", 3600),
            ("open", 0.1): (None, "2026-10-31T16:00:00Z", None),
        }
        seen = set()
        for rec in game._receipts(st):
            pnl = round(_manual_trade_pnl(rec), 2)
            key = (rec.get("status"), pnl)
            self.assertIn(key, plan, rec)
            self.assertNotIn(key, seen)
            seen.add(key)
            closed_at, sell_at, held = plan[key]
            if closed_at:
                rec["closed_at"] = closed_at
            for sell in rec.get("sells") or []:
                sell["at"] = sell_at
            if held is not None and rec.get("sells"):
                rec["sells"][-1]["held_s"] = held
        self.assertEqual(len(seen), 6)
        game.save_state(st)

    def test_stats_match_a_manual_sum_of_at_least_five_trades(self):
        st = self._five_closed_and_one_open()
        self._stamp(st)
        st = game.load_state()
        receipts = [r for r in game._receipts(st) if isinstance(r, dict)]
        closed = [r for r in receipts if r.get("status") == "closed"]
        self.assertGreaterEqual(len(closed), 5)
        manual = _manual_pnl(receipts)
        self.assertAlmostEqual(manual, 1.3)
        self.assertAlmostEqual(_manual_pnl(closed), 1.2)

        stats = game.profile_pnl_stats(st, "lifetime", now=NOW)
        self.assertAlmostEqual(stats["pnl_usd"], manual)
        self.assertEqual(stats["trades"], len(closed))
        wins = [r for r in closed if _manual_trade_pnl(r) > 0.005]
        losses = [r for r in closed if _manual_trade_pnl(r) < -0.005]
        self.assertEqual(stats["wins"], len(wins))
        self.assertEqual(stats["losses"], len(losses))
        self.assertEqual(stats["flats"], len(closed) - len(wins) - len(losses))
        self.assertAlmostEqual(stats["win_rate"], len(wins) / len(closed))
        self.assertEqual(stats["wins"], 3)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["flats"], 1)

        best = max(closed, key=_manual_trade_pnl)
        worst = min(closed, key=_manual_trade_pnl)
        self.assertEqual(stats["best"]["receipt_id"], best["id"])
        self.assertEqual(stats["worst"]["receipt_id"], worst["id"])
        self.assertAlmostEqual(stats["best"]["pnl_usd"], 1.0)
        self.assertAlmostEqual(stats["worst"]["pnl_usd"], -0.4)
        self.assertEqual(stats["streak"], 3)
        self.assertEqual(stats["streak_label"], "3 wins")
        self.assertEqual(stats["avg_hold_s"], 888)
        self.assertEqual(stats["avg_hold"], "14m 48s")

        by_day = {row["date"]: row["pnl_usd"] for row in stats["days"]}
        self.assertEqual(len(stats["days"]), 7)
        self.assertEqual(stats["days"][0]["date"], "2026-10-25")
        self.assertEqual(stats["days"][-1]["date"], "2026-10-31")
        self.assertEqual(stats["days"][-1]["label"], "Sat")
        self.assertAlmostEqual(by_day["2026-10-30"], -0.4)
        self.assertAlmostEqual(by_day["2026-10-31"], 1.7)
        self.assertAlmostEqual(sum(row["pnl_usd"] for row in stats["days"]), manual)
        for row in stats["days"]:
            if row["date"] not in ("2026-10-30", "2026-10-31"):
                self.assertEqual(row["pnl_usd"], 0.0)

        # The closed-trade book is not an input. A receipt sell is.
        tweaked = dict(st)
        tweaked["closed"] = [dict(st["closed"][0], pnl_usd=999.0)]
        self.assertAlmostEqual(
            game.profile_pnl_stats(tweaked, "lifetime", now=NOW)["pnl_usd"],
            manual,
        )
        bumped = json.loads(json.dumps(st))
        bumped["receipts"][0]["sells"][0]["pnl_usd"] = float(bumped["receipts"][0]["sells"][0]["pnl_usd"]) + 1
        self.assertAlmostEqual(
            game.profile_pnl_stats(bumped, "lifetime", now=NOW)["pnl_usd"],
            manual + 1,
        )

        view = game.public_state(st)
        self.assertAlmostEqual(view["pnl_stats"]["lifetime"]["pnl_usd"], manual)
        self.assertEqual(view["pnl_stats"]["lifetime"]["trades"], 5)
        self.assertEqual(view["pnl_stats"]["run"]["trades"], 5)

    def test_lifetime_survives_24h_reset_and_this_run_clears(self):
        game.start_session(force=True)
        self._buy_sell(1.5)
        self._buy_sell(0.5)
        self._buy_sell(1.2)
        self._buy_sell(0.8)
        bought, _first = self._buy_sell(1.1, fraction=0.5)
        self.prices[MINT] = (0.9, 0.2)
        closed = game.sell(bought["position"]["id"])
        self.assertTrue(closed["ok"], closed)
        self.assertEqual(game.load_state()["positions"], [])
        st = game.load_state()
        receipts = [r for r in game._receipts(st) if isinstance(r, dict)]
        closed_rows = [r for r in receipts if r.get("status") == "closed"]
        self.assertGreaterEqual(len(closed_rows), 5)
        before = game.profile_pnl_stats(st, "lifetime", now=NOW)
        self.assertAlmostEqual(before["pnl_usd"], _manual_pnl(receipts))
        self.assertEqual(before["trades"], 5)
        old_sid = st["session_id"]

        fresh = game.start_session(force=True)
        self.assertEqual(fresh["status"], "active")
        self.assertNotEqual(fresh["session_id"], old_sid)
        self.assertEqual(fresh["positions"], [])
        self.assertEqual(fresh["cash_usd"], 5.0)
        self.assertEqual(len(game._receipts(fresh)), len(receipts))
        life = game.profile_pnl_stats(fresh, "lifetime", now=NOW)
        run = game.profile_pnl_stats(fresh, "run", now=NOW)
        self.assertEqual(life["pnl_usd"], before["pnl_usd"])
        self.assertEqual(life["trades"], before["trades"])
        self.assertEqual(life["wins"], before["wins"])
        self.assertEqual(life["best"]["receipt_id"], before["best"]["receipt_id"])
        self.assertEqual(life["worst"]["receipt_id"], before["worst"]["receipt_id"])
        self.assertEqual(life["streak"], before["streak"])
        self.assertEqual(life["days"], before["days"])
        self.assertEqual(run["trades"], 0)
        self.assertEqual(run["pnl_usd"], 0.0)
        self.assertIsNone(run["win_rate"])
        self.assertIsNone(run["best"])
        self.assertIsNone(run["worst"])
        self.assertEqual(run["streak"], 0)
        self.assertTrue(all(row["pnl_usd"] == 0.0 for row in run["days"]))

        blank = dict(fresh)
        blank["session_id"] = None
        self.assertEqual(game.profile_pnl_stats(blank, "run", now=NOW)["trades"], 0)
        self.assertEqual(game.profile_pnl_stats(blank, "lifetime", now=NOW)["trades"], 5)

        self.prices[MINT] = (1.0, 0.2)
        nxt = game.buy(MINT, 1.0)
        self.assertTrue(nxt["ok"], nxt)
        self.prices[MINT] = (1.25, 0.2)
        sold = game.sell(nxt["position"]["id"])
        self.assertTrue(sold["ok"], sold)
        book = game.load_state()
        run2 = game.profile_pnl_stats(book, "run", now=NOW)
        life2 = game.profile_pnl_stats(book, "lifetime", now=NOW)
        self.assertEqual(run2["trades"], 1)
        self.assertAlmostEqual(run2["pnl_usd"], 0.25)
        self.assertEqual(run2["best"]["receipt_id"], sold["receipt"]["id"])
        self.assertEqual(run2["worst"]["receipt_id"], sold["receipt"]["id"])
        self.assertAlmostEqual(life2["pnl_usd"], before["pnl_usd"] + 0.25)
        self.assertEqual(life2["trades"], before["trades"] + 1)
        self.assertEqual(life2["best"]["receipt_id"], before["best"]["receipt_id"])
        view = game.public_state(book)
        self.assertEqual(view["pnl_stats"]["run"]["trades"], 1)
        self.assertEqual(view["pnl_stats"]["lifetime"]["trades"], before["trades"] + 1)


class ProfilePnlPanelTests(unittest.TestCase):
    def test_health_and_panel_keep_panel_v_and_wire_the_receipt(self):
        import server

        data = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 84)
        self.assertIn("profile_pnl", data["features"])
        self.assertIn("game_receipts", data["features"])
        self.assertIn("buy receipts", data["profile_pnl"])
        self.assertIn("24h reset", data["profile_pnl"])

        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 84", html)
        self.assertIn("paper only · v84", html)
        self.assertIn('id="gPnlCard"', html)
        self.assertIn('data-feature="profile_pnl"', html)
        self.assertIn('id="gPnlBest"', html)
        self.assertIn('id="gPnlWorst"', html)
        self.assertIn('data-extreme="best"', html)
        self.assertIn('data-extreme="worst"', html)
        self.assertIn('id="gPnlLife"', html)
        self.assertIn('id="gPnlRun"', html)
        self.assertIn("function openPnlExtreme", html)
        self.assertIn("function receiptForExtreme", html)
        pure = html[html.index("/* ship-ad-pure-start */"):html.index("/* ship-ad-pure-end */")]
        script = pure + r"""
const stats = {
  best: { receipt_id: "aaa", symbol: "NVD", pnl_usd: 1 },
  worst: { receipt_id: "bbb", symbol: "NOPE", pnl_usd: -0.4 },
  wins: 3,
  trades: 5,
};
const receipts = [{ id: "aaa", symbol: "NVD" }, { id: "bbb", symbol: "NOPE" }];
if (!receiptForExtreme(receipts, stats, "best") || receiptForExtreme(receipts, stats, "best").id !== "aaa") process.exit(2);
if (!receiptForExtreme(receipts, stats, "worst") || receiptForExtreme(receipts, stats, "worst").id !== "bbb") process.exit(3);
if (receiptForExtreme(receipts, { best: null }, "best")) process.exit(4);
if (fmtWinRate(stats) !== "60%") process.exit(5);
if (fmtSignedUsd(1.3) !== "+$1.30") process.exit(6);
if (fmtSignedUsd(-0.4) !== "−$0.40") process.exit(7);
if (fmtSignedUsd(0) !== "$0.00") process.exit(8);
const run = pnlScopeStats({ pnl_stats: { lifetime: { trades: 5 }, run: { trades: 0 } } }, "run");
if (!run || run.trades !== 0) process.exit(9);
const life = pnlScopeStats({ pnl_stats: { lifetime: { trades: 5 }, run: { trades: 0 } } }, "lifetime");
if (!life || life.trades !== 5) process.exit(10);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
