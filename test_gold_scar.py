#!/usr/bin/env python3
"""SHIP N — Gold Scar lane. Not a QA PASS.

The 7-gate stack is stricter than the owner note. Live context fails closed
on overheard and macro, so a high Token Flow score stays out of gold.
"""

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import gold_scar
import qa_gold
import qa_max
import server


def _panel() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


def _posts(ticker: str = "AURUM") -> list[dict]:
    return [
        {"username": "ada", "text": f"reading the {ticker} pool, still early"},
        {"username": "bea", "text": f"{ticker} liquidity looks real from here"},
    ]


def _macro_clear() -> dict:
    return {
        "clear": True,
        "solana_ok": True,
        "fed_quiet": True,
        "reason": "desk note: Fed quiet and Solana healthy",
    }


def _row(ticker: str = "AURUM", **over) -> dict:
    row = {
        "ticker": ticker,
        "name": over.pop("name", "Aurum example"),
        "mint": over.pop("mint", "Mint" + ticker + "111111111111111111111111"),
        "score_12": 12.0,
        "flagged": True,
        "hard_fails": [],
        "late_chase": False,
        "red_flags": [],
        "description": "A fresh joke about a quiet pond.",
        "metrics": {
            "liquidity_usd": 80_000.0,
            "volume_h1_usd": 40_000.0,
            "buys_h1": 220,
            "sells_h1": 140,
            "buy_sell_ratio_h1": 1.57,
            "pair_age_hours": 0.6,
            "price_change_h1": 12.0,
            "price_change_m5": 4.0,
            "price_change_h24": 18.0,
            "lp_locked_pct": 100.0,
        },
        "holders": {
            "rugcheck_available": True,
            "score_normalised": 1.0,
            "lp_locked_pct": 100.0,
            "risk_count": 0,
            "risks": [],
            "top1_pct": 4.5,
            "top10_pct": 22.0,
        },
        "socials": {"twitter": True, "twitter_url": "https://x.com/search?q=" + ticker},
    }
    row.update(over)
    return row


def _ready(row: dict | None = None, **ctx) -> dict:
    row = row or _row()
    base = {
        "family_counts": {gold_scar.family_key(row): 1},
        "overheard_posts": _posts(str(row.get("ticker") or "")),
        "macro": _macro_clear(),
    }
    base.update(ctx)
    return base


def _blocked(verdict: dict) -> set[str]:
    return set(verdict.get("blocked_by") or [])


