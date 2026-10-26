"""Lock the Slice 4 research card: honest inputs, no invented proof."""

from __future__ import annotations

import time
import unittest

from filter import (
    LIQ_GOOD,
    LIQ_OK,
    VOL_GOOD,
    VOL_OK,
    W_LIQUIDITY,
    W_VOLUME,
    curve_score,
    evaluate,
)
from research import research_card


class ResearchCardTests(unittest.TestCase):
    def test_score_math_unchanged_when_card_attached(self):
        now_ms = (time.time() - 3600) * 1000
        pair = {
            "baseToken": {"symbol": "TEST", "name": "Test Coin"},
            "liquidity": {"usd": 100000},
            "volume": {"h1": 100000, "h24": 100000},
            "pairCreatedAt": now_ms,
            "priceUsd": "0.01",
            "url": "https://dexscreener.com/solana/pairtest",
        }
        rug = {"score_normalised": 1, "lpLockedPct": 100, "risks": []}
        row = evaluate("MintAddress1111111111111111111111111111111", pair, {}, rug, "2026-10-26T00:00:00Z")
        self.assertIsNotNone(row)
        liq_pts = curve_score(100000, LIQ_OK, LIQ_GOOD, W_LIQUIDITY)
        vol_pts = curve_score(100000, VOL_OK, VOL_GOOD, W_VOLUME)
        self.assertEqual(row["breakdown"]["liquidity"], round(liq_pts, 1))
        self.assertEqual(row["breakdown"]["volume"], round(vol_pts, 1))
        self.assertEqual(row["breakdown"]["holders"], 25.0)
        self.assertEqual(row["score"], 80.0)
        self.assertIn("research", row)
        self.assertEqual(row["research"]["rug_risk"]["pct"], 3.6)
        self.assertIn("not a probability", row["research"]["rug_risk"]["label"])

    def test_missing_inputs_are_omitted_not_zeroed(self):
        card = research_card({})
        rug = card["rug_risk"]
        self.assertIsNone(rug["pct"])
        self.assertFalse(rug["available"])
        self.assertGreaterEqual(len(rug["omitted"]), 4)
        self.assertIn("LP lock %", rug["omitted"])
        self.assertNotIn("posts", card["content"])

    def test_lp_only_renormalizes(self):
        card = research_card({"metrics": {"lp_locked_pct": 40}})
        self.assertEqual(card["rug_risk"]["pct"], 60.0)
        self.assertEqual(card["rug_risk"]["input_count"], 1)
        self.assertTrue(any("LP locked" in line for line in card["rug_risk"]["inputs"]))

    def test_profit_and_vitality_empty_without_prints(self):
        card = research_card({"ticker": "X"})
        self.assertFalse(card["profit"]["available"])
        self.assertIn("unavailable", card["profit"]["headline"].lower())
        self.assertIn("No target, wallet, or order", card["profit"]["note"])
        self.assertFalse(card["vitality"]["available"])
        self.assertFalse(card["fomo_pump"]["available"])
        self.assertIn("No FOMO", card["fomo_pump"]["empty"])

    def test_profit_is_observed_print_only(self):
        card = research_card({
            "late_chase": True,
            "metrics": {"price_change_h1": 140, "price_change_h24": 20, "liquidity_usd": 50000},
        })
        self.assertTrue(card["profit"]["available"])
        self.assertIn("140", card["profit"]["headline"])
        self.assertIn("not a target", card["profit"]["headline"])
        self.assertNotIn("order", card["profit"]["headline"].lower())

    def test_vitality_uses_real_counts(self):
        quiet = research_card({"metrics": {"buys_h1": 0, "sells_h1": 0, "volume_h1_usd": 0}})
        self.assertEqual(quiet["vitality"]["label"], "thin")
        loud = research_card({"metrics": {"buys_h1": 10, "sells_h1": 2, "volume_h1_usd": 90000}})
        self.assertEqual(loud["vitality"]["label"], "active")

    def test_content_does_not_invent_posts_or_fomo_links(self):
        card = research_card({
            "description": "Born on chain.",
            "socials": {"twitter": True, "telegram": False, "website": False},
            "links": {
                "fomo": "https://fomo.family/coin/abc",
                "pump": "https://pump.fun/coin/abc",
                "gmgn": "https://gmgn.ai/sol/token/abc",
                "dex": "https://dexscreener.com/solana/pair",
            },
            "dex_url": "https://dexscreener.com/solana/pair",
        })
        urls = [link["url"] for link in card["content"]["links"]]
        self.assertNotIn("https://fomo.family/coin/abc", urls)
        self.assertNotIn("https://pump.fun/coin/abc", urls)
        self.assertNotIn("https://gmgn.ai/sol/token/abc", urls)
        self.assertIn("https://dexscreener.com/solana/pair", urls)
        self.assertEqual(card["content"]["profile_text"], "Born on chain.")
        self.assertTrue(any("no URL" in line for line in card["content"]["missing"]))
        self.assertNotIn("posts", card["content"])

    def test_fomo_boost_and_hour_print_are_labeled_market_fields(self):
        card = research_card({
            "boost_amount": 100,
            "metrics": {
                "price_change_h1": 40,
                "buys_h1": 30,
                "sells_h1": 10,
                "buy_sell_ratio_h1": 3,
            },
        })
        texts = " ".join(item["text"] for item in card["fomo_pump"]["items"])
        self.assertIn("DEX boost amount 100", texts)
        self.assertIn("not a FOMO post", texts)
        self.assertIn("not a social post", texts)
        self.assertNotIn("fomo.family", texts)

    def test_pool_account_is_not_called_a_wallet(self):
        report = {
            "mint": "M",
            "totalHolders": 10,
            "graphInsidersDetected": 0,
            "rugged": False,
            "creator": "CREATOR",
            "topHolders": [
                {"pct": 99.5, "owner": "PAIR", "address": "POOLACCT", "insider": False},
                {"pct": 12.0, "owner": "CREATOR", "address": "W1", "insider": True},
                {"pct": 1.5, "owner": "W2", "address": "A2", "insider": False},
            ],
            "markets": [{"pubkey": "PAIR"}],
        }
        row = {"metrics": {"pair_address": "PAIR", "pair_age_hours": 2.0}}
        holders = research_card(row, report)["holders"]
        self.assertTrue(holders["available"])
        self.assertTrue(holders["early"])
        self.assertEqual(holders["top1_pct"], 12.0)
        self.assertEqual(holders["top10_pct"], 13.5)
        self.assertIn("creator", " ".join(holders["inputs"]).lower())
        self.assertIn("Excluded 1 pool/market", " ".join(holders["inputs"]))
        self.assertNotIn("99.5", holders["headline"])

    def test_no_holder_list_stays_empty(self):
        card = research_card({"metrics": {"pair_age_hours": 1}})
        self.assertFalse(card["holders"]["available"])
        self.assertIn("No top-holder", card["holders"]["empty"])
        self.assertFalse(card["holders"]["early"])

    def test_rugged_boolean_does_not_force_100(self):
        card = research_card({}, {"mint": "M", "rugged": True, "score_normalised": 1, "risks": []})
        self.assertNotEqual(card["rug_risk"]["pct"], 100)
        self.assertTrue(card["rug_risk"]["rugged"])
        self.assertTrue(any("not folded" in line for line in card["rug_risk"]["inputs"]))

    def test_out_of_range_lp_is_ignored(self):
        card = research_card({"lp_locked_pct": 250})
        self.assertIsNone(card["rug_risk"]["pct"])
        self.assertTrue(any("outside 0–100" in line for line in card["rug_risk"]["omitted"]))


if __name__ == "__main__":
    unittest.main()
