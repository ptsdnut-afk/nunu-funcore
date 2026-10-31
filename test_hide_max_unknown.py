#!/usr/bin/env python3
"""SHIP O — prefer a real Max, omit “Max unknown” (AC-X01…X04). Not a QA PASS."""

import json
import subprocess
import unittest
from pathlib import Path

import server
from mcap_progress import build_mcap_progress


def _panel() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


def _renderer() -> str:
    html = _panel()
    fmt = html[html.index("function fmtUsd"):html.index("function fmtPct")]
    esc = html[html.index("function esc("):html.index("function score12Of")]
    block = html[html.index("var MCAP_AT_FDV"):html.index("function rowHtml")]
    return "\n".join(
        [
            "const QUOTE_SLA_S = 15;",
            fmt,
            esc,
            block,
        ]
    )


def _render(cases: dict) -> dict:
    script = _renderer() + """
const cases = %s;
const out = {};
for (const [name, row] of Object.entries(cases)) {
  const filter = mcapRowHtml(row);
  const drawer = mcapHeroHtml(row);
  const trade = mcapHeroHtml(row, { trade: true });
  out[name] = { filter, drawer, trade };
}
process.stdout.write(JSON.stringify(out));
""" % json.dumps(cases)
    proc = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


class HealthTests(unittest.TestCase):
    def test_health_is_v77_with_hide_max_unknown(self):
        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 93)
        self.assertIn("hide_max_unknown", data["features"])
        self.assertIn("trade_chart", data["features"])
        self.assertIn("sitting_keep", data["features"])
        self.assertIn("manual_update", data["features"])
        self.assertTrue(data["sitting_keep"])

    def test_panel_advertises_v77_and_drops_the_unknown_chip(self):
        html = _panel()
        self.assertIn("const PANEL_V = 93", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("paintPanelVersion", html)
        self.assertNotIn("?v=76", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("data-panel-v", html)
        self.assertIn('data-feature="hide_max_unknown"', html)
        self.assertNotIn("Max unknown", html)
        self.assertIn('const nowLab = trade ? "MCAP" : "Now"', html)
        self.assertIn('id="gChartPane"', html)
        self.assertIn('id="btnUpdateNow"', html)
        self.assertIn("sitting kept past 5m", html)


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bonding = build_mcap_progress(
            market_cap_usd=1_530_000,
            bonding_mcap_usd=3_500_000,
            fdv_usd=1_530_000,
            now_source="snapshot",
        )
        ath = build_mcap_progress(
            market_cap_usd=826_300,
            ath_mcap_usd=1_160_000,
            fdv_usd=826_300,
            now_source="snapshot",
        )
        fdv = build_mcap_progress(
            market_cap_usd=1_840_000,
            fdv_usd=8_400_000,
            now_source="snapshot",
        )
        at_fdv = build_mcap_progress(
            market_cap_usd=5_200_000,
            fdv_usd=5_200_000,
            now_source="snapshot",
        )
        unknown = build_mcap_progress(market_cap_usd=78_700, now_source="snapshot")
        flat = build_mcap_progress(
            market_cap_usd=100,
            fdv_usd=100,
            bonding_mcap_usd=100,
            ath_mcap_usd=101,
            now_source="snapshot",
        )
        cls.rendered = _render(
            {
                "bonding": bonding,
                "ath": ath,
                "fdv": fdv,
                "at_fdv": at_fdv,
                "unknown": unknown,
                "flat": flat,
            }
        )

    def test_honest_ceiling_is_a_labeled_max(self):
        for name, label in (("bonding", "Bonding"), ("ath", "ATH"), ("fdv", "FDV")):
            row = self.rendered[name]
            self.assertIn("Max $", row["filter"], name)
            self.assertIn("% gone", row["filter"], name)
            self.assertIn('title="' + label + '"', row["filter"], name)
            self.assertIn(">Max<", row["drawer"], name)
            self.assertIn(">" + label + "<", row["drawer"], name)
            self.assertIn("% gone", row["drawer"], name)
            self.assertIn("Ceiling math:", row["drawer"], name)
            self.assertNotIn("Max unknown", row["filter"], name)
            self.assertNotIn("100% gone", row["filter"] + row["drawer"] + row["trade"], name)
            self.assertGreater(row["filter"].count("Max $"), 0)

    def test_unknown_and_at_fdv_omit_the_chip(self):
        for name in ("at_fdv", "unknown", "flat"):
            row = self.rendered[name]
            for surface in ("filter", "drawer", "trade"):
                html = row[surface]
                self.assertNotIn("Max unknown", html, name + " " + surface)
                self.assertNotIn("max-unk", html, name + " " + surface)
                self.assertNotIn("100% gone", html, name + " " + surface)
                self.assertNotIn(">Max<", html, name + " " + surface)
                self.assertNotIn("Max $", html, name + " " + surface)
                self.assertIn("now-only", html, name + " " + surface)
            self.assertIn("$5.20M", self.rendered["at_fdv"]["filter"])
            self.assertIn("Mcap now", self.rendered["at_fdv"]["filter"])
            self.assertIn(">Now<", self.rendered["at_fdv"]["drawer"])
            self.assertIn(">MCAP<", self.rendered["at_fdv"]["trade"])
            self.assertIn("$5.20M", self.rendered["at_fdv"]["trade"])

    def test_slice9_still_refuses_a_ceiling_copied_from_now(self):
        prog = build_mcap_progress(market_cap_usd=86_800, fdv_usd=86_800)
        self.assertFalse(prog["ceiling_available"])
        self.assertIsNone(prog["pct_gone"])
        self.assertEqual(prog["ceiling_empty"], "at_fdv")
        self.assertIn("not 100% gone", prog["formula"])
        shown = self.rendered["at_fdv"]["drawer"]
        self.assertNotIn(prog["formula"], shown)
        self.assertNotIn("not 100% gone", shown)


if __name__ == "__main__":
    unittest.main()
