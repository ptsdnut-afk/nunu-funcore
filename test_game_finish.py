"""SHIP E game finish — points, shop, rocket tick decision, plain flag labels."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import game


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GameFinishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for patch in (
            mock.patch.object(game, "PROFILE_PATH", root / "profile.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
            mock.patch.object(game, "STATE_PATH", root / "state.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_daily_cap_clips_positive_and_keeps_taxes(self):
        profile = game.load_profile()
        self.assertEqual(profile["points"], 0)
        self.assertIsNotNone(game.apply_points(profile, 250, "board_first", "a"))
        self.assertIsNotNone(game.apply_points(profile, 250, "board_first", "b"))
        self.assertIsNone(game.apply_points(profile, 80, "finish_profit", "capped"))
        self.assertEqual(profile["points"], 400)
        self.assertEqual(profile["earned_today"], 400)
        tax = game.apply_points(profile, game.POINTS_EARLY_SELL, "early_sell", "tax")
        self.assertEqual(tax["applied"], -25)
        self.assertEqual(profile["points"], 375)
        self.assertEqual(profile["earned_today"], 400)
        profile["earned_day"] = "1999-01-01"
        nxt = game.apply_points(profile, 80, "finish_profit", "new day")
        self.assertEqual(nxt["applied"], 80)
        self.assertEqual(profile["earned_today"], 80)
        self.assertEqual(profile["points"], 455)

    def test_settle_profit_peak_and_board_once(self):
        game._write_json(game.SCORES_PATH, [{"session_id": "run1", "final_cash": 9}])
        st = {
            "session_id": "run1",
            "final_cash": 9.0,
            "cash_usd": 9.0,
            "start_bankroll": 5.0,
            "peak_equity": 9.0,
        }
        events = game.settle_run_points(st)
        reasons = [row["reason"] for row in events]
        self.assertEqual(reasons, ["finish_profit", "new_peak", "board_first"])
        profile = game.load_profile()
        self.assertEqual(profile["points"], game.POINTS_DAILY_CAP)
        self.assertEqual(profile["personal_peak"], 9.0)
        self.assertEqual(game.settle_run_points(st), [])
        self.assertTrue(st["points_settled"])

    def test_blow_up_and_flat_run(self):
        game._write_json(game.SCORES_PATH, [{"session_id": "other", "final_cash": 20}])
        st = {
            "session_id": "blow",
            "final_cash": 0.4,
            "cash_usd": 0.4,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
        }
        events = game.settle_run_points(st)
        self.assertEqual([row["reason"] for row in events], ["blow_up"])
        self.assertEqual(game.load_profile()["points"], game.POINTS_BLOW_UP)

    def test_early_sell_tax_is_manual_and_under_five_minutes(self):
        now = datetime.now(timezone.utc)
        recent = {
            "id": "p1",
            "symbol": "TEST",
            "stake_usd": 1.0,
            "entry_price_usd": 1.0,
            "qty_tokens": 1.0,
            "opened_at": _utc(now - timedelta(seconds=30)),
        }
        st = {"cash_usd": 4.0, "positions": [recent], "closed": [], "events": []}
        game._close_position(st, recent, 1.1, "manual")
        self.assertEqual(game.load_profile()["points"], game.POINTS_EARLY_SELL)

        held = {
            "id": "p2",
            "symbol": "HELD",
            "stake_usd": 1.0,
            "entry_price_usd": 1.0,
            "qty_tokens": 1.0,
            "opened_at": _utc(now - timedelta(minutes=6)),
        }
        game._close_position({"cash_usd": 1, "positions": [held], "closed": [], "events": []}, held, 1.2, "manual")
        self.assertEqual(game.load_profile()["points"], game.POINTS_EARLY_SELL)

        forced = {
            "id": "p3",
            "symbol": "FORCE",
            "stake_usd": 1.0,
            "entry_price_usd": 1.0,
            "qty_tokens": 1.0,
            "opened_at": _utc(now - timedelta(seconds=10)),
        }
        game._close_position(
            {"cash_usd": 1, "positions": [forced], "closed": [], "events": []},
            forced,
            0.5,
            "force_24h",
        )
        self.assertEqual(game.load_profile()["points"], game.POINTS_EARLY_SELL)

    def test_shop_buy_equip_and_profile(self):
        profile = game.load_profile()
        profile["points"] = 800
        game.save_profile(profile)
        denied = game.buy_cosmetic("crown")
        self.assertFalse(denied["ok"])
        self.assertIn("Need", denied["error"])
        bought = game.buy_cosmetic("acid")
        self.assertTrue(bought["ok"])
        self.assertEqual(bought["sound"], "unlock")
        self.assertEqual(bought["profile"]["points"], 50)
        acid = next(row for row in bought["profile"]["shop"] if row["id"] == "acid")
        self.assertTrue(acid["owned"])
        self.assertFalse(acid["locked"])
        crown = next(row for row in bought["profile"]["shop"] if row["id"] == "crown")
        self.assertTrue(crown["locked"])
        self.assertEqual(crown["price"], 3500)
        self.assertGreater(crown["need"], 0)
        equipped = game.equip_cosmetic("acid")
        self.assertTrue(equipped["ok"])
        self.assertEqual(equipped["profile"]["tint"], "acid")
        self.assertEqual(equipped["profile"]["equipped_name"], "Acid Trail")
        self.assertFalse(game.equip_cosmetic("void")["ok"])
        self.assertFalse(game.update_profile(name="  ", pfp="rocket")["ok"])
        self.assertFalse(game.update_profile(name="Pilot", pfp="nope")["ok"])
        saved = game.update_profile(name="  Nunu  Pilot  ", pfp="moon")
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["profile"]["name"], "Nunu Pilot")
        self.assertEqual(saved["profile"]["pfp"], "moon")
        again = game.buy_cosmetic("acid")
        self.assertFalse(again["ok"])

    def test_public_state_includes_profile_without_ticking(self):
        view = game.public_profile()
        self.assertEqual(view["equipped"], "nebula")
        self.assertIn("shop", view)
        self.assertEqual(len(view["shop"]), 6)
        out = game.public_state(game.empty_state())
        self.assertIn("profile", out)
        self.assertEqual(out["profile"]["points"], 0)

    def test_shop_and_profile_routes(self):
        import server

        profile = game.load_profile()
        profile["points"] = 800
        game.save_profile(profile)
        client = server.app.test_client()
        with mock.patch.object(server.game_mod, "public_state", return_value={"status": "idle"}):
            bad = client.post("/api/game/shop", json={"action": "equip", "id": "void"})
            self.assertEqual(bad.status_code, 400)
            buy = client.post("/api/game/shop", json={"action": "buy", "id": "acid"})
            self.assertEqual(buy.status_code, 200)
            body = buy.get_json()
            self.assertEqual(body["sound"], "unlock")
            self.assertEqual(body["state"]["status"], "idle")
            equip = client.post("/api/game/shop", json={"action": "equip", "id": "acid"})
            self.assertEqual(equip.status_code, 200)
            self.assertEqual(game.load_profile()["equipped"], "acid")
            unnamed = client.post("/api/game/profile", json={"name": "", "pfp": "star"})
            self.assertEqual(unnamed.status_code, 400)
            named = client.post("/api/game/profile", json={"name": "Ada", "pfp": "star"})
            self.assertEqual(named.status_code, 200)
            self.assertEqual(game.load_profile()["name"], "Ada")
            self.assertEqual(game.load_profile()["pfp"], "star")
            weird = client.post("/api/game/shop", json={"action": "nope", "id": "acid"})
            self.assertEqual(weird.status_code, 400)

    def test_qa_tick_view_offsets_equity_without_saving_or_mutating(self):
        payload = {
            "status": "active",
            "equity_usd": 6.52,
            "pnl_usd": 1.52,
            "pnl_pct": 30.4,
            "start_bankroll": 5.0,
            "positions": [{"id": "p", "last_price_usd": 1.0}],
        }
        with mock.patch.object(game, "save_state") as save:
            up = game.qa_tick_view(payload, "up")
            down = game.qa_tick_view(payload, "down")
            flat = game.qa_tick_view(payload, "flat")
            plain = game.qa_tick_view(payload, "")
            weird = game.qa_tick_view(payload, "both")
            idle = game.qa_tick_view({"status": "idle", "equity_usd": 5.0}, "up")
            save.assert_not_called()
        self.assertEqual(payload["equity_usd"], 6.52)
        self.assertEqual(payload["positions"][0]["last_price_usd"], 1.0)
        self.assertAlmostEqual(up["equity_usd"] - 6.52, game.QA_TICK_STEP_USD, places=6)
        self.assertGreaterEqual(up["qa_tick_delta_usd"], 0.005)
        self.assertEqual(up["qa_tick"], "up")
        self.assertAlmostEqual(up["pnl_usd"], 1.64, places=6)
        self.assertAlmostEqual(down["equity_usd"] - 6.52, -game.QA_TICK_STEP_USD, places=6)
        self.assertLessEqual(down["qa_tick_delta_usd"], -0.005)
        self.assertEqual(down["qa_tick"], "down")
        self.assertEqual(flat["equity_usd"], 6.52)
        self.assertEqual(flat["qa_tick"], "down")
        self.assertEqual(flat["qa_tick_delta_usd"], 0)
        self.assertNotIn("qa_tick", plain)
        self.assertNotIn("qa_tick", weird)
        self.assertEqual(weird["equity_usd"], 6.52)
        self.assertEqual(idle["equity_usd"], 5.0)
        self.assertEqual(idle["qa_tick"], "need-active")
        self.assertIs(up["positions"], payload["positions"])

    def test_qa_tick_route_does_not_persist(self):
        import server

        payload = {
            "status": "active",
            "equity_usd": 6.52,
            "pnl_usd": 1.52,
            "start_bankroll": 5.0,
            "positions": [{"id": "p"}],
        }
        client = server.app.test_client()
        with mock.patch.object(server.game_mod, "public_state", return_value=payload), mock.patch.object(
            server.game_mod, "save_state"
        ) as save:
            up = client.get("/api/game/state?qa_tick=up")
            self.assertEqual(up.status_code, 200)
            body = up.get_json()
            self.assertAlmostEqual(body["equity_usd"], 6.64, places=4)
            self.assertEqual(body["qa_tick"], "up")
            self.assertEqual(payload["equity_usd"], 6.52)
            plain = client.post("/api/game/state", json={})
            self.assertEqual(plain.get_json()["equity_usd"], 6.52)
            self.assertNotIn("qa_tick", plain.get_json())
            down = client.post("/api/game/state?qa_tick=down", json={})
            self.assertEqual(down.get_json()["qa_tick"], "down")
            self.assertAlmostEqual(down.get_json()["equity_usd"], 6.4, places=4)
            named = client.get("/api/game/state?qa_tick=both")
            self.assertEqual(named.get_json()["equity_usd"], 6.52)
            self.assertNotIn("qa_tick", named.get_json())
            save.assert_not_called()
        idle = {"status": "idle", "equity_usd": 5.0}
        with mock.patch.object(server.game_mod, "public_state", return_value=idle):
            blocked = client.get("/api/game/state?qa_tick=up")
            self.assertEqual(blocked.get_json()["qa_tick"], "need-active")
            self.assertEqual(blocked.get_json()["equity_usd"], 5.0)
            self.assertEqual(idle["equity_usd"], 5.0)

    def test_health_lists_game_finish(self):
        import server

        res = server.app.test_client().get("/api/health")
        data = res.get_json()
        self.assertEqual(data["panel_v"], 95)
        self.assertIn("delay_warning", data["features"])
        self.assertIn("game_polish", data["features"])
        self.assertEqual(data["qa_tick"], "page ?qa_tick=both|up|down")
        self.assertIn("game_finish", data["features"])
        self.assertIn("quote_sync", data["features"])


class PanelFinishTests(unittest.TestCase):
    def test_tick_decision_and_plain_flags(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="gRocket"', html)
        self.assertIn('id="gShop"', html)
        self.assertIn('id="profileChip"', html)
        self.assertIn('id="profileModal"', html)
        self.assertEqual(html.count('beep("tick")'), 1)
        self.assertIn('beep("down")', html)
        self.assertNotIn("t: String(rf)", html)
        self.assertNotIn('t: "late chase"', html)
        self.assertIn('return "Late chase"', html)
        tick = html[html.index("const TICK_EPS"):html.index("function fmtSignedTick")]
        flags = html[html.index("function plainFlagLabel"):html.index("function flagStrip")]
        script = tick + "\n" + flags + r"""
