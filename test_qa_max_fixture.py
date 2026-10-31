#!/usr/bin/env python3
"""QA Max fixture for AC-X01. Not a QA PASS.

Default Token Flow stays Max-empty when FDV ≈ NOW. ``?qa_max=1`` prepends one
synthetic bonding row so QA can see a labeled Max / % gone.
"""

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import qa_max
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
  out[name] = mcapRowHtml(row);
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


def _ceil_sig(row: dict) -> tuple:
    prog = row.get("mcap_progress") if isinstance(row, dict) else None
    if not isinstance(prog, dict):
        prog = {}
    return (
        row.get("mint") if isinstance(row, dict) else None,
        prog.get("ceiling_available"),
        prog.get("ceiling_empty"),
        prog.get("ceiling_usd"),
        prog.get("pct_gone"),
    )


class HealthTests(unittest.TestCase):
    def test_health_is_v77_with_qa_max_fixture(self):
        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 83)
        self.assertIn("qa_max_fixture", data["features"])
        self.assertIn("hide_max_unknown", data["features"])
        self.assertIn("trade_chart", data["features"])
        self.assertIn("sitting_keep", data["features"])
        self.assertIn("manual_update", data["features"])
        self.assertTrue(data["sitting_keep"])
        self.assertIn("qa_max=1", data["qa_max"])
        self.assertIn("/api/flagged?qa_max=1", data["qa_max"])
        self.assertIn("/api/qa/max-fixture?qa_max=1", data["qa_max"])

    def test_panel_wires_the_opt_in_query(self):
        html = _panel()
        self.assertIn("const PANEL_V = 83", html)
        self.assertIn("paper only · v83", html)
        self.assertNotIn("?v=76", html)
        self.assertIn("?v=83", html)
        self.assertIn('data-feature="hide_max_unknown"', html)
        self.assertIn('data-qa-max-fixture="opt-in"', html)
        self.assertIn("function wantsQaMax()", html)
        self.assertIn('"/api/flagged?qa_max=1"', html)
        self.assertIn('"/api/flagged"', html)
        self.assertIn(">QA MAX<", html)
        self.assertIn('data-qa-max="1"', html)
        self.assertIn("QA Max fixture — synthetic bonding row QAMAX", html)
        self.assertIn("?qa_max=1", html)
        self.assertNotIn("Max unknown", html)


