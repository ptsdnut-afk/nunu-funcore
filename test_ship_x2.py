"""SHIP X2 — new narrative, who's buying, catalyst, and the take-profit ladder.

panel_v stays 84. AC-X07 is the three rows plus their sources.
AC-X08 is a rerun FAIL. AC-X09 is a partial sell at each ladder rung.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import coin_checklist as cc
import game
import server
import test_coin_checklist as base

MINT = base.MINT
OTHER = "OtherMintxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
NOW = 1_700_000_500


def _launches(*hits, checked=None, failed=None):
    return {
        "checked": list(checked if checked is not None else ["Dexscreener", "Pump.fun"]),
        "failed": list(failed or []),
        "hits": list(hits),
    }


def _hit(symbol="PEPE", name="Pepe", created=None, source="Pump.fun", mint=OTHER):
    return {
        "mint": mint,
        "symbol": symbol,
        "name": name,
        "created_at": created,
        "source": source,
    }


class NarrativeRowTests(unittest.TestCase):
    def test_same_ticker_inside_seven_days_fails_and_names_the_source(self):
        pump = dict(base._clean_sources()["pump"])
        pump["symbol"] = "PEPE"
        pump["name"] = "Pepe"
        out = cc.assemble(MINT, base._clean_sources(
            pump=pump,
            launches=_launches(_hit(created=NOW - 2 * 86400, source="Pump.fun")),
        ), now=NOW)
        row = next(item for item in out["checks"] if item["id"] == "narrative")
        self.assertEqual(row["label"], "New narrative")
        self.assertEqual(row["status"], "fail")
        self.assertIn("Rerun", row["detail"])
        self.assertIn("PEPE", row["detail"])
        self.assertEqual(row["source"], "Pump.fun")

    def test_same_name_is_a_rerun_even_when_the_ticker_differs(self):
        pump = dict(base._clean_sources()["pump"])
        pump["symbol"] = "WIF"
        pump["name"] = "dog wif hat"
        out = cc.assemble(MINT, base._clean_sources(
            pump=pump,
            launches=_launches(_hit(
                symbol="HAT", name="dog wif hat", created=NOW - 86400, source="Dexscreener",
            )),
        ), now=NOW)
        row = next(item for item in out["checks"] if item["id"] == "narrative")
        self.assertEqual(row["status"], "fail")
        self.assertIn("same name", row["detail"])
        self.assertEqual(row["source"], "Dexscreener")

    def test_a_launch_older_than_seven_days_passes(self):
        pump = dict(base._clean_sources()["pump"])
        pump["symbol"] = "PEPE"
        pump["name"] = "Pepe"
        out = cc.assemble(MINT, base._clean_sources(
            pump=pump,
            launches=_launches(_hit(created=NOW - 8 * 86400)),
        ), now=NOW)
        row = next(item for item in out["checks"] if item["id"] == "narrative")
        self.assertEqual(row["status"], "pass")
        self.assertIn("7 days", row["detail"])
        self.assertEqual(row["source"], "Dexscreener + Pump.fun")

    def test_a_failed_search_stays_unknown_unless_the_other_source_found_a_rerun(self):
        pump = dict(base._clean_sources()["pump"])
        pump["symbol"] = "PEPE"
        pump["name"] = "Pepe"
        quiet = cc.assemble(MINT, base._clean_sources(
            pump=pump,
            launches=_launches(checked=["Dexscreener"], failed=["Pump.fun"]),
        ), now=NOW)
        quiet_row = next(item for item in quiet["checks"] if item["id"] == "narrative")
        self.assertEqual(quiet_row["status"], "unknown")
        self.assertIn("Pump.fun", quiet_row["detail"])

        found = cc.assemble(MINT, base._clean_sources(
            pump=pump,
            launches=_launches(
                _hit(created=NOW - 3600, source="Dexscreener"),
                checked=["Dexscreener"],
                failed=["Pump.fun"],
            ),
        ), now=NOW)
        found_row = next(item for item in found["checks"] if item["id"] == "narrative")
        self.assertEqual(found_row["status"], "fail")
        self.assertEqual(found_row["source"], "Dexscreener")

    def test_parsers_keep_dated_solana_hits_and_drop_other_chains(self):
        dex = cc._dex_hits({"pairs": [
            {
                "chainId": "solana",
                "pairCreatedAt": 1_700_000_000_000,
                "baseToken": {"address": OTHER, "symbol": "PEPE", "name": "Pepe"},
            },
            {
                "chainId": "bsc",
                "pairCreatedAt": 1_700_000_000_000,
                "baseToken": {"address": "0xabc", "symbol": "PEPE", "name": "Pepe"},
            },
        ]})
        self.assertEqual(len(dex), 1)
        self.assertEqual(dex[0]["source"], "Dexscreener")
        self.assertAlmostEqual(dex[0]["created_at"], 1_700_000_000)
        pump = cc._pump_hits([
            {"mint": OTHER, "symbol": "PEPE", "name": "Pepe", "created_timestamp": 1_700_000_000_000},
        ])
        self.assertEqual(pump[0]["source"], "Pump.fun")
        self.assertEqual(pump[0]["symbol"], "PEPE")


class BuyerAndCatalystTests(unittest.TestCase):
    def test_one_tracked_wallet_passes_and_shows_the_name(self):
        book = cc.load_watchlist()
        self.assertTrue(any(row["name"] == "PeatFossilYarn" and len(row["address"]) >= 32 for row in book))
        out = cc.assemble(MINT, base._clean_sources(buyers={
            "ok": True,
            "empty": False,
            "buyers": ["PeatFossilYarn"],
            "names": ["PeatFossilYarn"],
        }))
        row = next(item for item in out["checks"] if item["id"] == "buyers")
        self.assertEqual(row["label"], "Who's buying")
        self.assertEqual(row["status"], "pass")
        self.assertIn("PeatFossilYarn", row["detail"])
        self.assertIn("watchlist", row["source"])
        self.assertIn("Solana RPC", row["source"])

    def test_a_checked_watchlist_with_no_buy_fails_and_still_names_them(self):
        out = cc.assemble(MINT, base._clean_sources(buyers={
            "ok": True,
            "empty": False,
            "buyers": [],
            "names": ["PeatFossilYarn"],
        }))
        row = next(item for item in out["checks"] if item["id"] == "buyers")
        self.assertEqual(row["status"], "fail")
        self.assertIn("PeatFossilYarn", row["detail"])
        self.assertIn("None of the tracked wallets", row["detail"])

    def test_a_missed_balance_check_is_unknown(self):
        out = cc.assemble(MINT, base._clean_sources(buyers={
            "ok": False,
            "empty": False,
            "buyers": [],
            "names": ["PeatFossilYarn"],
        }))
        row = next(item for item in out["checks"] if item["id"] == "buyers")
        self.assertEqual(row["status"], "unknown")
        self.assertIn("Unknown", row["detail"])

    def test_catalyst_prints_the_real_signal_and_does_not_invent_one_from_price(self):
        dex = dict(base._clean_sources()["dex"])
        dex["priceChange"] = {"h1": 900}
        loud = cc.assemble(MINT, base._clean_sources(
            dex=dex,
            catalyst={"signal": "Dexscreener community takeover (approved)", "source": "Dexscreener"},
        ))
        row = next(item for item in loud["checks"] if item["id"] == "catalyst")
        self.assertEqual(row["status"], "pass")
        self.assertEqual(row["detail"], "Dexscreener community takeover (approved)")
        self.assertEqual(row["source"], "Dexscreener")

        quiet = cc.assemble(MINT, base._clean_sources(
            dex=dex,
            catalyst={"signal": None, "source": None, "checked": ["Dexscreener", "Pump.fun"]},
        ))
        quiet_row = next(item for item in quiet["checks"] if item["id"] == "catalyst")
        self.assertEqual(quiet_row["status"], "unknown")
        self.assertEqual(quiet_row["detail"], "Unknown")
        self.assertNotIn("900", quiet_row["detail"])

    def test_catalyst_signal_uses_boosts_live_and_paid_orders_only(self):
        boost = cc.catalyst_signal(None, [], {"amount": 30})
        self.assertEqual(boost["signal"], "Dexscreener boost 30")
        self.assertEqual(boost["source"], "Dexscreener")
        live = cc.catalyst_signal({"is_currently_live": True}, [], None)
        self.assertEqual(live["signal"], "Live on Pump.fun")
        self.assertEqual(live["source"], "Pump.fun")
        cancelled = cc.catalyst_signal(
            {"is_currently_live": False},
            [{"type": "communityTakeover", "status": "cancelled"}],
            None,
        )
        self.assertIsNone(cancelled["signal"])
        paid = cc.catalyst_signal(
            None,
            [{"type": "tokenProfile", "status": "approved"}],
            None,
        )
        self.assertEqual(paid["signal"], "Dexscreener token profile (approved)")
        self.assertEqual(paid["source"], "Dexscreener")
        self.assertIsNone(cc.catalyst_signal(None, None, None)["signal"])


def _tick(price: float) -> dict:
    return {
        "mint": MINT,
        "price_usd": price,
        "price_ts": "2026-10-31T01:02:03Z",
        "quoted_at": "2026-10-31T01:02:03Z",
        "quoted_epoch": time.time(),
        "quote_age_s": 0.2,
        "quote_live": True,
        "source": "test",
    }


def _position(entry=1.0, stake=1.0, ladder=None, tp_pct=None):
    qty = stake / entry
    return {
        "id": "pos1",
        "mint": MINT,
        "symbol": "NUNU",
        "name": "Nunu",
        "stake_usd": stake,
        "stake_original": stake,
        "entry_price_usd": entry,
        "qty_tokens": qty,
        "qty_original": qty,
        "last_price_usd": entry,
        "opened_at": "2026-10-31T00:00:00Z",
        "sl_pct": 20,
        "tp_pct": tp_pct,
        "tp_ladder": game.default_tp_ladder() if ladder is None else ladder,
        "tp_fired": [],
    }


class TakeProfitLadderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(game, "STATE_PATH", root / "state.json"),
            mock.patch.object(game, "SETTINGS_PATH", root / "settings.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def _run(self, price, pos):
        st = {
            "session_id": "x2",
            "status": "active",
            "started_at": "2026-10-31T00:00:00Z",
            "ends_at": "2026-10-31T23:00:00Z",
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [pos],
            "closed": [],
            "events": [],
        }
        with mock.patch.object(game, "fetch_live_tick", return_value=_tick(price)):
            return game.tick_session(st)

    def test_each_rung_logs_a_partial_sell_when_price_clears_4x(self):
        out = self._run(4.0, _position())
        self.assertEqual(out["positions"], [])
        closed = out["closed"]
        self.assertEqual(len(closed), 4)
        self.assertEqual([row["tp_rung"] for row in closed], ["4x", "3x", "2x", "+50%"])
        for row in closed:
            self.assertEqual(row["close_reason"], "tp")
            self.assertTrue(row["partial"])
            self.assertAlmostEqual(row["sell_pct"], 25.0, places=2)
            self.assertAlmostEqual(row["pnl_pct"], 300.0, places=2)
            self.assertAlmostEqual(row["exit_price_usd"], 4.0)
        self.assertAlmostEqual(out["cash_usd"], 8.0, places=4)
        kinds = [event["kind"] for event in out["events"] if event["kind"] == "tp"]
        self.assertEqual(len(kinds), 4)

    def test_only_rungs_the_price_has_reached_fire(self):
        out = self._run(2.0, _position())
        self.assertEqual(len(out["positions"]), 1)
        self.assertEqual([row["tp_rung"] for row in out["closed"]], ["2x", "+50%"])
        left = out["positions"][0]
        self.assertAlmostEqual(left["qty_tokens"], 0.5, places=6)
        self.assertEqual(left["tp_fired"], ["p50", "x2"])
        self.assertAlmostEqual(out["cash_usd"], 5.0, places=4)

    def test_a_stale_tick_does_not_take_profit(self):
        stale = dict(_tick(4.0))
        stale["quote_live"] = False
        stale["quote_age_s"] = 9
        st = {
            "session_id": "x2",
            "status": "active",
            "started_at": "2026-10-31T00:00:00Z",
            "ends_at": "2026-10-31T23:00:00Z",
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [_position()],
            "closed": [],
            "events": [],
        }
        with mock.patch.object(game, "fetch_live_tick", return_value=stale):
            out = game.tick_session(st)
        self.assertEqual(out["closed"], [])
        self.assertEqual(len(out["positions"]), 1)

    def test_edited_ladder_sells_the_saved_percent(self):
        ladder = [{"id": "half", "label": "+50%", "multiple": 1.5, "sell_pct": 40}]
        saved = game.save_tp_ladder(ladder)
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(game.load_tp_ladder()[0]["sell_pct"], 40)
        out = self._run(1.5, _position(ladder=game.load_tp_ladder()))
        self.assertEqual(len(out["closed"]), 1)
        self.assertAlmostEqual(out["closed"][0]["sell_pct"], 40.0, places=2)
        self.assertEqual(out["closed"][0]["tp_rung"], "+50%")
        self.assertEqual(len(out["positions"]), 1)

    def test_settings_route_rejects_a_ladder_that_is_not_a_real_rung(self):
        client = server.app.test_client()
        bad = client.post("/api/game/settings", json={"tp_ladder": [{"multiple": 1, "sell_pct": 25}]})
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.get_json()["ok"])
        good = client.post("/api/game/settings", json={"tp_ladder": game.default_tp_ladder()})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(len(good.get_json()["tp_ladder"]), 4)

    def test_health_keeps_panel_v_84_and_names_the_x2_rules(self):
        health = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(health["panel_v"], 84)
        self.assertIn("x2_rules", health["features"])
        self.assertIn("25%", health["x2_rules"])
        self.assertIn("New narrative", health["coin_checklist"])


if __name__ == "__main__":
    unittest.main()
