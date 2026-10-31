"""SHIP W2 — Jupiter mid ticks (AC-W01…W19). Not a QA PASS.

The Game mark is Jupiter Price API usdPrice. One shared fetch per mint per
second. A print older than 5s is not a fill. GeckoTerminal is not used.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import game
import server

MINT = "3KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jup(price: float, block: int = 10) -> tuple[int, dict]:
    return 200, {MINT: {"usdPrice": price, "blockId": block}}


class LiveTickCacheTests(unittest.TestCase):
    def setUp(self):
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        import trade_stream
        trade_stream.reset_for_tests()

    def tearDown(self):
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)

    def test_ttl_is_at_most_two_seconds_and_coalesces_callers(self):
        self.assertLessEqual(game.LIVE_TICK_TTL_S, 2.0)
        self.assertLessEqual(game.QUOTE_FRESH_SLA_S, 5.0)
        calls = {"n": 0}

        def fake(_url: str):
            calls["n"] += 1
            return _jup(0.0000785, calls["n"])

        with mock.patch.object(game, "_http_json", side_effect=fake):
            first = game.fetch_live_tick(MINT)
            after_miss = calls["n"]
            second = game.fetch_live_tick(MINT)
        self.assertEqual(after_miss, 1)
        self.assertEqual(calls["n"], after_miss)
        self.assertTrue(game.fillable_tick(first))
        self.assertEqual(second["price_usd"], first["price_usd"])
        self.assertEqual(second["price_ts"], first["price_ts"])
        self.assertLessEqual(second["quote_age_s"], game.LIVE_TICK_TTL_S)
        self.assertEqual(first["source"], "jupiter")

    def test_stale_cache_is_not_a_live_fill_and_backoff_skips_upstream(self):
        game._live_cache[MINT] = (
            time.time() - 8,
            {"mint": MINT, "price_usd": 0.5, "price_ts": "2020-01-01T00:00:00Z", "source": "jup"},
        )
        calls = {"n": 0}

        def fake(_url: str):
            calls["n"] += 1
            return 429, None

        with mock.patch.object(game, "_http_json", side_effect=fake):
            stale = game.fetch_live_tick(MINT)
            tried = calls["n"]
            again = game.fetch_live_tick(MINT)
        self.assertGreaterEqual(tried, 1)
        self.assertFalse(game.fillable_tick(stale))
        self.assertFalse(stale["quote_live"])
        self.assertGreater(stale["quote_age_s"], 5)
        self.assertFalse(again["quote_live"])
        self.assertEqual(calls["n"], tried)

    def test_missing_jupiter_price_does_not_call_gecko(self):
        seen = []

        def fake(url: str):
            seen.append(url)
            return 200, {}

        with mock.patch.object(game, "_http_json", side_effect=fake):
            tick = game.fetch_live_tick(MINT, bypass_cache=True)
        self.assertIsNone(tick)
        self.assertTrue(seen)
        self.assertTrue(all("jup.ag/price" in url for url in seen))
        self.assertFalse(any("gecko" in url for url in seen))
        self.assertFalse(any("swap/v1/quote" in url for url in seen))

    def test_price_api_mid_ignores_a_swap_quote(self):
        """usdPrice is the mid. A one-way quote that would print ~8% higher is not fetched."""
        def fake(url: str):
            self.assertNotIn("swap/v1/quote", url)
            return 200, {MINT: {"usdPrice": 0.000737, "decimals": 6, "blockId": 9}}

        with mock.patch.object(game, "_http_json", side_effect=fake):
            tick = game.fetch_live_tick(MINT, bypass_cache=True)
        self.assertTrue(game.fillable_tick(tick))
        self.assertAlmostEqual(tick["price_usd"], 0.000737)
        self.assertEqual(tick["source"], "jupiter")
        dex = 0.000737
        gap = abs(tick["price_usd"] - dex) / dex
        self.assertLess(gap, 0.02)
        inflated = 0.00080
        self.assertGreater(abs(tick["price_usd"] - inflated) / inflated, 0.02)

    def test_ten_concurrent_polls_make_one_upstream_call(self):
        calls = {"n": 0}
        started = threading.Barrier(10)

        def fake(_url: str):
            time.sleep(0.05)
            calls["n"] += 1
            return _jup(0.000737, 3)

        def worker():
            started.wait(timeout=2)
            game.fetch_live_tick(MINT)

        with mock.patch.object(game, "_http_json", side_effect=fake):
            threads = [threading.Thread(target=worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=3)
        self.assertEqual(calls["n"], 1)
        self.assertTrue(all(not t.is_alive() for t in threads))

    def test_qa_tick_down_walks_one_session_and_leaves_the_cache(self):
        with mock.patch.object(game, "_http_json", return_value=_jup(1.0, 4)):
            real = game.fetch_live_tick(MINT)
            game._qa_walks["qa-a"] = {MINT: {"t0": time.time() - (game.QA_WALK_SPAN_S - 1), "px0": 1.0}}
            walked = game.fetch_live_tick(MINT, qa_sid="qa-a")
            other = game.fetch_live_tick(MINT)
        self.assertAlmostEqual(real["price_usd"], 1.0)
        self.assertLessEqual(walked["price_usd"], 1.0 * (1.0 - 0.25))
        self.assertGreater(walked["qa_walk_drop"], 0.25)
        self.assertEqual(walked["source"], "qa_tick")
        self.assertEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        self.assertEqual(other["source"], "jupiter")
        self.assertAlmostEqual(other["price_usd"], 1.0)

    def test_same_price_one_second_polls_stay_under_two_seconds(self):
        """Ten polls, identical usdPrice. Age is since the fetch, not the print."""
        clock = {"t": 1_700_000_000.0}
        calls = {"n": 0}

        def now():
            return clock["t"]

        def fake(_url, timeout=None):
            calls["n"] += 1
            return _jup(0.0000785, 7)

        with mock.patch("game.time.time", side_effect=now), mock.patch.object(game, "_http_json", side_effect=fake):
            for _ in range(10):
                tick = game.fetch_live_tick(MINT)
                self.assertIsNotNone(tick)
                self.assertLess(tick["quote_age_s"], 2.0)
                self.assertFalse(game.tick_is_stale(tick["quote_age_s"], tick["quote_sla_s"]))
                self.assertTrue(tick["quote_live"])
                clock["t"] += 1.0
        self.assertEqual(calls["n"], 10)

    def test_fetch_failures_for_six_seconds_go_stale(self):
        clock = {"t": 1_700_000_000.0}
        calls = {"n": 0}

        def now():
            return clock["t"]

        def fake(_url, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _jup(1.25, 3)
            return 0, None

        with mock.patch("game.time.time", side_effect=now), mock.patch.object(game, "_http_json", side_effect=fake):
            fresh = game.fetch_live_tick(MINT)
            self.assertTrue(fresh["quote_live"])
            last = fresh
            for _ in range(6):
                clock["t"] += 1.0
                last = game.fetch_live_tick(MINT)
        self.assertGreater(calls["n"], 1)
        self.assertGreater(last["quote_age_s"], 5)
        self.assertTrue(game.tick_is_stale(last["quote_age_s"], last["quote_sla_s"]))
        self.assertFalse(last["quote_live"])

    def test_cold_tick_waits_and_returns_a_price(self):
        calls = {"n": 0}

        def fake(_url, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return 0, None
            return _jup(0.42, 1)

        with mock.patch.object(game, "_http_json", side_effect=fake):
            tick = game.fetch_live_tick(MINT)
            body = server.app.test_client().get("/api/game/tick?mint=" + MINT).get_json()
        self.assertTrue(game.fillable_tick(tick))
        self.assertAlmostEqual(tick["price_usd"], 0.42)
        self.assertTrue(body["ok"])
        self.assertAlmostEqual(body["price_usd"], 0.42)
        self.assertFalse(body["stale"])
        self.assertNotEqual(body.get("error"), "no price")

    def test_tick_quote_and_coin_mark_watched_and_start_the_stream(self):
        with mock.patch.object(game, "_http_json", return_value=_jup(1.0)), mock.patch.object(
            game, "fetch_dex_token", return_value={"symbol": "NVD", "price_usd": 1.0}
        ), mock.patch("trade_stream.ensure_started") as started, mock.patch(
            "trade_stream.note_view"
        ) as noted, mock.patch("filter.coin_payload", return_value={"ok": True, "mint": MINT}):
            game.quote_payload(MINT)
            client = server.app.test_client()
            tick = client.get("/api/game/tick?mint=" + MINT)
            coin = client.get("/api/coin?mint=" + MINT)
        self.assertEqual(tick.status_code, 200)
        self.assertTrue(tick.get_json().get("ok"))
        self.assertEqual(coin.status_code, 200)
        self.assertGreaterEqual(started.call_count, 3)
        watched = [call.args[0] for call in noted.call_args_list if call.args]
        self.assertIn(MINT, watched)


class TradeFillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        import trade_stream
        trade_stream.reset_for_tests()
        for patch in (
            mock.patch.object(game, "PROFILE_PATH", root / "profile.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
            mock.patch.object(game, "STATE_PATH", root / "state.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _active(self, positions):
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "livetick",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": positions,
            "closed": [],
            "events": [],
        })

    def test_buy_records_the_live_tick_not_the_dex_snapshot(self):
        dex = {
            "symbol": "NVD",
            "name": "Nvidia",
            "price_usd": 0.000112,
            "market_cap_usd": 111700,
            "pair_url": "https://dexscreener.com/solana/nvdia-pair",
            "quote_live": True,
            "mcap_progress": {"now_source": "dex", "now_available": True, "market_cap_usd": 111700},
        }
        game.start_session(force=True)
        before = time.time()
        with mock.patch.object(game, "fetch_dex_token", return_value=dex), mock.patch.object(
            game, "_http_json", return_value=_jup(0.0000785)
        ):
            out = game.buy(MINT, 1.0, sl_pct=20, tp_pct=100)
        self.assertTrue(out["ok"], out)
        pos = out["position"]
        self.assertAlmostEqual(pos["entry_price_usd"], 0.0000785)
        self.assertNotAlmostEqual(pos["entry_price_usd"], 0.000112)
        self.assertEqual(pos["price_ts"], pos["entry_price_ts"])
        stamped = datetime.strptime(pos["price_ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(abs(time.time() - stamped.timestamp()), 5)
        self.assertGreaterEqual(stamped.timestamp(), before - 1)
        # PnL mark uses that entry, not the Dex snapshot.
        self.assertAlmostEqual(pos["last_price_usd"], pos["entry_price_usd"])

    def test_sell_fills_at_the_tick_and_pnl_uses_those_prices(self):
        entry = 0.0000785
        exit_px = 0.0000913
        stake = 1.0
        qty = stake / entry
        self._active([{
            "id": "pos1",
            "mint": MINT,
            "symbol": "NVD",
            "name": "Nvidia",
            "stake_usd": stake,
            "entry_price_usd": entry,
            "entry_price_ts": "2026-10-31T00:00:00Z",
            "qty_tokens": qty,
            "last_price_usd": entry,
            "opened_at": _utc(datetime.now(timezone.utc) - timedelta(minutes=10)),
            "sl_pct": None,
            "tp_pct": None,
        }])
        with mock.patch.object(game, "_http_json", return_value=_jup(exit_px)):
            out = game.sell("pos1")
        self.assertTrue(out["ok"], out)
        closed = out["state"]["closed"][0]
        self.assertEqual(closed["close_reason"], "manual")
        self.assertAlmostEqual(closed["exit_price_usd"], exit_px)
        self.assertAlmostEqual(closed["entry_price_usd"], entry)
        self.assertEqual(closed["price_ts"], out["price_ts"])
        stamped = datetime.strptime(closed["price_ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(abs(time.time() - stamped.timestamp()), 5)
        expected_pnl = round(exit_px * qty - stake, 6)
        expected_pct = round((exit_px / entry - 1.0) * 100, 2)
        self.assertAlmostEqual(closed["pnl_usd"], expected_pnl)
        self.assertAlmostEqual(closed["pnl_pct"], expected_pct)

    def test_stop_fills_at_the_fresh_tick_and_ignores_a_stale_print(self):
        entry = 0.0001117
        shown = 0.00009174  # about -17.9%, inside a 20% stop
        trigger = 0.0000856  # about -23.4% — the tick that crosses
        stake = 1.0
        qty = stake / entry
        pos = {
            "id": "pos1",
            "mint": MINT,
            "symbol": "NVD",
            "stake_usd": stake,
            "entry_price_usd": entry,
            "qty_tokens": qty,
            "last_price_usd": shown,
            "opened_at": _utc(datetime.now(timezone.utc) - timedelta(minutes=10)),
            "sl_pct": 20,
            "tp_pct": 100,
        }
        self._active([dict(pos)])
        game._live_cache[MINT] = (
            time.time() - 8,
            {"mint": MINT, "price_usd": trigger, "price_ts": "2020-01-01T00:00:00Z", "source": "jup"},
        )
        with mock.patch.object(game, "_http_json", return_value=(429, None)):
            held = game.tick_session()
        self.assertEqual(held["status"], "active")
        self.assertEqual(len(held["positions"]), 1)
        self.assertAlmostEqual(held["positions"][0]["last_price_usd"], shown)

        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        with mock.patch.object(game, "_http_json", return_value=_jup(trigger)):
            closed = game.tick_session()
        self.assertEqual(closed["positions"], [])
        row = closed["closed"][0]
        self.assertEqual(row["close_reason"], "stop")
        fill = game.stop_fill_price(entry, trigger, 20)
        self.assertAlmostEqual(row["exit_price_usd"], fill)
        self.assertNotAlmostEqual(row["exit_price_usd"], shown)
        self.assertGreaterEqual(fill, entry * 0.79)
        self.assertTrue(row["price_ts"])
        stamped = datetime.strptime(row["price_ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(abs(time.time() - stamped.timestamp()), 5)
        expected_pnl = round(fill * qty - stake, 6)
        expected_pct = round((fill / entry - 1.0) * 100, 2)
        self.assertAlmostEqual(row["pnl_usd"], expected_pnl)
        self.assertAlmostEqual(row["pnl_pct"], expected_pct)

    def test_stop_lands_within_two_points_of_the_stop(self):
        entry = 1.0
        trigger = 0.79
        self._active([{
            "id": "pos1",
            "mint": MINT,
            "symbol": "NVD",
            "stake_usd": 1.0,
            "entry_price_usd": entry,
            "qty_tokens": 1.0,
            "last_price_usd": 0.85,
            "opened_at": _utc(datetime.now(timezone.utc) - timedelta(minutes=10)),
            "sl_pct": 20,
            "tp_pct": 100,
        }])
        with mock.patch.object(game, "_http_json", return_value=_jup(trigger)):
            closed = game.tick_session()
        row = closed["closed"][0]
        self.assertEqual(row["close_reason"], "stop")
        self.assertAlmostEqual(row["pnl_pct"], -21.0, places=2)
        self.assertLess(abs(row["pnl_pct"] - (-20)), 2.0)

    def test_qa_walk_reaches_a_twenty_percent_stop_within_sixty_seconds(self):
        """W07: armed walk crosses −20% inside the span and that stop fills.

        The shared cache stays the real Jupiter price, so another session's
        tick is not the walked print.
        """
        self.assertLessEqual(game.QA_WALK_SPAN_S, 60.0)
        self.assertGreaterEqual(game.QA_WALK_DROP, 0.20)
        cross_s = game.QA_WALK_SPAN_S * (0.20 / game.QA_WALK_DROP)
        self.assertLessEqual(cross_s, 60.0)
        self._active([{
            "id": "pos1",
            "mint": MINT,
            "symbol": "NVD",
            "stake_usd": 1.0,
            "entry_price_usd": 1.0,
            "qty_tokens": 1.0,
            "last_price_usd": 1.0,
            "opened_at": _utc(datetime.now(timezone.utc) - timedelta(minutes=10)),
            "sl_pct": 20,
            "tp_pct": 100,
        }])
        game._qa_walks["stop-sid"] = {MINT: {"t0": time.time() - (game.QA_WALK_SPAN_S - 1), "px0": 1.0}}
        game.set_qa_walk_sid("stop-sid")
        game.set_client_sid("stop-sid")
        booked = game.load_state()
        booked["positions"][0]["client_id"] = "stop-sid"
        game.save_state(booked)
        with mock.patch.object(game, "_http_json", return_value=_jup(1.0)):
            shown = game.fetch_live_tick(MINT)
            held = game.tick_session()
            game.set_qa_walk_sid(None)
            game.set_client_sid(None)
            other = game.fetch_live_tick(MINT)
        self.assertLessEqual(shown["price_usd"], 0.80)
        self.assertEqual(shown["source"], "qa_tick")
        self.assertEqual(held["positions"], [])
        self.assertEqual(held["closed"][0]["close_reason"], "stop")
        self.assertLessEqual(held["closed"][0]["pnl_pct"], -20.0)
        self.assertEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        self.assertEqual(game._live_cache[MINT][1]["source"], "jupiter")
        self.assertAlmostEqual(other["price_usd"], 1.0)
        self.assertEqual(other["source"], "jupiter")

    def test_one_request_id_opens_one_position(self):
        dex = {"symbol": "NVD", "name": "Nvidia", "price_usd": 0.000112, "pair_url": "https://dexscreener.com/solana/x"}
        game.start_session(force=True)
        with mock.patch.object(game, "fetch_dex_token", return_value=dex), mock.patch.object(
            game, "_http_json", return_value=_jup(0.0000785)
        ):
            first = game.buy(MINT, 1.0, sl_pct=20, tp_pct=100, client_request_id="click-1")
            second = game.buy(MINT, 1.0, sl_pct=20, tp_pct=100, client_request_id="click-1")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertTrue(second.get("deduped"))
        self.assertEqual(first["position"]["id"], second["position"]["id"])
        self.assertEqual(len(game.load_state()["positions"]), 1)
        self.assertAlmostEqual(game.load_state()["positions"][0]["entry_price_usd"], 0.0000785)

    def test_sell_all_leaves_zero_open(self):
        opened = _utc(datetime.now(timezone.utc) - timedelta(minutes=10))
        rows = []
        for pid in ("a", "b"):
            rows.append({
                "id": pid,
                "mint": MINT,
                "symbol": "APPLE",
                "stake_usd": 1.0,
                "entry_price_usd": 1.0,
                "qty_tokens": 1.0,
                "last_price_usd": 1.05,
                "opened_at": opened,
                "sl_pct": 20,
                "tp_pct": 100,
            })
        self._active(rows)
        with mock.patch.object(game, "_http_json", return_value=_jup(1.08)):
            out = game.sell_all()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["sold"], 2)
        self.assertEqual(out["open_count"], 0)
        self.assertEqual(out["state"]["positions"], [])
        self.assertEqual(len(out["state"]["closed"]), 2)

    def test_sell_all_closes_two_different_mints(self):
        """W11: a second mint must not stay open after Sell all."""
        other = "B" * 44
        opened = _utc(datetime.now(timezone.utc) - timedelta(minutes=10))
        rows = []
        for pid, mint, px in (("a", MINT, 1.0), ("b", other, 2.0)):
            rows.append({
                "id": pid,
                "mint": mint,
                "symbol": pid,
                "stake_usd": 1.0,
                "entry_price_usd": px,
                "qty_tokens": 1.0 / px,
                "last_price_usd": px,
                "opened_at": opened,
                "sl_pct": 20,
                "tp_pct": 100,
            })
        self._active(rows)

        def fake(url, timeout=None):
            if other in url:
                return 200, {other: {"usdPrice": 2.0, "blockId": 2}}
            return 200, {MINT: {"usdPrice": 1.0, "blockId": 1}}

        with mock.patch.object(game, "_http_json", side_effect=fake):
            out = game.sell_all()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["sold"], 2)
        self.assertEqual(out["open_count"], 0)
        self.assertEqual(out["state"]["positions"], [])
        self.assertEqual(len(out["state"]["closed"]), 2)


class HealthAndChartTests(unittest.TestCase):
    def test_health_is_v82_with_live_tick_and_a_five_second_sla(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 94)
        self.assertIn("live_tick", data["features"])
        self.assertLessEqual(data["quote_sla_s"], 5.0)
        self.assertEqual(data["quote_sla_s"], 5.0)
        self.assertLessEqual(data["live_tick_ttl_s"], 2.0)
        self.assertIn("stale", data["live_tick"])
        self.assertIn("price_ts", data["live_tick"])
        self.assertIn("qa_stale", data)
        self.assertIn("?qa_stale=1", data["qa_stale"])
        self.assertIn("does not change the server", data["qa_stale"])

    def test_tick_route_returns_the_live_price_and_a_stale_label(self):
        fresh = {
            "mint": MINT,
            "price_usd": 0.0000785,
            "price_ts": "2026-10-31T00:01:12Z",
            "quoted_at": "2026-10-31T00:01:12Z",
            "quoted_epoch": time.time(),
            "quote_age_s": 0.4,
            "quote_live": True,
            "source": "jupiter",
        }
        client = server.app.test_client()
        with mock.patch.object(server.game_mod, "fetch_live_tick", return_value=fresh):
            res = client.get("/api/game/tick?mint=" + MINT)
        body = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["price_usd"], 0.0000785)
        self.assertEqual(body["price_ts"], "2026-10-31T00:01:12Z")
        self.assertTrue(body["quote_live"])
        self.assertEqual(body["quote_sla_s"], 5.0)
        self.assertFalse(body["stale"])

        stale = dict(fresh, quote_live=False, quote_age_s=9.2, price_usd=0.0000785)
        with mock.patch.object(server.game_mod, "fetch_live_tick", return_value=stale):
            res = client.get("/api/game/tick?mint=" + MINT)
        body = res.get_json()
        self.assertFalse(body["quote_live"])
        self.assertTrue(body["stale"])
        self.assertIn("stale", body["stale_label"])
        self.assertIn("9", body["stale_label"])

        lied = dict(fresh, quote_live=True, quote_age_s=130.4, price_usd=0.0000785)
        with mock.patch.object(server.game_mod, "fetch_live_tick", return_value=lied):
            res = client.get("/api/game/tick?mint=" + MINT)
        body = res.get_json()
        self.assertGreater(body["quote_age_s"], body["quote_sla_s"])
        self.assertTrue(body["stale"])
        self.assertFalse(body["quote_live"])
        self.assertTrue(game.tick_is_stale(body["quote_age_s"], body["quote_sla_s"]))

    def test_stale_trade_buttons_say_waiting_for_live_price(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        start = html.index("function tradeButtonLabel")
        end = html.index("function tradeMarkHint")
        script = html[start:end] + """
