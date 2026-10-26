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

    def test_health_lists_game_finish(self):
        import server

        res = server.app.test_client().get("/api/health")
        data = res.get_json()
        self.assertEqual(data["panel_v"], 63)
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

    def test_start_sh_defaults_interval_to_five(self):
        text = Path("start.sh").read_text(encoding="utf-8")
        self.assertIn('INTERVAL_MIN="${INTERVAL_MIN:-5}"', text)
        self.assertNotIn('INTERVAL_MIN="${INTERVAL_MIN:-15}"', text)


if __name__ == "__main__":
    unittest.main()