const up = equityTickDecision(10, 10.12, "active");
if (up.sound !== "tick" || up.moodPulse !== "tick") process.exit(2);
const flat = equityTickDecision(10, 10.001, "active");
if (flat.sound !== "" || flat.moodPulse !== "") process.exit(3);
const dn = equityTickDecision(10, 9.9, "active");
if (dn.sound !== "down" || dn.moodPulse === "tick") process.exit(4);
if (equityTickDecision(null, 12, "active").sound !== "") process.exit(5);
if (equityTickDecision(10, 11, "idle").sound !== "") process.exit(6);
if (restingRocketMood(0, 0, false) !== "idle") process.exit(7);
if (restingRocketMood(-1, 4, true) !== "down") process.exit(8);
if (restingRocketMood(0.5, -3, true) !== "up") process.exit(9);
if (restingRocketMood(0, -1, false) !== "down") process.exit(10);
const samples = ["late_chase", "lp_weak_0", "lp_partial_40", "high_vol_liq", "sell_heavy", "rug_danger_90", "risks_x4"];
if (plainFlagLabel("late_chase") !== "Late chase") process.exit(11);
for (const raw of samples) {
  const lab = plainFlagLabel(raw);
  if (!lab || lab.includes("_") || lab === raw) process.exit(12);
}
"""
        subprocess.check_call(["node", "-e", script])

    def test_qa_force_tick_script_crosses_tick_eps_both_ways(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('fetch("/api/game/state" + qaQs', html)
        self.assertIn("data-qa-tick", html)
        self.assertEqual(html.count('beep("tick")'), 1)
        self.assertIn(f"const QA_TICK_STEP_USD = {game.QA_TICK_STEP_USD}", html)
        self.assertIn("?qa_tick=both", html)
        note = html[html.index("function noteEquityTick"):html.index("function notePointSounds")]
        self.assertLess(note.index("qaSkipNextTick"), note.index("equityTickDecision"))
        self.assertLess(note.index("applyQaTickRelease"), note.index("equityTickDecision"))
        self.assertIn("state.qaTickPulseMs || 1400", note)
        tick = html[html.index("const TICK_EPS"):html.index("function fmtSignedTick")]
        qa = html[html.index("const QA_TICK_STEP_USD"):html.index("/* qa-tick-pure-end */")]
        script = tick + "\n" + qa + r"""