class GateTests(unittest.TestCase):
    def test_full_stack_can_pass_and_stays_paper(self):
        row = _row()
        verdict = gold_scar.evaluate(row, _ready(row))
        self.assertTrue(verdict["gold"])
        self.assertEqual(verdict["tier"], "rare")
        self.assertEqual([gate["id"] for gate in verdict["gates"]], [gid for gid, _title in gold_scar.GATE_ORDER])
        self.assertTrue(all(gate["pass"] for gate in verdict["gates"]))
        self.assertIn("Paper research only", verdict["why"])
        self.assertIn("Not a buy", verdict["why"])
        self.assertNotIn("will moon", verdict["why"].lower())
        self.assertNotIn("guaranteed", verdict["why"].lower())
        self.assertNotIn("100x", verdict["why"].lower())

    def test_score_10_is_above_the_owner_skip_line_and_still_not_gold(self):
        row = _row()
        row["score_12"] = 10.4
        verdict = gold_scar.evaluate(row, _ready(row))
        self.assertFalse(verdict["gold"])
        self.assertIn("filter", _blocked(verdict))

    def test_score_11_clears_the_stricter_filter_floor(self):
        row = _row()
        row["score_12"] = 11.0
        verdict = gold_scar.evaluate(row, _ready(row))
        self.assertTrue(verdict["gates"][0]["pass"])
        self.assertTrue(verdict["gold"])

    def test_high_score_without_the_rest_of_the_stack_stays_quiet(self):
        row = _row()
        row["holders"] = {}
        row["metrics"]["price_change_m5"] = None
        verdict = gold_scar.evaluate(row, gold_scar.live_context() | {"family_counts": {gold_scar.family_key(row): 1}})
        self.assertFalse(verdict["gold"])
        self.assertGreaterEqual(len(_blocked(verdict)), 2)
        self.assertIn("overheard", _blocked(verdict))
        self.assertIn("macro", _blocked(verdict))

    def test_copy_pack_of_three_fails_every_member(self):
        rows = [_row("DOGE"), _row("DOGE2", name="Doge two"), _row("DOGECOIN", name="Doge coin")]
        out = gold_scar.annotate(rows, {"overheard_posts": _posts("DOGE"), "macro": _macro_clear()})
        # overheard posts only name DOGE, so the others fail overheard too.
        # The pack gate still fires on all three.
        for row in out:
            self.assertFalse(row["gold_scar"]["gold"])
            self.assertIn("clone", _blocked(row["gold_scar"]))
        doge = next(row for row in out if row["ticker"] == "DOGE")
        posts = [
            {"username": "ada", "text": "reading the DOGE pool, still early"},
            {"username": "bea", "text": "DOGE liquidity looks real from here"},
        ]
        alone = gold_scar.evaluate(doge, _ready(doge, overheard_posts=posts, family_counts={"doge": 3}))
        self.assertIn("clone", _blocked(alone))
        pair = gold_scar.evaluate(doge, _ready(doge, overheard_posts=posts, family_counts={"doge": 2}))
        self.assertNotIn("clone", _blocked(pair))

    def test_crowded_politics_name_fails_narrative(self):
        row = _row("TRUMP2028", name="Trump 2028")
        verdict = gold_scar.evaluate(row, _ready(row, overheard_posts=_posts("TRUMP2028")))
        self.assertFalse(verdict["gold"])
        self.assertIn("narrative", _blocked(verdict))

    def test_compound_ticker_hits_elon_and_melon_does_not(self):
        # Prefix-only matching let GTAELON through. Interior stem now fails closed.
        self.assertEqual(gold_scar._crowded_hit({"ticker": "GTAELON", "name": "GTAELON"}), "elon")
        self.assertEqual(gold_scar._crowded_hit({"ticker": "ELON2028", "name": "ELON2028"}), "elon")
        self.assertIsNone(gold_scar._crowded_hit({"ticker": "MELON", "name": "Melon"}))
        self.assertIsNone(gold_scar._crowded_hit({"ticker": "ADVANCE", "name": "Advance"}))
        self.assertIsNone(gold_scar._crowded_hit({"ticker": "XXELON", "name": "XXELON"}))
        row = _row("GTAELON", name="GTAELON")
        verdict = gold_scar.evaluate(row, _ready(row, overheard_posts=_posts("GTAELON")))
        self.assertFalse(verdict["gold"])
        self.assertIn("narrative", _blocked(verdict))
        self.assertIn("elon", verdict["gates"][2]["reason"])
        melon = _row("MELON", name="Melon")
        melon_verdict = gold_scar.evaluate(melon, _ready(melon, overheard_posts=_posts("MELON")))
        self.assertNotIn("narrative", _blocked(melon_verdict))

    def test_tape_is_stricter_than_plus_300_and_requires_5m(self):
        hot = _row()
        hot["metrics"]["price_change_h1"] = 200
        verdict = gold_scar.evaluate(hot, _ready(hot))
        self.assertIn("tape", _blocked(verdict))
        self.assertLess(200, 300)

        missing = _row()
        missing["metrics"]["price_change_m5"] = None
        verdict = gold_scar.evaluate(missing, _ready(missing))
        self.assertIn("tape", _blocked(verdict))
        self.assertIn("5m", verdict["gates"][3]["reason"])

        late = _row()
        late["late_chase"] = True
        verdict = gold_scar.evaluate(late, _ready(late))
        self.assertIn("tape", _blocked(verdict))

        ran = _row()
        ran["metrics"]["price_change_h24"] = 900
        verdict = gold_scar.evaluate(ran, _ready(ran))
        self.assertIn("tape", _blocked(verdict))

    def test_dirty_onchain_and_one_wallet_fail(self):
        dirty = _row()
        dirty["holders"]["risks"] = ["snipers detected"]
        dirty["holders"]["risk_count"] = 1
        verdict = gold_scar.evaluate(dirty, _ready(dirty))
        self.assertIn("onchain", _blocked(verdict))

        whale = _row()
        whale["holders"]["top1_pct"] = 48
        verdict = gold_scar.evaluate(whale, _ready(whale))
        self.assertFalse(verdict["gold"])
        self.assertTrue({"filter", "onchain"} & _blocked(verdict))

        summary_only = _row()
        summary_only["holders"] = {
            "rugcheck_available": True,
            "score_normalised": 1,
            "lp_locked_pct": 100,
            "risk_count": 0,
            "risks": [],
        }
        verdict = gold_scar.evaluate(summary_only, _ready(summary_only))
        self.assertIn("onchain", _blocked(verdict))
        self.assertIn("filter", _blocked(verdict))

    def test_profile_link_is_not_overheard(self):
        row = _row()
        verdict = gold_scar.evaluate(row, _ready(row, overheard_posts=[]))
        self.assertIn("overheard", _blocked(verdict))
        self.assertIn("profile link", verdict["gates"][5]["reason"])

        promo = _posts()
        promo[1] = {"username": "bea", "text": "AURUM will moon 100x"}
        verdict = gold_scar.evaluate(row, _ready(row, overheard_posts=promo))
        self.assertIn("overheard", _blocked(verdict))

        same = [
            {"username": "ada", "text": "AURUM liquidity looks real from here"},
            {"username": "bea", "text": "AURUM liquidity looks real from here"},
        ]
        verdict = gold_scar.evaluate(row, _ready(row, overheard_posts=same))
        self.assertIn("overheard", _blocked(verdict))

    def test_macro_missing_is_a_veto_and_a_full_clearance_can_pass(self):
        self.assertFalse(gold_scar.read_macro(Path("/tmp/does-not-exist-gold-macro.json"))["clear"])
        row = _row()
        veto = gold_scar.evaluate(row, _ready(row, macro=gold_scar.read_macro(Path("/tmp/does-not-exist-gold-macro.json"))))
        self.assertIn("macro", _blocked(veto))
        half = {"clear": True, "solana_ok": False, "fed_quiet": True, "reason": "Fed quiet only"}
        verdict = gold_scar.evaluate(row, _ready(row, macro=half))
        self.assertIn("macro", _blocked(verdict))
        opened = gold_scar.evaluate(row, _ready(row))
        self.assertTrue(opened["gates"][6]["pass"])

    def test_disk_stamp_cannot_self_promote(self):
        forged = _row()
        forged["gold"] = True
        forged["gold_scar"] = {"gold": True, "why": "will moon"}
        forged["qa_gold_fixture"] = True
        out = gold_scar.annotate([forged], gold_scar.live_context())
        self.assertNotIn("gold", out[0])
        self.assertNotIn("qa_gold_fixture", out[0])
        self.assertFalse(out[0]["gold_scar"]["gold"])
        self.assertNotIn("will moon", json.dumps(out[0]["gold_scar"]))

    def test_qa_max_high_score_is_not_gold(self):
        row = qa_max.prepared_row()
        out = gold_scar.annotate([row], {"overheard_posts": _posts("QAMAX"), "macro": _macro_clear()})
        self.assertFalse(out[0]["gold_scar"]["gold"])
        self.assertTrue(out[0]["qa_max_fixture"])

    def test_live_feed_is_empty_of_gold(self):
        rows = json.loads(Path("flagged.json").read_text(encoding="utf-8"))
        out = gold_scar.annotate(rows, gold_scar.live_context())
        self.assertTrue(out)
        self.assertFalse(any(row.get("gold_scar", {}).get("gold") for row in out if isinstance(row, dict)))


class FixtureRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.path = server.FLAGGED_PATH
        self.before = self.path.read_bytes()

    def tearDown(self):
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_health_is_v77_with_gold_scar(self):
        data = self.client.get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 94)
        self.assertIn("gold_scar", data["features"])
        for feature in (
            "game_ai_review",
            "qa_max_fixture",
            "hide_max_unknown",
            "trade_chart",
            "sitting_keep",
            "manual_update",
            "token_flow_new_sound",
            "game_polish",
            "delay_warning",
            "game_follow_tab",
            "filter_smart",
        ):
            self.assertIn(feature, data["features"])
        self.assertIn("−12", data["filter_smart"])
        self.assertIn("Not a buy", data["filter_smart"])
        self.assertIn("qa_gold=1", data["qa_gold"])
        self.assertIn("qa_gold=arrive", data["qa_gold"])
        self.assertIn("7-gate", data["gold_scar"])

    def test_default_feed_has_no_gold_and_no_fixture(self):
        rows = self.client.get("/api/flagged").get_json()
        self.assertTrue(rows)
        self.assertFalse(any(row.get("qa_gold_fixture") for row in rows))
        self.assertFalse(any(row.get("gold_scar", {}).get("gold") for row in rows))
        gold = self.client.get("/api/gold").get_json()
        self.assertEqual(gold["count"], 0)
        self.assertEqual(gold["picks"], [])
        self.assertTrue(gold["empty_ok"])
        self.assertEqual(gold["gates"], [gid for gid, _title in gold_scar.GATE_ORDER])
        hook = self.client.get("/api/qa/gold-fixture").get_json()
        self.assertFalse(hook["enabled"])
        self.assertNotIn("row", hook)

    def test_query_adds_one_synthetic_card_and_does_not_promote_the_rest(self):
        plain = self.client.get("/api/flagged").get_json()
        qa = self.client.get("/api/flagged?qa_gold=1").get_json()
        self.assertEqual(len(qa), len(plain) + 1)
        head = qa[0]
        self.assertTrue(head["qa_gold_fixture"])
        self.assertEqual(head["ticker"], "QAGOLD")
        self.assertTrue(head["gold_scar"]["gold"])
        self.assertEqual(len(head["gold_scar"]["gates"]), 7)
        self.assertIn("Paper research only", head["gold_scar"]["why"])
        self.assertNotIn("will moon", head["gold_scar"]["why"].lower())
        self.assertTrue(head["links"]["dex"])
        self.assertTrue(head["mint"])
        self.assertFalse(any(row.get("gold_scar", {}).get("gold") for row in qa[1:]))
        arrived = self.client.get("/api/flagged?qa_gold=arrive").get_json()
        self.assertEqual(arrived[0]["mint"], head["mint"])
        self.assertNotEqual(
            self.client.get("/api/flagged").headers["ETag"],
            self.client.get("/api/flagged?qa_gold=1").headers["ETag"],
        )
        gold = self.client.get("/api/gold?qa_gold=1").get_json()
        self.assertEqual(gold["count"], 1)
        self.assertEqual(gold["picks"][0]["ticker"], "QAGOLD")
        hook = self.client.get("/api/qa/gold-fixture?qa_gold=1").get_json()
        self.assertTrue(hook["qa_gold_fixture"])
        self.assertIn("Not a live mint", hook["note"])

    def test_other_query_values_stay_off(self):
        for raw in ("", "0", "off", "2", "gold"):
            rows = self.client.get("/api/flagged", query_string={"qa_gold": raw}).get_json()
            self.assertFalse(any(row.get("qa_gold_fixture") for row in rows), raw)

    def test_fixture_mint_does_not_scan(self):
        import filter

        with mock.patch.object(filter, "coin_payload") as scan:
            blocked = self.client.get("/api/coin", query_string={"mint": qa_gold.QA_GOLD_MINT})
            self.assertEqual(blocked.status_code, 400)
            scan.assert_not_called()
            opened = self.client.get("/api/coin", query_string={"mint": qa_gold.QA_GOLD_MINT, "qa_gold": "1"})
            self.assertEqual(opened.status_code, 200)
            self.assertEqual(opened.get_json()["source"], "qa_gold_fixture")
            scan.assert_not_called()

    def test_filter_stores_the_5m_print_the_tape_gate_needs(self):
        import filter

        row = filter.evaluate(
            "Mint111",
            {
                "priceChange": {"m5": -3.5, "h1": 4, "h24": 9},
                "baseToken": {"symbol": "M5", "name": "M5 coin"},
            },
            {},
            None,
            "2026-10-27T00:00:00Z",
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["metrics"]["price_change_m5"], -3.5)


