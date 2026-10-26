"""Market-cap progress: labeled ceiling, or empty. No invented numbers."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import game
import server
from mcap_progress import apply_mcap, build_mcap_progress, progress_from_pair, progress_from_row


class ProgressMathTests(unittest.TestCase):
    def test_bonding_beats_fdv_and_ath(self):
        prog = build_mcap_progress(
            market_cap_usd=420_000,
            fdv_usd=1_000_000,
            bonding_mcap_usd=690_000,
            graduation_mcap_usd=800_000,
            ath_mcap_usd=2_000_000,
        )
        self.assertEqual(prog["ceiling_kind"], "bonding")
        self.assertEqual(prog["ceiling_label"], "Bonding")
        self.assertEqual(prog["ceiling_usd"], 690_000)
        self.assertEqual(prog["pct_gone"], 60.9)
        self.assertEqual(prog["bar_pct"], 60.9)
        self.assertIn("Bonded / stated max", prog["formula"])
        self.assertNotIn("guaranteed", prog["formula"].lower())

    def test_graduation_when_bonding_missing_uses_bonding_label(self):
        prog = build_mcap_progress(
            market_cap_usd=420_000,
            fdv_usd=1_000_000,
            graduation_mcap_usd=690_000,
        )
        self.assertEqual(prog["ceiling_kind"], "graduation")
        self.assertEqual(prog["ceiling_label"], "Bonding")
        self.assertEqual(prog["ceiling_usd"], 690_000)
        self.assertIn("Graduation target", prog["formula"])

    def test_fdv_when_no_bonding_target(self):
        prog = build_mcap_progress(market_cap_usd=1_840_000, fdv_usd=8_400_000)
        self.assertEqual(prog["ceiling_kind"], "fdv")
        self.assertEqual(prog["ceiling_label"], "FDV")
        self.assertEqual(prog["ceiling_usd"], 8_400_000)
        self.assertEqual(prog["pct_gone"], 21.9)
        self.assertIn("FDV @ max supply", prog["formula"])

    def test_equal_fdv_is_not_a_full_bar(self):
        """Phook shape: FDV ≈ NOW is fully circulating, not max-hit."""
        prog = build_mcap_progress(market_cap_usd=86_800, fdv_usd=86_800)
        self.assertEqual(prog["market_cap_usd"], 86_800)
        self.assertEqual(prog["fdv_usd"], 86_800)
        self.assertIsNone(prog["ceiling_usd"])
        self.assertIsNone(prog["ceiling_label"])
        self.assertIsNone(prog["pct_gone"])
        self.assertIsNone(prog["bar_pct"])
        self.assertFalse(prog["ceiling_available"])
        self.assertFalse(prog["progress_available"])
        self.assertEqual(prog["ceiling_empty"], "at_fdv")
        self.assertIn("fully circulating", prog["formula"])
        self.assertIn("not 100% gone", prog["formula"])
        self.assertNotIn("guaranteed", prog["formula"].lower())

    def test_fdv_within_two_percent_is_skipped(self):
        prog = build_mcap_progress(market_cap_usd=100, fdv_usd=102)
        self.assertEqual(prog["ceiling_empty"], "at_fdv")
        self.assertIsNone(prog["pct_gone"])
        self.assertIsNone(prog["ceiling_usd"])

    def test_fdv_just_above_two_percent_keeps_labeled_progress(self):
        prog = build_mcap_progress(market_cap_usd=100, fdv_usd=102.01)
        self.assertEqual(prog["ceiling_kind"], "fdv")
        self.assertEqual(prog["ceiling_label"], "FDV")
        self.assertEqual(prog["ceiling_usd"], 102.01)
        self.assertLess(prog["pct_gone"], 100.0)
        self.assertGreater(prog["pct_gone"], 90.0)
        self.assertIsNone(prog["ceiling_empty"])
        self.assertIn("Not a promise of max price", prog["formula"])

    def test_flat_bonding_falls_through_to_a_higher_fdv(self):
        prog = build_mcap_progress(
            market_cap_usd=86_800,
            bonding_mcap_usd=86_800,
            fdv_usd=200_000,
        )
        self.assertEqual(prog["ceiling_kind"], "fdv")
        self.assertEqual(prog["ceiling_label"], "FDV")
        self.assertEqual(prog["ceiling_usd"], 200_000)
        self.assertLess(prog["pct_gone"], 100.0)

    def test_flat_bonding_and_fdv_use_a_higher_ath(self):
        prog = build_mcap_progress(
            market_cap_usd=100,
            bonding_mcap_usd=100,
            fdv_usd=101,
            ath_mcap_usd=400,
        )
        self.assertEqual(prog["ceiling_kind"], "ath")
        self.assertEqual(prog["ceiling_label"], "ATH")
        self.assertEqual(prog["pct_gone"], 25.0)

    def test_passed_ath_with_no_fdv_is_no_upside_not_over_100(self):
        prog = build_mcap_progress(market_cap_usd=150, ath_mcap_usd=100)
        self.assertIsNone(prog["ceiling_usd"])
        self.assertIsNone(prog["pct_gone"])
        self.assertIsNone(prog["bar_pct"])
        self.assertEqual(prog["ceiling_empty"], "no_upside")
        self.assertEqual(prog["market_cap_usd"], 150)
        self.assertIn("Nothing invented", prog["formula"])

    def test_ath_is_last(self):
        prog = build_mcap_progress(market_cap_usd=100, ath_mcap_usd=500)
        self.assertEqual(prog["ceiling_kind"], "ath")
        self.assertEqual(prog["ceiling_label"], "ATH")
        self.assertEqual(prog["pct_gone"], 20.0)

    def test_fdv_fallback_is_not_a_second_ceiling(self):
        prog = build_mcap_progress(
            market_cap_usd=100,
            fdv_usd=100,
            market_cap_from_fdv=True,
            ath_mcap_usd=400,
        )
        self.assertEqual(prog["ceiling_kind"], "ath")
        self.assertEqual(prog["ceiling_usd"], 400)
        self.assertEqual(prog["pct_gone"], 25.0)

        bare = build_mcap_progress(market_cap_usd=100, fdv_usd=100, market_cap_from_fdv=True)
        self.assertIsNone(bare["ceiling_usd"])
        self.assertIsNone(bare["pct_gone"])
        self.assertEqual(bare["market_cap_usd"], 100)
        self.assertEqual(bare["ceiling_empty"], "at_fdv")
        self.assertIn("fully circulating", bare["formula"])

    def test_missing_inputs_stay_empty(self):
        prog = build_mcap_progress(
            market_cap_usd=None,
            fdv_usd=0,
            bonding_mcap_usd=-5,
            ath_mcap_usd="—",
            graduation_mcap_usd=float("nan"),
        )
        self.assertIsNone(prog["market_cap_usd"])
        self.assertIsNone(prog["ceiling_usd"])
        self.assertIsNone(prog["pct_gone"])
        self.assertIsNone(prog["bar_pct"])
        self.assertFalse(prog["now_available"])
        self.assertFalse(prog["ceiling_available"])
        self.assertEqual(prog["ceiling_empty"], "unknown")
        self.assertIn("Nothing invented", prog["formula"])
        self.assertNotIn("guaranteed", json.dumps(prog).lower())

    def test_ceiling_without_now_has_no_percent(self):
        prog = build_mcap_progress(fdv_usd=10)
        self.assertEqual(prog["ceiling_label"], "FDV")
        self.assertIsNone(prog["pct_gone"])
        self.assertFalse(prog["progress_available"])

    def test_bool_and_junk_are_not_numbers(self):
        prog = build_mcap_progress(market_cap_usd=True, fdv_usd="nope", ath_mcap_usd="1.2M")
        self.assertIsNone(prog["market_cap_usd"])
        self.assertIsNone(prog["ceiling_usd"])


class SourceTests(unittest.TestCase):
    def test_pair_keeps_market_cap_and_fdv_distinct(self):
        prog = progress_from_pair(
            {"marketCap": 420000, "fdv": 690000, "priceUsd": "0.1"},
            now_source="dex",
        )
        self.assertEqual(prog["market_cap_usd"], 420000)
        self.assertEqual(prog["ceiling_label"], "FDV")
        self.assertEqual(prog["now_source"], "dex")
        self.assertAlmostEqual(prog["pct_gone"], 60.9, places=1)

    def test_pair_equal_fdv_is_at_fdv_for_filter_and_quote(self):
        prog = progress_from_pair({"marketCap": 86800, "fdv": 86800, "priceUsd": "0.0001"})
        self.assertEqual(prog["market_cap_usd"], 86800)
        self.assertEqual(prog["ceiling_empty"], "at_fdv")
        self.assertIsNone(prog["pct_gone"])
        self.assertIsNone(prog["ceiling_usd"])
        self.assertTrue(prog["now_available"])

    def test_pair_fdv_only_does_not_invent_progress(self):
        prog = progress_from_pair({"fdv": 2500, "marketCap": 0})
        self.assertEqual(prog["market_cap_usd"], 2500)
        self.assertTrue(prog["market_cap_from_fdv"])
        self.assertIsNone(prog["ceiling_usd"])
        self.assertIsNone(prog["pct_gone"])

    def test_pair_reads_explicit_bonding_and_ath_keys_only(self):
        prog = progress_from_pair(
            {
                "marketCap": 100,
                "fdv": 500,
                "bondingMarketCap": 250,
                "athMarketCap": 900,
            }
        )
        self.assertEqual(prog["ceiling_kind"], "bonding")
        self.assertEqual(prog["ceiling_usd"], 250)

    def test_row_reads_stored_metrics_and_does_not_upgrade_them(self):
        row = {"metrics": {"market_cap_usd": 1578621.0}, "market_cap_usd": 1}
        prog = progress_from_row(row)
        self.assertEqual(prog["market_cap_usd"], 1578621.0)
        self.assertIsNone(prog["ceiling_usd"])
        self.assertEqual(prog["now_source"], "snapshot")

    def test_apply_live_pair_overrides_stale_snapshot(self):
        row = {"metrics": {"market_cap_usd": 1_578_621.0, "liquidity_usd": 10}}
        out = apply_mcap(row, {"marketCap": 2224, "fdv": 2224})
        self.assertEqual(out["metrics"]["liquidity_usd"], 10)
        self.assertEqual(out["metrics"]["market_cap_usd"], 2224)
        self.assertEqual(out["metrics"]["fdv_usd"], 2224)
        self.assertIsNone(out["mcap_progress"]["ceiling_label"])
        self.assertIsNone(out["mcap_progress"]["pct_gone"])
        self.assertEqual(out["mcap_progress"]["ceiling_empty"], "at_fdv")
        self.assertEqual(out["mcap_progress"]["now_source"], "dex")
        self.assertEqual(row["metrics"]["market_cap_usd"], 1_578_621.0)

    def test_apply_without_pair_leaves_snapshot_empty_ceiling(self):
        row = {"metrics": {"market_cap_usd": 100.0}}
        out = apply_mcap(row)
        self.assertEqual(out["mcap_progress"]["market_cap_usd"], 100.0)
        self.assertIsNone(out["mcap_progress"]["ceiling_usd"])
        self.assertEqual(row["metrics"]["market_cap_usd"], 100.0)


class FilterAndApiTests(unittest.TestCase):
    def test_evaluate_stores_fdv_ceiling_from_the_pair(self):
        import filter as filter_mod

        pair = {
            "baseToken": {"symbol": "MAYA", "name": "Maya"},
            "priceUsd": "0.002",
            "marketCap": 1840000,
            "fdv": 8400000,
            "liquidity": {"usd": 90000},
            "volume": {"h1": 80000, "h24": 100000},
            "txns": {"h1": {"buys": 10, "sells": 8}},
            "priceChange": {},
            "dexId": "pumpswap",
            "pairAddress": "pair",
            "url": "https://dexscreener.com/solana/pair",
        }
        row = filter_mod.evaluate("mintmintmintmintmintmintmintmintmint", pair, {}, None, "2026-10-26T00:00:00Z")
        self.assertIsNotNone(row)
        self.assertEqual(row["metrics"]["market_cap_usd"], 1840000)
        self.assertEqual(row["metrics"]["fdv_usd"], 8400000)
        self.assertFalse(row["metrics"]["market_cap_from_fdv"])
        self.assertEqual(row["mcap_progress"]["ceiling_label"], "FDV")
        self.assertEqual(row["mcap_progress"]["pct_gone"], 21.9)

    def test_evaluate_equal_fdv_does_not_paint_100_gone(self):
        import filter as filter_mod

        pair = {
            "baseToken": {"symbol": "PHOOK", "name": "Pump Hooks"},
            "priceUsd": "0.0001",
            "marketCap": 86800,
            "fdv": 86800,
            "liquidity": {"usd": 25100},
            "volume": {"h1": 80000, "h24": 100000},
            "txns": {"h1": {"buys": 10, "sells": 8}},
            "priceChange": {},
            "dexId": "pumpswap",
            "pairAddress": "pair",
            "url": "https://dexscreener.com/solana/pair",
        }
        row = filter_mod.evaluate("mintmintmintmintmintmintmintmintmint", pair, {}, None, "2026-10-26T00:00:00Z")
        prog = row["mcap_progress"]
        self.assertEqual(prog["market_cap_usd"], 86800)
        self.assertEqual(prog["fdv_usd"], 86800)
        self.assertIsNone(prog["ceiling_usd"])
        self.assertIsNone(prog["pct_gone"])
        self.assertEqual(prog["ceiling_empty"], "at_fdv")
        self.assertNotEqual(prog["pct_gone"], 100.0)

    def test_evaluate_without_caps_stays_empty(self):
        import filter as filter_mod

        pair = {
            "baseToken": {"symbol": "NOSUP", "name": "No supply"},
            "liquidity": {"usd": 90000},
            "volume": {"h1": 80000, "h24": 100000},
            "txns": {"h1": {"buys": 1, "sells": 1}},
            "priceChange": {},
        }
        row = filter_mod.evaluate("mintmintmintmintmintmintmintmintmint", pair, {}, None, "2026-10-26T00:00:00Z")
        self.assertIsNone(row["metrics"]["market_cap_usd"])
        self.assertIsNone(row["mcap_progress"]["ceiling_usd"])
        self.assertIsNone(row["mcap_progress"]["pct_gone"])
        self.assertIn("Nothing invented", row["mcap_progress"]["formula"])

    def test_flagged_api_shows_stored_mcap_and_does_not_invent_a_ceiling(self):
        res = server.app.test_client().get("/api/flagged")
        self.assertEqual(res.status_code, 200)
        rows = res.get_json()
        self.assertTrue(rows)
        with open("flagged.json", encoding="utf-8") as fh:
            raw = json.load(fh)
        for got, src in zip(rows, raw):
            stored = (src.get("metrics") or {}).get("market_cap_usd")
            prog = got["mcap_progress"]
            self.assertEqual(prog["market_cap_usd"], stored)
            self.assertIsNone(prog["ceiling_usd"])
            self.assertIsNone(prog["pct_gone"])
            self.assertIsNone(prog["ceiling_label"])
            self.assertIn("Nothing invented", prog["formula"])

    def test_quote_attaches_live_fdv_progress(self):
        payload = {
            "pairs": [
                {
                    "chainId": "solana",
                    "dexId": "pumpswap",
                    "pairAddress": "pair",
                    "url": "https://dexscreener.com/solana/pair",
                    "baseToken": {"symbol": "MAYA", "name": "Maya"},
                    "priceUsd": "0.000002233",
                    "marketCap": 2224,
                    "fdv": 8800,
                    "liquidity": {"usd": 500},
                    "volume": {"h1": 10},
                    "priceChange": {"h1": 1},
                    "txns": {"h1": {"buys": 2, "sells": 1}},
                }
            ]
        }
        game._quote_cache.clear()

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(payload).encode()

        with mock.patch.object(game, "urlopen", return_value=Resp()):
            quote = game.fetch_dex_token("3KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump", bypass_cache=True)
        self.assertEqual(quote["market_cap_usd"], 2224)
        self.assertEqual(quote["mcap_progress"]["ceiling_label"], "FDV")
        self.assertEqual(quote["mcap_progress"]["ceiling_usd"], 8800)
        self.assertEqual(quote["mcap_progress"]["pct_gone"], 25.3)
        self.assertEqual(quote["mcap_progress"]["now_source"], "dex")


if __name__ == "__main__":
    unittest.main()
