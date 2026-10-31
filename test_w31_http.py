"""W11, W07, W25 through the Flask app. Real routes and a real book. No price mocks."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import game
import server

SOL = "So11111111111111111111111111111111111111112"
MINT_A = "MintA1111111111111111111111111111111111111"
MINT_B = "MintB2222222222222222222222222222222222222"


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LiveHttpPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._paths = []
        for name, path in (
            ("STATE_PATH", root / "state.json"),
            ("SCORES_PATH", root / "scores.json"),
            ("PROFILE_PATH", root / "profile.json"),
        ):
            self._paths.append((name, getattr(game, name)))
            setattr(game, name, path)
        game._live_cache.clear()
        game._live_backoff_until.clear()
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        self.client = server.app.test_client()

    def tearDown(self):
        for name, path in self._paths:
            setattr(game, name, path)
        game._qa_walks.clear()
        game.set_qa_walk_sid(None)
        game._live_cache.clear()

    def _book(self, positions):
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "livebook",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 3.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": positions,
            "closed": [],
            "events": [],
        })

    def _pos(self, pid, mint, entry):
        return {
            "id": pid,
            "mint": mint,
            "symbol": pid,
            "stake_usd": 1.0,
            "entry_price_usd": entry,
            "qty_tokens": 1.0 / entry,
            "last_price_usd": entry,
            "price_ts": _utc(datetime.now(timezone.utc)),
            "opened_at": _utc(datetime.now(timezone.utc) - timedelta(minutes=10)),
            "sl_pct": 20,
            "tp_pct": 100,
        }

    def test_sell_all_closes_both_mints_over_http(self):
        self._book([
            self._pos("one", MINT_A, 1.0),
            self._pos("two", MINT_B, 2.0),
        ])
        res = self.client.post("/api/game/sell", json={"all": True})
        body = res.get_json()
        self.assertEqual(res.status_code, 200, body)
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(body.get("sold"), 2, body)
        self.assertEqual(body.get("open_count"), 0, body)
        saved = game.load_state()
        self.assertEqual(saved["positions"], [])
        self.assertEqual(len(saved["closed"]), 2)
        bases = {row.get("fill_basis") for row in saved["closed"]}
        self.assertTrue(bases <= {"live", "last_known"}, bases)
        self.assertEqual(len(bases), len([b for b in bases if b]))

    def test_tick_walk_reaches_twenty_percent_in_sixty_polls(self):
        self._book([self._pos("one", SOL, 1.0)])
        first = self.client.get("/api/game/tick?mint=" + SOL + "&qa_tick=down")
        self.assertEqual(first.status_code, 200, first.get_json())
        start = float(first.get_json()["price_usd"])
        self.assertGreater(start, 0)
        last = first.get_json()
        for _ in range(59):
            res = self.client.get("/api/game/tick?mint=" + SOL + "&qa_tick=down")
            self.assertEqual(res.status_code, 200)
            last = res.get_json()
        price = float(last["price_usd"])
        self.assertLessEqual(price, start * 0.80, last)
        self.assertEqual(last.get("source"), "qa_tick")

    def test_stop_fills_within_one_tick_of_the_trigger(self):
        self.client.set_cookie(game.CLIENT_COOKIE, "client-http")
        probe = self.client.get("/api/game/tick?mint=" + SOL)
        self.assertEqual(probe.status_code, 200, probe.get_json())
        spot = float(probe.get_json()["price_usd"])
        self.assertGreater(spot, 0)
        pos = self._pos("one", SOL, spot)
        pos["client_id"] = "client-http"
        self._book([pos])
        armed = self.client.get("/api/game/tick?mint=" + SOL + "&qa_tick=down")
        self.assertEqual(armed.status_code, 200)
        for _ in range(59):
            self.client.get("/api/game/tick?mint=" + SOL + "&qa_tick=down")
        state = self.client.get("/api/game/state")
        body = state.get_json()
        self.assertEqual(body.get("positions"), [], body)
        closed = body["closed"][0]
        self.assertEqual(closed["close_reason"], "stop")
        pnl = float(closed["pnl_pct"])
        one_tick = 1.0
        self.assertLessEqual(pnl, -20.0)
        self.assertGreaterEqual(pnl, -20.0 - one_tick - 0.05, closed)

    def test_quote_then_tick_is_not_no_price(self):
        quoted = self.client.get("/api/game/quote?mint=" + SOL)
        qbody = quoted.get_json()
        self.assertTrue(qbody.get("ok"), qbody)
        tick = self.client.get("/api/game/tick?mint=" + SOL)
        body = tick.get_json()
        self.assertNotEqual(body.get("error"), "no price", body)
        self.assertGreater(float(body.get("price_usd") or 0), 0, body)
        self.assertIn("src", body)
        self.assertTrue(body.get("fetched_at"), body)

    def test_health_reports_stream_attachment(self):
        data = self.client.get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 93)
        stream = data["stream"]
        self.assertIn("why", stream)
        self.assertIn("ws_state", stream)
        self.assertIn("ws_error", stream)
        self.assertIn("subscriptions", stream)
        self.assertIn("last_event", stream)
        self.assertGreaterEqual(stream["watch_ttl_s"], 60)

    def test_version_labels_share_one_constant_and_score_is_its_own_cell(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 93", html)
        self.assertGreaterEqual(html.count("data-panel-v"), 2)
        self.assertIn("function paintPanelVersion()", html)
        self.assertIn("/* w31-score-cell", html)
        cell = html[html.index("/* w31-score-cell"):html.index("/* w31-score-cell") + 700]
        self.assertIn("grid-column: 2", cell)
        self.assertIn("#modeFilter .tok > .coin", cell)
        self.assertIn("overflow: hidden", cell)
        chrome = html[html.index("function currentChrome()"):html.index("function applyTickFlagToQuote()")]
        self.assertIn("state.lastTickResponse", chrome)
        self.assertIn("tick.stale", chrome)
        self.assertIn("function paintSharedPct()", html)
        note = html[html.index("function noteLiveTick("):html.index("function noteLiveTick(") + 1800]
        self.assertIn("paintSharedPct()", note)
        self.assertIn("applyTickFlagToQuote()", note)
        rocket = html[html.index("function paintRocket("):html.index("function paintRocket(") + 2200]
        self.assertIn("sharedTickPct(g).pct", rocket)


if __name__ == "__main__":
    unittest.main()
