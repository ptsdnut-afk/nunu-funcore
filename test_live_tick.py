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
        import trade_stream
        trade_stream.reset_for_tests()

    def tearDown(self):
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)

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
        self.assertAlmostEqual(row["exit_price_usd"], trigger)
        self.assertNotAlmostEqual(row["exit_price_usd"], shown)
        self.assertTrue(row["price_ts"])
        stamped = datetime.strptime(row["price_ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(abs(time.time() - stamped.timestamp()), 5)
        expected_pnl = round(trigger * qty - stake, 6)
        expected_pct = round((trigger / entry - 1.0) * 100, 2)
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

    def test_qa_walk_does_not_stop_or_fill_at_the_synthetic_price(self):
        """W24: the shared book stops and sells on the real tick only."""
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
        with mock.patch.object(game, "_http_json", return_value=_jup(1.0)):
            shown = game.fetch_live_tick(MINT)
            held = game.tick_session()
            sold = game.sell("pos1")
        self.assertLessEqual(shown["price_usd"], 0.75)
        self.assertEqual(shown["source"], "qa_tick")
        self.assertEqual(len(held["positions"]), 1)
        self.assertEqual(game._live_cache[MINT][1]["price_usd"], 1.0)
        self.assertEqual(game._live_cache[MINT][1]["source"], "jupiter")
        self.assertTrue(sold["ok"], sold)
        self.assertAlmostEqual(sold["state"]["closed"][0]["exit_price_usd"], 1.0)

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


class HealthAndChartTests(unittest.TestCase):
    def test_health_is_v82_with_live_tick_and_a_five_second_sla(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 85)
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
        self.assertIn("LIVE_TICK_MS: 1000", html)
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
        status = trade_stream.ingest_swap(MINT, 1.0, anchor=1.0)
        self.assertEqual(status, "ok")
        with mock.patch.object(game, "_http_json", side_effect=AssertionError("jupiter")):
            tick = game.fetch_live_tick(MINT, apply_qa=False)
        self.assertEqual(tick["source"], "stream")
        self.assertAlmostEqual(tick["price_usd"], 1.0)
        self.assertTrue(tick.get("trade_ts"))
        self.assertTrue(tick.get("render_ts"))
        trade_stream._seen[MINT] = time.time() - 6
        with mock.patch.object(game, "_http_json", return_value=_jup(1.01)):
            fallback = game.fetch_live_tick(MINT, bypass_cache=True, apply_qa=False)
        self.assertEqual(fallback["source"], "jupiter")

    def test_stream_rejects_an_outlier_over_three_percent(self):
        import trade_stream
        status = trade_stream.ingest_swap(MINT, 1.04, anchor=1.0)
        self.assertEqual(status, "outlier")
        self.assertNotIn(MINT, game._live_cache)
        self.assertTrue(trade_stream.recent_rejects())

    def test_whirlpool_price_uses_the_watched_side(self):
        import trade_stream
        raw = bytearray(220)
        raw[65:81] = (1 << 64).to_bytes(16, "little")
        raw[101:133] = trade_stream.b58decode(trade_stream.SOL_MINT)
        raw[181:213] = trade_stream.b58decode(MINT)
        px = trade_stream.whirlpool_swap_usd(bytes(raw), MINT, sol_usd=100.0, decimals={MINT: 9})
        self.assertAlmostEqual(px, 100.0, places=6)

    def test_health_documents_the_stream_and_panel_85(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 85)
        self.assertIn("trade_stream", data["features"])
        self.assertIn("stream", data["live_tick"])
        self.assertIn("PumpPortal", data["live_tick"])
        self.assertIn("price_ts", data["live_tick"])

    def test_w20_w22_w23_w26_w27_w28_panel_contract(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 85", html)
        self.assertIn("?v=85", html)
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
if (line.indexOf("source=stream") < 0 || line.indexOf("trade_ts=") < 0 || line.indexOf("render_ts=") < 0) process.exit(23);
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


if __name__ == "__main__":
    unittest.main()