const label = "Waiting for live price";
if (tradeButtonLabel("buy", true) !== label) process.exit(2);
if (tradeButtonLabel("sell", true) !== label) process.exit(3);
if (tradeButtonLabel("sellall", true) !== label) process.exit(4);
if (tradeButtonLabel("quote", true) !== label) process.exit(5);
if (tradeButtonLabel("buy", false) !== "▲ BUY") process.exit(6);
if (tradeButtonLabel("sell", false) !== "Sell this") process.exit(7);
if (tradeButtonLabel("sellall", false) !== "▼ SELL ALL") process.exit(8);
if (tradeButtonLabel("buy", false).indexOf("Waiting") >= 0) process.exit(9);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn('tradeButtonLabel("buy", stale)', html)
        self.assertIn('tradeButtonLabel("sell", stale)', html)
        self.assertIn('tradeButtonLabel("sellall", stale)', html)
        self.assertIn("function staleLabel", html)

    def test_panel_chart_ignores_zero_and_rocket_keeps_moving(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('data-feature="live_tick"', html)
        self.assertIn("LIVE_TICK_MS: 500", html)
        self.assertIn("/api/game/tick?mint=", html)
        self.assertIn("rocket-sink", html)
        down = html[html.index('.rocket-live[data-mood="down"] .rocket-wrap'):html.index('.rocket-live[data-mood="down"] .rocket-wrap') + 180]
        self.assertNotIn("animation: none", down)
        self.assertNotIn("padding-bottom: var(--follow-clearance, 72px)", html)
        self.assertIn("function qaStaleOn()", html)
        self.assertIn('get("qa_stale") === "1"', html)
        self.assertIn("client_request_id", html)
        self.assertNotIn('id="gLastTick">$0.00', html)
        self.assertIn("data-redraw", html)
        self.assertIn("position: static; display: inline-flex;", html)
        pure = html[html.index("/* ship-w-pure-start */"):html.index("/* ship-w-pure-end */")]
        script = pure + r"""
const flat = seriesPoints([0, 0, 0]);
if (flat) process.exit(2);
const one = seriesPoints([0.0000785]);
if (one) process.exit(3);
const line = seriesPoints([0, 0.0000785, 0.0000913]);
if (!line || line.indexOf("NaN") >= 0) process.exit(4);
const parts = line.split(" ");
if (parts.length !== 2) process.exit(5);
const y1 = Number(parts[0].split(",")[1]);
const y2 = Number(parts[1].split(",")[1]);
if (!(y1 > 0 && y1 < 72 && y2 > 0 && y2 < 72)) process.exit(6);
if (!(y2 < y1)) process.exit(7);
if (rocketLiftPx(0, 1) !== 0) process.exit(8);
if (!(rocketLiftPx(2, 1) < 0)) process.exit(9);
if (!(rocketLiftPx(1, 2) > 0)) process.exit(10);
if (staleLabel(5) !== "") process.exit(11);
if (staleLabel(4.9) !== "") process.exit(12);
if (staleLabel(6) !== "stale 6s") process.exit(13);
if (staleLabel(null) !== "") process.exit(14);
const up = rocketCue(18, -1, true);
if (up.mood !== "up" || !(up.tilt < 0)) process.exit(20);
const down = rocketCue(-2.3, 40, true);
if (down.mood !== "down" || down.tilt < 8) process.exit(21);
const book = rocketCue(null, -2.3, false);
if (book.mood !== "down" || book.tilt < 8) process.exit(22);
if (tradeMarkHint("active", 1, "01:02:03", 9).indexOf("stale 9s") !== 0) process.exit(23);
if (tradeMarkHint("active", 1, "01:02:03", 1).indexOf("Live") !== 0) process.exit(24);
if (tradeMarkHint("active", 2, "01:02:03", null).indexOf("stale") >= 0) process.exit(25);
if (feedStale(4.9, 5) !== false) process.exit(30);
if (feedStale(5, 5) !== false) process.exit(31);
if (feedStale(5.01, 5) !== true) process.exit(32);
if (feedStale(130, 5) !== true) process.exit(33);
if (feedStale(null, 5) !== true) process.exit(34);
let shown = rocketShownPct(null, 1.2);
shown = rocketShownPct(shown, 1.5);
shown = rocketShownPct(shown, 1.1);
if (!(shown > 0)) process.exit(35);
const nose = rocketCue(shown, 0, true);
if (nose.mood !== "up" || !(nose.tilt < 0)) process.exit(36);
let downShown = rocketShownPct(null, -1.2);
downShown = rocketShownPct(downShown, -2.4);
downShown = rocketShownPct(downShown, -1.8);
if (!(downShown < 0)) process.exit(37);
const noseDn = rocketCue(downShown, 99, true);
if (noseDn.mood !== "down" || !(noseDn.tilt > 0)) process.exit(38);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


OTHER = "So11111111111111111111111111111111111111112"


class ShipW3Tests(unittest.TestCase):
    def setUp(self):
        import trade_stream
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        trade_stream.reset_for_tests()

    def tearDown(self):
        import trade_stream
        game._live_cache.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        trade_stream.reset_for_tests()

    def test_w24_session_b_stop_and_sell_stay_on_the_real_price(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        patch = mock.patch.object(game, "STATE_PATH", root / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "shared-book",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [{
                "id": "pos1",
                "mint": MINT,
                "symbol": "ICAT",
                "stake_usd": 1.0,
                "entry_price_usd": 1.0,
                "qty_tokens": 1.0,
                "last_price_usd": 1.0,
                "opened_at": _utc(now - timedelta(minutes=2)),
                "sl_pct": 20,
                "tp_pct": 100,
            }],
            "closed": [],
            "events": [],
        })
        game._qa_walks["session-a"] = {MINT: {"t0": time.time() - (game.QA_WALK_SPAN_S - 1), "px0": 1.0}}
        game.set_qa_walk_sid("session-a")
        with mock.patch.object(game, "_http_json", return_value=_jup(1.0)):
            walked = game.fetch_live_tick(MINT, qa_sid="session-a")
            game.set_qa_walk_sid(None)
            real = game.fetch_live_tick(MINT, qa_sid=None, apply_qa=False)
            held = game.tick_session()
            sold = game.sell("pos1")
        self.assertEqual(walked["source"], "qa_tick")
        self.assertLessEqual(walked["price_usd"], 0.75)
        self.assertEqual(real["source"], "jupiter")
        self.assertAlmostEqual(real["price_usd"], 1.0)
        self.assertEqual(len(held["positions"]), 1)
        self.assertAlmostEqual(sold["state"]["closed"][0]["exit_price_usd"], 1.0)
        self.assertNotEqual(sold["state"]["closed"][0].get("price_source"), "qa_tick")
        self.assertEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        self.assertNotEqual(game._live_cache[MINT][1]["source"], "qa_tick")

    def test_w24_walk_does_not_cover_a_second_mint(self):
        game.arm_qa_walk("only-one", MINT)
        game.arm_qa_walk("only-one", OTHER)
        self.assertEqual(list(game._qa_walks["only-one"]), [MINT])
        def priced(url):
            if OTHER in url:
                return 200, {OTHER: {"usdPrice": 2.0, "blockId": 1}}
            return _jup(1.0)
        with mock.patch.object(game, "_http_json", side_effect=priced):
            other = game.fetch_live_tick(OTHER, qa_sid="only-one")
        self.assertEqual(other["source"], "jupiter")
        self.assertNotEqual(other.get("qa_tick"), "down")

    def test_w21_off_up_and_zero_clear_the_walk(self):
        client = server.app.test_client()
        for query in ("off", "0", "up"):
            game._qa_walks.clear()
            game.arm_qa_walk("sid", MINT)
            client.set_cookie(game.QA_WALK_COOKIE, "sid")
            with mock.patch.object(game, "_http_json", return_value=_jup(1.0)):
                res = client.get("/api/game/tick?mint=" + MINT + "&qa_tick=" + query)
            self.assertEqual(res.status_code, 200)
            cleared = res.headers.getlist("Set-Cookie")
            self.assertTrue(any("Max-Age=0" in c and game.QA_WALK_COOKIE in c for c in cleared), cleared)
            self.assertNotIn("sid", game._qa_walks)

    def test_w24_fresh_session_sees_real_price_then_off_clears_a(self):
        """A arms the walk. B has no cookie and gets the real price. A off does too."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "book",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [{
                "id": "pos1",
                "mint": MINT,
                "symbol": "ICAT",
                "stake_usd": 1.0,
                "entry_price_usd": 1.0,
                "qty_tokens": 1.0,
                "last_price_usd": 1.0,
                "opened_at": _utc(now - timedelta(minutes=1)),
                "sl_pct": 20,
                "tp_pct": 100,
            }],
            "closed": [],
            "events": [],
        })
        a = server.app.test_client()
        b = server.app.test_client()
        with mock.patch.object(game, "_http_json", return_value=_jup(1.0)):
            armed = a.get("/api/game/tick?mint=" + MINT + "&qa_tick=down")
            self.assertEqual(armed.status_code, 200)
            raw_cookie = armed.headers.get("Set-Cookie") or ""
            self.assertIn(game.QA_WALK_COOKIE + "=", raw_cookie)
            sid = raw_cookie.split(game.QA_WALK_COOKIE + "=", 1)[1].split(";", 1)[0]
            game._qa_walks[sid][MINT]["t0"] = time.time() - (game.QA_WALK_SPAN_S - 1)
            game._qa_walks[sid][MINT]["px0"] = 1.0
            walked = a.get("/api/game/tick?mint=" + MINT)
            fresh = b.get("/api/game/tick?mint=" + MINT)
            held = game.tick_session()
            game.set_qa_walk_sid(None)
            sold = game.sell("pos1")
            off = a.get("/api/game/tick?mint=" + MINT + "&qa_tick=off")
            after = a.get("/api/game/tick?mint=" + MINT)
        self.assertEqual(walked.get_json()["source"], "qa_tick")
        self.assertLessEqual(walked.get_json()["price_usd"], 0.75)
        self.assertEqual(game._live_cache[MINT][1]["source"], "jupiter")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        self.assertNotEqual(id(game._live_cache[MINT][1]), id(walked.get_json()))
        body_b = fresh.get_json()
        self.assertEqual(body_b["source"], "jupiter")
        self.assertAlmostEqual(body_b["price_usd"], 1.0)
        self.assertNotEqual(body_b.get("qa_tick"), "down")
        self.assertLess(time.time() - game._live_cache[MINT][0], 1.0)
        self.assertEqual(len(held["positions"]), 1)
        self.assertTrue(sold["ok"], sold)
        self.assertAlmostEqual(sold["state"]["closed"][0]["exit_price_usd"], 1.0)
        off_cookie = " ".join(off.headers.getlist("Set-Cookie"))
        self.assertIn("Max-Age=0", off_cookie)
        self.assertNotIn(sid, game._qa_walks)
        off_body = off.get_json()
        self.assertEqual(off_body["source"], "jupiter")
        self.assertAlmostEqual(off_body["price_usd"], 1.0)
        self.assertNotEqual(off_body.get("source"), "qa_tick")
        after_body = after.get_json()
        self.assertEqual(after_body["source"], "jupiter")
        self.assertAlmostEqual(after_body["price_usd"], 1.0)

    def test_w25_cold_quote_is_stale_not_no_fresh_price(self):
        with mock.patch.object(game, "_http_json", return_value=(0, None)), mock.patch.object(
            game, "fetch_dex_token", return_value={"symbol": "COLD", "price_usd": 1.0}
        ):
            out = game.quote_payload(MINT)
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("stale"))
        self.assertFalse(out.get("quote_live"))
        self.assertEqual(out.get("stale_label"), "STALE")
        blob = json.dumps(out)
        self.assertNotIn("No fresh price", blob)

    def test_quiet_refetch_age_is_since_the_fetch_not_the_price_change(self):
        game._live_cache[MINT] = (
            time.time() - 30,
            {"mint": MINT, "price_usd": 1.25, "price_ts": "2020-01-01T00:00:00Z", "source": "jupiter"},
        )
        with mock.patch.object(game, "_http_json", return_value=_jup(1.25, 4)):
            tick = game.fetch_live_tick(MINT, bypass_cache=True, apply_qa=False)
        self.assertTrue(game.fillable_tick(tick))
        self.assertLess(tick["quote_age_s"], 1.0)
        self.assertAlmostEqual(tick["price_usd"], 1.25)
        self.assertEqual(tick["source"], "jupiter")

    def test_stream_serves_a_swap_and_falls_back_after_silence(self):
        import trade_stream
        status = trade_stream.ingest_swap(MINT, 1.0, anchor=1.0, seed_ok=True)
        self.assertEqual(status, "ok")
        with mock.patch.object(game, "_http_json", side_effect=AssertionError("jupiter")):
            tick = game.fetch_live_tick(MINT, apply_qa=False)
        self.assertEqual(tick["source"], "pool_swap")
        self.assertAlmostEqual(tick["price_usd"], 1.0)
        self.assertTrue(tick.get("trade_ts"))
        self.assertTrue(tick.get("render_ts"))
        self.assertEqual(tick.get("driver"), "swap")
        held_at = time.time() - 6
        game._live_cache[MINT] = (held_at, dict(game._live_cache[MINT][1]))
        trade_stream._seen[MINT] = held_at
        with mock.patch.object(game, "_http_json", side_effect=AssertionError("jupiter")):
            held = game.fetch_live_tick(MINT, bypass_cache=True, apply_qa=False)
        self.assertEqual(held["source"], "pool_swap")
        self.assertFalse(game.fillable_tick(held))
        stale_at = time.time() - 11
        game._live_cache[MINT] = (stale_at, dict(game._live_cache[MINT][1]))
        trade_stream._seen[MINT] = stale_at
        with mock.patch.object(game, "_http_json", return_value=_jup(1.01)):
            fallback = game.fetch_live_tick(MINT, bypass_cache=True, apply_qa=False)
        self.assertEqual(fallback["source"], "jupiter_fallback")
        self.assertFalse(game.fillable_tick(fallback))
        self.assertFalse(fallback.get("quote_live"))
        self.assertEqual(game._live_cache[MINT][1]["source"], "pool_swap")

    def test_stream_publishes_a_four_percent_trade_and_holds_a_spike(self):
        import trade_stream
        status = trade_stream.ingest_swap(MINT, 1.04, anchor=1.0, seed_ok=True)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.04)
        held = trade_stream.ingest_swap(MINT, 0.78, anchor=1.04, source="pool_swap")
        self.assertEqual(held, "spike")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.04)
        self.assertTrue(trade_stream.recent_spikes())
        confirmed = trade_stream.ingest_swap(MINT, 0.781, anchor=1.04, source="pool_vault")
        self.assertEqual(confirmed, "ok")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 0.781, places=3)

    def test_sol_usd_repricing_is_not_a_swap_driver(self):
        import trade_stream
        first = trade_stream.ingest_swap(MINT, 1.0, anchor=1.0, seed_ok=True, sol=100.0)
        self.assertEqual(first, "ok")
        self.assertEqual(game._live_cache[MINT][1]["driver"], "swap")
        again = trade_stream.ingest_swap(MINT, 1.01, anchor=1.0, sol=101.0)
        self.assertEqual(again, "ok")
        self.assertEqual(game._live_cache[MINT][1]["driver"], "sol_usd")
        traded = trade_stream.ingest_swap(MINT, 1.08, anchor=1.01, sol=101.0)
        self.assertEqual(traded, "ok")
        self.assertEqual(game._live_cache[MINT][1]["driver"], "swap")

    def test_one_sided_vault_read_is_not_published(self):
        import trade_stream
        trade_stream.ingest_swap(MINT, 100.0, anchor=100.0, seed_ok=True, sol=100.0)
        trade_stream._pools[MINT] = {
            "mint": MINT,
            "kind": "pumpswap",
            "base_mint": MINT,
            "quote_mint": trade_stream.SOL_MINT,
            "base_vault": "BASEVAULT",
            "quote_vault": "QUOTEVAULT",
            "base_dec": 6,
            "quote_dec": 9,
            "virtual_quote": 0,
            "base_raw": 1_000_000,
            "quote_raw": 1_000_000_000,
            "base_slot": 1,
            "quote_slot": 1,
        }
        trade_stream._apply_vault_amount(trade_stream._pools[MINT], "BASEVAULT", 1_050_000, 100.0, 2)
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 100.0)
        trade_stream._apply_vault_amount(trade_stream._pools[MINT], "QUOTEVAULT", 1_000_000_000, 100.0, 2)
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 100.0 / 1.05, places=4)

    def test_unconfirmed_spike_does_not_stop_out(self):
        import trade_stream
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "spike-book",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [{
                "id": "pos1",
                "mint": MINT,
                "symbol": "AGEN",
                "stake_usd": 1.0,
                "entry_price_usd": 1.0,
                "qty_tokens": 1.0,
                "last_price_usd": 1.0,
                "opened_at": _utc(now - timedelta(minutes=1)),
                "sl_pct": 20,
                "tp_pct": 100,
            }],
            "closed": [],
            "events": [],
        })
        trade_stream.ingest_swap(MINT, 1.0, anchor=1.0, seed_ok=True, source="pool_vault")
        trade_stream.note_mid(MINT, 1.0)
        self.assertEqual(trade_stream.ingest_swap(MINT, 0.75, source="pool_swap"), "spike")
        held = game.tick_session()
        self.assertEqual(len(held["positions"]), 1)
        self.assertEqual(trade_stream.ingest_swap(MINT, 0.752, source="pool_vault"), "ok")
        sold = game.tick_session()
        self.assertEqual(sold["positions"], [])
        self.assertEqual(sold["closed"][0]["close_reason"], "stop")

    def test_whirlpool_price_uses_the_watched_side(self):
        import trade_stream
        raw = bytearray(220)
        raw[65:81] = (1 << 64).to_bytes(16, "little")
        raw[101:133] = trade_stream.b58decode(trade_stream.SOL_MINT)
        raw[181:213] = trade_stream.b58decode(MINT)
        px = trade_stream.whirlpool_swap_usd(bytes(raw), MINT, sol_usd=100.0, decimals={MINT: 9})
        self.assertAlmostEqual(px, 100.0, places=6)

    def test_pumpswap_mid_uses_decimals_virtual_quote_and_sol(self):
        import trade_stream
        px = trade_stream.pumpswap_mid_usd(
            1_000_000,
            6,
            2_000_000_000,
            9,
            MINT,
            trade_stream.SOL_MINT,
            MINT,
            100.0,
            virtual_quote=1_000_000_000,
        )
        self.assertAlmostEqual(px, 300.0, places=6)
        raw = bytearray(261)
        raw[:8] = trade_stream.PUMPSWAP_DISC
        raw[43:75] = trade_stream.b58decode(MINT)
        raw[75:107] = trade_stream.b58decode(trade_stream.SOL_MINT)
        raw[139:171] = trade_stream.b58decode(trade_stream.USDC_MINT)
        raw[171:203] = trade_stream.b58decode(trade_stream.USDT_MINT)
        raw[245:261] = (1_000_000_000).to_bytes(16, "little", signed=True)
        view = trade_stream.pumpswap_pool_view(bytes(raw))
        self.assertEqual(view["base_mint"], MINT)
        self.assertEqual(view["quote_mint"], trade_stream.SOL_MINT)
        self.assertEqual(view["base_vault"], trade_stream.USDC_MINT)
        self.assertEqual(view["quote_vault"], trade_stream.USDT_MINT)
        self.assertEqual(view["virtual_quote"], 1_000_000_000)

    def test_pumpswap_event_price_and_mid_anchor(self):
        import base64
        import struct
        import trade_stream
        body = bytearray(144)
        struct.pack_into("<Q", body, 40, 5_000_000)
        struct.pack_into("<Q", body, 48, 1_000_000_000)
        body[112:144] = trade_stream.b58decode(trade_stream.USDC_MINT)
        raw = trade_stream.BUY_EVENT_DISC + bytes(body)
        line = "Program data: " + base64.b64encode(raw).decode()
        pool = {
            "kind": "pumpswap",
            "mint": MINT,
            "address": trade_stream.USDC_MINT,
            "base_mint": MINT,
            "quote_mint": trade_stream.SOL_MINT,
            "base_dec": 6,
            "quote_dec": 9,
            "virtual_quote": 0,
        }
        px = trade_stream.price_from_program_logs(["Program log: ignored", line], pool, 100.0)
        self.assertAlmostEqual(px, 20.0, places=6)
        trade_stream.mark_pool_kind(MINT, "pumpswap")
        trade_stream.note_anchor(MINT, 50.0)
        trade_stream.note_mid(MINT, 20.0)
        self.assertEqual(trade_stream.anchor_price(MINT), 20.0)
        self.assertEqual(trade_stream.ingest_swap(MINT, 20.2, seed_ok=True), "ok")
        self.assertEqual(trade_stream.ingest_swap(MINT, 22.0), "ok")
        self.assertEqual(game._live_cache[MINT][1]["price_usd"], 22.0)

    def test_pump_mid_beats_a_jupiter_quote_more_than_two_percent_off(self):
        import trade_stream
        trade_stream.mark_pool_kind(MINT, "pumpswap")
        trade_stream.note_mid(MINT, 1.0)
        with mock.patch.object(game, "_http_json", return_value=_jup(1.22)):
            tick = game.fetch_live_tick(MINT, bypass_cache=True, apply_qa=False)
        self.assertEqual(tick["source"], "pool_vault")
        self.assertAlmostEqual(tick["price_usd"], 1.0, places=6)

    def test_health_documents_the_stream_and_panel_87(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 94)
        self.assertIn("trade_stream", data["features"])
        self.assertIn("stream", data["live_tick"])
        self.assertIn("PumpPortal", data["live_tick"])
        self.assertIn("logsSubscribe", data["live_tick"])
        self.assertIn("jupiter_fallback", data["live_tick"])
        self.assertIn("price_ts", data["live_tick"])
        self.assertIn("spike_rejects", data["stream"])
        self.assertIn("15%", data["live_tick"])

    def test_w20_w22_w23_w26_w27_w28_panel_contract(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 94", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("data-panel-v", html)
        pure = html[html.index("/* ship-w-pure-start */"):html.index("/* ship-w-pure-end */")]
        script = pure + r"""
const nothing = surfaceChrome(null, 5, false, false, "active", 0, "01:02:03");
if (nothing.stale) process.exit(2);
if (nothing.header.indexOf("Live · 0 open") !== 0) process.exit(3);
if (nothing.buy !== "▲ BUY") process.exit(4);
if (nothing.equity !== "LIVE") process.exit(5);
if (nothing.rocket === "Stale") process.exit(6);
const cold = surfaceChrome(null, 5, false, true, "active", 0, "01:02:03");
if (!cold.stale) process.exit(7);
if (cold.header.indexOf("Live") === 0) process.exit(8);
if (cold.buy.indexOf("Waiting") < 0) process.exit(9);
if (cold.equity !== "STALE") process.exit(10);
const old = surfaceChrome(8, 5, false, true, "active", 1, "01:02:03");
if (!old.stale || old.header.indexOf("stale") !== 0) process.exit(11);
if (old.sell.indexOf("Waiting") < 0) process.exit(12);
if (old.label.indexOf("stale") < 0) process.exit(13);
if (old.sellToast !== "Price stale — can't sell yet") process.exit(14);
const fresh = surfaceChrome(3, 5, false, true, "active", 1, "01:02:03");
if (fresh.stale) process.exit(15);
if (fresh.sell !== "Sell this") process.exit(16);
if (fresh.equity !== "LIVE") process.exit(17);
if (acceptServerTickAge(1, 8) !== 8) process.exit(18);
if (acceptServerTickAge(8, 0.4) !== 0.4) process.exit(19);
const book = { positions: [{ pnl_usd: -0.007, stake_usd: 1 }], pnl_pct: -8.2, start_bankroll: 5, equity_usd: 4.59 };
const basis = sharedPnlBasis(book);
if (basis.baseline !== "entry") process.exit(20);
if (Math.abs(basis.pct - (-0.7)) > 0.05) process.exit(21);
const flat = sharedPnlBasis({ positions: [], pnl_pct: -8.2, start_bankroll: 5, equity_usd: 4.59 });
if (flat.baseline !== "start" || Math.abs(flat.pct - (-8.2)) > 0.001) process.exit(22);
const line = tickDebugLine({ source: "stream", trade_ts: "2026-10-31T00:00:00.000Z", mint: "MINTA" }, 1, "2026-10-31T00:00:01.000Z");
if (line.indexOf("src=pool_swap") < 0 || line.indexOf("trade_ts=") < 0 || line.indexOf("render_ts=") < 0) process.exit(23);
const fall = tickDebugLine({ source: "jupiter_fallback", trade_ts: "2026-10-31T00:00:00.000Z", mint: "MINTA" }, 1, "2026-10-31T00:00:01.000Z");
if (fall.indexOf("src=jupiter_fallback") < 0) process.exit(27);
const rows = tickLogLines([{ mint: "A", line: "a" }, { mint: "B", line: "b" }], "A");
if (rows.length !== 1 || rows[0].mint !== "A") process.exit(24);
if (sellAllStaleToast(true) !== "Price stale — can't sell yet") process.exit(25);
if (sellAllStaleToast(false) !== "") process.exit(26);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("/* w23-flow-numbers */", html)
        self.assertIn("text-overflow: clip", html[html.index("/* w23-flow-numbers */"):html.index("/* w23-flow-numbers-end */")])
        self.assertIn("body.mode-game .game-wrap { grid-template-columns: minmax(0, 1fr); }", html)
        self.assertIn("Price stale — can't sell yet", html)
        self.assertIn("tickDebugByMint", html)


class ShipW6Tests(unittest.TestCase):
    def setUp(self):
        import trade_stream
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def tearDown(self):
        import trade_stream
        game._live_cache.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def test_w34_other_session_stop_stays_on_the_chain_price(self):
        """A's ?qa_tick=down walk to -21% must not fill B's stop. B's tick is the chain price."""
        import trade_stream
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        chain = 0.9882  # -1.18% from a $1 entry. Inside a 20% stop.
        now = datetime.now(timezone.utc)
        opened = _utc(now - timedelta(minutes=2))
        game.save_state({
            "session_id": "shared-book",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 3.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [
                {
                    "id": "pos-a",
                    "mint": MINT,
                    "symbol": "ICAT",
                    "stake_usd": 1.0,
                    "entry_price_usd": 1.0,
                    "qty_tokens": 1.0,
                    "last_price_usd": 1.0,
                    "opened_at": opened,
                    "sl_pct": 20,
                    "tp_pct": 100,
                    "client_id": "client-a",
                    "price_source": "pool_vault",
                },
                {
                    "id": "pos-b",
                    "mint": MINT,
                    "symbol": "ICAT",
                    "stake_usd": 1.0,
                    "entry_price_usd": 1.0,
                    "qty_tokens": 1.0,
                    "last_price_usd": 1.0,
                    "opened_at": opened,
                    "sl_pct": 20,
                    "tp_pct": 100,
                    "client_id": "client-b",
                    "price_source": "pool_vault",
                },
            ],
            "closed": [],
            "events": [],
        })
        trade_stream.ingest_swap(MINT, chain, seed_ok=True, sol=100.0, source="pool_vault")
        a = server.app.test_client()
        b = server.app.test_client()
        a.set_cookie(game.CLIENT_COOKIE, "client-a")
        b.set_cookie(game.CLIENT_COOKIE, "client-b")
        with mock.patch.object(game, "_http_json", return_value=_jup(chain)):
            armed = a.get("/api/game/tick?mint=" + MINT + "&qa_tick=down")
            self.assertEqual(armed.status_code, 200)
            raw_cookie = armed.headers.get("Set-Cookie") or ""
            self.assertIn(game.QA_WALK_COOKIE + "=", raw_cookie)
            sid = raw_cookie.split(game.QA_WALK_COOKIE + "=", 1)[1].split(";", 1)[0]
            game._qa_walks[sid][MINT]["t0"] = time.time() - game.QA_WALK_SPAN_S * (0.21 / game.QA_WALK_DROP)
            game._qa_walks[sid][MINT]["px0"] = 1.0
            game.set_qa_walk_sid(None)
            game.set_client_sid(None)
            background = game.tick_session()
            tick_b = b.get("/api/game/tick?mint=" + MINT)
            state_b = b.post("/api/game/state")
            state_a = a.post("/api/game/state")
            tick_b_after = b.get("/api/game/tick?mint=" + MINT)
        self.assertEqual(len(background["positions"]), 2)
        body_b = tick_b.get_json()
        self.assertAlmostEqual(body_b["price_usd"], chain)
        self.assertNotEqual(body_b.get("source"), "qa_tick")
        self.assertEqual(body_b.get("driver"), "swap")
        self.assertNotIn("qa_tick", body_b)
        open_b = [p["id"] for p in state_b.get_json()["positions"]]
        self.assertIn("pos-b", open_b)
        self.assertIn("pos-a", open_b)
        closed_a = state_a.get_json()["closed"]
        self.assertTrue(any(row.get("id") == "pos-a" and row.get("close_reason") == "stop" for row in closed_a))
        filled = next(row for row in closed_a if row.get("id") == "pos-a")
        self.assertEqual(filled.get("price_source"), "qa_tick")
        self.assertLessEqual(filled["pnl_pct"], -20.0)
        self.assertGreaterEqual(filled["pnl_pct"], -22.0)
        still_b = [p["id"] for p in state_a.get_json()["positions"]]
        self.assertIn("pos-b", still_b)
        self.assertNotIn("pos-a", still_b)
        after = tick_b_after.get_json()
        self.assertAlmostEqual(after["price_usd"], chain)
        self.assertNotEqual(after.get("source"), "qa_tick")
        self.assertEqual(game._live_cache[MINT][1]["source"], "pool_vault")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], chain)
        book = game.load_state()
        self.assertTrue(any(p.get("id") == "pos-b" for p in book["positions"]))
        self.assertFalse(any(p.get("id") == "pos-b" and p.get("price_source") == "qa_tick" for p in book["closed"]))

    def test_logs_subscribe_survives_a_full_watch_list(self):
        import trade_stream
        pools = []
        for i in range(20):
            pools.append({
                "mint": "M" + str(i),
                "address": "P" + str(i),
                "kind": "pumpswap",
                "base_vault": "B" + str(i),
                "quote_vault": "Q" + str(i),
            })
        want = trade_stream.prioritize_subscriptions(pools, limit=48)
        logs = [key for key in want if key.startswith("logs:")]
        self.assertEqual(len(logs), 20)
        self.assertLessEqual(len(want), 48)

    def test_non_sol_quote_is_pool_ratio_times_quote_usd(self):
        import trade_stream
        quote = "BABA_MINT_NOT_SOL"
        px = trade_stream.pumpswap_mid_usd(
            1_000_000,
            6,
            2_000_000,
            6,
            MINT,
            quote,
            MINT,
            100.0,
            virtual_quote=0,
            extra_usd={quote: 2.5},
        )
        self.assertAlmostEqual(px, 5.0)
        bare = trade_stream.pumpswap_mid_usd(
            1_000_000, 6, 2_000_000, 6, MINT, quote, MINT, 100.0, 0,
        )
        self.assertIsNone(bare)

    def test_tick_route_exposes_driver(self):
        import trade_stream
        trade_stream.ingest_swap(MINT, 1.5, seed_ok=True, sol=100.0, source="pool_swap")
        body = server.app.test_client().get("/api/game/tick?mint=" + MINT).get_json()
        self.assertEqual(body["source"], "pool_swap")
        self.assertEqual(body["driver"], "swap")
        self.assertAlmostEqual(body["price_usd"], 1.5)

    def test_buy_entry_uses_the_on_chain_tick(self):
        import trade_stream
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        trade_stream.ingest_swap(MINT, 2.0, seed_ok=True, sol=100.0, source="pool_vault")
        game.start_session(force=True)
        dex = {"symbol": "N", "name": "N", "price_usd": 9.0, "pair_url": "https://dexscreener.com/solana/x"}
        with mock.patch.object(game, "fetch_dex_token", return_value=dex), mock.patch.object(
            game, "_http_json", return_value=_jup(9.0)
        ):
            out = game.buy(MINT, 1.0, sl_pct=20, tp_pct=100)
        self.assertTrue(out["ok"], out)
        self.assertAlmostEqual(out["position"]["entry_price_usd"], 2.0)
        self.assertEqual(out["position"].get("price_source"), "pool_vault")


