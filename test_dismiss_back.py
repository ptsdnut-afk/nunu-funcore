#!/usr/bin/env python3
"""SHIP J — X / Back dismiss (AC-D01…AC-D03) and panel_v 69."""

import unittest
from pathlib import Path


def _panel():
    return Path("static/index.html").read_text(encoding="utf-8")


class HealthTests(unittest.TestCase):
    def test_health_is_v69_with_dismiss_back(self):
        import server

        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 92)
        self.assertIn("dismiss_back", data["features"])
        self.assertIn("token_flow_new_sound", data["features"])
        self.assertIn("game_polish", data["features"])
        self.assertIn("delay_warning", data["features"])


class ChromeTests(unittest.TestCase):
    def test_score_coin_and_trade_have_a_top_right_x(self):
        html = _panel()
        self.assertIn("const PANEL_V = 92", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn('class="dismiss-x"', html)
        self.assertIn('data-dismiss="score"', html)
        self.assertIn('data-dismiss="coin"', html)
        self.assertIn('data-dismiss="trade"', html)
        self.assertIn('id="gTradeBack"', html)
        self.assertIn('id="coinSheetClose"', html)
        self.assertIn("← Back to Token Flow", html)
        self.assertIn("← Back / clear selection", html)
        self.assertIn("function clearGameSelection()", html)
        self.assertIn("function dismissScoreView(", html)
        self.assertIn("function closeCoinDetail()", html)
        self.assertNotIn('id="coinSheetClose">Close<', html)
        clear = html[html.index("function clearGameSelection()"):html.index("function collapseScore(")]
        self.assertNotIn("location.reload", clear)
        self.assertIn("state.lastQuoteMint = null", clear)
        self.assertIn("markTradeFocus(false)", clear)
        self.assertNotIn("gPositions", clear)
        score = html[html.index("function scoreDetailHtml("):html.index("function scorePopHtml(")]
        self.assertIn('data-dismiss="score"', score)
        self.assertIn(">✕</button>", score)
        trade_head = html[html.index('id="gTradeBody"') - 400:html.index('id="gTradeBody"')]
        self.assertIn('id="gTradeBack"', trade_head)
        self.assertIn("hidden", trade_head)

    def test_dismiss_does_not_retune_ship_i_or_restart(self):
        html = _panel()
        self.assertIn("token_flow_new_sound", Path("server.py").read_text(encoding="utf-8"))
        self.assertEqual(html.count('beep("tick")'), 1)
        self.assertIn('beep("down")', html)
        self.assertIn('startTone(ctx, "newtoken"', html)
        self.assertIn(">Social Tap<", html)
        self.assertIn('id="tradersClose">Close<', html)
        start = html[html.index('id="gStart"') - 80:html.index('id="gStart"') + 40]
        self.assertIn("Start 24h ($5)", start)


if __name__ == "__main__":
    unittest.main()
