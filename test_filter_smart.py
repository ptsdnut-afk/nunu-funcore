#!/usr/bin/env python3
"""SHIP U — Token Flow demotions and scan efficiency. Not a QA PASS.

Worked examples use one maxed book (liquidity 30 + volume 25 + holders 25
+ socials 20 + buy-heavy flow +3, capped at 100) so the before/after is
the penalty, not a vibe.
"""

import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import filter
import gold_scar
import server


CLEAN_RUG = {"score_normalised": 5, "lpLockedPct": 100, "risks": []}
FULL_SIDE = {
    "description": "a fresh pond joke",
    "profile_links": [
        {"type": "twitter", "url": "https://x.com/example"},
        {"type": "telegram", "url": "https://t.me/example"},
        {"type": "website", "url": "https://example.com"},
    ],
}


def _created_ms(age_h: float) -> int:
    return int(time.time() * 1000 - age_h * 3600 * 1000)


def _pair(symbol: str, name: str, age_h: float, h1: float = 10, h24: float = 20, m5: float = 2) -> dict:
    return {
        "baseToken": {"symbol": symbol, "name": name},
        "liquidity": {"usd": 80_000},
        "volume": {"h1": 100_000, "h24": 200_000},
        "pairCreatedAt": _created_ms(age_h),
        "txns": {"h1": {"buys": 120, "sells": 100}},
        "priceChange": {"m5": m5, "h1": h1, "h24": h24},
        "priceUsd": "0.001",
        "url": "https://dexscreener.com/solana/" + symbol,
        "pairAddress": "Pair" + symbol,
    }


def _eval(symbol: str, name: str, age_h: float = 0.4, h1: float = 10, h24: float = 20, side: dict | None = None) -> dict:
    row = filter.evaluate(
        symbol + "Mint111111111111111111111111111",
        _pair(symbol, name, age_h, h1=h1, h24=h24),
        FULL_SIDE if side is None else side,
        CLEAN_RUG,
        "2026-10-27T06:00:00Z",
    )
    assert row is not None
    return row


