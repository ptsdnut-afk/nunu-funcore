#!/usr/bin/env python3
"""SHIP M — Trade chart + $ label families (AC-C01…C04). Not a QA PASS."""

import subprocess
import unittest
from pathlib import Path

import server


def _panel() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


class TradeChartTests(unittest.TestCase):
    def test_health_is_v73_with_trade_chart(self):
        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 94)
        self.assertIn("trade_chart", data["features"])
        self.assertIn("manual_update", data["features"])
        self.assertIn("sitting_keep", data["features"])
        self.assertTrue(data["sitting_keep"])
        self.assertEqual(data["sitting_drop_min"], 5.0)

    def test_panel_advertises_v73_and_keeps_prior_ships(self):
        html = _panel()
        self.assertIn("const PANEL_V = 94", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("paintPanelVersion", html)
        self.assertNotIn("?v=76", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("data-panel-v", html)
        self.assertIn('id="btnUpdateNow"', html)
        self.assertIn('data-dismiss="trade"', html)
        self.assertIn("const FEED_LOOP_MIN = 5", html)
        self.assertIn('id="gStake" value="1"', html)
        self.assertIn("Where it’s at · DEX Screener", html)

    def test_chart_pane_and_dollar_families(self):
        html = _panel()
        self.assertIn('id="gChartPane"', html)
        self.assertIn('data-feature="trade_chart"', html)
        self.assertIn("No coin selected. Quote a CA or open a paper trade to see the Dex chart.", html)
        self.assertIn("Chart did not load. No Dex pair for this coin, or the Dex embed was blocked.", html)
        self.assertIn("Select an open position to see its Dex chart.", html)
        self.assertIn("function dexEmbedUrl", html)
        self.assertIn("function paintTradeChart", html)
        self.assertIn("function selectPositionChart", html)
        self.assertIn('embed=1&theme=dark&trades=0&info=0', html)
        self.assertIn('const nowLab = trade ? "MCAP" : "Now"', html)
        self.assertIn("Market cap · where it’s at · DEX Screener", html)
        self.assertIn("MCAP is market cap. Stake, mark, and P&amp;L are paper position dollars.", html)
        self.assertIn("P&L · paper $", html)
        self.assertIn('tradeStat("paper", "Stake", "paper $"', html)
        self.assertIn('tradeStat("paper", "Mark", "position $"', html)
        self.assertIn('tradeStat("price", "Entry", "token price"', html)
        self.assertIn(">token price</span>", html)
        self.assertIn("Dex chart is live on DexScreener. MCAP on the card can lag this chart", html)
        self.assertIn("mcapHeroHtml(p, { trade: true })", html)
        self.assertIn("mcapHeroHtml(q, { trade: true })", html)
        self.assertIn("fmtGameUsd(mark)", html)
        self.assertNotIn("fmtUsd(mark)", html)

    def test_embed_url_follows_the_selected_mint(self):
        html = _panel()
        start = html.index("function dexEmbedUrl")
        end = html.index("function openPositions")
        fn = html[start:end]
        script = """
function looksLikeMint(s) {
  s = String(s || "").trim();
  if (s.length < 32 || s.length > 50) return false;
  return /^[1-9A-HJ-NP-Za-km-z]+$/.test(s);
}
""" + fn + """
const mintA = "DMnMwUvo1a9HbSnixbdNaTsoHczJ8cdamUwVRM1Qpump";
const mintB = "5qDiSWLy5mXog6L5u1rZGwCerDN29X5tFtVBp8j5pump";
const pair = "https://dexscreener.com/solana/j6ek9fv4ksnh87vtu42wagcuhmptuferfftjzg3jfe5m?maker=1";
const fromPair = dexEmbedUrl(pair, mintA);
const onlyA = dexEmbedUrl("", mintA);
const onlyB = dexEmbedUrl("", mintB);
const bad = dexEmbedUrl("javascript:alert(1)", "nope");
const fallback = dexEmbedUrl("javascript:alert(1)", mintA);
const evil = dexEmbedUrl("https://evil.example/solana/" + mintA, mintA);
if (!fromPair.includes("embed=1")) process.exit(2);
if (!fromPair.includes("/solana/j6ek9fv4ksnh87vtu42wagcuhmptuferfftjzg3jfe5m?")) process.exit(3);
if (fromPair.includes("maker=")) process.exit(4);
if (!onlyA.includes(mintA) || !onlyB.includes(mintB)) process.exit(5);
if (onlyA === onlyB) process.exit(6);
if (bad) process.exit(7);
if (!evil.includes(mintA) || evil.includes("evil.example")) process.exit(8);
if (dexEmbedUrl(pair, mintB).includes(mintB)) process.exit(9);
if (!fallback.includes(mintA) || fallback.includes("javascript")) process.exit(10);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
