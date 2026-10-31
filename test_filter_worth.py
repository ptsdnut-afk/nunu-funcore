#!/usr/bin/env python3
"""SHIP Y — strict Filter. Default list is an X verdict of 6/8 or better, not a forced SKIP.

Trending or new alone never qualifies. An empty list is allowed.
Show all reveals the rest, dimmed, each with its verdict.
panel_v stays 84 (panel_v: TBD).
"""

from __future__ import annotations

import inspect
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import coin_checklist as cc
import server

MINT_A = "5G3pzMiNQxiFctE1aZRTctYJLz5zGdfA1S65QMT5pump"
MINT_B = "6G3pzMiNQxiFctE1aZRTctYJLz5zGdfA1S65QMT5pump"
MINT_C = "7G3pzMiNQxiFctE1aZRTctYJLz5zGdfA1S65QMT5pump"
MINT_D = "8G3pzMiNQxiFctE1aZRTctYJLz5zGdfA1S65QMT5pump"


def _sheet(mint: str, verdict: str, passes: int, *, forced: bool = False, total: int = 8) -> dict:
    return {
        "ok": True,
        "mint": mint,
        "verdict": verdict,
        "pass_count": passes,
        "check_count": total,
        "forced_skip": forced,
        "count_verdict": "WATCH" if passes >= 6 else "SKIP",
    }


def _panel() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


def _worth_js() -> str:
    html = _panel()
    start = html.index("    const WORTH_MIN_PASS = 6;")
    end = html.index("    function applyFilters(rows)")
    return html[start:end]


class WorthRuleTests(unittest.TestCase):
    def test_only_six_of_eight_and_not_forced_skip(self):
        self.assertTrue(cc.worth_watching(_sheet(MINT_A, "WATCH", 6)))
        self.assertTrue(cc.worth_watching(_sheet(MINT_A, "WATCH", 7)))
        self.assertTrue(cc.worth_watching(_sheet(MINT_A, "BUY-WATCH", 8)))
        self.assertFalse(cc.worth_watching(_sheet(MINT_A, "SKIP", 5)))
        self.assertFalse(cc.worth_watching(_sheet(MINT_A, "SKIP", 0)))
        self.assertFalse(cc.worth_watching(_sheet(MINT_A, "SKIP", 7, forced=True)))
        self.assertFalse(cc.worth_watching(_sheet(MINT_A, "SKIP", 8, forced=True)))
        self.assertFalse(cc.worth_watching(_sheet(MINT_A, "SKIP", 6)))
        self.assertFalse(cc.worth_watching(None))
        self.assertFalse(cc.worth_watching({"ok": False, "verdict": "WATCH", "pass_count": 8, "check_count": 8}))
        self.assertEqual(cc.WORTH_EMPTY, "Nothing worth watching right now")

    def test_trending_or_new_is_not_an_input(self):
        params = list(inspect.signature(cc.worth_watching).parameters)
        self.assertEqual(params, ["checklist"])
        self.assertIn("Trending or new is not an input", cc.worth_watching.__doc__ or "")
        bare = cc.assemble(MINT_A, {})
        self.assertNotEqual(bare["verdict"], "BUY-WATCH")
        self.assertFalse(cc.worth_watching(bare))

    def test_a_later_wider_sheet_still_needs_six_eighths(self):
        self.assertFalse(cc.worth_watching(_sheet(MINT_A, "WATCH", 6, total=10)))
        self.assertTrue(cc.worth_watching(_sheet(MINT_A, "WATCH", 8, total=10)))


class ApiTests(unittest.TestCase):
    def test_health_keeps_panel_v_and_names_the_gate(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 84)
        self.assertIn("filter_worth", data["features"])
        self.assertIn("coin_checklist", data["features"])
        self.assertIn("Nothing worth watching right now", data["filter_worth"])
        self.assertIn("Trending or new alone never qualifies", data["filter_worth"])
        self.assertIn("panel_v is unchanged", data["filter_worth"])

    def test_batch_matches_the_drawer_and_marks_worth(self):
        book = {
            MINT_A: _sheet(MINT_A, "WATCH", 6),
            MINT_B: _sheet(MINT_B, "SKIP", 5),
            MINT_C: _sheet(MINT_C, "SKIP", 7, forced=True),
            MINT_D: _sheet(MINT_D, "BUY-WATCH", 8),
        }

        def fake(mint, bypass=False):
            return dict(book[mint])

        with mock.patch("coin_checklist.for_mint", side_effect=fake):
            client = server.app.test_client()
            single = client.get("/api/coin/checklist?mint=" + MINT_A).get_json()
            batch = client.get(
                "/api/coin/checklists?mints=" + ",".join([MINT_C, MINT_A, MINT_B, MINT_D])
            ).get_json()
        self.assertEqual(single["verdict"], "WATCH")
        self.assertEqual(single["pass_count"], 6)
        self.assertTrue(single["worth"])
        self.assertEqual(batch["panel_v"], 84)
        self.assertEqual(batch["empty_text"], cc.WORTH_EMPTY)
        self.assertFalse(batch["trending_or_new_qualifies"])
        self.assertFalse(batch["forced_skip_qualifies"])
        by_mint = {item["mint"]: item for item in batch["items"]}
        self.assertEqual(by_mint[MINT_A]["verdict"], single["verdict"])
        self.assertEqual(by_mint[MINT_A]["pass_count"], single["pass_count"])
        self.assertTrue(by_mint[MINT_A]["worth"])
        self.assertTrue(by_mint[MINT_D]["worth"])
        self.assertEqual(by_mint[MINT_D]["verdict"], "BUY-WATCH")
        self.assertFalse(by_mint[MINT_B]["worth"])
        self.assertEqual(by_mint[MINT_B]["verdict"], "SKIP")
        self.assertFalse(by_mint[MINT_C]["worth"])
        self.assertTrue(by_mint[MINT_C]["forced_skip"])
        self.assertEqual([item["mint"] for item in batch["items"]], [MINT_C, MINT_A, MINT_B, MINT_D])

    def test_batch_requires_mints(self):
        res = server.app.test_client().get("/api/coin/checklists")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json()["ok"])


