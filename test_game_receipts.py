"""SHIP Z — paper buy receipts (AC-Z01…Z06). panel_v stays at main."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import game


MINT = "3KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
SKIP_MINT = "4KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tick(mint: str, price: float, age: float, ts: str = "2026-10-31T14:20:05Z") -> dict:
    return {
        "mint": mint,
        "price_usd": price,
        "quote_age_s": age,
        "quote_live": True,
        "price_ts": ts,
        "quoted_epoch": time.time() - age,
        "source": "jup",
    }


class ReceiptBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.root = root
        self.flagged = root / "flagged.json"
        for patch in (
            mock.patch.object(game, "PROFILE_PATH", root / "profile.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
            mock.patch.object(game, "STATE_PATH", root / "state.json"),
            mock.patch.object(game, "FLAGGED_PATH", self.flagged),
            mock.patch.object(game, "fetch_dex_token", return_value={
                "symbol": "NVD",
                "name": "Nvidia",
                "price_usd": 0.000112,
                "pair_url": "https://dexscreener.com/solana/nvdia-pair",
            }),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.prices = {MINT: (0.0000785, 1.25), game.SOL_MINT: (150.0, 0.4)}

        def fake_tick(mint, bypass_cache=False):
            px, age = self.prices.get(mint, (None, None))
            if px is None:
                return None
            return _tick(mint, px, age)

        tick_patch = mock.patch.object(game, "fetch_live_tick", side_effect=fake_tick)
        tick_patch.start()
        self.addCleanup(tick_patch.stop)

    def _buy(self, mint=MINT, stake=1.0, points=None, **kw):
        game.start_session(force=True)
        return game.buy(mint, stake, sl_pct=20, tp_pct=100, points=points, **kw)

    def test_buy_receipt_freezes_fill_price_and_quote_age(self):
        points = [0.00007 + i * 0.0000001 for i in range(70)]
        out = self._buy(points=points)
        self.assertTrue(out["ok"], out)
        rec = out["receipt"]
        tick_age = 1.25
        self.assertEqual(rec["quote_age_s"], tick_age)
        self.assertEqual(rec["quote_age_s"], game.fill_quote_age_s(_tick(MINT, 0.0000785, tick_age)))
        self.assertAlmostEqual(rec["entry_price_usd"], 0.0000785)
        self.assertAlmostEqual(rec["entry_price_usd"], out["position"]["entry_price_usd"])
        self.assertAlmostEqual(rec["entry_price_sol"], 0.0000785 / 150.0)
        self.assertAlmostEqual(rec["size_usd"], 1.0)
        self.assertAlmostEqual(rec["remaining_usd"], 1.0)
        self.assertTrue(rec["time_et"].endswith("ET"))
        self.assertIn("2026", rec["time_et"])
        self.assertAlmostEqual(rec["stop_price_usd"], 0.0000785 * 0.8)
        self.assertAlmostEqual(rec["tp_price_usd"], 0.0000785 * 2.0)
        self.assertEqual(rec["verdict"], "unknown")
        self.assertFalse(rec["stale"])
        self.assertEqual(rec["status"], "open")
        self.assertEqual(len(rec["points"]), 60)
        self.assertEqual(rec["points"][0], points[-60])
        self.assertEqual(rec["points"][-1], points[-1])
        self.assertEqual(rec["fill_point"]["p"], rec["entry_price_usd"])
        self.assertNotIn("png", json.dumps(rec).lower())
        saved = game.load_state()["receipts"][0]
        self.assertEqual(saved["quote_age_s"], tick_age)
        self.assertEqual(saved["id"], rec["id"])

    def test_receipts_survive_reload_and_the_24h_reset(self):
        out = self._buy()
        rid = out["receipt"]["id"]
        game.save_state(game.load_state())
        again = game.load_state()
        self.assertEqual(again["receipts"][0]["id"], rid)

        code = (
            "import json, game\n"
            "from pathlib import Path\n"
            f"game.STATE_PATH = Path({str(game.STATE_PATH)!r})\n"
            "st = game.load_state()\n"
            "print(json.dumps([r.get('id') for r in st.get('receipts') or []]))\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout.strip()), [rid])

        fresh = game.start_session(force=True)
        self.assertEqual(fresh["status"], "active")
        self.assertNotEqual(fresh["session_id"], out["state"]["session_id"])
        self.assertEqual(fresh["positions"], [])
        self.assertEqual(fresh["cash_usd"], 5.0)
        self.assertEqual(fresh["closed"], [])
        self.assertEqual([r["id"] for r in fresh["receipts"]], [rid])
        self.assertEqual(game.load_state()["receipts"][0]["entry_price_usd"], out["receipt"]["entry_price_usd"])

    def test_two_half_sells_make_two_rows_and_close(self):
        self.prices[MINT] = (1.0, 0.2)
        out = self._buy(stake=2.0)
        self.assertTrue(out["ok"], out)
        pid = out["position"]["id"]
        self.prices[MINT] = (1.5, 0.3)
        first = game.sell(pid, fraction=0.5)
        self.assertTrue(first["ok"], first)
        rec = first["receipt"]
        self.assertEqual(rec["status"], "open")
        self.assertEqual(len(rec["sells"]), 1)
        self.assertAlmostEqual(rec["sells"][0]["size_usd"], 1.0)
        self.assertAlmostEqual(rec["sells"][0]["exit_price_usd"], 1.5)
        self.assertAlmostEqual(rec["sells"][0]["pnl_usd"], 0.5)
        self.assertAlmostEqual(rec["remaining_usd"], 1.0)
        self.assertEqual(len(first["state"]["positions"]), 1)
        self.assertAlmostEqual(first["state"]["positions"][0]["stake_usd"], 1.0)

        self.prices[MINT] = (0.9, 0.4)
        second = game.sell(pid, fraction=0.5)
        self.assertTrue(second["ok"], second)
        rec = second["receipt"]
        self.assertEqual(rec["status"], "closed")
        self.assertEqual(rec["remaining_usd"], 0)
        self.assertEqual(len(rec["sells"]), 2)
        self.assertAlmostEqual(rec["sells"][1]["size_usd"], 1.0)
        self.assertAlmostEqual(rec["sells"][1]["exit_price_usd"], 0.9)
        self.assertAlmostEqual(rec["sells"][1]["pnl_usd"], -0.1)
        self.assertEqual(rec["sells"][1]["exit_reason"], "manual")
        self.assertTrue(rec["sells"][1]["held"])
        self.assertEqual(second["state"]["positions"], [])
        self.assertEqual(len(second["state"]["closed"]), 1)
        self.assertAlmostEqual(second["state"]["closed"][0]["pnl_usd"], 0.4)
        self.assertAlmostEqual(second["state"]["cash_usd"], 5.4)

    def test_skip_verdict_is_stored_and_unknown_when_missing(self):
        self.flagged.write_text(json.dumps([
            {"mint": MINT, "score_12": 8.4, "trench_band": False, "ticker": "NVD"},
        ]), encoding="utf-8")
        out = self._buy()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["receipt"]["verdict"], "SKIP")

        self.flagged.write_text(json.dumps([
            {"mint": MINT, "ticker": "NVD"},
        ]), encoding="utf-8")
        out = self._buy()
        self.assertEqual(out["receipt"]["verdict"], "unknown")

        self.flagged.write_text(json.dumps([
            {
                "mint": MINT,
                "score_12": 11.2,
                "trench_band": True,
                "gold_scar": {"gold": True},
            },
        ]), encoding="utf-8")
        out = self._buy()
        self.assertEqual(out["receipt"]["verdict"], "GOLD")

        self.flagged.write_text("[]", encoding="utf-8")
        self.prices[SKIP_MINT] = (0.00004, 0.8)
        out = self._buy(mint=SKIP_MINT)
        self.assertEqual(out["receipt"]["verdict"], "unknown")

    def test_review_cites_receipt_fields_for_this_run(self):
        self.flagged.write_text(json.dumps([
            {"mint": MINT, "score_12": 7.0, "trench_band": False, "ticker": "NVD"},
        ]), encoding="utf-8")
        self.prices[MINT] = (1.0, 1.25)
        bought = self._buy(stake=1.0)
        pid = bought["position"]["id"]
        self.prices[MINT] = (1.4, 0.2)
        sold = game.sell(pid, fraction=0.5)
        self.assertEqual(sold["receipt"]["status"], "open")
        ended = game.force_end_session()
        self.assertEqual(ended["status"], "ended")
        rec = game.load_state()["receipts"][0]
        self.assertEqual(rec["status"], "closed")
        self.assertEqual(len(rec["sells"]), 2)
        view = game.public_state()
        review = view["review"]
        text = " ".join(
            [review.get("headline") or "", review.get("summary") or ""]
            + [b.get("text") or "" for b in review.get("bullets") or []]
        )
        self.assertEqual(review["status"], "ready")
        self.assertIn("NVD", text)
        self.assertIn("SKIP", text)
        self.assertIn("1.25", text)
        self.assertIn("receipt", text.lower())
        self.assertTrue(review["receipts"])
        self.assertEqual(review["receipts"][0]["verdict"], "SKIP")
        self.assertEqual(review["receipts"][0]["quote_age_s"], 1.25)
        self.assertEqual(review["cited"][0]["verdict"], "SKIP")
        self.assertEqual(review["cited"][0]["quote_age_s"], 1.25)
        fresh = game.start_session(force=True)
        self.assertEqual(game.public_state(fresh)["review"]["status"], "none")
        self.assertEqual(len(fresh["receipts"]), 1)

    def test_health_keeps_panel_v_and_names_receipts(self):
        import server

        data = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 84)
        self.assertIn("game_receipts", data["features"])
        self.assertIn("live_tick", data["features"])
        self.assertIn("game_state.json", data["game_receipts"])
        self.assertIn("?qa_receipt=1", data["qa_receipt"])


class ReceiptPanelTests(unittest.TestCase):
    def test_panel_draws_receipts_at_mains_panel_v(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 84", html)
        self.assertIn("paper only · v84", html)
        self.assertIn('id="gReceiptSheet"', html)
        self.assertIn('id="gReceiptSave"', html)
        self.assertIn('id="gReceiptCopy"', html)
        self.assertIn('id="gHistReceipts"', html)
        self.assertIn('id="gSkipWarn"', html)
        self.assertIn('data-feature="game_receipts"', html)
        self.assertIn("function receiptCopyText", html)
        self.assertIn("function receiptChartGeometry", html)
        self.assertIn("function skipBuyDecision", html)
        self.assertIn("function coinVerdict", html)
        pure = html[html.index("/* ship-z-pure-start */"):html.index("/* ship-z-pure-end */")]
        script = pure + r"""