if (readQaTickPlan("both").join(",") !== "up,down") process.exit(20);
if (readQaTickPlan("UP").join(",") !== "up") process.exit(21);
if (readQaTickPlan("down").join(",") !== "down") process.exit(22);
if (readQaTickPlan("nope").length !== 0) process.exit(23);
const base = 6.52;
const s = freshQaTickScript(readQaTickPlan("both"));
const arm = planQaTickRequest(s, 0);
if (arm.mode !== "" || arm.expect !== "" || s.phase !== "armed") process.exit(24);
const dup = planQaTickRequest(s, 5);
if (dup !== arm || s.step !== -1) process.exit(25);
settleQaTickRequest(s);
const up = planQaTickRequest(s, 2000);
if (up.mode !== "up" || up.expect !== "up" || up.dir !== "up" || !s.pulsePending) process.exit(26);
const upEq = Math.round((base + QA_TICK_STEP_USD) * 1000) / 1000;
const upDec = equityTickDecision(base, upEq, "active");
if (upDec.sound !== "tick" || upDec.moodPulse !== "tick") process.exit(27);
s.pulsePending = false;
settleQaTickRequest(s);
const hold = planQaTickRequest(s, 4000);
if (hold.mode !== "up" || hold.expect !== "" || s.pulsePending) process.exit(28);
if (equityTickDecision(upEq, upEq, "active").sound !== "") process.exit(29);
settleQaTickRequest(s);
const dn = planQaTickRequest(s, s.holdUntil);
if (dn.mode !== "flat" || dn.expect !== "down" || dn.dir !== "down") process.exit(30);
const dnDec = equityTickDecision(upEq, base, "active");
if (dnDec.sound !== "down" || dnDec.moodPulse === "tick") process.exit(31);
if (!(base <= upEq - 0.005)) process.exit(32);
s.pulsePending = false;
settleQaTickRequest(s);
const done = planQaTickRequest(s, s.holdUntil);
if (done.release || s.releaseOnce || s.phase !== "done" || done.mode !== "") process.exit(33);
const upOnly = freshQaTickScript(readQaTickPlan("up"));
planQaTickRequest(upOnly, 0);
settleQaTickRequest(upOnly);
planQaTickRequest(upOnly, 10);
settleQaTickRequest(upOnly);
const upEnd = planQaTickRequest(upOnly, upOnly.holdUntil);
if (!upEnd.release || !upOnly.releaseOnce) process.exit(34);
const rel = applyQaTickRelease(true, base + QA_TICK_STEP_USD, base);
if (rel.prev !== base || rel.releaseOnce) process.exit(35);
if (equityTickDecision(rel.prev, base, "active").sound !== "") process.exit(36);
const downOnly = freshQaTickScript(["down"]);
planQaTickRequest(downOnly, 0);
settleQaTickRequest(downOnly);
const loss = planQaTickRequest(downOnly, 10);
if (loss.mode !== "down" || loss.expect !== "down") process.exit(37);
const lossEq = Math.round((base - QA_TICK_STEP_USD) * 1000) / 1000;
if (equityTickDecision(base, lossEq, "active").sound !== "down") process.exit(38);
settleQaTickRequest(downOnly);
const back = planQaTickRequest(downOnly, downOnly.holdUntil);
if (!back.release) process.exit(39);
const backRel = applyQaTickRelease(true, lossEq, base);
if (equityTickDecision(backRel.prev, base, "active").sound !== "") process.exit(40);
const keep = applyQaTickRelease(false, base, upEq);
if (keep.prev !== base || keep.releaseOnce) process.exit(41);
if (equityTickDecision(keep.prev, upEq, "active").sound !== "tick") process.exit(42);
if (equityTickDecision(base, upEq, "idle").sound !== "") process.exit(43);
"""
        subprocess.check_call(["node", "-e", script])

    def test_start_sh_defaults_interval_to_five(self):
        text = Path("start.sh").read_text(encoding="utf-8")
        self.assertIn('INTERVAL_MIN="${INTERVAL_MIN:-5}"', text)
        self.assertNotIn('INTERVAL_MIN="${INTERVAL_MIN:-15}"', text)

    def test_ship_g_craft_chart_and_quote_guard(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('class="canopy-glow"', html)
        self.assertIn('class="flame"', html)
        self.assertIn('class="chart-stage"', html)
        self.assertIn('data-stage-hook="chart-v1"', html)
        self.assertIn('data-sfx-hook="casino-cues"', html)
        self.assertIn('id="gSoundOnBtn"', html)
        self.assertIn('id="gSoundMuteBtn"', html)
        self.assertIn(">Sound on<", html)
        self.assertIn(">Mute<", html)
        self.assertNotIn('id="gStartIdle"', html)
        self.assertNotIn('id="gIdleCta"', html)
        self.assertEqual(html.count('id="gStart"'), 1)
        self.assertNotIn(">No sound<", html)
        self.assertIn("♪♪ WIN", html)
        self.assertIn("♪ LOSS", html)
        self.assertIn('class="mini"', html)
        self.assertIn("body.mode-game .qa-foot { display: none; }", html)
        self.assertIn("canopy-glow", html)
        pure = html[html.index("/* ship-g-pure-start */"):html.index("/* ship-g-pure-end */")]
        script = pure + r"""