class PanelTests(unittest.TestCase):
    def test_panel_keeps_v84_and_the_show_all_gate(self):
        html = _panel()
        self.assertIn("const PANEL_V = 84", html)
        self.assertIn("paper only · v84", html)
        self.assertNotIn("const PANEL_V = 85", html)
        self.assertIn('id="showAll"', html)
        self.assertIn("> Show all</label>", html)
        self.assertIn('data-feature="filter_worth"', html)
        self.assertIn("Nothing worth watching right now", html)
        self.assertIn("The rest · under 6/8 or SKIP", html)
        self.assertIn(".tok.dim", html)
        self.assertIn('(worth ? "" : " dim")', html)
        self.assertIn("On-chain · filtered · sitting kept past 5m", html)
        self.assertIn("None cleared the flag line", html)
        self.assertIn("No tokens flagged this run", html)
        self.assertIn("No rows match filters (min vol / trench / hide late).", html)
        render_fn = html[html.index("function render()"):html.index("function refreshFilterWorth")]
        self.assertIn("worthEmptyHtml", render_fn)
        self.assertIn("splitWorth", render_fn)
        self.assertNotIn("noteTokenFlowFlags", render_fn)
        self.assertEqual(html.count("noteTokenFlowFlags("), 2)

    def test_default_list_drops_under_six_and_show_all_keeps_them(self):
        script = _worth_js() + r"""
const watch = { ok: true, verdict: "WATCH", pass_count: 6, check_count: 8, forced_skip: false };
const skip = { ok: true, verdict: "SKIP", pass_count: 5, check_count: 8, forced_skip: false };
const forced = { ok: true, verdict: "SKIP", pass_count: 7, check_count: 8, forced_skip: true };
const buy = { ok: true, verdict: "BUY-WATCH", pass_count: 8, check_count: 8, forced_skip: false };
const lowWatch = { ok: true, verdict: "WATCH", pass_count: 6, check_count: 10, forced_skip: false };
if (!worthWatching(watch) || !worthWatching(buy)) process.exit(2);
if (worthWatching(skip) || worthWatching(forced) || worthWatching(null) || worthWatching(lowWatch)) process.exit(3);
if (worthWatching({ ok: false, verdict: "WATCH", pass_count: 8, check_count: 8 })) process.exit(4);

const rows = [
  { mint: "new", ticker: "NEW", trending: true, pair_age_hours: 0.1, score_12: 12 },
  { mint: "trend", ticker: "HOT", trending: true, pair_age_hours: 3, score_12: 12 },
  { mint: "watch", ticker: "KEEP", trending: false, pair_age_hours: 5, score_12: 4 },
  { mint: "buy", ticker: "BEST", trending: true, pair_age_hours: 0.2, score_12: 11 },
  { mint: "forced", ticker: "RUG", trending: true, pair_age_hours: 0.2, score_12: 12 },
];
const checks = { new: skip, trend: null, watch: watch, buy: buy, forced: forced };
const strict = splitWorth(rows, checks, false);
if (strict.visible.map((r) => r.mint).join(",") !== "watch,buy") process.exit(5);
if (strict.rest.some((r) => r.mint === "watch" || r.mint === "buy")) process.exit(6);
if (!strict.rest.some((r) => r.mint === "new" && r.trending)) process.exit(7);
if (!strict.rest.some((r) => r.mint === "trend")) process.exit(8);
if (strict.empty) process.exit(9);
const shown = splitWorth(rows, checks, true);
if (shown.visible.length !== 5 || shown.empty) process.exit(10);
if (shown.rest.map((r) => r.mint).join(",") !== "new,trend,forced") process.exit(11);
const none = splitWorth(
  [{ mint: "a", trending: true, pair_age_hours: 0.1 }, { mint: "b", trending: true }],
  { a: skip, b: forced },
  false
);
if (!none.empty || none.visible.length) process.exit(12);
const ready = worthEmptyHtml(true);
if (ready.indexOf("Nothing worth watching right now") < 0) process.exit(13);
if (ready.indexOf('data-empty="worth"') < 0) process.exit(14);
const pending = worthEmptyHtml(false);
if (pending.indexOf("Nothing worth watching right now") >= 0) process.exit(15);
if (pending.indexOf('data-empty="checking"') < 0) process.exit(16);
process.stdout.write(JSON.stringify({ strict: strict.visible.length, rest: shown.rest.length }));
"""
        proc = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
        got = json.loads(proc.stdout)
        self.assertEqual(got["strict"], 2)
        self.assertEqual(got["rest"], 3)


if __name__ == "__main__":
    unittest.main()