class _FakeSocket:
    def __init__(self):
        self.sent = []

    def send(self, raw):
        self.sent.append(json.loads(raw))


def _pumpswap_pool():
    import trade_stream
    return {
        "mint": MINT,
        "kind": "pumpswap",
        "address": trade_stream.USDC_MINT,
        "base_mint": MINT,
        "quote_mint": trade_stream.SOL_MINT,
        "base_vault": "BaseVault111111111111111111111111111111111",
        "quote_vault": "QuoteVault11111111111111111111111111111111",
        "base_dec": 6,
        "quote_dec": 9,
        "virtual_quote": 0,
    }


def _fill_log(base_amt: int, user_quote: int, pool_addr: str, *, base_res: int = 0, quote_res: int = 0) -> str:
    import base64
    import struct
    import trade_stream
    body = bytearray(144)
    struct.pack_into("<Q", body, 8, base_amt)
    struct.pack_into("<Q", body, 40, base_res or base_amt)
    struct.pack_into("<Q", body, 48, quote_res or user_quote)
    struct.pack_into("<Q", body, 104, user_quote)
    body[112:144] = trade_stream.b58decode(pool_addr)
    raw = trade_stream.BUY_EVENT_DISC + bytes(body)
    return "Program data: " + base64.b64encode(raw).decode()