const empty = emptyQuoteDecision("  ");
if (empty.ok || empty.start || empty.message.indexOf("Enter a coin") < 0) process.exit(2);
const named = emptyQuoteDecision("BONK");
if (!named.ok || named.start || named.mint !== "BONK") process.exit(3);
if (emptyQuoteDecision(null).start) process.exit(4);
const stale = sessionChromeDecision("active", "Session ended · final cash $4.10");
if (!stale.replace || stale.reason !== "stale-ended") process.exit(5);
if (sessionChromeDecision("active", "24h run live — $5.00 paper").replace) process.exit(6);
if (sessionChromeDecision("active", "Quoted BONK").replace) process.exit(7);
if (sessionChromeDecision("ended", "Session ended · final cash $5.00").replace) process.exit(8);
const boot = sessionChromeDecision("ended", "Loading game…");
if (!boot.replace || boot.reason !== "boot-ended") process.exit(9);
const down = pnlChipText("down", 12.4);
if (down.indexOf("+") >= 0 || down.indexOf("−12.4%") < 0 || down.indexOf("▼ DOWN") < 0) process.exit(10);
if (pnlChipText("down", -8).indexOf("+") >= 0) process.exit(11);
const up = pnlChipText("tick", 14.2);
if (up.indexOf("▲ UP") < 0 || up.indexOf("+14.2%") < 0) process.exit(12);
if (pnlChipText("idle", 9) !== "— FLAT") process.exit(13);
if (rocketSoundBadge(false, "idle", false) !== "Sound on") process.exit(14);
if (rocketSoundBadge(true, "tick", false) !== "Mute") process.exit(15);
if (rocketSoundBadge(false, "tick", false) !== "♪♪ WIN") process.exit(16);
if (rocketSoundBadge(false, "down", true) !== "♪ LOSS") process.exit(17);
if (rocketSoundBadge(false, "down", false).indexOf("WIN") >= 0) process.exit(18);
const tickPts = chartPointsFor("tick");
const downPts = chartPointsFor("down");
if (tickPts === downPts || chartPointsFor("idle") === tickPts) process.exit(19);
"""
        subprocess.check_call(["node", "-e", script])

    def test_empty_quote_does_not_start_a_run(self):
        import server

        client = server.app.test_client()
        with mock.patch.object(server.game_mod, "start_session") as start:
            res = client.get("/api/game/quote")
            self.assertEqual(res.status_code, 400)
            body = res.get_json()
            self.assertFalse(body.get("ok", True))
            self.assertIn("Enter a coin", body.get("error") or "")
            self.assertFalse(body.get("started", True))
            start.assert_not_called()
            blank = client.get("/api/game/quote?mint=%20%20")
            self.assertEqual(blank.status_code, 400)
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