const skip = coinVerdict({ score_12: 8.4, trench_band: false });
if (skip !== "SKIP") process.exit(2);
const play = coinVerdict({ score_12: 10.2, trench_band: true });
if (play !== "PLAY") process.exit(3);
const gold = coinVerdict({ score_12: 11, gold_scar: { gold: true } });
if (gold !== "GOLD") process.exit(4);
if (coinVerdict(null) !== "unknown") process.exit(5);
if (coinVerdict({ ticker: "X" }) !== "unknown") process.exit(6);
const first = skipBuyDecision("SKIP", false);
if (first.proceed || !first.needConfirm || !first.warn) process.exit(7);
const second = skipBuyDecision("SKIP", true);
if (!second.proceed || second.needConfirm || second.verdict !== "SKIP") process.exit(8);
const plain = skipBuyDecision("PLAY", false);
if (!plain.proceed || plain.warn) process.exit(9);
if (!receiptIsStale(5.01) || receiptIsStale(5) || receiptIsStale(null)) process.exit(10);
const rec = {
  symbol: "NVD",
  entry_price_usd: 0.0000785,
  entry_price_sol: 0.000000523,
  size_usd: 1,
  remaining_usd: 0.5,
  time_et: "Oct 31, 2026 2:20 AM ET",
  quote_age_s: 6.2,
  stop_price_usd: 0.0000628,
  tp_price_usd: 0.000157,
  verdict: "SKIP",
  status: "open",
  points: [0.00007, 0.000074, 0.000076],
  fill_point: { p: 0.0000785 },
  sells: [{ size_usd: 0.5, exit_price_usd: 0.00009, pnl_usd: 0.07, held: "4m 0s", exit_reason: "manual" }],
};
const text = receiptCopyText(rec);
["NVD", "0.0000785", "0.000000523", "Oct 31, 2026 2:20 AM ET", "6.2", "STALE", "SKIP", "0.0000628", "0.000157", "manual", "0.07"].forEach((bit) => {
  if (text.indexOf(bit) < 0) process.exit(11);
});
const geo = receiptChartGeometry(rec, 320, 120);
if (!geo.entry || !geo.line.length) process.exit(12);
const last = geo.line[geo.line.length - 1];
if (Math.abs(last.x - geo.entry.x) > 0.001 || Math.abs(last.y - geo.entry.y) > 0.001) process.exit(13);
if (geo.line.length !== 4) process.exit(14);
if (!(geo.stop > 0) || !(geo.tp > 0) || !(geo.tp < geo.stop)) process.exit(15);
if (!receiptFileName(rec).endsWith(".png")) process.exit(16);
const dotOnly = receiptChartGeometry({ fill_point: { p: 1.25 }, points: [] }, 200, 80);
if (!dotOnly.entry) process.exit(17);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