def _logs_msg(line: str, sig: str, sub_id: int = 7, slot: int = 1) -> dict:
    return {
        "method": "logsNotification",
        "params": {
            "subscription": sub_id,
            "result": {
                "context": {"slot": slot},
                "value": {"signature": sig, "err": None, "logs": [line]},
            },
        },
    }


class ShipW90Tests(unittest.TestCase):
    def setUp(self):
        import trade_stream
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def tearDown(self):
        import trade_stream
        game._live_cache.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def test_each_pool_keeps_its_own_log_plan_through_churn(self):
        import trade_stream
        pool = self._seed_pool()
        key = "logs:" + pool["address"]
        self.assertIn(key, trade_stream._wanted_log_ops())
        self.assertEqual(trade_stream._wanted_log_ops()[key]["params"][1]["commitment"], "confirmed")
        trade_stream._views[MINT] = time.monotonic() - 60
        self.assertIn(key, trade_stream._wanted_log_ops())
        ws = _FakeSocket()
        subs, meta, pending, rpc = trade_stream._sync_subs(
            ws, {}, {}, {}, 1, "http://unused", include_logs=False,
        )
        methods = [msg["method"] for msg in ws.sent]
        self.assertNotIn("logsSubscribe", methods)
        self.assertEqual(methods.count("accountSubscribe"), 2)
        trade_stream._views[MINT] = time.monotonic() - 301
        self.assertEqual(trade_stream._wanted_log_ops(), {})
        trade_stream.note_view(MINT)
        self.assertIn(key, trade_stream._wanted_log_ops())

    def test_logs_commitment_is_confirmed(self):
        import trade_stream
        pool = _pumpswap_pool()
        ops = trade_stream._ops_for(pool)
        logs = ops["logs:" + pool["address"]]
        self.assertEqual(logs["params"][1]["commitment"], "confirmed")
        self.assertEqual(logs["params"][0]["mentions"], [pool["address"]])
        self.assertIn("mainnet-beta.solana.com", trade_stream.logs_endpoint())

    def test_view_stays_watched_for_five_minutes_and_a_position_outlives_it(self):
        import trade_stream
        trade_stream.note_view(MINT)
        trade_stream._views[MINT] = time.monotonic() - 60
        self.assertIn(MINT, trade_stream.watched_mints())
        self.assertIn(MINT, trade_stream.view_mints())
        trade_stream._views[MINT] = time.monotonic() - 301
        self.assertNotIn(MINT, trade_stream.view_mints())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "w90-book",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [{
                "id": "pos-w90",
                "mint": MINT,
                "symbol": "ICAT",
                "stake_usd": 1.0,
                "entry_price_usd": 1.0,
                "qty_tokens": 1.0,
                "last_price_usd": 1.0,
                "opened_at": _utc(now - timedelta(minutes=2)),
                "sl_pct": 20,
                "tp_pct": 100,
            }],
            "closed": [],
            "events": [],
        })
        self.assertIn(MINT, trade_stream.watched_mints())

    def _seed_pool(self):
        import trade_stream
        pool = _pumpswap_pool()
        trade_stream._pools[MINT] = pool
        trade_stream.note_view(MINT)
        trade_stream.note_anchor(trade_stream.SOL_MINT, 100.0, force=True)
        return pool

    def _ack(self, ws, subs, meta, pending):
        import trade_stream
        for msg in list(ws.sent):
            method = str(msg.get("method") or "")
            if not method.endswith("Subscribe") or method.endswith("Unsubscribe"):
                continue
            trade_stream._take_sub_ack(
                {"jsonrpc": "2.0", "id": msg["id"], "result": 1000 + int(msg["id"])},
                subs, meta, pending,
            )
        ws.sent.clear()

    def test_log_subs_survive_churn_inside_five_minutes_and_resubscribe(self):
        import trade_stream
        pool = self._seed_pool()
        ws = _FakeSocket()
        subs, meta, pending, rpc = {}, {}, {}, 1
        subs, meta, pending, rpc = trade_stream._sync_subs(ws, subs, meta, pending, rpc, "http://unused")
        self._ack(ws, subs, meta, pending)
        logs = [key for key in subs if key.startswith("logs:")]
        vaults = [key for key in subs if key.startswith("vault:")]
        self.assertEqual(logs, ["logs:" + pool["address"]])
        self.assertEqual(len(vaults), 2)
        trade_stream._views[MINT] = time.monotonic() - 60
        subs, meta, pending, rpc = trade_stream._sync_subs(ws, subs, meta, pending, rpc, "http://unused")
        self.assertEqual(ws.sent, [])
        self.assertIn("logs:" + pool["address"], subs)
        self.assertEqual(len([key for key in subs if key.startswith("vault:")]), 2)
        trade_stream._views[MINT] = time.monotonic() - 301
        subs, meta, pending, rpc = trade_stream._sync_subs(ws, subs, meta, pending, rpc, "http://unused")
        methods = [msg["method"] for msg in ws.sent]
        self.assertIn("logsUnsubscribe", methods)
        self.assertEqual(methods.count("accountUnsubscribe"), 2)
        self.assertNotIn("logs:" + pool["address"], subs)
        trade_stream.note_view(MINT)
        ws.sent.clear()
        fresh, fresh_meta, fresh_pending, rpc = {}, {}, {}, rpc
        fresh, fresh_meta, fresh_pending, rpc = trade_stream._sync_subs(
            ws, fresh, fresh_meta, fresh_pending, rpc, "http://unused",
        )
        resent = [msg["method"] for msg in ws.sent]
        self.assertIn("logsSubscribe", resent)
        self.assertEqual(resent.count("accountSubscribe"), 2)

    def test_subscribe_error_and_string_id_do_not_stick_pending(self):
        import trade_stream
        pool = self._seed_pool()
        pending = {4: {"key": "logs:" + pool["address"], "method": "logsSubscribe", "sent": time.monotonic()}}
        subs, meta = {}, {}
        self.assertTrue(trade_stream._take_sub_ack(
            {"id": "4", "error": {"code": -32600, "message": "logsSubscribe rejected"}},
            subs, meta, pending,
        ))
        self.assertEqual(subs, {})
        self.assertEqual(pending, {})
        pending = {4: {"key": "logs:" + pool["address"], "method": "logsSubscribe"}}
        self.assertTrue(trade_stream._take_sub_ack({"id": "4", "result": 55}, subs, meta, pending))
        self.assertEqual(subs["logs:" + pool["address"]], 55)
        self.assertEqual(pending, {})
        # An ack that never arrives is dropped so the next sync sends it again.
        ws = _FakeSocket()
        stale = {9: {
            "key": "logs:" + pool["address"],
            "method": "logsSubscribe",
            "params": [{"mentions": [pool["address"]]}, {"commitment": "confirmed"}],
            "sent": time.monotonic() - 5,
        }}
        subs, meta, pending, rpc = trade_stream._sync_subs(ws, {}, {}, stale, 9, "http://unused")
        self.assertTrue(any(msg.get("method") == "logsSubscribe" for msg in ws.sent))
        self.assertNotIn(9, pending)

    def test_two_agreeing_reads_override_a_stuck_baseline(self):
        import trade_stream
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 1.49e-6, seed_ok=True, source="pool_vault"),
            "ok",
        )
        trade_stream._seen[MINT] = time.time() - 6
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.92e-6, source="pool_vault", signature="a"), "spike")
        # An echo of the stale baseline must not refresh it or erase the candidate.
        echo = trade_stream.ingest_swap(MINT, 1.49e-6, source="pool_vault")
        self.assertEqual(echo, "spike")
        self.assertGreater(time.time() - trade_stream._seen[MINT], 5)
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.49e-6)
        # Same signature is not a second independent read.
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.925e-6, source="pool_swap", signature="a"), "spike")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.49e-6)
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.93e-6, source="pool_swap", signature="b"), "ok")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.93e-6)
        kinds = [row.get("kind") for row in trade_stream.recent_spikes()]
        self.assertIn("override", kinds)
        override = next(row for row in trade_stream.recent_spikes() if row.get("kind") == "override")
        self.assertGreater(override["baseline_age"], 5)
        self.assertLessEqual(override["agree"], 0.02)

    def test_executed_fill_beats_the_raw_vault_ratio(self):
        import trade_stream
        pool = self._seed_pool()
        # 1.000000 token and 0.010 SOL is $1 at SOL=$100. The raw reserves are ~7% under that.
        line = _fill_log(1_000_000, 10_700_000, pool["address"], base_res=1_000_000, quote_res=10_000_000)
        px = trade_stream.price_from_program_logs([line], pool, 100.0)
        raw = trade_stream.pumpswap_mid_usd(
            1_000_000, 6, 10_000_000, 9, MINT, trade_stream.SOL_MINT, MINT, 100.0, 0,
        )
        corrected = trade_stream.pumpswap_mid_usd(
            1_000_000, 6, 10_000_000, 9, MINT, trade_stream.SOL_MINT, MINT, 100.0, 700_000,
        )
        self.assertAlmostEqual(px, 1.07, places=6)
        self.assertLess(raw, px * 0.94)
        self.assertAlmostEqual(corrected, px, places=6)
        trade_stream.ingest_swap(MINT, 1.07, seed_ok=True, source="pool_vault")
        trade_stream._on_logs(_logs_msg(line, "fill-1"), {7: {"mint": MINT, "role": "logs"}})
        self.assertEqual(game._live_cache[MINT][1]["source"], "pool_swap")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.07, places=6)
        held = trade_stream._publish(MINT, raw, compare_mid=False, source="pool_vault", sol=100.0)
        self.assertEqual(held, "held")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.07, places=6)
        trade_stream._swap_at[MINT] = time.time() - 6
        with mock.patch.object(trade_stream, "_vault_amounts", return_value=(1_000_000, 10_000_000)), \
             mock.patch.object(trade_stream, "mint_decimals", side_effect=lambda http, mint: 9 if mint == trade_stream.SOL_MINT else 6), \
             mock.patch.object(trade_stream, "anchor_price", return_value=100.0), \
             mock.patch.object(trade_stream, "_extra_for_pool", return_value=None):
            pool["virtual_quote"] = 700_000
            trade_stream._pools[MINT]["virtual_quote"] = 700_000
            got = trade_stream._read_vault_mid("http://unused", pool)
        self.assertAlmostEqual(got, 1.07, places=4)
        self.assertEqual(game._live_cache[MINT][1]["source"], "pool_vault")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.07, places=4)

    def test_log_notifications_are_not_collapsed(self):
        import trade_stream
        pool = self._seed_pool()
        meta = {7: {"mint": MINT, "role": "logs"}}
        trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_swap")
        price = 1.0
        last = price
        for i in range(9):
            quote = int(round(price * 10_000_000))
            last = quote / 10_000_000
            line = _fill_log(1_000_000, quote, pool["address"])
            trade_stream._on_logs(_logs_msg(line, f"sig-{i}", slot=i + 1), meta)
            price *= 1.10
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], last, places=6)
        self.assertEqual(game._live_cache[MINT][1]["source"], "pool_swap")

    def test_uncovered_pool_is_polled_even_when_the_mid_is_fresh(self):
        import trade_stream
        pool = self._seed_pool()
        trade_stream.note_mid(MINT, 1.0)
        calls = []

        def _read(http, pool):
            calls.append(pool.get("mint"))
            return 1.0

        with mock.patch.object(trade_stream, "_read_vault_mid", side_effect=_read), \
             mock.patch.object(trade_stream, "_vault_sub_covered", return_value=False):
            trade_stream._refresh_pump_mids("http://unused")
        self.assertEqual(calls, [MINT])
        calls.clear()
        # A live logs socket may skip the HTTP poll. No logs socket means the
        # 1s vault poll still runs, even when the vault accountSubscribe is up.
        with mock.patch.object(trade_stream, "_read_vault_mid", side_effect=_read), \
             mock.patch.object(trade_stream, "_vault_sub_covered", return_value=True):
            trade_stream._refresh_pump_mids("http://unused")
        self.assertEqual(calls, [MINT])
        calls.clear()
        trade_stream._ws["subscriptions"] = {"logs:" + pool["address"]: 7}
        with mock.patch.object(trade_stream, "_read_vault_mid", side_effect=_read), \
             mock.patch.object(trade_stream, "_vault_sub_covered", return_value=True):
            trade_stream._refresh_pump_mids("http://unused")
        self.assertEqual(calls, [])


