"""Quote sync: labeled DEX NOW / LIVE USD is a fresh same-path quote, or Unknown."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock
from urllib.error import URLError

import game
import server
from mcap_progress import build_mcap_progress

MINT = "3KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"


def _resp(payload: dict):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return Resp()


def _pair(*, price="0.000585", mcap=566_000, fdv=566_000, liq=80_000, url="https://dexscreener.com/solana/bruzil-pair"):
    return {
        "chainId": "solana",
        "dexId": "pumpswap",
        "pairAddress": "bruzilpair",
        "url": url,
        "baseToken": {"symbol": "BRUZIL", "name": "Bruzil"},
        "priceUsd": price,
        "marketCap": mcap,
        "fdv": fdv,
        "liquidity": {"usd": liq},
        "volume": {"h1": 10},
        "priceChange": {"h1": 1},
        "txns": {"h1": {"buys": 2, "sells": 1}},
    }


class BandTests(unittest.TestCase):
    def test_owner_bruzil_gap_is_inside_five_percent(self):
        # Cross-tab skew from the bug pack, not a formula miss. 591.1k vs 566k.
        self.assertTrue(game.within_quote_band(591_100, 566_000))
        self.assertTrue(game.within_quote_band(0.00061, 0.0005847))

    def test_outside_band(self):
        self.assertFalse(game.within_quote_band(800_000, 566_000))
        self.assertFalse(game.within_quote_band(None, 566_000))


class FetchFreshnessTests(unittest.TestCase):
    def setUp(self):
        game._quote_cache.clear()

    def tearDown(self):
        game._quote_cache.clear()

    def test_price_mcap_and_link_come_from_the_same_pair(self):
        payload = {
            "pairs": [
                _pair(price="0.00061", mcap=800_000, fdv=800_000, liq=1_000, url="https://dexscreener.com/solana/thin"),
                _pair(price="0.000585", mcap=566_000, fdv=566_000, liq=90_000, url="https://dexscreener.com/solana/deep"),
            ]
        }
        with mock.patch.object(game, "urlopen", return_value=_resp(payload)):
            quote = game.fetch_dex_token(MINT, bypass_cache=True)
        self.assertTrue(quote["quote_live"])
        self.assertEqual(quote["price_usd"], 0.000585)
        self.assertEqual(quote["market_cap_usd"], 566_000)
        self.assertEqual(quote["pair_url"], "https://dexscreener.com/solana/deep")
        self.assertEqual(quote["mcap_progress"]["now_source"], "dex")
        self.assertEqual(quote["mcap_progress"]["market_cap_usd"], 566_000)
        # Fully circulating: do not paint 100% gone.
        self.assertEqual(quote["mcap_progress"]["ceiling_empty"], "at_fdv")
        self.assertIsNone(quote["mcap_progress"]["pct_gone"])
        self.assertIsNone(quote["mcap_progress"]["ceiling_usd"])
        self.assertTrue(game.within_quote_band(quote["market_cap_usd"], 566_000))
        self.assertTrue(game.within_quote_band(quote["price_usd"], 0.000585))

    def test_failed_refresh_keeps_a_quote_only_inside_the_sla(self):
        game._quote_cache[MINT] = (time.time() - 5, {"price_usd": 0.000585, "market_cap_usd": 566_000, "mcap_progress": {"now_source": "dex", "now_available": True, "market_cap_usd": 566_000}, "quoted_at": "2026-10-26T16:28:00Z", "pair_url": "https://dexscreener.com/solana/deep"})
        with mock.patch.object(game, "urlopen", side_effect=URLError("down")):
            quote = game.fetch_dex_token(MINT, bypass_cache=True)
        self.assertIsNotNone(quote)
        self.assertTrue(quote["quote_live"])
        self.assertEqual(quote["price_usd"], 0.000585)
        self.assertLessEqual(quote["quote_age_s"], game.QUOTE_FRESH_SLA_S)

    def test_failed_refresh_past_sla_does_not_return_the_old_print(self):
        stale = {
            "price_usd": 0.00061,
            "market_cap_usd": 591_100,
            "mcap_progress": {
                "now_source": "dex",
                "now_available": True,
                "market_cap_usd": 591_100,
            },
            "quoted_at": "2026-10-26T16:00:00Z",
            "pair_url": "https://dexscreener.com/solana/deep",
        }
        game._quote_cache[MINT] = (time.time() - game.QUOTE_FRESH_SLA_S - 5, stale)
        with mock.patch.object(game, "urlopen", side_effect=URLError("down")):
            quote = game.fetch_dex_token(MINT, bypass_cache=True)
        self.assertIsNone(quote)

    def test_quote_endpoint_hides_a_stale_number(self):
        stale = {
            "price_usd": 0.00061,
            "market_cap_usd": 591100,
            "symbol": "BRUZIL",
            "mcap_progress": {"now_source": "dex", "now_available": True, "market_cap_usd": 591100},
            "quoted_at": "2026-10-26T16:00:00Z",
        }
        game._quote_cache[MINT] = (time.time() - 60, stale)
        with mock.patch.object(game, "urlopen", side_effect=URLError("down")):
            res = server.app.test_client().get("/api/game/quote?fresh=1&mint=" + MINT)
        body = res.get_json()
        blob = json.dumps(body)
        self.assertFalse(body.get("ok"))
        self.assertNotIn("quote", body)
        self.assertNotIn("591100", blob)
        self.assertNotIn("0.00061", blob)


class PositionHonestyTests(unittest.TestCase):
    def test_stale_position_drops_live_usd_and_dex_now_but_keeps_at_fdv_note(self):
        prog = build_mcap_progress(market_cap_usd=566_000, fdv_usd=566_000, now_source="dex")
        self.assertEqual(prog["ceiling_empty"], "at_fdv")
        self.assertIsNone(prog["pct_gone"])
        shown = game.present_open_position(
            {
                "last_price_usd": 0.000585,
                "quote_epoch": time.time() - 60,
                "quoted_at": "2026-10-26T16:00:00Z",
                "market_cap_usd": 566_000,
                "mcap_progress": prog,
            }
        )
        self.assertFalse(shown["quote_live"])
        self.assertIsNone(shown["live_price_usd"])
        self.assertIsNone(shown["market_cap_usd"])
        self.assertFalse(shown["mcap_progress"]["now_available"])
        self.assertNotEqual(shown["mcap_progress"].get("now_source"), "dex")
        self.assertIsNone(shown["mcap_progress"]["pct_gone"])
        self.assertIn("not 100% gone", shown["mcap_progress"]["formula"])
        # Paper mark can still see the stored fill price; it is not the live label.
        self.assertEqual(shown["last_price_usd"], 0.000585)

    def test_fresh_position_keeps_same_path_dex_now(self):
        prog = build_mcap_progress(market_cap_usd=566_000, fdv_usd=900_000, now_source="dex")
        shown = game.present_open_position(
            {
                "last_price_usd": 0.000585,
                "quote_epoch": time.time(),
                "market_cap_usd": 566_000,
                "mcap_progress": prog,
            }
        )
        self.assertTrue(shown["quote_live"])
        self.assertEqual(shown["live_price_usd"], 0.000585)
        self.assertEqual(shown["mcap_progress"]["now_source"], "dex")
        self.assertEqual(shown["mcap_progress"]["market_cap_usd"], 566_000)
        self.assertEqual(shown["mcap_progress"]["ceiling_label"], "FDV")
        self.assertLess(shown["mcap_progress"]["pct_gone"], 100)


class AttachTests(unittest.TestCase):
    def setUp(self):
        game._quote_cache.clear()

    def tearDown(self):
        game._quote_cache.clear()

    def test_snapshot_outside_band_is_replaced_by_the_live_quote(self):
        payload = {"pairs": [_pair(price="0.000585", mcap=566_000, fdv=566_000, url="https://dexscreener.com/solana/deep")]}
        row = {"mint": MINT, "metrics": {"market_cap_usd": 800_000, "price_usd": 0.001}, "dex_url": "https://dexscreener.com/solana/old"}
        with mock.patch.object(game, "urlopen", return_value=_resp(payload)):
            out = game.attach_live_dex_quote(row, MINT, fresh=True)
        self.assertTrue(out["quote_live"])
        self.assertEqual(out["mcap_progress"]["now_source"], "dex")
        self.assertEqual(out["market_cap_usd"], 566_000)
        self.assertEqual(out["metrics"]["price_usd"], 0.000585)
        self.assertEqual(out["dex_url"], "https://dexscreener.com/solana/deep")
        self.assertTrue(game.within_quote_band(out["market_cap_usd"], 566_000))
        self.assertFalse(game.within_quote_band(800_000, out["market_cap_usd"]))
        self.assertEqual(out["mcap_progress"]["ceiling_empty"], "at_fdv")
        self.assertIsNone(out["mcap_progress"]["pct_gone"])

    def test_failed_quote_does_not_label_the_snapshot_as_dex(self):
        row = {
            "mint": MINT,
            "metrics": {"market_cap_usd": 800_000, "price_usd": 0.001, "fdv_usd": 800_000},
            "mcap_progress": {"now_source": "dex", "now_available": True, "market_cap_usd": 800_000},
        }
        with mock.patch.object(game, "urlopen", side_effect=URLError("down")):
            out = game.attach_live_dex_quote(row, MINT, fresh=True)
        self.assertFalse(out["quote_live"])
        self.assertEqual(out["mcap_progress"]["now_source"], "snapshot")
        self.assertEqual(out["mcap_progress"]["market_cap_usd"], 800_000)
        self.assertNotEqual(out["mcap_progress"]["now_source"], "dex")


if __name__ == "__main__":
    unittest.main()