class ScoreDemotionTests(unittest.TestCase):
    def test_constants_replaced_the_cosmetic_late_chase(self):
        self.assertEqual(filter.LATE_CHASE_MIN_AGE_H, 1.0)
        self.assertEqual(filter.LATE_CHASE_MIN_H1_PCT, 180.0)
        self.assertEqual(filter.LATE_CHASE_PENALTY, 12.0)
        self.assertEqual(filter.ALREADY_RAN_H24_PCT, 250.0)
        self.assertEqual(filter.ALREADY_RAN_MIN_AGE_H, 2.0)
        self.assertEqual(filter.ALREADY_RAN_PENALTY, 18.0)
        self.assertEqual(filter.ALREADY_RAN_HARD_H24_PCT, 500.0)
        self.assertEqual(filter.ALREADY_RAN_HARD_MIN_AGE_H, 3.0)
        self.assertEqual(filter.CROWDED_PENALTY, 12.0)
        self.assertEqual(filter.CLONE_PACK_MIN, 3)
        self.assertEqual(filter.CLONE_PACK_MIN, gold_scar.CLONE_PACK_MIN)
        self.assertEqual(filter.CLONE_PACK_PENALTY, 8.0)
        self.assertEqual(filter.SOCIALS_PROFILE_ONLY_CAP, 6.0)
        self.assertEqual(filter.TRENCH_SCORE_12_MIN, 10.0)
        self.assertEqual(filter.TRENCH_MAX_AGE_H, 6.0)
        self.assertEqual(filter.MIN_SCORE_TO_FLAG, 55.0)
        self.assertTrue(filter.SITTING_DROP_MIN == 5.0)

    def test_maxed_book_is_100_before_penalties(self):
        row = _eval("FROG", "Frog joke", age_h=0.4, h1=12, h24=18)
        self.assertEqual(row["score_before_demotion"], 100.0)
        self.assertEqual(row["score"], 100.0)
        self.assertEqual(row["score_12"], 12.0)
        self.assertTrue(row["flagged"])
        self.assertTrue(row["trench_band"])
        self.assertFalse(row["late_chase"])
        self.assertFalse(row["crowded_narrative"])
        self.assertFalse(row["already_ran"])

    def test_elon2028_loses_12_and_loses_trench(self):
        # Same maxed book as FROG. Before SHIP U this was 100.0 / 12.0 and trench.
        # Live-style 98.3 (11.8/12) would land at 86.3 (10.4/12), still not trench.
        row = _eval("ELON2028", "ELON2028", age_h=1.2, h1=20, h24=40)
        self.assertEqual(row["score_before_demotion"], 100.0)
        self.assertEqual(row["score"], 88.0)
        self.assertEqual(row["score_12"], 10.6)
        self.assertEqual(row["breakdown"]["crowded"], -12.0)
        self.assertEqual(row["crowded_stem"], "elon")
        self.assertTrue(row["flagged"])
        self.assertFalse(row["trench_band"])
        self.assertFalse(row["late_chase"])
        self.assertNotIn("already_ran_h24>=500", row["hard_fails"])
        style = 98.3 - filter.CROWDED_PENALTY
        self.assertEqual(style, 86.3)
        self.assertEqual(round(style / 100.0 * 12.0, 1), 10.4)

    def test_gtaelon_is_the_same_crowded_demotion(self):
        row = _eval("GTAELON", "GTAELON", age_h=0.8, h1=15, h24=30)
        self.assertEqual(gold_scar._crowded_hit({"ticker": "GTAELON", "name": "GTAELON"}), "elon")
        self.assertEqual(row["score"], 88.0)
        self.assertEqual(row["score_12"], 10.6)
        self.assertFalse(row["trench_band"])
        self.assertTrue(row["flagged"])
        self.assertIn("crowded_narrative", row["red_flags"])

    def test_aged_huge_h24_is_a_real_penalty_and_the_extreme_unflags(self):
        # CHICO-class: age >= 2h and h24 >= +250%. A maxed 100 becomes 82.0 / 9.8.
        # A book that was 95.0 (11.4/12) becomes 77.0 (9.2/12). Still flaggable.
        chico = _eval("CHICO", "Chico", age_h=4.0, h1=40, h24=400)
        self.assertTrue(chico["already_ran"])
        self.assertFalse(chico["already_ran_hard"])
        self.assertFalse(chico["late_chase"])
        self.assertEqual(chico["breakdown"]["already_ran"], -18.0)
        self.assertEqual(chico["score"], 82.0)
        self.assertEqual(chico["score_12"], 9.8)
        self.assertTrue(chico["flagged"])
        self.assertFalse(chico["trench_band"])
        was_114 = 95.0
        self.assertEqual(round((was_114 - 18.0) / 100.0 * 12.0, 1), 9.2)

        extreme = _eval("BAGS", "Bags", age_h=4.0, h1=40, h24=800)
        self.assertTrue(extreme["already_ran_hard"])
        self.assertIn("already_ran_h24>=500", extreme["hard_fails"])
        self.assertEqual(extreme["score"], 82.0)
        self.assertFalse(extreme["flagged"])
        self.assertFalse(extreme["trench_band"])

    def test_late_chase_now_costs_12_and_blocks_trench(self):
        late = _eval("ZOOM", "Zoom", age_h=1.5, h1=200, h24=40)
        self.assertTrue(late["late_chase"])
        self.assertEqual(late["breakdown"]["late_chase"], -12.0)
        self.assertEqual(late["score"], 88.0)
        self.assertEqual(late["score_12"], 10.6)
        self.assertTrue(late["flagged"])
        self.assertFalse(late["trench_band"])
        # The retired cosmetic mark (age >= 2h and h1 >= +100%) does not fire.
        mild = _eval("MILD", "Mild", age_h=2.5, h1=120, h24=40)
        self.assertFalse(mild["late_chase"])
        self.assertEqual(mild["score"], 100.0)
        self.assertTrue(mild["trench_band"])

    def test_late_chase_and_already_ran_stack(self):
        row = _eval("STACK", "Stack", age_h=3.5, h1=200, h24=300)
        self.assertTrue(row["late_chase"])
        self.assertTrue(row["already_ran"])
        self.assertFalse(row["already_ran_hard"])
        self.assertEqual(row["score"], 70.0)
        self.assertEqual(row["score_12"], 8.4)
        self.assertTrue(row["flagged"])
        self.assertFalse(row["trench_band"])

    def test_clone_pack_of_three_costs_8_each(self):
        rows = [
            _eval("DOGE", "Doge", age_h=0.5),
            _eval("DOGE2", "Doge two", age_h=0.5),
            _eval("DOGECOIN", "Doge coin", age_h=0.5),
        ]
        for row in rows:
            self.assertEqual(row["score"], 100.0)
        filter.apply_clone_demotion(rows)
        for row in rows:
            self.assertEqual(row["family_key"], "doge")
            self.assertEqual(row["family_count"], 3)
            self.assertTrue(row["clone_pack"])
            self.assertEqual(row["breakdown"]["clone_pack"], -8.0)
            self.assertEqual(row["score"], 92.0)
            self.assertEqual(row["score_12"], 11.0)
            self.assertTrue(row["trench_band"])
            self.assertTrue(row["flagged"])
        # A pair is under the bar.
        pair = [_eval("FROG", "Frog joke"), _eval("FROG2", "Frog two")]
        filter.apply_clone_demotion(pair)
        self.assertFalse(any(row["clone_pack"] for row in pair))
        self.assertTrue(all(row["score"] == 100.0 for row in pair))

    def test_crowded_pack_stacks_clone_on_the_narrative_penalty(self):
        rows = [
            _eval("TRUMP", "Trump", age_h=1.0, h1=10, h24=20),
            _eval("TRUMP2028", "Trump 2028", age_h=1.0, h1=10, h24=20),
            _eval("TRUMPCOIN", "Trump coin", age_h=1.0, h1=10, h24=20),
        ]
        for row in rows:
            self.assertEqual(row["score"], 88.0)
        filter.apply_clone_demotion(rows)
        for row in rows:
            self.assertEqual(row["family_key"], "trump")
            self.assertEqual(row["score"], 80.0)
            self.assertEqual(row["score_12"], 9.6)
            self.assertFalse(row["trench_band"])
            self.assertTrue(row["flagged"])

    def test_profile_url_only_caps_x_at_6(self):
        x_only = {
            "profile_links": [{"type": "twitter", "url": "https://x.com/example"}],
        }
        capped = _eval("FROG", "Frog joke", side=x_only)
        self.assertTrue(capped["socials"]["profile_only"])
        self.assertEqual(capped["breakdown"]["socials"], 6.0)
        self.assertEqual(capped["score"], 89.0)  # 100 - (9 - 6), cap applied before the 100 ceiling
        # 30+25+25+6+3 = 89. The uncapped X-only book would be 30+25+25+9+3 = 92.
        self.assertEqual(capped["score_before_demotion"], 89.0)

        described = {
            "description": "people are actually talking about the frog",
            "profile_links": [{"type": "twitter", "url": "https://x.com/example"}],
        }
        full = _eval("FROG", "Frog joke", side=described)
        self.assertFalse(full["socials"]["profile_only"])
        self.assertEqual(full["breakdown"]["socials"], 11.0)

        both = {
            "profile_links": [
                {"type": "twitter", "url": "https://x.com/example"},
                {"type": "telegram", "url": "https://t.me/example"},
            ],
        }
        second = _eval("FROG", "Frog joke", side=both)
        self.assertFalse(second["socials"]["profile_only"])
        self.assertEqual(second["breakdown"]["socials"], 14.0)

    def test_trench_requires_age_under_6h(self):
        aged = _eval("FROG", "Frog joke", age_h=8.0, h1=5, h24=10)
        self.assertEqual(aged["score"], 100.0)
        self.assertTrue(aged["flagged"])
        self.assertFalse(aged["trench_band"])
        self.assertFalse(aged["already_ran"])

    def test_sitting_keep_still_holds(self):
        kept, dropped = filter.apply_sitting_rule(
            [{
                "mint": "SitMint",
                "ticker": "FROG",
                "score": 88.0,
                "sitting_minutes": 6.1,
                "flagged": True,
            }]
        )
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0]["flagged"])
        self.assertTrue(kept[0]["sitting_kept"])
        self.assertEqual(kept[0]["sitting_band"], "over")


