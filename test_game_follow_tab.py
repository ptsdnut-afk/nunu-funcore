#!/usr/bin/env python3
"""SHIP R — Filter follow tab for an active Game position (AC-F01…F05).

Not a QA PASS. The tab is Filter chrome over the existing Game book.
Sell goes through gameSell → POST /api/game/sell (game.sell). No second ledger.
"""

import subprocess
import unittest
from pathlib import Path

import server


def _panel() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


class FollowHealthTests(unittest.TestCase):
    def test_health_is_v78_with_game_follow_tab(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 84)
        for feature in (
            "game_follow_tab",
            "gold_scar",
            "game_ai_review",
            "trade_chart",
            "hide_max_unknown",
            "qa_max_fixture",
            "sitting_keep",
            "manual_update",
            "game_24h_restart",
            "dismiss_back",
        ):
            self.assertIn(feature, data["features"])


class FollowPanelTests(unittest.TestCase):
    def test_panel_advertises_v78_and_follow_chrome(self):
        html = _panel()
        self.assertIn("const PANEL_V = 84", html)
        self.assertIn("paper only · v84", html)
        self.assertIn("?v=84", html)
        self.assertIn('id="gameFollow"', html)
        self.assertIn('data-feature="game_follow_tab"', html)
        self.assertIn('data-covers-flow="0"', html)
        self.assertIn('data-replaces-flow="0"', html)
        self.assertIn('id="gameFollowChip"', html)
        self.assertIn('id="gameFollowSheet"', html)
        self.assertIn('id="gameFollowScrim"', html)
        css = html[html.index("/* ship-r-follow-start */"):html.index("/* ship-r-follow-end */")]
        self.assertIn("position: fixed", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn("width: max-content", css)
        self.assertIn("width: min(360px, 100%)", css)
        self.assertIn("max-width: min(260px, 100%)", css)
        self.assertIn("syncToastOffset", html)
        self.assertIn("--toast-bottom", html)
        self.assertNotIn("--follow-clearance", html)
        self.assertNotIn("height: calc(100vh - 56px)", html)
        block = html[html.index('id="gameFollow"'):html.index('id="modeGame"')]
        self.assertNotIn('id="table-wrap"', block)
        self.assertNotIn('id="gChartPane"', block)

    def test_sell_uses_game_sell_path(self):
        html = _panel()
        card = html[html.index("function followCardHtml"):html.index("function paintFollowTab")]
        self.assertIn("Sell this", card)
        self.assertIn("data-follow-sell", card)
        self.assertIn("P&amp;L · paper $", card)
        self.assertIn("position $", card)
        self.assertIn("token price", card)
        self.assertIn("fmtGameUsd(mark)", card)
        self.assertIn("fmtPx(p.entry_price_usd)", card)
        self.assertNotIn("/api/game/sell", card)
        wire = html[html.index("function wireFollowTab"):html.index("/* ship-r-ui-end */")]
        self.assertIn('gameSell(sell.getAttribute("data-follow-sell"))', wire)
        self.assertNotIn("/api/", wire)
        sell = html[html.index("async function gameSell"):html.index("async function gameSellAll")]
        self.assertIn('"/api/game/sell"', sell)
        self.assertIn("position_id: pid", sell)
        self.assertNotIn("follow_ledger", html)
        self.assertNotIn("/api/follow", html)

    def test_follow_tab_decision_hides_when_flat_and_on_game(self):
        html = _panel()
        pure = html[html.index("/* ship-r-pure-start */"):html.index("/* ship-r-pure-end */")]
        script = pure + r"""
const pos = { id: "abc123", mint: "Mint111", symbol: "PEBBLE", pnl_pct: 10 };
const live = followTabDecision({ status: "active", remain_seconds: 3600, positions: [pos] }, "filter");
if (!live.show || live.flat || live.count !== 1 || live.ids[0] !== "abc123" || live.replacesFlow) process.exit(2);
const two = followTabDecision({
  status: "active",
  remain_seconds: 10,
  positions: [pos, { id: "def456", mint: "Mint222", symbol: "BOW" }],
}, "filter");
if (!two.show || two.count !== 2) process.exit(3);
const flat = followTabDecision({ status: "active", remain_seconds: 100, positions: [] }, "filter");
if (flat.show || !flat.flat || flat.count !== 0) process.exit(4);
const ended = followTabDecision({ status: "ended", remain_seconds: 0, positions: [pos] }, "filter");
if (ended.show || ended.count !== 0) process.exit(5);
const dead = followTabDecision({ status: "active", remain_seconds: 0, positions: [pos] }, "filter");
if (dead.show) process.exit(6);
const onGame = followTabDecision({ status: "active", remain_seconds: 100, positions: [pos] }, "game");
if (onGame.show || !onGame.flat) process.exit(7);
const nameless = followTabDecision({
  status: "active",
  remain_seconds: 100,
  positions: [{ id: "", mint: "Mint111" }, { id: "x", mint: "" }],
}, "filter");
if (nameless.show) process.exit(8);
const idle = followTabDecision(null, "filter");
if (idle.show || idle.ids.length) process.exit(9);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