class FixtureMathTests(unittest.TestCase):
    def test_bonding_ceiling_clears_the_slice9_gate(self):
        row = qa_max.prepared_row()
        prog = row["mcap_progress"]
        self.assertTrue(row["qa_max_fixture"])
        self.assertEqual(row["ticker"], "QAMAX")
        self.assertEqual(row["mint"], qa_max.QA_MAX_MINT)
        self.assertEqual(prog["ceiling_kind"], "bonding")
        self.assertEqual(prog["ceiling_label"], "Bonding")
        self.assertEqual(prog["ceiling_usd"], qa_max.QA_MAX_BONDING_USD)
        self.assertEqual(prog["market_cap_usd"], qa_max.QA_MAX_NOW_USD)
        self.assertGreater(prog["ceiling_usd"], prog["market_cap_usd"] * 1.02)
        self.assertEqual(prog["pct_gone"], 17.4)
        self.assertTrue(prog["ceiling_available"])
        self.assertTrue(prog["progress_available"])
        self.assertIsNone(prog["ceiling_empty"])
        self.assertEqual(prog["fdv_usd"], qa_max.QA_MAX_NOW_USD)
        self.assertIn("Bonded", prog["formula"])

    def test_fdv_equal_to_now_is_still_not_a_ceiling(self):
        """The fixture's FDV is NOW. Without bonding, Slice 9 stays empty."""
        bare = build_mcap_progress(
            market_cap_usd=qa_max.QA_MAX_NOW_USD,
            fdv_usd=qa_max.QA_MAX_NOW_USD,
        )
        self.assertFalse(bare["ceiling_available"])
        self.assertIsNone(bare["pct_gone"])
        self.assertEqual(bare["ceiling_empty"], "at_fdv")
        self.assertIn("not 100% gone", bare["formula"])

    def test_row_html_shows_labeled_max_and_hides_at_fdv(self):
        bonding = qa_max.prepared_row()["mcap_progress"]
        at_fdv = build_mcap_progress(
            market_cap_usd=5_200_000,
            fdv_usd=5_200_000,
            now_source="snapshot",
        )
        shown = _render({"bonding": bonding, "at_fdv": at_fdv})
        self.assertIn("Max $", shown["bonding"])
        self.assertIn("% gone", shown["bonding"])
        self.assertIn('title="Bonding"', shown["bonding"])
        self.assertIn("$690.0k", shown["bonding"])
        self.assertIn("17.4% gone", shown["bonding"])
        self.assertNotIn("Max unknown", shown["bonding"])
        self.assertNotIn("100% gone", shown["bonding"])
        self.assertNotIn("Max $", shown["at_fdv"])
        self.assertNotIn(">Max<", shown["at_fdv"])
        self.assertNotIn("% gone", shown["at_fdv"])
        self.assertIn("now-only", shown["at_fdv"])
        self.assertIn("$5.20M", shown["at_fdv"])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.path = server.FLAGGED_PATH
        self.before = self.path.stat().st_mtime_ns if self.path.exists() else None
        self.before_bytes = self.path.read_bytes() if self.path.exists() else None

    def tearDown(self):
        if self.before is None:
            return
        self.assertEqual(self.path.stat().st_mtime_ns, self.before)
        self.assertEqual(self.path.read_bytes(), self.before_bytes)

    def test_default_feed_has_no_fixture(self):
        res = self.client.get("/api/flagged")
        self.assertEqual(res.status_code, 200)
        rows = res.get_json()
        self.assertIsInstance(rows, list)
        self.assertTrue(rows)
        self.assertFalse(any(isinstance(row, dict) and row.get("qa_max_fixture") for row in rows))
        self.assertNotIn(qa_max.QA_MAX_MINT, json.dumps(rows))

    def test_query_prepends_one_bonding_row_and_leaves_the_rest(self):
        plain = self.client.get("/api/flagged")
        qa = self.client.get("/api/flagged?qa_max=1")
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(qa.status_code, 200)
        base = plain.get_json()
        rows = qa.get_json()
        self.assertEqual(len(rows), len(base) + 1)
        head = rows[0]
        self.assertTrue(head["qa_max_fixture"])
        self.assertEqual(head["ticker"], "QAMAX")
        prog = head["mcap_progress"]
        self.assertEqual(prog["ceiling_kind"], "bonding")
        self.assertEqual(prog["ceiling_label"], "Bonding")
        self.assertGreater(prog["ceiling_usd"], prog["market_cap_usd"] * 1.02)
        self.assertEqual(prog["pct_gone"], 17.4)
        self.assertFalse(any(isinstance(row, dict) and row.get("qa_max_fixture") for row in rows[1:]))
        self.assertEqual([_ceil_sig(row) for row in base], [_ceil_sig(row) for row in rows[1:]])
        self.assertNotEqual(plain.headers.get("ETag"), qa.headers.get("ETag"))
        replay = self.client.get(
            "/api/flagged?qa_max=1",
            headers={"If-None-Match": plain.headers["ETag"]},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.get_json()[0]["qa_max_fixture"])
        cached = self.client.get(
            "/api/flagged",
            headers={"If-None-Match": plain.headers["ETag"]},
        )
        self.assertEqual(cached.status_code, 304)

    def test_other_query_values_stay_off(self):
        for raw in ("", "0", "off", "2", "bonding"):
            res = self.client.get("/api/flagged", query_string={"qa_max": raw})
            rows = res.get_json()
            self.assertFalse(any(isinstance(row, dict) and row.get("qa_max_fixture") for row in rows), raw)

    def test_hook_is_disabled_until_the_query_is_on(self):
        off = self.client.get("/api/qa/max-fixture")
        self.assertEqual(off.status_code, 200)
        body = off.get_json()
        self.assertFalse(body["enabled"])
        self.assertFalse(body["qa_max_fixture"])
        self.assertNotIn("row", body)
        on = self.client.get("/api/qa/max-fixture?qa_max=1")
        self.assertEqual(on.status_code, 200)
        hooked = on.get_json()
        self.assertTrue(hooked["enabled"])
        self.assertTrue(hooked["qa_max_fixture"])
        self.assertEqual(hooked["ceiling_kind"], "bonding")
        self.assertEqual(hooked["ceiling_label"], "Bonding")
        self.assertEqual(hooked["row"]["mcap_progress"]["pct_gone"], 17.4)
        self.assertIn("Not a live mint", hooked["note"])

    def test_fixture_mint_does_not_scan_and_needs_the_query(self):
        import filter

        with mock.patch.object(filter, "coin_payload") as scan:
            blocked = self.client.get("/api/coin", query_string={"mint": qa_max.QA_MAX_MINT})
            self.assertEqual(blocked.status_code, 400)
            self.assertFalse(blocked.get_json()["ok"])
            scan.assert_not_called()
            opened = self.client.get(
                "/api/coin",
                query_string={"mint": qa_max.QA_MAX_MINT, "qa_max": "1"},
            )
            self.assertEqual(opened.status_code, 200)
            payload = opened.get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["source"], "qa_max_fixture")
            self.assertEqual(payload["row"]["mcap_progress"]["ceiling_label"], "Bonding")
            scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
