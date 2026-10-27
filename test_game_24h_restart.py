"""SHIP K — 24h end shows ended, Start/Restart opens a fresh run (AC-E01…E03)."""

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


def _pos() -> dict:
    return {
        "id": "poshachi",
        "mint": "DMnMwUVo1a9HbSnixbdNaTsoHczJ8cdamUVRM1Qpump",
        "symbol": "HACHI",
        "name": "HACHI",
        "stake_usd": 1.0,
        "entry_price_usd": 1.0,
        "qty_tokens": 1.0,
        "last_price_usd": 1.0,
        "opened_at": _utc(datetime.now(timezone.utc)),
    }


class ShipKRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for patch in (
            mock.patch.object(game, "PROFILE_PATH", root / "profile.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
            mock.patch.object(game, "STATE_PATH", root / "state.json"),
            mock.patch.object(game, "fetch_dex_token", return_value=None),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _save_active(self, *, ends_at, positions=None, cash=4.0, session_id="oldrun"):
        now = datetime.now(timezone.utc)
        st = {
            "session_id": session_id,
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": ends_at,
            "cash_usd": cash,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": list(positions or []),
            "closed": [],
            "events": [],
            "disclaimer": "Paper research only. Not financial advice. No real trades.",
        }
        game.save_state(st)
        return st

    def test_expired_clock_ends_and_closes_open_once(self):
        past = _utc(datetime.now(timezone.utc) - timedelta(seconds=5))
        self._save_active(ends_at=past, positions=[_pos()])
        out = game.tick_session()
        self.assertEqual(out["status"], "ended")
        self.assertEqual(out["positions"], [])
        self.assertEqual(out["final_cash"], 5.0)
        self.assertEqual(out["cash_usd"], 5.0)
        self.assertEqual(len(out["closed"]), 1)
        self.assertEqual(out["closed"][0]["symbol"], "HACHI")
        self.assertEqual(out["closed"][0]["close_reason"], "force_24h")
        self.assertTrue(out.get("ended_at"))
        kinds = [e.get("kind") for e in out["events"]]
        self.assertIn("end", kinds)
        self.assertIn("force_24h", kinds)
        again = game.tick_session()
        self.assertEqual(again["status"], "ended")
        self.assertEqual(len(again["closed"]), 1)
        self.assertEqual(again["cash_usd"], 5.0)

    def test_missing_clock_on_active_run_is_ended_not_a_ghost(self):
        self._save_active(ends_at=None, positions=[_pos()])
        out = game.public_state()
        self.assertEqual(out["status"], "ended")
        self.assertEqual(out["open_count"], 0)
        self.assertEqual(out["positions"], [])
        self.assertEqual(out["remain_seconds"], 0)
        self.assertEqual(out["final_cash"], 5.0)

    def test_future_clock_stays_active(self):
        future = _utc(datetime.now(timezone.utc) + timedelta(hours=20))
        self._save_active(ends_at=future, positions=[_pos()])
        out = game.tick_session()
        self.assertEqual(out["status"], "active")
        self.assertEqual(len(out["positions"]), 1)
        self.assertEqual(out["positions"][0]["symbol"], "HACHI")
        self.assertEqual(out["cash_usd"], 4.0)

    def test_force_end_closes_without_starting_another_run(self):
        future = _utc(datetime.now(timezone.utc) + timedelta(hours=23))
        self._save_active(ends_at=future, positions=[_pos()], session_id="ghost")
        ended = game.force_end_session()
        self.assertEqual(ended["status"], "ended")
        self.assertEqual(ended["session_id"], "ghost")
        self.assertEqual(ended["positions"], [])
        self.assertEqual(ended["final_cash"], 5.0)
        self.assertEqual(len(ended["closed"]), 1)
        view = game.public_state()
        self.assertEqual(view["status"], "ended")
        self.assertEqual(view["session_id"], "ghost")
        self.assertEqual(view["open_count"], 0)
        self.assertEqual(view["remain_seconds"], 0)
        again = game.force_end_session()
        self.assertEqual(again["status"], "ended")
        self.assertEqual(again["session_id"], "ghost")
        self.assertEqual(len(again["closed"]), 1)

    def test_start_after_end_is_a_fresh_baseline(self):
        future = _utc(datetime.now(timezone.utc) + timedelta(hours=23))
        self._save_active(ends_at=future, positions=[_pos()], session_id="ghost")
        game.force_end_session()
        started = game.start_session(force=False)
        self.assertEqual(started["status"], "active")
        self.assertNotEqual(started["session_id"], "ghost")
        self.assertEqual(started["positions"], [])
        self.assertEqual(started["closed"], [])
        self.assertEqual(started["cash_usd"], 5.0)
        self.assertFalse(started.get("ended_at"))
        view = game.public_state(started)
        self.assertEqual(view["equity_usd"], 5.0)
        self.assertEqual(view["pnl_usd"], 0.0)
        self.assertEqual(view["open_count"], 0)
        self.assertGreater(view["remain_seconds"], 23 * 3600)
        self.assertLessEqual(view["remain_seconds"], 24 * 3600)

    def test_restart_force_wipes_open_position(self):
        future = _utc(datetime.now(timezone.utc) + timedelta(hours=23))
        self._save_active(ends_at=future, positions=[_pos()], session_id="live")
        started = game.start_session(force=True)
        self.assertEqual(started["status"], "active")
        self.assertNotEqual(started["session_id"], "live")
        self.assertEqual(started["positions"], [])
        self.assertEqual(started["cash_usd"], 5.0)
        self.assertEqual(started["closed"], [])

    def test_nonforce_start_on_live_run_does_not_wipe(self):
        future = _utc(datetime.now(timezone.utc) + timedelta(hours=23))
        self._save_active(ends_at=future, positions=[_pos()], session_id="live")
        same = game.start_session(force=False)
        self.assertEqual(same["session_id"], "live")
        self.assertEqual(same["status"], "active")
        self.assertEqual(len(same["positions"]), 1)

    def test_routes_end_then_start_and_do_not_autostart(self):
        import server

        future = _utc(datetime.now(timezone.utc) + timedelta(hours=23))
        self._save_active(ends_at=future, positions=[_pos()], session_id="ghost")
        client = server.app.test_client()
        ended = client.post("/api/game/end")
        self.assertEqual(ended.status_code, 200)
        body = ended.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"]["status"], "ended")
        self.assertEqual(body["state"]["session_id"], "ghost")
        self.assertEqual(body["state"]["positions"], [])
        self.assertEqual(body["state"]["open_count"], 0)
        self.assertEqual(body["sound"], "end")
        stayed = client.get("/api/game/state")
        self.assertEqual(stayed.get_json()["status"], "ended")
        self.assertEqual(stayed.get_json()["session_id"], "ghost")
        qa = client.get("/api/game/state?qa_end=1")
        self.assertEqual(qa.get_json()["status"], "ended")
        self.assertEqual(qa.get_json()["session_id"], "ghost")
        started = client.post("/api/game/start", json={"force": False})
        fresh = started.get_json()["state"]
        self.assertEqual(fresh["status"], "active")
        self.assertNotEqual(fresh["session_id"], "ghost")
        self.assertEqual(fresh["cash_usd"], 5.0)
        self.assertEqual(fresh["equity_usd"], 5.0)
        self.assertEqual(fresh["positions"], [])
        self.assertEqual(fresh["open_count"], 0)
        self.assertGreater(fresh["remain_seconds"], 23 * 3600)

    def test_qa_end_on_active_run_persists_ended(self):
        import server

        future = _utc(datetime.now(timezone.utc) + timedelta(hours=23))
        self._save_active(ends_at=future, positions=[_pos()], session_id="ghost")
        client = server.app.test_client()
        res = client.post("/api/game/state?qa_end=1", json={})
        body = res.get_json()
        self.assertEqual(body["status"], "ended")
        self.assertEqual(body["positions"], [])
        self.assertEqual(body["final_cash"], 5.0)
        follow = client.get("/api/game/state")
        self.assertEqual(follow.get_json()["status"], "ended")
        self.assertEqual(follow.get_json()["session_id"], "ghost")

    def test_health_is_v70_with_game_24h_restart(self):
        import server

        res = server.app.test_client().get("/api/health")
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 70)
        self.assertIn("game_24h_restart", data["features"])
        self.assertIn("dismiss_back", data["features"])
        self.assertIn("game_polish", data["features"])
        self.assertIn("game_24h", data["features"])
        self.assertIn("qa_end", data)
        self.assertIn("qa_end=1", data["qa_end"])


class ShipKPanelTests(unittest.TestCase):
    def test_panel_resets_rocket_and_hides_ghost_open(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 70", html)
        self.assertIn("paper only · v70", html)
        self.assertIn("?v=70", html)
        self.assertIn("function sessionEndView(", html)
        self.assertIn("function freshRunBaseline(", html)
        self.assertIn("function rocketResetDecision(", html)
        self.assertIn("function requestDeadClockEnd(", html)
        self.assertIn('"/api/game/end"', html)
        self.assertIn("qa_end=1", html)
        self.assertIn("No open positions — session ended", html)
        self.assertIn('status === "ended" ? "ENDED"', html)
        self.assertIn("data-open", html)
        pure = html[html.index("/* ship-k-pure-start */"):html.index("/* ship-k-pure-end */")]
        script = pure + r"""
const ghost = sessionEndView({
  status: "active",
  remain_seconds: 0,
  positions: [{ symbol: "HACHI", pnl_usd: 0 }],
});
if (!ghost.ended || !ghost.deadClock || ghost.chip !== "Session ended" || ghost.showOpen || ghost.openCount !== 0 || ghost.clock !== "00:00:00") process.exit(2);
const live = sessionEndView({
  status: "active",
  remain_seconds: 86399,
  positions: [{ symbol: "HACHI" }],
});
if (live.ended || !live.showOpen || live.openCount !== 1 || live.chip !== "ACTIVE") process.exit(3);
const ended = sessionEndView({ status: "ended", remain_seconds: 0, positions: [{ symbol: "HACHI" }] });
if (!ended.ended || ended.showOpen || ended.chip !== "Session ended") process.exit(4);
const fresh = freshRunBaseline({
  status: "active", cash_usd: 5, equity_usd: 5, start_bankroll: 5, positions: [],
});
if (!fresh.ok || fresh.mood !== "idle" || fresh.chip !== "none" || fresh.tick !== "clear") process.exit(5);
const dirty = freshRunBaseline({
  status: "active", cash_usd: 4, equity_usd: 5, start_bankroll: 5, positions: [{ symbol: "HACHI" }],
});
if (dirty.ok) process.exit(6);
const resetEnd = rocketResetDecision(
  { sessionId: "old", phase: "active" },
  { session_id: "old", status: "ended", positions: [{ symbol: "HACHI", pnl_usd: 1 }], remain_seconds: 0 }
);
if (!resetEnd.reset || resetEnd.mood !== "idle" || resetEnd.chip !== "none" || resetEnd.tick !== "clear" || resetEnd.reason !== "ended") process.exit(7);
const resetDead = rocketResetDecision(
  { sessionId: "old", phase: "active" },
  { session_id: "old", status: "active", remain_seconds: 0, positions: [{ symbol: "HACHI" }] }
);
if (resetDead.reason !== "ended" || resetDead.chip !== "none" || resetDead.mood !== "idle") process.exit(8);
const resetFresh = rocketResetDecision(
  { sessionId: "old", phase: "ended" },
  { session_id: "new", status: "active", cash_usd: 5, equity_usd: 5, start_bankroll: 5, positions: [], remain_seconds: 86400 }
);
if (resetFresh.reason !== "fresh-run" || resetFresh.mood !== "idle" || resetFresh.chip !== "none" || resetFresh.tick !== "clear") process.exit(9);
const keep = rocketResetDecision(
  { sessionId: "same", phase: "active" },
  { session_id: "same", status: "active", remain_seconds: 1000, positions: [{ symbol: "HACHI" }] }
);
if (keep.reset || keep.reason !== "same-run") process.exit(10);
"""
        subprocess.check_call(["node", "-e", script])


if __name__ == "__main__":
    unittest.main()
