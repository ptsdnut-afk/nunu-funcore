"""SHIP W — live game ticks (AC-W01…W05). Not a QA PASS.

The Game mark is a fresh Jupiter/Gecko tick with a <=2s server cache.
Buy, sell, and stops fill at that tick and store price_ts. A print older
than 5s is not a fill. The rocket chart is driven by those prices.
"""

from __future__ import annotations

import subprocess
import tempfile
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
        game._pool_cache.clear()

    def tearDown(self):
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._pool_cache.clear()

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
        self.assertGreaterEqual(after_miss, 1)
        self.assertLessEqual(after_miss, 4)
        self.assertEqual(calls["n"], after_miss)
        self.assertTrue(game.fillable_tick(first))
        self.assertEqual(second["price_usd"], first["price_usd"])
        self.assertEqual(second["price_ts"], first["price_ts"])
        self.assertLessEqual(second["quote_age_s"], game.LIVE_TICK_TTL_S)
        self.assertEqual(first["source"], "jup")

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

    def test_gecko_is_used_when_jupiter_has_no_price(self):
        def fake(url: str):
            if "jup.ag" in url:
                return 200, {}
            return 200, {
                "data": {"attributes": {"token_prices": {MINT: "0.0000913"}}},
            }

        with mock.patch.object(game, "_http_json", side_effect=fake):
            tick = game.fetch_live_tick(MINT, bypass_cache=True)
        self.assertTrue(game.fillable_tick(tick))
        self.assertEqual(tick["source"], "gecko")
        self.assertAlmostEqual(tick["price_usd"], 0.0000913)

    def test_sol_quote_beats_a_stale_jupiter_mark(self):
        """Pool quote, not the slower Jupiter usdPrice and not Dex priceUsd."""
        def fake(url: str):
            if "swap/v1/quote" in url:
                return 200, {"outAmount": "1000000"}
            return 200, {
                game.SOL_MINT: {"usdPrice": 100.0, "decimals": 9},
                MINT: {"usdPrice": 0.5, "decimals": 6, "blockId": 9},
            }

        with mock.patch.object(game, "_http_json", side_effect=fake):
            tick = game.fetch_live_tick(MINT, bypass_cache=True)
        self.assertTrue(game.fillable_tick(tick))
        self.assertAlmostEqual(tick["price_usd"], 0.1)
        self.assertNotAlmostEqual(tick["price_usd"], 0.5)
        self.assertEqual(tick["source"], "jup")
        dex = 0.101
        gap = abs(tick["price_usd"] - dex) / dex
        self.assertLess(gap, 0.02)

    def test_pool_trade_is_the_tick_not_a_jupiter_mark(self):
        pool = "Pool111111111111111111111111111111111111111"
        def fake(url: str):
            if "/pools?" in url or url.endswith("/pools?page=1"):
                return 200, {
                    "data": [{
                        "id": "solana_" + pool,
                        "attributes": {"reserve_in_usd": "50000"},
                        "relationships": {
                            "base_token": {"data": {"id": "solana_" + MINT}},
                            "quote_token": {"data": {"id": "solana_" + game.SOL_MINT}},
                        },
                    }]
                }
            if "/trades" in url:
                return 200, {
                    "data": [{
                        "attributes": {
                            "from_token_address": MINT,
                            "to_token_address": game.SOL_MINT,
                            "price_from_in_usd": "0.0008173",
                            "price_to_in_usd": "100",
                        }
                    }]
                }
            return 200, {MINT: {"usdPrice": 0.000762, "decimals": 6, "blockId": 1}}

        with mock.patch.object(game, "_http_json", side_effect=fake):
            tick = game.fetch_live_tick(MINT, bypass_cache=True)
        self.assertEqual(tick["source"], "gecko")
        self.assertAlmostEqual(tick["price_usd"], 0.0008173)
        self.assertLess(abs(tick["price_usd"] - 0.0008173) / 0.0008173, 0.02)


class TradeFillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._live_backoff_s.clear()
        game._pool_cache.clear()
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
        game._pool_cache.clear()
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
        self.assertEqual(data["panel_v"], 83)
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
            "source": "jup",
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

    def test_panel_chart_ignores_zero_and_rocket_keeps_moving(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('data-feature="live_tick"', html)
        self.assertIn("LIVE_TICK_MS: 2000", html)
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
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
