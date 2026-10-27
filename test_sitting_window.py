"""SHIP F Slice 1 — sitting drop (~5m) and the near-window band."""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import filter
import viral


def iso_at(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(mint: str, score: float = 70.0, ticker: str | None = None) -> dict:
    return {
        "mint": mint,
        "ticker": ticker or mint[:6],
        "score": score,
        "score_12": round(score / 100.0 * 12.0, 1),
        "flagged": True,
        "trench_band": score >= 83.4,
    }


class SittingWindowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = filter.FLAG_HISTORY_PATH
        filter.FLAG_HISTORY_PATH = Path(self._tmp.name) / "flag_history.json"

    def tearDown(self):
        filter.FLAG_HISTORY_PATH = self._orig
        self._tmp.cleanup()

    def test_constants_and_pair_age_stay_put(self):
        self.assertEqual(filter.SITTING_DROP_MIN, 5.0)
        self.assertEqual(filter.SITTING_NEAR_LEAD_MIN, 1.0)
        self.assertEqual(filter.SITTING_FRESH_MIN, 2.0)
        self.assertEqual(filter.MAX_PAIR_AGE_HOURS, 72.0)
        self.assertEqual(filter.MIN_SCORE_TO_FLAG, 55.0)
        self.assertEqual(filter.W_LIQUIDITY, 30.0)
        self.assertEqual(filter.W_VOLUME, 25.0)
        self.assertEqual(filter.W_HOLDERS, 25.0)
        self.assertEqual(filter.W_SOCIALS, 20.0)
        src = Path("filter.py").read_text(encoding="utf-8")
        self.assertIn("finalize_flagged(flagged, finished)", src)

    def test_drop_is_strictly_past_five_minutes(self):
        rows = [
            {"mint": "edge", "ticker": "EDGE", "score": 70, "flagged": True, "sitting_minutes": 5.0},
            {"mint": "over", "ticker": "OVER", "score": 72, "flagged": True, "sitting_minutes": 5.1},
            {"mint": "near", "ticker": "NEAR", "score": 75, "flagged": True, "sitting_minutes": 4.0},
            {"mint": "mid", "ticker": "MID", "score": 68, "flagged": True, "sitting_minutes": 3.6},
            {"mint": "new", "ticker": "NEW", "score": 81, "flagged": True, "sitting_minutes": 1.8},
            {"mint": "nosit", "ticker": "NOSIT", "score": 60, "flagged": True},
        ]
        kept, dropped = filter.drop_stale_sitters(rows)
        self.assertEqual([r["ticker"] for r in dropped], ["OVER"])
        self.assertFalse(dropped[0]["flagged"])
        self.assertTrue(dropped[0]["sitting_dropped"])
        self.assertEqual(dropped[0]["score"], 72)
        bands = {r["ticker"]: r["sitting_band"] for r in kept}
        self.assertEqual(bands["EDGE"], "near")
        self.assertEqual(bands["NEAR"], "near")
        self.assertEqual(bands["MID"], "inside")
        self.assertEqual(bands["NEW"], "fresh")
        self.assertEqual(bands["NOSIT"], "unknown")
        self.assertTrue(all(r["sitting_drop_min"] == 5.0 for r in kept))
        self.assertTrue(all(r["flagged"] for r in kept))

    def test_fourteen_minute_sitter_leaves_on_this_scan_and_the_next(self):
        now = time.time()
        maya = "MayaMint1111111111111111111111111111111111"
        ribbit = "RibbitMint111111111111111111111111111111"
        lunex = "LunexMint11111111111111111111111111111111"
        filter.update_flag_history([row(maya, 72, "MAYA")], iso_at(now - 14 * 60))
        filter.update_flag_history([row(lunex, 75, "LUNEX")], iso_at(now - 4 * 60))

        board = [row(maya, 72, "MAYA"), row(ribbit, 81, "RIBBIT"), row(lunex, 75, "LUNEX")]
        kept, dropped = filter.finalize_flagged(board, iso_at(now))

        self.assertEqual([r["ticker"] for r in dropped], ["MAYA"])
        self.assertGreater(dropped[0]["sitting_minutes"], filter.SITTING_DROP_MIN)
        self.assertEqual([r["ticker"] for r in kept], ["RIBBIT", "LUNEX"])
        self.assertEqual(kept[0]["sitting_band"], "fresh")
        self.assertLess(kept[0]["sitting_minutes"], filter.SITTING_FRESH_MIN)
        self.assertEqual(kept[1]["sitting_band"], "near")
        self.assertGreaterEqual(kept[1]["sitting_minutes"], 4.0)
        self.assertLessEqual(kept[1]["sitting_minutes"], filter.SITTING_DROP_MIN)
        self.assertEqual(kept[1]["score"], 75)

        again = [row(maya, 72, "MAYA"), row(ribbit, 81, "RIBBIT")]
        kept2, dropped2 = filter.finalize_flagged(again, iso_at(now + 60))
        self.assertEqual([r["ticker"] for r in dropped2], ["MAYA"])
        self.assertEqual([r["ticker"] for r in kept2], ["RIBBIT"])
        self.assertGreater(dropped2[0]["sitting_minutes"], 14)

    def test_five_point_zero_rounds_stay_and_five_point_one_go(self):
        now = float(int(time.time()))
        stay = "StayMint111111111111111111111111111111111"
        go = "GoMint11111111111111111111111111111111111"
        filter.update_flag_history([row(stay, 60, "STAY")], iso_at(now - 5 * 60))
        filter.update_flag_history([row(go, 60, "GO")], iso_at(now - (5 * 60 + 7)))
        kept, dropped = filter.finalize_flagged(
            [row(stay, 60, "STAY"), row(go, 60, "GO")],
            iso_at(now),
        )
        self.assertEqual([r["ticker"] for r in kept], ["STAY"])
        self.assertEqual(kept[0]["sitting_minutes"], 5.0)
        self.assertEqual(kept[0]["sitting_band"], "near")
        self.assertEqual([r["ticker"] for r in dropped], ["GO"])
        self.assertGreaterEqual(dropped[0]["sitting_minutes"], 5.1)


class HealthAndPanelTests(unittest.TestCase):
    def test_health_documents_the_sitting_constant(self):
        import server

        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["panel_v"], 70)
        self.assertIn("delay_warning", data["features"])
        self.assertIn("game_polish", data["features"])
        self.assertEqual(data["sitting_drop_min"], filter.SITTING_DROP_MIN)
        self.assertEqual(data["sitting_near_lead_min"], filter.SITTING_NEAR_LEAD_MIN)
        self.assertEqual(data["sitting_fresh_min"], filter.SITTING_FRESH_MIN)
        self.assertEqual(data["social_tap_fresh_min"], viral.SOCIAL_TAP_FRESH_MIN)
        self.assertIn("sitting_window", data["features"])
        self.assertIn("social_tap_fresh", data["features"])
        self.assertIn("game_finish", data["features"])
        self.assertIn("quote_sync", data["features"])
        self.assertEqual(data["quote_sla_s"], 15.0)
        self.assertEqual(data["qa_tick"], "page ?qa_tick=both|up|down")

    def test_panel_shows_sitting_and_honest_social_tap(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const SITTING_DROP_MIN = 5", html)
        self.assertIn("const SITTING_NEAR_LEAD_MIN = 1", html)
        self.assertIn("const SITTING_FRESH_MIN = 2", html)
        self.assertIn("const SOCIAL_TAP_FRESH_MIN = 5", html)
        self.assertIn(">Sitting<", html)
        self.assertIn("sitting drop 5m", html)
        self.assertIn('class="sit"', html)
        self.assertIn("sit-near", html)
        self.assertIn("near ~", html)
        self.assertIn("Social Tap is past the 5-min feed loop. Nothing invented.", html)
        self.assertIn('id="viralFresh"', html)
        self.assertIn("data-sitting-drop-min", html)
        self.assertIn("Nothing invented.", html)
        self.assertNotIn("stale chrome", html)
        band = html[html.index("function sittingDropMin"):html.index("function sittingHtml")]
        fresh = html[html.index("function feedLoopMin"):html.index("function setViralFresh")]
        script = (
            "const state = { latest: { thresholds: {} } };\n"
            "const SITTING_DROP_MIN = 5;\n"
            "const SITTING_NEAR_LEAD_MIN = 1;\n"
            "const SITTING_FRESH_MIN = 2;\n"
            "const SOCIAL_TAP_FRESH_MIN = 5;\n"
            + band
            + "\n"
            + fresh
            + r"""
function bandOf(mins) {
  return sittingBand({ sitting_minutes: mins });
}
if (bandOf(5) !== "near") process.exit(2);
if (bandOf(5.1) !== "over") process.exit(3);
if (bandOf(4) !== "near") process.exit(4);
if (bandOf(3.6) !== "inside") process.exit(5);
if (bandOf(1.8) !== "fresh") process.exit(6);
if (sittingBand({ sitting_minutes: null }) !== "unknown") process.exit(7);
if (sittingBand({ sitting_minutes: 14, sitting_band: "over" }) !== "over") process.exit(8);
if (sittingBand({ sitting_minutes: 4, sitting_band: "near" }) !== "near") process.exit(9);
const freshFeed = {
  updated_at: new Date(Date.now() - 120000).toISOString(),
  posts: [{ title: "x" }],
  fresh_sec: 300,
  stale: false,
};
if (socialTapPastLoop(freshFeed)) process.exit(10);
const idle = {
  updated_at: new Date(Date.now() - 42 * 60000).toISOString(),
  posts: [{ title: "idle" }],
  fresh_sec: 300,
  stale: false,
};
if (!socialTapPastLoop(idle)) process.exit(11);
const edge = {
  updated_at: new Date(Date.now() - 300000).toISOString(),
  posts: [{ title: "edge" }],
  fresh_sec: 300,
  stale: false,
};
if (socialTapPastLoop(edge)) process.exit(12);
"""
        )
        subprocess.check_call(["node", "-e", script])


if __name__ == "__main__":
    unittest.main()
