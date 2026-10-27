"""SHIP F sitting bands, retuned by SHIP S: keep sitters, age out without wiping."""

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
        self.assertEqual(filter.SITTING_AGED_MIN, 180.0)
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
        self.assertIn("def apply_sitting_rule(", src)
        self.assertNotIn('row["sitting_dropped"] = True', src)

    def test_past_five_minutes_stays_with_sitting_chrome(self):
        rows = [
            {"mint": "edge", "ticker": "EDGE", "score": 70, "flagged": True, "sitting_minutes": 5.0},
            {"mint": "over", "ticker": "OVER", "score": 72, "flagged": True, "sitting_minutes": 5.1},
            {"mint": "near", "ticker": "NEAR", "score": 75, "flagged": True, "sitting_minutes": 4.0},
            {"mint": "mid", "ticker": "MID", "score": 68, "flagged": True, "sitting_minutes": 3.6},
            {"mint": "new", "ticker": "NEW", "score": 81, "flagged": True, "sitting_minutes": 1.8},
            {"mint": "nosit", "ticker": "NOSIT", "score": 60, "flagged": True},
        ]
        kept, aged = filter.apply_sitting_rule(rows)
        self.assertEqual(aged, [])
        self.assertEqual(
            [r["ticker"] for r in kept],
            ["EDGE", "OVER", "NEAR", "MID", "NEW", "NOSIT"],
        )
        over = next(r for r in kept if r["ticker"] == "OVER")
        self.assertTrue(over["flagged"])
        self.assertTrue(over["sitting_kept"])
        self.assertFalse(over.get("sitting_dropped"))
        self.assertEqual(over["sitting_band"], "over")
        self.assertEqual(over["score"], 72)
        bands = {r["ticker"]: r["sitting_band"] for r in kept}
        self.assertEqual(bands["EDGE"], "near")
        self.assertEqual(bands["NEAR"], "near")
        self.assertEqual(bands["MID"], "inside")
        self.assertEqual(bands["NEW"], "fresh")
        self.assertEqual(bands["NOSIT"], "unknown")
        self.assertTrue(all(r["sitting_drop_min"] == 5.0 for r in kept))
        self.assertTrue(all(r["sitting_aged_min"] == 180.0 for r in kept))
        self.assertFalse(any(r.get("sitting_aged_hold") for r in kept))
        self.assertTrue(all(r["flagged"] for r in kept))

    def test_fourteen_minute_sitter_stays_on_this_scan_and_the_next(self):
        now = time.time()
        maya = "MayaMint1111111111111111111111111111111111"
        ribbit = "RibbitMint111111111111111111111111111111"
        lunex = "LunexMint11111111111111111111111111111111"
        filter.update_flag_history([row(maya, 72, "MAYA")], iso_at(now - 14 * 60))
        filter.update_flag_history([row(lunex, 75, "LUNEX")], iso_at(now - 4 * 60))

        board = [row(maya, 72, "MAYA"), row(ribbit, 81, "RIBBIT"), row(lunex, 75, "LUNEX")]
        kept, aged = filter.finalize_flagged(board, iso_at(now))

        self.assertEqual(aged, [])
        self.assertEqual([r["ticker"] for r in kept], ["MAYA", "RIBBIT", "LUNEX"])
        self.assertGreater(kept[0]["sitting_minutes"], filter.SITTING_DROP_MIN)
        self.assertEqual(kept[0]["sitting_band"], "over")
        self.assertTrue(kept[0]["sitting_kept"])
        self.assertTrue(kept[0]["flagged"])
        self.assertEqual(kept[0]["score"], 72)
        self.assertEqual(kept[1]["sitting_band"], "fresh")
        self.assertLess(kept[1]["sitting_minutes"], filter.SITTING_FRESH_MIN)
        self.assertEqual(kept[2]["sitting_band"], "near")
        self.assertGreaterEqual(kept[2]["sitting_minutes"], 4.0)
        self.assertLessEqual(kept[2]["sitting_minutes"], filter.SITTING_DROP_MIN)
        self.assertEqual(kept[2]["score"], 75)

        again = [row(maya, 72, "MAYA"), row(ribbit, 81, "RIBBIT")]
        kept2, aged2 = filter.finalize_flagged(again, iso_at(now + 60))
        self.assertEqual(aged2, [])
        self.assertEqual([r["ticker"] for r in kept2], ["MAYA", "RIBBIT"])
        self.assertGreater(kept2[0]["sitting_minutes"], 14)
        self.assertEqual(kept2[0]["sitting_band"], "over")
        self.assertTrue(kept2[0]["flagged"])

    def test_five_point_zero_stays_near_and_five_point_one_stays_sitting(self):
        now = float(int(time.time()))
        stay = "StayMint111111111111111111111111111111111"
        go = "GoMint11111111111111111111111111111111111"
        filter.update_flag_history([row(stay, 60, "STAY")], iso_at(now - 5 * 60))
        filter.update_flag_history([row(go, 60, "GO")], iso_at(now - (5 * 60 + 7)))
        kept, aged = filter.finalize_flagged(
            [row(stay, 60, "STAY"), row(go, 60, "GO")],
            iso_at(now),
        )
        self.assertEqual(aged, [])
        by = {r["ticker"]: r for r in kept}
        self.assertEqual(set(by), {"STAY", "GO"})
        self.assertEqual(by["STAY"]["sitting_minutes"], 5.0)
        self.assertEqual(by["STAY"]["sitting_band"], "near")
        self.assertTrue(by["STAY"]["flagged"])
        self.assertGreaterEqual(by["GO"]["sitting_minutes"], 5.1)
        self.assertEqual(by["GO"]["sitting_band"], "over")
        self.assertTrue(by["GO"]["sitting_kept"])
        self.assertTrue(by["GO"]["flagged"])

    def test_repro_cohort_at_5_1_minutes_stays_on_the_board(self):
        """Live panel_v 70: 10 coins at 5.1m were hard-dropped to flagged 0."""
        now = float(int(time.time()))
        names = [
            ("TRUMP2028", 98.0),
            ("CHICO", 98.0),
            ("WIF", 93.5),
            ("ELON2028", 86.8),
            ("HACHI", 77.0),
            ("N64", 70.0),
            ("Wifoween", 68.0),
            ("XCAT", 66.0),
            ("NETWORK", 62.0),
            ("PEAK", 60.0),
        ]
        mints = []
        seeded = []
        for i, (ticker, score) in enumerate(names):
            mint = f"Mint{i:02d}" + "1" * 32
            mints.append(mint)
            seeded.append(row(mint, score, ticker))
            filter.update_flag_history([row(mint, score, ticker)], iso_at(now - (5 * 60 + 6)))
        kept, aged = filter.finalize_flagged(
            [row(m, s, t) for (t, s), m in zip(names, mints)],
            iso_at(now),
        )
        self.assertEqual(aged, [])
        self.assertEqual(len(kept), 10)
        self.assertTrue(all(r["flagged"] for r in kept))
        self.assertTrue(all(r["sitting_band"] == "over" for r in kept))
        self.assertTrue(all(r["sitting_kept"] for r in kept))
        self.assertTrue(all(r["sitting_minutes"] >= 5.1 for r in kept))
        self.assertEqual(kept[0]["ticker"], "TRUMP2028")
        self.assertEqual(kept[0]["score"], 98.0)
        self.assertEqual(kept[1]["ticker"], "CHICO")
        again, aged2 = filter.finalize_flagged(
            [row(m, s, t) for (t, s), m in zip(names, mints)],
            iso_at(now + 60),
        )
        self.assertEqual(aged2, [])
        self.assertEqual(len(again), 10)
        self.assertTrue(all(r["flagged"] and r["sitting_band"] == "over" for r in again))

    def test_aged_chrome_does_not_remove_the_row(self):
        old = {"mint": "old", "ticker": "OLD", "score": 90, "flagged": True, "sitting_minutes": 180.1}
        young = {"mint": "young", "ticker": "NEW", "score": 80, "flagged": True, "sitting_minutes": 1.0}
        edge = {"mint": "edge180", "ticker": "EDGE", "score": 70, "flagged": True, "sitting_minutes": 180.0}
        kept, aged = filter.apply_sitting_rule([old, young, edge])
        self.assertEqual(aged, [])
        self.assertEqual([r["ticker"] for r in kept], ["OLD", "NEW", "EDGE"])
        by = {r["ticker"]: r for r in kept}
        self.assertTrue(by["OLD"]["flagged"])
        self.assertTrue(by["OLD"]["sitting_kept"])
        self.assertTrue(by["OLD"]["sitting_aged_hold"])
        self.assertEqual(by["OLD"]["sitting_band"], "over")
        self.assertFalse(by["EDGE"]["sitting_aged_hold"])
        self.assertEqual(by["EDGE"]["sitting_band"], "over")
        self.assertFalse(by["NEW"]["sitting_aged_hold"])
        self.assertEqual(by["NEW"]["sitting_band"], "fresh")
        self.assertTrue(all(r["flagged"] for r in kept))

        solo = {"mint": "solo", "ticker": "SOLO", "score": 98, "flagged": True, "sitting_minutes": 200.0}
        held, aged_solo = filter.apply_sitting_rule([dict(solo)])
        self.assertEqual(aged_solo, [])
        self.assertEqual(len(held), 1)
        self.assertTrue(held[0]["flagged"])
        self.assertTrue(held[0]["sitting_kept"])
        self.assertTrue(held[0]["sitting_aged_hold"])
        self.assertEqual(held[0]["sitting_band"], "over")

        cohort = [
            {"mint": f"c{i}", "ticker": f"C{i}", "score": 90 - i, "flagged": True, "sitting_minutes": 181.0}
            for i in range(4)
        ]
        held_all, aged_all = filter.apply_sitting_rule(cohort)
        self.assertEqual(aged_all, [])
        self.assertEqual(len(held_all), 4)
        self.assertTrue(all(r["sitting_aged_hold"] and r["flagged"] for r in held_all))