class PanelTests(unittest.TestCase):
    def test_gold_section_is_separate_and_versioned(self):
        html = _panel()
        self.assertIn("const PANEL_V = 94", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("data-panel-v", html)
        self.assertLess(html.index('id="goldScar"'), html.index("<h2>Token flow</h2>"))
        self.assertIn('data-feature="gold_scar"', html)
        self.assertIn("No gold picks yet — rare on purpose", html)
        self.assertIn("Paper research only. Not a buy. Not financial advice.", html)
        self.assertIn("♪ GOLD", html)
        self.assertIn("Copy CA", html)
        self.assertIn("?qa_gold=1", html)
        self.assertIn("?qa_gold=arrive", html)
        self.assertNotIn("will moon", html[html.index('id="goldScar"'):html.index('id="goldScar"') + 1800].lower())

    def test_gold_cue_is_distinct_and_mute_kills_it(self):
        html = _panel()
        seq = html[html.index("const SOUND_SEQ"):html.index("function soundLabel")]
        pure_i = html[html.index("/* ship-i-pure-start */"):html.index("/* ship-i-pure-end */")]
        pure_n = html[html.index("/* ship-n-pure-start */"):html.index("/* ship-n-pure-end */")]
        player = html[html.index("function playGoldChime"):html.index("function paintGoldArrival")]
        self.assertIn("state.mute", player)
        self.assertIn('startTone(ctx, "gold"', player)
        self.assertNotIn("newtoken", player)
        self.assertNotIn("beep(", player)
        script = seq + "\n" + pure_i + "\n" + pure_n + r"""
if (SOUND_WAVE.gold !== "triangle") process.exit(2);
if (SOUND_WAVE.newtoken !== "sine") process.exit(3);
const gold = JSON.stringify(SOUND_SEQ.gold);
if (gold === JSON.stringify(SOUND_SEQ.newtoken)) process.exit(4);
if (gold === JSON.stringify(SOUND_SEQ.tick)) process.exit(5);
if (gold === JSON.stringify(SOUND_SEQ.down)) process.exit(6);
if (gold === JSON.stringify(SOUND_SEQ.review)) process.exit(7);
if (gold === JSON.stringify(SOUND_SEQ.buy)) process.exit(8);
if (SOUND_SEQ.gold.length < 3) process.exit(9);

const row = (mint, ticker, fixture) => ({
  mint: mint,
  ticker: ticker,
  qa_gold_fixture: !!fixture,
  gold_scar: { gold: true },
});
const plain = { mint: "LiveMint", ticker: "NOMO", gold_scar: { gold: false } };
const seed = goldAlertPlan(null, [row("GoldMint", "QAGOLD", true), plain], { muted: false, unlocked: true, arrive: false });
if (seed.outcome !== "seed" || seed.play.length || seed.fresh.length) process.exit(10);
if (seed.next.join(",") !== "GoldMint") process.exit(11);

const arrive = goldAlertPlan(null, [row("GoldMint", "QAGOLD", true), plain], { muted: false, unlocked: true, arrive: true });
if (arrive.outcome !== "play" || arrive.play.join(",") !== "GoldMint") process.exit(12);
const arriveCopy = goldArrivalCopy(arrive.outcome);
if (arriveCopy.title !== "♪ GOLD" || !arriveCopy.flash) process.exit(13);
if (arriveCopy.detail.indexOf("new-token") < 0) process.exit(14);

const muted = goldAlertPlan(null, [row("GoldMint", "QAGOLD", true)], { muted: true, unlocked: true, arrive: true });
if (muted.outcome !== "muted" || muted.play.length) process.exit(15);
const mutedCopy = goldArrivalCopy(muted.outcome);
if (mutedCopy.flash || !mutedCopy.muteNote || mutedCopy.toastKind !== "silent") process.exit(16);

const again = goldAlertPlan(arrive.next, [row("GoldMint", "QAGOLD", true)], { muted: false, unlocked: true, arrive: true });
if (again.outcome !== "same" || again.play.length) process.exit(17);

const blocked = goldAlertPlan(null, [row("GoldMint", "QAGOLD", true)], { muted: false, unlocked: false, arrive: true });
if (blocked.outcome !== "blocked" || blocked.play.length) process.exit(18);

const liveLater = goldAlertPlan(seed.next, [row("GoldMint", "QAGOLD", true), { mint: "LiveGold", ticker: "AURUM", gold_scar: { gold: true } }], { muted: false, unlocked: true, arrive: false });
if (liveLater.outcome !== "play" || liveLater.play.join(",") !== "LiveGold") process.exit(19);
"""
        subprocess.check_call(["node", "-e", script])


if __name__ == "__main__":
    unittest.main()