def _pk(n: int) -> str:
    import trade_stream
    return trade_stream.b58encode(int(n).to_bytes(32, "big"))


def _ps_info(base: str, quote: str, base_vault: str, quote_vault: str) -> dict:
    import base64
    import trade_stream
    raw = bytearray(261)
    raw[:8] = trade_stream.PUMPSWAP_DISC
    raw[43:75] = trade_stream.b58decode(base)
    raw[75:107] = trade_stream.b58decode(quote)
    raw[139:171] = trade_stream.b58decode(base_vault)
    raw[171:203] = trade_stream.b58decode(quote_vault)
    return {
        "owner": trade_stream.PUMPSWAP_OWNER,
        "data": [base64.b64encode(bytes(raw)).decode(), "base64"],
    }


class ShipW91Tests(unittest.TestCase):
    def setUp(self):
        import trade_stream
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def tearDown(self):
        import trade_stream
        game._live_cache.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def test_crash_publishes_on_the_confirming_read_within_3s(self):
        """A ~98% drop publishes on the vault read that confirms the swap. Max hold is 3s."""
        import trade_stream
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_vault"), "ok")
        started = time.perf_counter()
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 0.02, source="pool_swap", signature="dump"),
            "spike",
        )
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        # Swap and vault within 5%. A 7% gap is not agreement and must not publish.
        status = trade_stream.ingest_swap(MINT, 0.0192, source="pool_vault", signature="vault")
        self.assertEqual(status, "ok")
        self.assertLess(time.perf_counter() - started, 3.0)
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 0.0192, places=4)

    def test_crash_held_for_31s_publishes_when_the_next_read_confirms(self):
        import trade_stream
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_swap"), "ok")
        self.assertEqual(trade_stream.ingest_swap(MINT, 0.02, source="pool_swap", signature="a"), "spike")
        trade_stream._pending_spikes[MINT]["at"] = time.time() - 31
        status = trade_stream.ingest_swap(MINT, 0.0195, source="pool_vault", signature="b")
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 0.0195, places=4)

    def test_moving_crash_publishes_once_the_3s_hold_ends(self):
        import trade_stream
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_vault"), "ok")
        self.assertEqual(trade_stream.ingest_swap(MINT, 0.50, source="pool_vault", signature="a"), "spike")
        trade_stream._pending_spikes[MINT]["at"] = time.time() - 3.1
        # 4% from the held print. Agreement inside the 3s window publishes.
        status = trade_stream.ingest_swap(MINT, 0.48, source="pool_vault", signature="b")
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 0.48, places=4)

    def test_one_off_64x_is_rejected(self):
        import trade_stream
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_vault"), "ok")
        self.assertEqual(trade_stream.ingest_swap(MINT, 64.0, source="pool_swap", signature="fake"), "spike")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        # A different print inside the hold is not a confirm of the 64x.
        self.assertEqual(trade_stream.ingest_swap(MINT, 2.0, source="pool_swap", signature="other"), "spike")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        trade_stream._pending_spikes[MINT]["at"] = time.time() - 31
        trade_stream._seen[MINT] = time.time() - 6
        echo = trade_stream.ingest_swap(MINT, 1.0, source="pool_vault")
        self.assertEqual(echo, "spike")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)

    def test_sis_and_fafa_use_the_quote_sol_or_usdc_pool(self):
        """SPCX-quoted SIS and 发发 price off the quote token's SOL or USDC pool, not Jupiter."""
        import trade_stream
        sis, spcx = _pk(11), _pk(12)
        fafa, quote = _pk(13), _pk(14)
        sol, usdc = trade_stream.SOL_MINT, trade_stream.USDC_MINT
        sis_pair, sol_pair = _pk(21), _pk(22)
        busy_pair, usdc_pair = _pk(23), _pk(24)
        spcx_pairs = [
            {
                "pairAddress": sis_pair,
                "liquidity": {"usd": 900000},
                "baseToken": {"address": spcx},
                "quoteToken": {"address": sis},
            },
            {
                "pairAddress": sol_pair,
                "liquidity": {"usd": 40000},
                "baseToken": {"address": spcx},
                "quoteToken": {"address": sol},
            },
        ]
        fafa_pairs = [
            {
                "pairAddress": busy_pair,
                "liquidity": {"usd": 700000},
                "baseToken": {"address": quote},
                "quoteToken": {"address": fafa},
            },
            {
                "pairAddress": usdc_pair,
                "liquidity": {"usd": 25000},
                "baseToken": {"address": quote},
                "quoteToken": {"address": usdc},
            },
        ]
        accounts = {
            sis_pair: _ps_info(spcx, sis, _pk(31), _pk(32)),
            sol_pair: _ps_info(spcx, sol, _pk(33), _pk(34)),
            busy_pair: _ps_info(quote, fafa, _pk(35), _pk(36)),
            usdc_pair: _ps_info(quote, usdc, _pk(37), _pk(38)),
        }

        def dex(mint):
            if mint == spcx:
                return spcx_pairs
            if mint == quote:
                return fafa_pairs
            return []

        with mock.patch.object(trade_stream, "_dex_pairs", side_effect=dex), \
             mock.patch.object(trade_stream, "_account_info", side_effect=lambda http, addr: accounts.get(addr)):
            spcx_usd = trade_stream._resolve_usd_pool(spcx, "http://rpc")
            spcx_main = trade_stream._resolve_pool(spcx, "http://rpc")
            fafa_usd = trade_stream._resolve_usd_pool(quote, "http://rpc")
            fafa_main = trade_stream._resolve_pool(quote, "http://rpc")
        self.assertEqual(spcx_main["address"], sis_pair)
        self.assertEqual(spcx_usd["address"], sol_pair)
        self.assertEqual(spcx_usd["quote_mint"], sol)
        self.assertEqual(fafa_main["address"], busy_pair)
        self.assertEqual(fafa_usd["quote_mint"], usdc)

        sis_pool = {
            "mint": sis,
            "kind": "pumpswap",
            "address": _pk(41),
            "base_mint": sis,
            "quote_mint": spcx,
            "base_vault": _pk(51),
            "quote_vault": _pk(52),
            "base_dec": 6,
            "quote_dec": 6,
            "virtual_quote": 0,
        }
        spcx_pool = {
            "mint": spcx,
            "kind": "pumpswap",
            "address": sol_pair,
            "base_mint": spcx,
            "quote_mint": sol,
            "base_vault": _pk(33),
            "quote_vault": _pk(34),
            "base_dec": 6,
            "quote_dec": 9,
            "virtual_quote": 0,
        }
        fafa_pool = {
            "mint": fafa,
            "kind": "pumpswap",
            "address": _pk(43),
            "base_mint": fafa,
            "quote_mint": quote,
            "base_vault": _pk(55),
            "quote_vault": _pk(56),
            "base_dec": 6,
            "quote_dec": 6,
            "virtual_quote": 0,
        }
        quote_pool = {
            "mint": quote,
            "kind": "pumpswap",
            "address": usdc_pair,
            "base_mint": quote,
            "quote_mint": usdc,
            "base_vault": _pk(37),
            "quote_vault": _pk(38),
            "base_dec": 6,
            "quote_dec": 6,
            "virtual_quote": 0,
        }
        amounts = {
            (_pk(51), _pk(52)): (1_000_000, 2_000_000),
            (_pk(33), _pk(34)): (1_000_000, 1_000_000_000),
            (_pk(55), _pk(56)): (1_000_000, 4_000_000),
            (_pk(37), _pk(38)): (1_000_000, 2_000_000),
        }
        trade_stream._pools[sis] = sis_pool
        trade_stream._pools[fafa] = fafa_pool
        trade_stream._quote_pools[spcx] = spcx_pool
        trade_stream._quote_pools[quote] = quote_pool
        trade_stream.note_anchor(sol, 100.0, force=True)
        trade_stream.note_view(sis)
        trade_stream.note_view(fafa)

        def vaults(http, base, quote_vault):
            return amounts[(base, quote_vault)]

        def decimals(http, mint):
            if mint == sol:
                return 9
            return 6

        with mock.patch.object(trade_stream, "_vault_amounts", side_effect=vaults), \
             mock.patch.object(trade_stream, "mint_decimals", side_effect=decimals), \
             mock.patch.object(game, "_http_json", side_effect=AssertionError("jupiter")):
            sis_px = trade_stream._read_vault_mid("http://rpc", sis_pool)
            fafa_px = trade_stream._read_vault_mid("http://rpc", fafa_pool)
            tick_sis = game.fetch_live_tick(sis, apply_qa=False)
            tick_fafa = game.fetch_live_tick(fafa, apply_qa=False)
        self.assertAlmostEqual(sis_px, 200.0)
        self.assertAlmostEqual(fafa_px, 8.0)
        # SPCX math is unchanged and did not ask Jupiter. A lone vault is stale
        # until a swap agrees within 5%.
        self.assertFalse(tick_sis.get("quote_live"))
        self.assertFalse(tick_fafa.get("quote_live"))
        self.assertEqual(trade_stream.ingest_swap(sis, sis_px, source="pool_swap", signature="sis"), "ok")
        self.assertEqual(trade_stream.ingest_swap(fafa, fafa_px, source="pool_swap", signature="fafa"), "ok")
        tick_sis = game.fetch_live_tick(sis, apply_qa=False)
        tick_fafa = game.fetch_live_tick(fafa, apply_qa=False)
        self.assertAlmostEqual(tick_sis["price_usd"], 200.0)
        self.assertAlmostEqual(tick_fafa["price_usd"], 8.0)
        self.assertIn(tick_sis["source"], ("pool_swap", "pool_vault"))
        self.assertIn(tick_fafa["source"], ("pool_swap", "pool_vault"))
        logs = trade_stream._wanted_log_ops()
        self.assertIn("logs:" + sis_pool["address"], logs)
        self.assertIn("logs:" + spcx_pool["address"], logs)
        self.assertIn("logs:" + fafa_pool["address"], logs)
        self.assertIn("logs:" + quote_pool["address"], logs)
        self.assertEqual(trade_stream._quote_pools[spcx]["quote_mint"], sol)
        self.assertEqual(trade_stream._quote_pools[quote]["quote_mint"], usdc)

    def test_six_vault_polls_share_one_rpc(self):
        import base64
        import struct
        import trade_stream
        pools = []
        for i in range(6):
            mint = _pk(100 + i)
            pool = {
                "mint": mint,
                "kind": "pumpswap",
                "address": _pk(200 + i),
                "base_mint": mint,
                "quote_mint": trade_stream.SOL_MINT,
                "base_vault": _pk(300 + i),
                "quote_vault": _pk(400 + i),
                "base_dec": 6,
                "quote_dec": 9,
                "virtual_quote": 0,
            }
            trade_stream._pools[mint] = pool
            trade_stream.note_view(mint)
            pools.append(pool)
        trade_stream.note_anchor(trade_stream.SOL_MINT, 100.0, force=True)
        calls = []

        def post(http, body, timeout):
            payload = json.loads(body.decode())
            calls.append(payload)
            vals = []
            for _addr in payload["params"][0]:
                buf = bytearray(80)
                struct.pack_into("<Q", buf, 64, 1_000_000_000)
                vals.append({"data": [base64.b64encode(bytes(buf)).decode(), "base64"]})
            return {"result": {"value": vals}}

        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post), \
             mock.patch.object(trade_stream, "mint_decimals", side_effect=lambda http, mint: 9 if mint == trade_stream.SOL_MINT else 6):
            trade_stream._refresh_pump_mids("http://rpc")
        self.assertEqual([row["method"] for row in calls], ["getMultipleAccounts"])
        self.assertEqual(len(calls[0]["params"][0]), 12)
        for pool in pools:
            # One shared poll still priced every vault. It is not a live tick
            # until a swap agrees, so a lone read cannot publish an override.
            self.assertNotIn(pool["mint"], game._live_cache)
            vault_px = trade_stream._pairs[pool["mint"]]["pool_vault"]["px"]
            self.assertGreater(vault_px, 0)
            self.assertEqual(
                trade_stream.ingest_swap(pool["mint"], vault_px, source="pool_swap", signature="swap"),
                "ok",
            )
            tick = game._live_cache[pool["mint"]][1]
            self.assertGreater(tick["price_usd"], 0)

    def test_raydium_usdc_pool_prices_a_non_sol_quote(self):
        """A quote token whose SOL/USDC market is Raydium CLMM still prices the coin. No Jupiter."""
        import math
        import trade_stream
        coin, quote = _pk(71), _pk(72)
        usdc = trade_stream.USDC_MINT
        raw = bytearray(280)
        raw[:8] = trade_stream.CLMM_DISC
        raw[73:105] = trade_stream.b58decode(quote)
        raw[105:137] = trade_stream.b58decode(usdc)
        raw[233] = 6
        raw[234] = 6
        sqrt = int(math.sqrt(2.0) * (2 ** 64))
        raw[253:269] = sqrt.to_bytes(16, "little")
        px = trade_stream.raydium_clmm_usd(bytes(raw), quote, 100.0)
        self.assertAlmostEqual(px, 2.0, places=4)
        view = trade_stream.raydium_clmm_view(bytes(raw))
        self.assertEqual(view["mint_0"], quote)
        self.assertEqual(view["mint_1"], usdc)
        coin_pool = {
            "mint": coin,
            "kind": "pumpswap",
            "address": _pk(81),
            "base_mint": coin,
            "quote_mint": quote,
            "base_vault": _pk(82),
            "quote_vault": _pk(83),
            "base_dec": 6,
            "quote_dec": 6,
            "virtual_quote": 0,
        }
        quote_pool = {
            "mint": quote,
            "kind": "raydium",
            "address": _pk(84),
            "owner": trade_stream.RAYDIUM_CLMM_OWNER,
            "base_mint": quote,
            "quote_mint": usdc,
            "base_dec": 6,
            "quote_dec": 6,
        }
        trade_stream._pools[coin] = coin_pool
        trade_stream._quote_pools[quote] = quote_pool
        trade_stream.note_anchor(trade_stream.SOL_MINT, 100.0, force=True)
        trade_stream.note_view(coin)

        def acct(http, addr):
            if addr == quote_pool["address"]:
                return {"owner": trade_stream.RAYDIUM_CLMM_OWNER, "data": [__import__("base64").b64encode(bytes(raw)).decode(), "base64"]}
            return None

        with mock.patch.object(trade_stream, "_account_info", side_effect=acct), \
             mock.patch.object(trade_stream, "_vault_amounts", return_value=(1_000_000, 3_000_000)), \
             mock.patch.object(trade_stream, "mint_decimals", return_value=6), \
             mock.patch.object(game, "_http_json", side_effect=AssertionError("jupiter")):
            got = trade_stream._read_vault_mid("http://rpc", coin_pool)
        # 3 quote tokens * $2
        self.assertAlmostEqual(got, 6.0, places=3)
        ops = trade_stream._ops_for(quote_pool)
        self.assertIn("acct:" + quote_pool["address"], ops)
        self.assertEqual(ops["acct:" + quote_pool["address"]]["method"], "accountSubscribe")
        self.assertTrue(any(key.startswith("logs:" + coin_pool["address"]) for key in trade_stream._wanted_log_ops()))

    def test_sis_uses_spcx_usd_and_log_sockets_stay_capped(self):
        """SIS/SPCX is the pool ratio times SPCX's SOL or USDC price. Logs stay on one socket."""
        import struct
        import trade_stream
        spcx = "SPCXxcqXj6e5dJDVNovHN8744zkbhM2bYudU45BimGb"
        sis = "3LgReEUsJxzdJz95zCsBXeMzqs8grMGRoUH34jz1pump"
        sol = trade_stream.SOL_MINT
        # Meteora DLMM: active id 0, bin step 10, both 9 decimals → 1 SOL per SPCX.
        raw = bytearray(160)
        raw[:8] = trade_stream.DLMM_DISC
        struct.pack_into("<i", raw, 76, 0)
        struct.pack_into("<H", raw, 80, 10)
        raw[88:120] = trade_stream.b58decode(spcx)
        raw[120:152] = trade_stream.b58decode(sol)
        px = trade_stream.meteora_dlmm_usd(bytes(raw), spcx, 100.0, {spcx: 9, sol: 9})
        self.assertAlmostEqual(px, 100.0, places=4)
        # Raydium CLMM SPCX/USDC at $2, then SIS vault is 4 SPCX per token → $8.
        clmm = bytearray(280)
        clmm[:8] = trade_stream.CLMM_DISC
        clmm[73:105] = trade_stream.b58decode(spcx)
        clmm[105:137] = trade_stream.b58decode(trade_stream.USDC_MINT)
        clmm[233] = 6
        clmm[234] = 6
        import math
        clmm[253:269] = int(math.sqrt(2.0) * (2 ** 64)).to_bytes(16, "little")
        self.assertAlmostEqual(trade_stream.raydium_clmm_usd(bytes(clmm), spcx, 100.0), 2.0, places=3)
        sis_pool = {
            "mint": sis,
            "kind": "pumpswap",
            "address": _pk(91),
            "base_mint": sis,
            "quote_mint": spcx,
            "base_vault": _pk(92),
            "quote_vault": _pk(93),
            "base_dec": 6,
            "quote_dec": 6,
            "virtual_quote": 0,
        }
        trade_stream._pools[sis] = sis_pool
        trade_stream._quote_pools[spcx] = {
            "mint": spcx,
            "kind": "raydium",
            "address": _pk(94),
            "base_mint": spcx,
            "quote_mint": trade_stream.USDC_MINT,
        }
        trade_stream.note_anchor(sol, 100.0, force=True)
        trade_stream.note_view(sis)

        def acct(http, addr):
            if addr == _pk(94):
                import base64
                return {"data": [base64.b64encode(bytes(clmm)).decode(), "base64"]}
            return None

        with mock.patch.object(trade_stream, "_account_info", side_effect=acct), \
             mock.patch.object(trade_stream, "_vault_amounts", return_value=(1_000_000, 4_000_000)), \
             mock.patch.object(trade_stream, "mint_decimals", return_value=6), \
             mock.patch.object(game, "_http_json", side_effect=AssertionError("jupiter")):
            got = trade_stream._read_vault_mid("http://rpc", sis_pool)
        self.assertAlmostEqual(got, 8.0, places=3)
        # SPCX math is unchanged. A lone vault is not a live tick until a swap agrees.
        self.assertEqual(
            trade_stream.ingest_swap(sis, got, source="pool_swap", signature="sis-fill"),
            "ok",
        )
        self.assertAlmostEqual(game._live_cache[sis][1]["price_usd"], 8.0, places=3)
        # Ten watched pools, one of them a position. The socket plan is capped
        # and the position is first.
        trade_stream.reset_for_tests()
        held = _pk(200)
        for i in range(10):
            mint = held if i == 7 else _pk(210 + i)
            trade_stream._pools[mint] = {
                "mint": mint,
                "kind": "pumpswap",
                "address": _pk(300 + i),
                "base_vault": _pk(400 + i),
                "quote_vault": _pk(500 + i),
            }
            trade_stream.note_view(mint)
            time.sleep(0.002)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        now = datetime.now(timezone.utc)
        game.save_state({
            "status": "active",
            "positions": [{"mint": held}],
            "closed": [],
            "events": [],
        })
        plan = trade_stream._priority_log_ops()
        self.assertLessEqual(len(plan), trade_stream.MAX_LOG_SUBS)
        self.assertEqual(trade_stream.MAX_LOG_SOCKETS, 1)
        first = next(iter(plan.values()))
        self.assertEqual(first["mint"], held)
        self.assertEqual(len(trade_stream._wanted_log_ops()), 10)

    def test_rpc_urls_come_from_env_and_defaults_stay(self):
        import os
        import trade_stream
        keys = [k for k in os.environ if k.startswith("SOLANA_") or k == "HELIUS_API_KEY"]
        saved = {k: os.environ.pop(k) for k in keys}
        try:
            wss, http, name = trade_stream.endpoints()
            self.assertEqual(wss, "wss://solana-rpc.publicnode.com")
            self.assertEqual(http, "https://solana-rpc.publicnode.com")
            self.assertEqual(name, "public")
            self.assertIn("mainnet-beta.solana.com", trade_stream.logs_endpoint())
            with mock.patch.dict(os.environ, {
                "SOLANA_RPC_URL": "https://rpc.example/sol",
                "SOLANA_WS_URL": "wss://rpc.example/sol",
            }):
                wss, http, name = trade_stream.endpoints()
                self.assertEqual(http, "https://rpc.example/sol")
                self.assertEqual(wss, "wss://rpc.example/sol")
                self.assertEqual(name, "env")
                self.assertEqual(trade_stream.logs_endpoint(), "wss://rpc.example/sol")
        finally:
            os.environ.update(saved)