class HealthAndPanelTests(unittest.TestCase):
    def test_health_documents_the_sitting_constant(self):
        import server

        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["panel_v"], 77)
        self.assertIn("delay_warning", data["features"])
        self.assertIn("game_polish", data["features"])
        self.assertEqual(data["sitting_drop_min"], filter.SITTING_DROP_MIN)
        self.assertEqual(data["sitting_aged_min"], filter.SITTING_AGED_MIN)
        self.assertTrue(data["sitting_keep"])
        self.assertIn("sitting_keep", data["features"])
        self.assertIn("manual_update", data["features"])
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
        self.assertIn("const SITTING_AGED_MIN = 180", html)
        self.assertIn("aged · still listed", html)
        self.assertIn("const SITTING_NEAR_LEAD_MIN = 1", html)
        self.assertIn("const SITTING_FRESH_MIN = 2", html)
        self.assertIn("const SOCIAL_TAP_FRESH_MIN = 5", html)
        self.assertIn(">Sitting<", html)
        self.assertIn("sitting kept past 5m", html)
        self.assertIn("still listed", html)
        self.assertIn('class="badge sitting"', html)
        self.assertIn("function sittingRescueRows", html)
        self.assertIn("function scanHasLiveScores", html)
        self.assertIn('data-empty="scored"', html)
        self.assertIn("None cleared the flag line", html)
        self.assertIn("Nothing invented.", html)
        self.assertNotIn("sitting drop 5m", html)
        self.assertNotIn("past ~5m drop", html)
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
        rescue = html[html.index("function scanHasLiveScores"):html.index("function render()")]
        rescue_script = (
            "const SITTING_DROP_MIN = 5;\n"
            + rescue
            + r"""
const repro = {
  data_as_of: "2026-10-27T02:36:50Z",
  evaluated: 45,
  thresholds: { sitting_drop_min: 5 },
  sitting_dropped: [
    { ticker: "TRUMP2028", mint: "M1", score: 98, sitting_minutes: 5.1 },
    { ticker: "CHICO", mint: "M2", score: 98, sitting_minutes: 5.1 },
  ],
  top_scores: [
    { ticker: "TRUMP2028", mint: "M1", score: 98, score_12: 11.8, flagged: false, trench_band: true },
    { ticker: "LOW", mint: "M9", score: 12, score_12: 1.4, flagged: false, trench_band: false },
  ],
};
if (!scanHasLiveScores(repro)) process.exit(20);
const rows = sittingRescueRows(repro);
if (rows.length !== 2) process.exit(21);
if (rows[0].ticker !== "TRUMP2028" || rows[0].sitting_band !== "over") process.exit(22);
if (rows[0].flagged !== true || rows[0].score !== 98) process.exit(23);
if (rows[0].score_12 !== 11.8) process.exit(24);
if (rows.some((r) => r.ticker === "LOW")) process.exit(25);
const sub = {
  data_as_of: "2026-10-27T02:36:50Z",
  evaluated: 45,
  top_scores: [{ ticker: "LOW", mint: "M9", score: 12, flagged: false }],
  sitting_dropped: [],
};
if (sittingRescueRows(sub).length !== 0) process.exit(26);
if (!scanHasLiveScores(sub)) process.exit(27);
if (scanHasLiveScores({ evaluated: 0, top_scores: [], data_as_of: "x" })) process.exit(28);
"""
        )
        subprocess.check_call(["node", "-e", rescue_script])


if __name__ == "__main__":
    unittest.main()
