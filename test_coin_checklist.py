"""SHIP X — coin checklist verdicts (AC-X01…X06) and panel_v 83.

Unknown is not a pass. Bands live in VERDICT_BANDS so a later 10-row sheet
can retune them without hunting through the UI. The API verdict name is the
value a strict Filter or an AI trader should read.
"""

from __future__ import annotations

import unittest
from unittest import mock

import coin_checklist as cc
import server

MINT = "5G3pzMiNQxiFctE1aZRTctYJLz5zGdfA1S65QMT5pump"
CREATOR = "Hkj2F1XtKUoJgzejpRWx7hSPcqaVo3mqZc1yks9g928"
WALLET_A = "9fT2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaQ4m"
WALLET_B = "9fT2bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbQ4m"
WALLET_C = "9fT2cccccccccccccccccccccccccccccccccQ4m"
FUNDER = "Fund11111111111111111111111111111111111111"


def _holders(*pcts, owners=None):
    rows = []
    for index, pct in enumerate(pcts):
        owner = (owners[index] if owners else f"Own{index}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        rows.append({"owner": owner, "pct": pct, "excluded": False})
    return rows


def _clean_sources(**overrides):
    """Every row passable except narrative, which has no source."""
    base = {
        "pump": {
            "mint": MINT,
            "creator": CREATOR,
            "description": "A real description from the launchpad.",
            "created_timestamp": 1_700_000_000_000,
            "complete": True,
            "pump_swap_pool": "Pool111111111111111111111111111111111111111",
            "twitter": "https://x.com/example",
            "website": "https://example.com",
            "telegram": "https://t.me/example",
        },
        "dex": {
            "priceUsd": "0.0000913",
            "marketCap": 286000,
            "priceChange": {"h1": 38},
            "dexId": "pumpswap",
            "pairCreatedAt": 1_700_000_100_000,
            "txns": {"h1": {"buys": 80, "sells": 40}},
            "info": {
                "websites": [{"url": "https://example.com"}],
                "socials": [
                    {"type": "twitter", "url": "https://x.com/example"},
                    {"type": "telegram", "url": "https://t.me/example"},
                ],
            },
        },
        "rug": {"lpLockedPct": 100, "totalHolders": 1240, "rugged": False},
        "creator_coins": {
            "count": 1,
            "coins": [{"mint": MINT, "ath_market_cap": 1000}],
        },
        "holders": _holders(10, 8, 5),
        "holder_source": "Solana RPC",
        "authorities": {"known": True, "mint_authority": None, "freeze_authority": None, "source": "Solana RPC"},
        "traces": [
            {"owner": WALLET_A, "pct": 10, "funder": "solo-a", "source": "public RPC"},
            {"owner": WALLET_B, "pct": 8, "funder": "solo-b", "source": "public RPC"},
            {"owner": WALLET_C, "pct": 5, "funder": "solo-c", "source": "public RPC"},
        ],
        "organic": {"known": True, "unique_traders": 140, "max_wallet_pct": 4.0, "source": "Helius"},
        "social_checks": {
            "status": "pass",
            "detail": "X + website loaded · X is older than the coin",
            "source": "X + Dexscreener",
        },
        "live_narratives": ["nomatch"],
        "bubblemaps": "paid_key_required",
    }
    base.update(overrides)
    return base


class VerdictBandTests(unittest.TestCase):
    def test_bands_are_explicit_and_unknown_does_not_pass(self):
        self.assertEqual(cc.verdict_for(8, forced_skip=False), "BUY-WATCH")
        self.assertEqual(cc.verdict_for(7, forced_skip=False), "WATCH")
        self.assertEqual(cc.verdict_for(6, forced_skip=False), "WATCH")
        self.assertEqual(cc.verdict_for(5, forced_skip=False), "SKIP")
        self.assertEqual(cc.verdict_for(0, forced_skip=False), "SKIP")
        self.assertEqual(cc.verdict_for(8, forced_skip=True), "SKIP")
        self.assertFalse(cc.rules_payload()["unknown_passes"])
        self.assertEqual(cc.TOP10_PASS_BELOW, 30.0)
        self.assertEqual(cc.CLUSTER_BUNDLE_AT, 10.0)
        self.assertEqual(cc.ORGANIC_MIN_TRADERS, 100)
        self.assertEqual([row[0] for row in cc.CHECKS], [
            "lp", "mint_freeze", "dev_history", "not_bundled", "top10", "socials", "organic", "narrative",
        ])

    def test_narrative_miss_is_a_real_fail_and_a_match_can_buy_watch(self):
        out = cc.assemble(MINT, _clean_sources(), now=1_700_000_500)
        by_id = {row["id"]: row for row in out["checks"]}
        self.assertEqual(by_id["narrative"]["status"], "fail")
        self.assertEqual(by_id["narrative"]["source"], "Dexscreener")
        self.assertEqual(out["pass_count"], 7)
        self.assertEqual(out["verdict"], "WATCH")
        matched = cc.assemble(MINT, _clean_sources(live_narratives=["real"]), now=1_700_000_500)
        self.assertEqual(matched["verdict"], "BUY-WATCH")
        self.assertEqual(matched["pass_count"], 8)
        self.assertIn("legend", matched["rules"])
        self.assertIn(str(matched["check_count"]), matched["rules"]["legend"])
        self.assertNotIn("8/8 =", "hardcoded")
        self.assertIn("8/8", matched["rules"]["legend"])
        self.assertEqual(out["count_verdict"], "WATCH")
        self.assertFalse(out["forced_skip"])
        self.assertEqual(len(out["checks"]), 8)
        self.assertEqual(out["header"]["price_usd"], 0.0000913)
        self.assertEqual(out["header"]["price_source"], "Dexscreener")
        self.assertEqual(out["origin"]["launchpad"], "Pump.fun → bonded to PumpSwap")
        self.assertTrue(out["origin"]["description"].startswith("A real description"))
        for row in out["checks"]:
            self.assertTrue(row["source"])
            self.assertIn(row["status"], {"pass", "fail", "unknown"})


class HardRuleTests(unittest.TestCase):
    def test_rug_history_forces_skip_even_when_the_count_would_watch(self):
        out = cc.assemble(MINT, _clean_sources(
            creator_coins={
                "count": 2,
                "coins": [
                    {"mint": MINT},
                    {"mint": "OtherMintxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "ath_market_cap": 50},
                ],
            },
            prior_rugs=1,
            prior_rugs_checked=1,
        ), now=1_700_000_500)
        self.assertEqual(out["verdict"], "SKIP")
        self.assertTrue(out["forced_skip"])
        self.assertIn("rug-history dev", out["forced_reasons"])
        self.assertEqual(out["count_verdict"], "WATCH")
        dev = next(row for row in out["checks"] if row["id"] == "dev_history")
        self.assertEqual(dev["status"], "fail")

    def test_cluster_at_10_percent_forces_skip(self):
        out = cc.assemble(MINT, _clean_sources(traces=[
            {"owner": WALLET_A, "pct": 6, "funder": FUNDER, "source": "public RPC"},
            {"owner": WALLET_B, "pct": 4, "funder": FUNDER, "source": "public RPC"},
            {"owner": WALLET_C, "pct": 3, "funder": "other", "source": "public RPC"},
        ], holders=_holders(6, 4, 3, owners=[WALLET_A, WALLET_B, WALLET_C])))
        self.assertEqual(out["verdict"], "SKIP")
        self.assertTrue(out["bundle"]["bundled"])
        self.assertAlmostEqual(out["bundle"]["cluster_pct"], 10.0)
        self.assertIn("linked cluster ≥10%", out["forced_reasons"])
        row = next(item for item in out["checks"] if item["id"] == "not_bundled")
        self.assertEqual(row["status"], "fail")

    def test_cluster_under_10_does_not_force_skip(self):
        out = cc.assemble(MINT, _clean_sources(traces=[
            {"owner": WALLET_A, "pct": 5, "funder": FUNDER, "source": "public RPC"},
            {"owner": WALLET_B, "pct": 4.9, "funder": FUNDER, "source": "public RPC"},
            {"owner": WALLET_C, "pct": 3, "funder": "other", "source": "public RPC"},
        ]))
        self.assertFalse(out["bundle"]["bundled"])
        self.assertAlmostEqual(out["bundle"]["cluster_pct"], 9.9)
        self.assertEqual(out["verdict"], "WATCH")
        self.assertEqual(out["bundle"]["traces"][0]["funder"], FUNDER)
        self.assertEqual(len(out["bundle"]["traces"]), 3)

    def test_untraced_holders_stay_unknown_and_do_not_force_skip(self):
        out = cc.assemble(MINT, _clean_sources(traces=[
            {"owner": WALLET_A, "pct": 12, "funder": None, "source": "public RPC"},
            {"owner": WALLET_B, "pct": 4, "funder": "solo", "source": "public RPC"},
        ]))
        row = next(item for item in out["checks"] if item["id"] == "not_bundled")
        self.assertEqual(row["status"], "unknown")
        self.assertIsNone(out["bundle"]["cluster_pct"])
        self.assertFalse(out["forced_skip"])
        self.assertNotEqual(out["verdict"], "BUY-WATCH")


class ThresholdTests(unittest.TestCase):
    def test_top10_under_30_passes_and_matches_rugcheck_within_two_points(self):
        rug_holders = [{"pct": 12}, {"pct": 10}, {"pct": 5.5}]
        out = cc.assemble(MINT, _clean_sources(
            holders=_holders(12.2, 10.1, 5.4),
            rug={"lpLockedPct": 100, "topHolders": rug_holders, "totalHolders": 40},
        ))
        row = next(item for item in out["checks"] if item["id"] == "top10")
        self.assertEqual(row["status"], "pass")
        self.assertAlmostEqual(out["bundle"]["top10_pct"], 27.7)
        self.assertAlmostEqual(out["bundle"]["rugcheck_top10_pct"], 27.5)
        self.assertTrue(out["bundle"]["rugcheck_within_2pts"])
        self.assertLessEqual(out["bundle"]["rugcheck_delta"], 2.0)
        self.assertIn("RugCheck", row["detail"])
        self.assertEqual(row["source"], "Solana RPC")

    def test_top10_at_30_fails(self):
        out = cc.assemble(MINT, _clean_sources(holders=_holders(20, 10)))
        row = next(item for item in out["checks"] if item["id"] == "top10")
        self.assertEqual(row["status"], "fail")

    def test_missing_holder_list_is_unknown_not_a_pass(self):
        out = cc.assemble(MINT, _clean_sources(holders=[], traces=[]))
        row = next(item for item in out["checks"] if item["id"] == "top10")
        self.assertEqual(row["status"], "unknown")
        self.assertIn("Unknown", row["detail"])
        self.assertFalse(row["pass"])

    def test_organic_rules(self):
        few = cc.assemble(MINT, _clean_sources(
            dex={**_clean_sources()["dex"], "txns": {"h1": {"buys": 4, "sells": 3}}},
            organic={"known": False},
        ))
        few_row = next(item for item in few["checks"] if item["id"] == "organic")
        self.assertEqual(few_row["status"], "fail")
        self.assertIn("under 100", few_row["detail"])

        many = cc.assemble(MINT, _clean_sources(organic={"known": False}))
        many_row = next(item for item in many["checks"] if item["id"] == "organic")
        self.assertEqual(many_row["status"], "unknown")
        self.assertIn("Unknown", many_row["detail"])

        whale = cc.assemble(MINT, _clean_sources(
            organic={"known": True, "unique_traders": 120, "max_wallet_pct": 10.01, "source": "Helius"},
        ))
        whale_row = next(item for item in whale["checks"] if item["id"] == "organic")
        self.assertEqual(whale_row["status"], "fail")

        ok = cc.assemble(MINT, _clean_sources(
            organic={"known": True, "unique_traders": 100, "max_wallet_pct": 10, "source": "Helius"},
        ))
        ok_row = next(item for item in ok["checks"] if item["id"] == "organic")
        self.assertEqual(ok_row["status"], "pass")

    def test_socials_fail_when_the_x_account_is_not_older_than_the_coin(self):
        out = cc.assemble(MINT, _clean_sources(social_checks={
            "status": "fail",
            "detail": "X @example is not older than the coin",
            "source": "X",
        }))
        row = next(item for item in out["checks"] if item["id"] == "socials")
        self.assertEqual(row["status"], "fail")
        self.assertIn("not older", row["detail"])
        self.assertEqual(row["source"], "X")

    def test_mint_and_lp_do_not_invent_a_pass(self):
        out = cc.assemble(MINT, _clean_sources(rug={"totalHolders": 3}, authorities={"known": False}))
        lp = next(item for item in out["checks"] if item["id"] == "lp")
        auth = next(item for item in out["checks"] if item["id"] == "mint_freeze")
        self.assertEqual(lp["status"], "unknown")
        self.assertEqual(auth["status"], "unknown")
        self.assertEqual(lp["source"], "RugCheck")

    def test_zero_prior_coins_is_a_clean_dev(self):
        out = cc.assemble(MINT, _clean_sources())
        dev = next(item for item in out["checks"] if item["id"] == "dev_history")
        self.assertEqual(dev["status"], "pass")
        self.assertEqual(out["dev"]["wallet"], CREATOR)
        self.assertEqual(out["dev"]["launched"], 1)
        self.assertEqual(out["dev"]["launched_source"], "Pump.fun")
        self.assertIsNone(out["dev"]["sold"])
        self.assertIn("no API key", out["dev"]["sold_note"])


class CacheAndApiTests(unittest.TestCase):
    def setUp(self):
        cc.clear_cache()

    def tearDown(self):
        cc.clear_cache()

    def test_server_cache_collapses_repeat_lookups(self):
        calls = {"n": 0}

        def fake(mint):
            calls["n"] += 1
            return _clean_sources()

        with mock.patch.object(cc, "gather", side_effect=fake):
            first = cc.for_mint(MINT)
            second = cc.for_mint(MINT)
        self.assertEqual(calls["n"], 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["verdict"], first["verdict"])
        self.assertEqual(first["verdict"], "WATCH")

    def test_health_and_checklist_route_expose_the_verdict_name(self):
        health = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(health["panel_v"], 83)
        self.assertIn("qa_stale", health)
        self.assertIn("live_tick", health["features"])
        self.assertIn("coin_checklist", health["features"])
        self.assertIn("usd_price", health["features"])
        self.assertIn("live_tick", health["features"])

        with mock.patch.object(cc, "gather", return_value=_clean_sources()):
            cc.clear_cache()
            res = server.app.test_client().get("/api/coin/checklist?mint=" + MINT)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["verdict"], "WATCH")
        self.assertEqual(data["check_count"], 8)
        self.assertEqual([row["id"] for row in data["checks"]], [row[0] for row in cc.CHECKS])
        self.assertNotIn("EXAMPLE", res.get_data(as_text=True))


class QaXFixtureTests(unittest.TestCase):
    def test_buy_maps_to_buy_watch_and_skip_covers_both_reasons(self):
        import qa_x

        self.assertEqual(qa_x.mode_from_query("buy"), "buy-watch")
        self.assertEqual(qa_x.mode_from_query("buy-watch"), "buy-watch")
        self.assertIsNone(qa_x.mode_from_query(""))
        buy = qa_x.checklist_for(qa_x.QA_BUY_MINT)
        watch = qa_x.checklist_for(qa_x.QA_WATCH_MINT)
        rug = qa_x.checklist_for(qa_x.QA_RUG_MINT)
        bundled = qa_x.checklist_for(qa_x.QA_BUNDLE_MINT)
        self.assertEqual(buy["verdict"], "BUY-WATCH")
        self.assertFalse(buy["forced_skip"])
        self.assertEqual(watch["verdict"], "WATCH")
        self.assertFalse(watch["forced_skip"])
        self.assertEqual(rug["verdict"], "SKIP")
        self.assertIn("rug-history dev", rug["forced_reasons"])
        self.assertEqual(bundled["verdict"], "SKIP")
        self.assertIn("linked cluster ≥10%", bundled["forced_reasons"])
        self.assertEqual([row["mint"] for row in qa_x.rows_for("skip")], [qa_x.QA_RUG_MINT, qa_x.QA_BUNDLE_MINT])
        self.assertEqual(qa_x.rows_for(None), [])

        client = server.app.test_client()
        absent = client.get("/api/flagged").get_json()
        self.assertFalse(any(isinstance(row, dict) and row.get("qa_x_fixture") for row in absent))
        listed = client.get("/api/flagged?qa_x=skip").get_json()
        flags = [row for row in listed if isinstance(row, dict) and row.get("qa_x_fixture")]
        self.assertEqual(len(flags), 2)
        self.assertEqual({row["checklist_verdict"] for row in flags}, {"SKIP"})
        sheet = client.get("/api/coin/checklist?mint=" + qa_x.QA_BUNDLE_MINT + "&qa_x=skip").get_json()
        self.assertEqual(sheet["verdict"], "SKIP")
        self.assertTrue(sheet["forced_skip"])
        blocked = client.get("/api/coin/checklist?mint=" + qa_x.QA_RUG_MINT)
        self.assertEqual(blocked.status_code, 400)

    def test_lp_fallback_counts_and_unknown_does_not_force_skip(self):
        out = cc.assemble(MINT, _clean_sources(
            rug={"totalHolders": 3},
            lp_fallback={"status": "pass", "detail": "PumpSwap LP supply is 0 (burned)", "source": "Solana RPC"},
        ))
        lp = next(row for row in out["checks"] if row["id"] == "lp")
        self.assertEqual(lp["status"], "pass")
        self.assertEqual(lp["source"], "Solana RPC")
        self.assertFalse(out["forced_skip"])

    def test_dead_prior_within_one_hour_is_a_rug_dev(self):
        out = cc.assemble(MINT, _clean_sources(
            creator_coins={
                "count": 2,
                "coins": [
                    {"mint": MINT},
                    {
                        "mint": "OtherMintxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                        "created_timestamp": 1_600_000_000_000,
                        "last_trade_timestamp": 1_600_000_600_000,
                        "complete": False,
                    },
                ],
            },
        ), now=1_700_000_500)
        dev = next(row for row in out["checks"] if row["id"] == "dev_history")
        self.assertEqual(dev["status"], "fail")
        self.assertIn("dead within 1h", dev["detail"])
        self.assertEqual(out["verdict"], "SKIP")
        self.assertIn("rug-history dev", out["forced_reasons"])


if __name__ == "__main__":
    unittest.main()