def _parse_iso(ts: str) -> float:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()


class ShipW92Tests(unittest.TestCase):
    """Spike guard: no unconfirmed override, and a hard 3.0s hold cap."""

    def setUp(self):
        import trade_stream
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def tearDown(self):
        import trade_stream
        game._live_cache.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def test_no_baseline_override_is_refused_and_the_tick_is_stale(self):
        """Two identical swaps with baseline_age 1e9 must not publish an override."""
        import trade_stream
        bad = 2.99e-4
        real = 1.96e-6
        self.assertEqual(
            trade_stream.ingest_swap(MINT, bad, anchor=real, source="pool_swap", signature="a"),
            "spike",
        )
        self.assertEqual(
            trade_stream.ingest_swap(MINT, bad, anchor=real, source="pool_swap", signature="b"),
            "spike",
        )
        self.assertNotIn(MINT, game._live_cache)
        kinds = [row.get("kind") for row in trade_stream.recent_spikes()]
        self.assertNotIn("override", kinds)
        self.assertTrue(any(row.get("reason") == "no-baseline" for row in trade_stream.recent_spikes()))
        self.assertGreater(trade_stream._baseline_age(MINT), trade_stream.BASELINE_HUGE_S)
        tick = game.fetch_live_tick(MINT, apply_qa=False)
        self.assertTrue(tick.get("stale"))
        self.assertFalse(tick.get("quote_live"))
        self.assertGreater(tick.get("quote_age_s"), 5)
        body = server.app.test_client().get("/api/game/tick?mint=" + MINT).get_json()
        self.assertTrue(body["stale"])
        self.assertFalse(body["quote_live"])

    def test_swap_and_vault_within_5_percent_publish(self):
        import trade_stream
        real = 1.96e-6
        self.assertEqual(
            trade_stream.ingest_swap(MINT, real, anchor=real * 100, source="pool_swap", signature="swap"),
            "spike",
        )
        self.assertNotIn(MINT, game._live_cache)
        status = trade_stream.ingest_swap(MINT, real * 1.04, source="pool_vault", signature="vault")
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], real * 1.04, places=12)
        self.assertFalse(trade_stream.awaiting_baseline(MINT))
        trade_stream.reset_for_tests()
        game._live_cache.clear()
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 1.0, anchor=50.0, source="pool_swap", signature="swap"),
            "spike",
        )
        # 6% is outside the 5% pair. It must not become the tick.
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 1.06, source="pool_vault", signature="vault"),
            "spike",
        )
        self.assertNotIn(MINT, game._live_cache)

    def test_two_agreeing_reads_replace_a_stuck_price(self):
        import trade_stream
        bad = 2.99e-4
        good = 1.96e-6
        self.assertEqual(trade_stream.ingest_swap(MINT, bad, seed_ok=True, source="pool_swap"), "ok")
        self.assertEqual(
            trade_stream.ingest_swap(MINT, good, source="pool_vault", signature="vault"),
            "spike",
        )
        # An echo of the stuck price must not erase the vault read.
        echo = trade_stream.ingest_swap(MINT, bad, source="pool_swap", signature="echo")
        self.assertIn(echo, ("ok", "same"))
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], bad)
        status = trade_stream.ingest_swap(MINT, good * 1.02, source="pool_swap", signature="swap")
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], good * 1.02, places=12)

    def test_hard_3s_cap_with_no_agreement_goes_stale(self):
        """agree=0 at the deadline does not publish. The last price is stale."""
        import trade_stream
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_vault"), "ok")
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 0.06, source="pool_swap", signature="dump"),
            "spike",
        )
        timer = trade_stream._hold_timers.get(MINT)
        self.assertIsNotNone(timer)
        self.assertLessEqual(timer.interval, trade_stream.SPIKE_HOLD_S + 0.05)
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        trade_stream._release_hold(MINT, trade_stream._hold_gen.get(MINT, 0))
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        row = next(r for r in trade_stream.recent_spikes() if r.get("reason") == "hold-cap")
        self.assertEqual(row.get("agree"), 0)
        self.assertTrue(row.get("stale"))
        self.assertNotEqual(row.get("kind"), "override")
        tick = game.fetch_live_tick(MINT, apply_qa=False)
        self.assertFalse(game.fillable_tick(tick))
        self.assertGreater(tick.get("quote_age_s"), game.QUOTE_FRESH_SLA_S)
        body = server.app.test_client().get("/api/game/tick?mint=" + MINT).get_json()
        self.assertTrue(body["stale"])
        self.assertAlmostEqual(body["price_usd"], 1.0)
        # A new buy, which is how a new stop is opened, stays blocked.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json"):
            game.start_session(force=True)
            refused = game.buy(MINT, 1.0, sl_pct=20)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["state"]["positions"], [])

    def test_one_off_disagreement_blocks_the_timer(self):
        """A later read that disagrees keeps the 64x out, including if the timer fires."""
        import trade_stream
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_vault"), "ok")
        self.assertEqual(trade_stream.ingest_swap(MINT, 64.0, source="pool_swap", signature="fake"), "spike")
        self.assertEqual(trade_stream.ingest_swap(MINT, 2.0, source="pool_swap", signature="other"), "spike")
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        trade_stream._release_hold(MINT, trade_stream._hold_gen.get(MINT, 0))
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        self.assertTrue(trade_stream._pending_spikes[MINT].get("rejected"))