class RugSkipTests(unittest.TestCase):
    def test_hard_fail_and_already_ran_skip_summary(self):
        fresh = _pair("FROG", "Frog joke", 0.4, h1=10, h24=20)
        self.assertTrue(filter.rug_summary_wanted(fresh))

        thin = _pair("THIN", "Thin", 0.4)
        thin["liquidity"] = {"usd": 10_000}
        thin["volume"] = {"h1": 8_000, "h24": 9_000}
        self.assertTrue(filter.clears_half_floors(thin))
        self.assertFalse(filter.rug_summary_wanted(thin))

        dust = _pair("DUST", "Dust", 0.4)
        dust["liquidity"] = {"usd": 1_000}
        dust["volume"] = {"h1": 100, "h24": 100}
        self.assertFalse(filter.clears_half_floors(dust))
        self.assertFalse(filter.rug_summary_wanted(dust))

        ran = _pair("CHICO", "Chico", 4.0, h1=40, h24=400)
        self.assertTrue(filter.clears_half_floors(ran))
        self.assertFalse(filter.rug_summary_wanted(ran))

        old = _pair("OLD", "Old", 80.0, h1=10, h24=10)
        self.assertFalse(filter.rug_summary_wanted(old))


class HealthAndGameSmokeTests(unittest.TestCase):
    def test_health_bumps_filter_smart_and_keeps_disclaimer(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 88)
        for feature in ("filter_smart", "gold_scar", "game_follow_tab", "sitting_keep", "game_ai_review"):
            self.assertIn(feature, data["features"])
        self.assertIn("Paper research only. Not financial advice. No trades placed.", data["disclaimer"])
        blob = json.dumps(data).lower()
        for banned in ("will moon", "buy now", "guaranteed", "100x", "copytrade", "auto-buy"):
            self.assertNotIn(banned, blob)
        self.assertTrue(data["sitting_keep"])
        self.assertEqual(data["sitting_drop_min"], 5.0)

    def test_game_sell_finish_and_follow_contracts_remain(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('"/api/game/sell"', html)
        self.assertIn('"/api/game/end"', html)
        self.assertIn('id="gameFollow"', html)
        self.assertIn('data-feature="game_follow_tab"', html)
        self.assertIn("Paper research only", html)
        self.assertIn("Not a buy", html)
        rules = server.app.url_map
        paths = {rule.rule for rule in rules.iter_rules()}
        self.assertIn("/api/game/sell", paths)
        self.assertIn("/api/game/end", paths)
        self.assertIn("/api/game/start", paths)
        self.assertIn("/api/latest", paths)
        latest = server.app.test_client().get("/api/latest").get_json()
        self.assertIn("Paper research only", latest.get("disclaimer") or "")

    def test_panel_trench_honors_an_explicit_false(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        src = html[html.index("function score12Of"):html.index("function fmtRel")]
        script = src + """
const crowded = { ticker: "ELON2028", score: 88, score_12: 10.6, trench_band: false, late_chase: false, metrics: { pair_age_hours: 1.2 } };
const fresh = { ticker: "FROG", score: 100, score_12: 12, trench_band: true, metrics: { pair_age_hours: 0.4 } };
const legacy = { ticker: "OLD", score: 90, score_12: 10.8, metrics: { pair_age_hours: 2 } };
if (inTrenchBand(crowded) !== false) process.exit(2);
if (sectionOf(crowded) === "trench") process.exit(3);
if (inTrenchBand(fresh) !== true) process.exit(4);
if (sectionOf(fresh) !== "new") process.exit(5);
if (inTrenchBand(legacy) !== true) process.exit(6);
if (sectionOf(legacy) !== "trench") process.exit(7);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class AnnotateCacheTests(unittest.TestCase):
    def test_flagged_annotate_is_cached_until_mtime_changes(self):
        path = server.FLAGGED_PATH
        stamp = path.stat()
        old = (stamp.st_atime, stamp.st_mtime)
        server._prepare_cache.clear()
        client = server.app.test_client()
        try:
            first = client.get("/api/flagged")
            self.assertEqual(first.status_code, 200)
            with mock.patch.object(gold_scar, "annotate", side_effect=AssertionError("cached read should not annotate")):
                second = client.get("/api/flagged")
                self.assertEqual(second.status_code, 200)
            self.assertEqual(first.get_json(), second.get_json())
            os.utime(path, None)
            calls = {"n": 0}
            real = gold_scar.annotate

            def wrapped(rows, context=None):
                calls["n"] += 1
                return real(rows, context)

            with mock.patch.object(gold_scar, "annotate", side_effect=wrapped):
                third = client.get("/api/flagged")
                self.assertEqual(third.status_code, 200)
            self.assertGreaterEqual(calls["n"], 1)
            qa = client.get("/api/flagged?qa_gold=1")
            self.assertEqual(qa.status_code, 200)
            body = qa.get_json()
            self.assertTrue(body[0].get("qa_gold_fixture"))
            self.assertIn("Paper research only", body[0]["gold_scar"]["why"])
        finally:
            os.utime(path, old)
            server._prepare_cache.clear()


if __name__ == "__main__":
    unittest.main()