def _swap_tx(pool_addr: str, base_vault: str, quote_vault: str, base_raw: int, quote_raw: int, *, block_time: int | None = None) -> dict:
    """A confirmed swap whose post-trade vault balances are the price."""
    import base64
    import struct
    import trade_stream
    body = bytearray(160)
    struct.pack_into("<Q", body, 8, 1_000)
    struct.pack_into("<Q", body, 104, 1_000)
    body[112:144] = trade_stream.b58decode(pool_addr)
    raw = trade_stream.BUY_EVENT_DISC + bytes(body)
    line = "Program data: " + base64.b64encode(raw).decode()
    when = int(time.time()) - 20000 if block_time is None else block_time
    return {
        "blockTime": when,
        "meta": {
            "err": None,
            "logMessages": ["Program log: ignored", line],
            "postTokenBalances": [
                {"accountIndex": 1, "uiTokenAmount": {"amount": str(base_raw), "decimals": 6}},
                {"accountIndex": 2, "uiTokenAmount": {"amount": str(quote_raw), "decimals": 9}},
            ],
        },
        "transaction": {"message": {"accountKeys": ["payer", base_vault, quote_vault]}},
    }


def _batch_rpc(sig_result, tx_result):
    calls = []

    def post(http, body, timeout):
        payload = json.loads(body.decode())
        items = payload if isinstance(payload, list) else [payload]
        calls.append(items)
        if not isinstance(payload, list):
            method = payload.get("method")
            if method == "getSignaturesForAddress":
                return {"jsonrpc": "2.0", "id": payload.get("id", 1), "result": sig_result}
            if method == "getTransaction":
                return {"jsonrpc": "2.0", "id": payload.get("id", 1), "result": tx_result}
            return {"jsonrpc": "2.0", "id": payload.get("id", 1), "result": None}
        out = []
        for item in payload:
            method = item.get("method")
            if method == "getSignaturesForAddress":
                out.append({"jsonrpc": "2.0", "id": item["id"], "result": sig_result})
            elif method == "getTransaction":
                out.append({"jsonrpc": "2.0", "id": item["id"], "result": tx_result})
            else:
                out.append({"jsonrpc": "2.0", "id": item["id"], "error": {"code": -1, "message": "no"}})
        return out

    return post, calls


class ShipW93Tests(unittest.TestCase):
    """Pools past the logs cap baseline from the newest on-chain swap."""

    def setUp(self):
        import trade_stream
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def tearDown(self):
        import trade_stream
        game._live_cache.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def _arm(self, n: int, quote: str, *, base_dec: int = 6, quote_dec: int = 9, virtual: int = 0) -> tuple[str, dict]:
        import trade_stream
        mint = _pk(n)
        pool = {
            "mint": mint,
            "kind": "pumpswap",
            "address": _pk(n + 1),
            "base_mint": mint,
            "quote_mint": quote,
            "base_vault": _pk(n + 2),
            "quote_vault": _pk(n + 3),
            "base_dec": base_dec,
            "quote_dec": quote_dec,
            "virtual_quote": virtual,
        }
        trade_stream._pools[mint] = pool
        trade_stream.note_view(mint)
        trade_stream.note_anchor(trade_stream.SOL_MINT, 100.0, force=True)
        return mint, pool

    def test_no_logs_pool_baselines_from_an_old_signature(self):
        """No logs subscription. An hours-old swap's post-trade price confirms the vault."""
        import trade_stream
        mint, pool = self._arm(610, trade_stream.SOL_MINT, virtual=1_000_000_000)
        # (2 SOL + 1 virtual) * $100. The swap is hours old; blockTime is not a gate.
        tx = _swap_tx(
            pool["address"], pool["base_vault"], pool["quote_vault"],
            1_000_000, 2_000_000_000,
            block_time=int(time.time()) - 20000,
        )
        sigs = [{"signature": "oldswap", "err": None, "blockTime": tx["blockTime"]}]
        post, calls = _batch_rpc(sigs, tx)
        self.assertEqual(
            trade_stream.ingest_swap(mint, 300.0, source="pool_vault", signature="vault"),
            "spike",
        )
        self.assertNotIn(mint, game._live_cache)
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._run_chain_baselines("http://rpc", [mint])
        self.assertIn(mint, game._live_cache)
        tick = game._live_cache[mint][1]
        self.assertAlmostEqual(tick["price_usd"], 300.0, places=6)
        self.assertEqual(tick["source"], "pool_swap")
        self.assertFalse(trade_stream.awaiting_baseline(mint))
        methods = [item.get("method") for batch in calls for item in batch]
        self.assertIn("getSignaturesForAddress", methods)
        self.assertIn("getTransaction", methods)
        for batch in calls:
            for item in batch:
                if item.get("method") == "getSignaturesForAddress":
                    self.assertLessEqual(item["params"][1]["limit"], trade_stream.SIG_LOOKBACK)
        fetched = game.fetch_live_tick(mint, apply_qa=False)
        self.assertFalse(fetched.get("stale"))
        self.assertAlmostEqual(fetched["price_usd"], 300.0, places=6)

    def test_non_sol_quote_uses_the_quote_usd(self):
        import trade_stream
        quote = _pk(630)
        mint, pool = self._arm(640, quote, quote_dec=6, virtual=0)
        trade_stream._quote_px[quote] = (time.time(), 2.0)
        # 4 quote tokens * $2.
        tx = _swap_tx(
            pool["address"], pool["base_vault"], pool["quote_vault"],
            1_000_000, 4_000_000,
        )
        post, _calls = _batch_rpc([{"signature": "qswap", "err": None}], tx)
        self.assertEqual(trade_stream.ingest_swap(mint, 8.0, source="pool_vault", signature="v"), "spike")
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post), \
             mock.patch.object(game, "_http_json", side_effect=AssertionError("jupiter")):
            trade_stream._run_chain_baselines("http://rpc", [mint])
        self.assertAlmostEqual(game._live_cache[mint][1]["price_usd"], 8.0, places=6)
        self.assertIn(game._live_cache[mint][1]["source"], ("pool_swap", "pool_vault"))

    def test_swap_more_than_5_percent_from_the_vault_stays_stale(self):
        import trade_stream
        mint, pool = self._arm(660, trade_stream.SOL_MINT, virtual=1_000_000_000)
        tx = _swap_tx(
            pool["address"], pool["base_vault"], pool["quote_vault"],
            1_000_000, 2_000_000_000,
        )
        post, _calls = _batch_rpc([{"signature": "far", "err": None}], tx)
        # 8% above the post-trade price. The 5% pair refuses it.
        self.assertEqual(trade_stream.ingest_swap(mint, 324.0, source="pool_vault", signature="v1"), "spike")
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._run_chain_baselines("http://rpc", [mint])
        self.assertNotIn(mint, game._live_cache)
        self.assertNotIn(mint, trade_stream._chain_empty)
        # A later vault that agrees with the first vault must not sneak through.
        self.assertEqual(trade_stream.ingest_swap(mint, 325.0, source="pool_vault", signature="v2"), "spike")
        self.assertNotIn(mint, game._live_cache)
        tick = game.fetch_live_tick(mint, apply_qa=False)
        self.assertTrue(tick.get("stale"))
        self.assertIsNone(tick.get("price_usd"))
        self.assertGreater(trade_stream._baseline_age(mint), trade_stream.BASELINE_HUGE_S)

    def test_no_decodable_swap_baselines_on_two_close_vaults(self):
        import trade_stream
        mint, pool = self._arm(680, trade_stream.SOL_MINT)
        tx = {
            "blockTime": int(time.time()) - 5000,
            "meta": {"err": None, "logMessages": ["Program log: not a swap"], "postTokenBalances": []},
            "transaction": {"message": {"accountKeys": ["payer"]}},
        }
        post, _calls = _batch_rpc([{"signature": "noswap", "err": None}], tx)
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._run_chain_baselines("http://rpc", [mint])
        self.assertIn(mint, trade_stream._chain_empty)
        self.assertEqual(trade_stream.ingest_swap(mint, 10.0, source="pool_vault", signature="a"), "spike")
        self.assertNotIn(mint, game._live_cache)
        # 2% is outside the 1% vault pair.
        self.assertEqual(trade_stream.ingest_swap(mint, 10.2, source="pool_vault", signature="b"), "spike")
        self.assertNotIn(mint, game._live_cache)
        trade_stream.reset_for_tests()
        game._live_cache.clear()
        mint, pool = self._arm(690, trade_stream.SOL_MINT)
        post, _calls = _batch_rpc([{"signature": "noswap2", "err": None}], tx)
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._run_chain_baselines("http://rpc", [mint])
        self.assertEqual(trade_stream.ingest_swap(mint, 10.0, source="pool_vault", signature="a"), "spike")
        self.assertEqual(trade_stream.ingest_swap(mint, 10.05, source="pool_vault", signature="b"), "ok")
        self.assertAlmostEqual(game._live_cache[mint][1]["price_usd"], 10.05, places=6)
        self.assertEqual(game._live_cache[mint][1]["source"], "pool_vault")
        self.assertFalse(trade_stream.awaiting_baseline(mint))

    def test_unconfirmed_recheck_waits_10s_and_a_live_log_skips_rpc(self):
        import trade_stream
        mint, pool = self._arm(700, trade_stream.SOL_MINT, virtual=1_000_000_000)
        tx = _swap_tx(
            pool["address"], pool["base_vault"], pool["quote_vault"],
            1_000_000, 2_000_000_000,
        )
        post, calls = _batch_rpc([{"signature": "far", "err": None}], tx)
        trade_stream.ingest_swap(mint, 324.0, source="pool_vault", signature="v")
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._run_chain_baselines("http://rpc", [mint])
            used = len(calls)
            self.assertGreater(used, 0)
            trade_stream._run_chain_baselines("http://rpc", [mint])
            self.assertEqual(len(calls), used)
            trade_stream._chain_at[mint] = time.monotonic() - 11
            trade_stream._run_chain_baselines("http://rpc", [mint])
            self.assertGreater(len(calls), used)
        # A pool whose logs subscription already delivered a swap is not fetched.
        trade_stream.reset_for_tests()
        mint, pool = self._arm(710, trade_stream.SOL_MINT)
        trade_stream._ws["subscriptions"]["logs:" + pool["address"]] = 7
        trade_stream._logs_priced[pool["address"]] = time.monotonic()
        post, calls = _batch_rpc([{"signature": "x", "err": None}], tx)
        with mock.patch.object(trade_stream, "_post_rpc", side_effect=post):
            trade_stream._run_chain_baselines("http://rpc", [mint])
        self.assertEqual(calls, [])


class ShipW94Tests(unittest.TestCase):
    """AgenC dust fills stay on the curve, and the 3s cap needs agreement."""

    def setUp(self):
        import trade_stream
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def tearDown(self):
        import trade_stream
        game._live_cache.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game.set_client_sid(None)
        trade_stream.reset_for_tests()

    def test_swap_and_vault_more_than_5_percent_apart_never_publish_fresh(self):
        import trade_stream
        spot = 2.80e-6
        dust = 2.02e-3
        self.assertEqual(trade_stream.ingest_swap(MINT, spot, seed_ok=True, source="pool_vault"), "ok")
        self.assertEqual(
            trade_stream.ingest_swap(MINT, dust, source="pool_swap", signature="dust"),
            "spike",
        )
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], spot)
        # The other leg keeps printing the curve. It must not confirm the dust.
        self.assertEqual(
            trade_stream.ingest_swap(MINT, spot * 1.04, source="pool_vault", signature="vault"),
            "ok",
        )
        self.assertLess(abs(game._live_cache[MINT][1]["price_usd"] - spot) / spot, 0.05)
        self.assertGreater(abs(game._live_cache[MINT][1]["price_usd"] - dust) / dust, 0.5)
        tick = game.fetch_live_tick(MINT, apply_qa=False)
        self.assertTrue(game.fillable_tick(tick))
        self.assertFalse(tick.get("stale"))
        trade_stream._release_hold(MINT, trade_stream._hold_gen.get(MINT, 0))
        self.assertLess(abs(game._live_cache[MINT][1]["price_usd"] - spot) / spot, 0.05)

    def test_cap_with_agreement_publishes_by_3s(self):
        import trade_stream
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_vault"), "ok")
        t0 = time.time()
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 0.50, source="pool_swap", signature="crash"),
            "spike",
        )
        # 2% under the swap. Within 5%, so the crash publishes before the cap.
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 0.51, source="pool_vault", signature="vault"),
            "ok",
        )
        self.assertLess(time.time() - t0, 3.0)
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 0.51, places=6)
        tick = game.fetch_live_tick(MINT, apply_qa=False)
        self.assertTrue(game.fillable_tick(tick))
        # The deadline itself also publishes when the two reads already agree.
        trade_stream.reset_for_tests()
        game._live_cache.clear()
        self.assertEqual(trade_stream.ingest_swap(MINT, 1.0, seed_ok=True, source="pool_vault"), "ok")
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 0.40, source="pool_swap", signature="crash2"),
            "spike",
        )
        trade_stream._remember_pair(MINT, 0.41, "pool_vault", "vault2")
        gen = trade_stream._hold_gen.get(MINT, 0)
        started = time.time()
        trade_stream._release_hold(MINT, gen)
        self.assertLess(time.time() - started, 3.0)
        self.assertAlmostEqual(game._live_cache[MINT][1]["price_usd"], 0.41, places=6)
        row = next(r for r in trade_stream.recent_spikes() if r.get("reason") == "hold-cap")
        self.assertLessEqual(row["hold_s"], 3.0)
        self.assertLess(row["agree"], 0.05)
        self.assertFalse(row.get("stale"))

    def test_open_stop_does_not_fire_on_the_unconfirmed_price(self):
        import trade_stream
        entry = 1.0
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state_patch = mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json")
        state_patch.start()
        self.addCleanup(state_patch.stop)
        game.start_session(force=True)
        with mock.patch.object(game, "fetch_dex_token", return_value={"symbol": "NVD", "name": "N"}), \
             mock.patch.object(game, "_http_json", return_value=_jup(entry)):
            opened = game.buy(MINT, 1.0, sl_pct=20)
        self.assertTrue(opened["ok"], opened)
        trade_stream.reset_for_tests()
        game._live_cache.clear()
        self.assertEqual(trade_stream.ingest_swap(MINT, entry, seed_ok=True, source="pool_vault"), "ok")
        self.assertEqual(
            trade_stream.ingest_swap(MINT, 0.06, source="pool_swap", signature="dump"),
            "spike",
        )
        trade_stream._release_hold(MINT, trade_stream._hold_gen.get(MINT, 0))
        st = game.tick_session()
        self.assertEqual(len(st["positions"]), 1)
        self.assertEqual(st["positions"][0]["close_reason"] if False else st["closed"], [])

    def test_agenc_recorded_swap_and_vault_match_the_size_bearing_fill(self):
        """Pool ABF43s. Dust user-quote is not the price. A real buy is."""
        import base64
        import trade_stream
        fixture = json.loads(Path("qa/agenc_panel94_fixture.json").read_text(encoding="utf-8"))
        sol = float(fixture["sol_usd"])
        pool_raw = base64.b64decode(fixture["pool_account_b64"])
        view = trade_stream.pumpswap_pool_view(pool_raw)
        self.assertEqual(view["base_mint"], fixture["mint"])
        self.assertEqual(view["quote_mint"], trade_stream.SOL_MINT)
        self.assertNotEqual(view["virtual_quote"], 0)
        pool = {
            "kind": "pumpswap",
            "mint": fixture["mint"],
            "address": fixture["pool"],
            "base_mint": view["base_mint"],
            "quote_mint": view["quote_mint"],
            "base_dec": 6,
            "quote_dec": 9,
            "virtual_quote": view["virtual_quote"],
        }
        dust_raw = base64.b64decode(fixture["dust"]["event_b64"])
        real_raw = base64.b64decode(fixture["real"]["event_b64"])
        dust_px = trade_stream.price_from_program_logs(
            ["Program data: " + fixture["dust"]["event_b64"]], pool, sol,
        )
        real_px = trade_stream.price_from_program_logs(
            ["Program data: " + fixture["real"]["event_b64"]], pool, sol,
        )
        real_fill = trade_stream.pumpswap_event_fill(real_raw)
        real_res = trade_stream.pumpswap_event_reserves(real_raw)
        real_virt = trade_stream.pumpswap_event_virtual_quote(real_raw)
        executed = trade_stream.pumpswap_mid_usd(
            real_fill[0], 6, real_fill[1], 9,
            view["base_mint"], view["quote_mint"], fixture["mint"], sol, 0,
        )
        vault = trade_stream.pumpswap_mid_usd(
            real_res[0], 6, real_res[1], 9,
            view["base_mint"], view["quote_mint"], fixture["mint"], sol, real_virt,
        )
        self.assertIsNotNone(executed)
        self.assertIsNotNone(vault)
        self.assertLess(abs(vault - executed) / executed, 0.02)
        self.assertLess(abs(real_px - executed) / executed, 0.02)
        self.assertLess(abs(dust_px - executed) / executed, 0.02)
        # The raw user-quote on the dust buy is the 2x-high print. It must not win.
        dust_fill = trade_stream.pumpswap_event_fill(dust_raw)
        bogus = trade_stream.pumpswap_mid_usd(
            dust_fill[0], 6, dust_fill[1], 9,
            view["base_mint"], view["quote_mint"], fixture["mint"], sol, 0,
        )
        self.assertGreater(abs(bogus - executed) / executed, 2.0)
        self.assertLess(dust_fill[1], trade_stream.DUST_QUOTE_RAW)

    def test_buy_refuses_a_requote_more_than_5_percent_off_the_screen(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state_patch = mock.patch.object(game, "STATE_PATH", Path(tmp.name) / "state.json")
        state_patch.start()
        self.addCleanup(state_patch.stop)
        game.start_session(force=True)
        with mock.patch.object(game, "fetch_dex_token", return_value={"symbol": "NVD", "name": "N"}), \
             mock.patch.object(game, "_http_json", return_value=_jup(1.0)):
            refused = game.buy(MINT, 1.0, sl_pct=20, displayed_price=1.06)
        self.assertFalse(refused["ok"])
        self.assertIn("5%", refused["error"])
        self.assertEqual(refused["state"]["positions"], [])
        game._live_cache.clear()
        with mock.patch.object(game, "fetch_dex_token", return_value={"symbol": "NVD", "name": "N"}), \
             mock.patch.object(game, "_http_json", return_value=_jup(1.03)):
            filled = game.buy(MINT, 1.0, sl_pct=20, displayed_price=1.0)
        self.assertTrue(filled["ok"], filled)
        self.assertAlmostEqual(filled["fill_price_usd"], 1.03)
        self.assertAlmostEqual(filled["position"]["entry_price_usd"], 1.03)
        self.assertNotAlmostEqual(filled["position"]["entry_price_usd"], 1.0)
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("displayed_price", html)
        self.assertIn("fill_price_usd", html)
        self.assertIn('toast("Bought " + sym + " $" + stake + " @ " + fillTxt)', html)


if __name__ == "__main__":
    unittest.main()
