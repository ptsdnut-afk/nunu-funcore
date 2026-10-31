#!/usr/bin/env python3
"""SHIP L — Update now kicks the bot loop (AC-U01…U05). Not a QA PASS."""

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import bot
import filter
import manual_update
import server


def _panel() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


class HealthAndChromeTests(unittest.TestCase):
    def test_health_is_v71_with_manual_update(self):
        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 92)
        self.assertIn("manual_update", data["features"])
        self.assertIn("delay_warning", data["features"])
        self.assertIn("game_24h_restart", data["features"])
        self.assertEqual(data["sitting_drop_min"], 5.0)

    def test_update_route_is_the_bot_loop_not_scan(self):
        import inspect

        src = inspect.getsource(server.api_update_now)
        self.assertIn("manual_update.request_update", src)
        self.assertNotIn("scan_mint", src)
        self.assertIs(bot.run_filter, filter.main)

    def test_loop_interval_stays_five_minutes(self):
        text = Path("start.sh").read_text(encoding="utf-8")
        self.assertIn('INTERVAL_MIN="${INTERVAL_MIN:-5}"', text)

    def test_update_now_sits_beside_refresh(self):
        html = _panel()
        self.assertIn("const PANEL_V = 92", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("paintPanelVersion", html)
        self.assertNotIn("?v=76", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("data-panel-v", html)
        update_at = html.index('id="btnUpdateNow"')
        refresh_at = html.index('id="btnRefresh"')
        self.assertLess(update_at, refresh_at)
        self.assertLess(refresh_at - update_at, 700)
        self.assertIn(">Update now<", html)
        self.assertIn(">Refresh<", html)
        self.assertIn("Pull fresh · may take ~30s", html)
        self.assertIn("body.mode-game .update-cluster { display: none !important; }", html)
        self.assertIn("Behind live · not Dex 1s", html)
        self.assertIn("Near live · just scanned", html)
        self.assertIn("still not Dex 1s", html)
        self.assertIn("const FEED_LOOP_MIN = 5", html)
        block = html[html.index("/* ship-l-update-start */"):html.index("/* ship-l-update-end */")]
        self.assertIn('"/api/update-now"', block)
        self.assertIn('method: "POST"', block)
        self.assertIn("Updating…", block)
        self.assertIn("state.updateInFlight", block)
        self.assertIn("Token Flow updated", block)
        self.assertIn("btn.disabled", block)
        self.assertNotIn("/api/scan", block)
        update_fn = block[block.index("async function updateNow"):]
        self.assertLess(update_fn.index('method: "POST"'), update_fn.index("finishUpdate"))
        finish_fn = block[block.index("async function finishUpdate"):block.index("async function updateNow")]
        self.assertIn("loadData(true)", finish_fn)
        refresh = html[html.index('getElementById("btnRefresh")'):html.index('getElementById("btnRefresh")') + 240]
        self.assertIn("loadData(true)", refresh)
        self.assertNotIn("/api/update-now", refresh)
        self.assertNotIn("updateNow(", refresh)


class KickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._old = (
            manual_update.KICK_PATH,
            manual_update.LOCK_PATH,
            bot.STATUS_PATH,
            bot.DATA_DIR,
            server.STATUS_PATH,
            server.LATEST_PATH,
        )
        manual_update.KICK_PATH = root / "manual_update.json"
        manual_update.LOCK_PATH = root / "manual_update.lock"
        bot.STATUS_PATH = root / "bot_status.json"
        bot.DATA_DIR = root
        server.STATUS_PATH = bot.STATUS_PATH
        server.LATEST_PATH = root / "latest.json"
        server._json_cache.clear()
        self.addCleanup(self._restore)
        self.client = server.app.test_client()

    def _restore(self):
        (
            manual_update.KICK_PATH,
            manual_update.LOCK_PATH,
            bot.STATUS_PATH,
            bot.DATA_DIR,
            server.STATUS_PATH,
            server.LATEST_PATH,
        ) = self._old
        server._json_cache.clear()

    def _alive(self, **extra):
        payload = {
            "mode": "loop every 5 min",
            "interval_min": 5,
            "running": True,
            "phase": "idle",
            "cycles": 1,
            "last_exit_code": 0,
            "last_run_started": "2026-10-27T02:00:00Z",
            "last_run_finished": "2026-10-27T02:00:20Z",
            "manual_id": "",
        }
        payload.update(extra)
        bot.write_status(**payload)

    def _write_latest(self, data_as_of: str, *, fresh_mtime: bool = False):
        server.LATEST_PATH.write_text(
            json.dumps(
                {
                    "data_as_of": data_as_of,
                    "run_finished": data_as_of,
                    "flagged_count": 1,
                }
            ),
            encoding="utf-8",
        )
        if fresh_mtime:
            os.utime(server.LATEST_PATH, None)
        server._json_cache.clear()

    def _kick(self) -> dict:
        return json.loads(manual_update.KICK_PATH.read_text(encoding="utf-8"))

    def test_missing_bot_is_an_error_not_a_silent_reload(self):
        res = self.client.post("/api/update-now")
        self.assertEqual(res.status_code, 503)
        body = res.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "Bot loop is not running")
        self.assertFalse(manual_update.KICK_PATH.exists())

    def test_dead_pid_does_not_queue(self):
        proc = subprocess.Popen(["sleep", "30"])
        proc.kill()
        proc.wait(timeout=5)
        self._alive()
        bot.write_status(
            mode="loop every 5 min",
            interval_min=5,
            running=True,
            phase="idle",
            pid=proc.pid,
        )
        res = self.client.post("/api/update-now")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(manual_update.KICK_PATH.exists())

    def test_mash_while_queued_is_one_kick(self):
        self._alive()
        codes = []
        ids = []
        for _ in range(5):
            res = self.client.post("/api/update-now")
            body = res.get_json()
            codes.append(res.status_code)
            ids.append(body["id"])
        self.assertEqual(codes, [202, 409, 409, 409, 409])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self._kick()["state"], "queued")
        self.assertEqual(self._kick()["pipeline"], "bot_loop")
        self.assertFalse(self._kick()["attached"])

    def test_kick_runs_filter_main_and_advances_data_as_of(self):
        self._alive()
        self._write_latest("2026-10-27T02:30:00Z", fresh_mtime=True)
        first = self.client.post("/api/update-now")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.get_json()["pipeline"], "bot_loop")
        self.assertEqual(first.get_json()["interval_min"], 5)
        calls = []

        def fake():
            calls.append("filter.main")
            self._write_latest("2026-10-27T02:34:00Z")
            return 0

        with mock.patch.object(bot, "run_filter", fake), mock.patch.object(
            filter, "scan_mint", side_effect=AssertionError("scan_mint")
        ):
            code, cycles, forced = bot.iteration(
                skip_fresh=240,
                once=False,
                interval_min=5,
                mode="loop every 5 min",
                cycles=0,
            )
        self.assertEqual(calls, ["filter.main"])
        self.assertTrue(forced)
        self.assertEqual(code, 0)
        self.assertEqual(cycles, 1)
        status = self.client.get("/api/update-now")
        body = status.get_json()
        self.assertEqual(body["state"], "done")
        self.assertEqual(body["data_as_of"], "2026-10-27T02:34:00Z")
        self.assertEqual(body["exit_code"], 0)
        latest = self.client.get("/api/latest").get_json()
        self.assertEqual(latest["data_as_of"], "2026-10-27T02:34:00Z")
        bot_status = json.loads(bot.STATUS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(bot_status["trigger"], "manual")
        self.assertEqual(bot_status["phase"], "idle")
        again = self.client.post("/api/update-now")
        self.assertEqual(again.status_code, 429)
        self.assertEqual(again.get_json()["state"], "cooldown")
        self.assertGreater(again.get_json()["retry_after_s"], 0)
        self.assertIn("Retry-After", again.headers)
        self.assertEqual(self._kick()["state"], "done")
        cooled = self._kick()
        cooled["cooldown_until"] = "2020-01-01T00:00:00Z"
        manual_update.KICK_PATH.write_text(json.dumps(cooled), encoding="utf-8")
        queued = self.client.post("/api/update-now")
        self.assertEqual(queued.status_code, 202)
        self.assertEqual(queued.get_json()["state"], "queued")
        self.assertNotEqual(queued.get_json()["id"], first.get_json()["id"])

    def test_scheduled_skip_does_not_run_or_swallow_a_later_kick(self):
        self._alive()
        self._write_latest("2026-10-27T02:30:00Z", fresh_mtime=True)
        calls = []

        def fake():
            calls.append(1)
            return 0

        with mock.patch.object(bot, "run_filter", fake):
            _code, _cycles, forced = bot.iteration(
                skip_fresh=240,
                once=False,
                interval_min=5,
                mode="loop every 5 min",
                cycles=3,
            )
        self.assertEqual(calls, [])
        self.assertFalse(forced)
        phase = json.loads(bot.STATUS_PATH.read_text(encoding="utf-8"))["phase"]
        self.assertEqual(phase, "skipped_fresh")

    def test_failed_pass_is_visible_and_cooled_down(self):
        self._alive()
        self.client.post("/api/update-now")

        def fake():
            raise RuntimeError("dex down")

        with mock.patch.object(bot, "run_filter", fake):
            code, _cycles, forced = bot.iteration(
                skip_fresh=0,
                once=False,
                interval_min=5,
                mode="loop every 5 min",
                cycles=0,
            )
        self.assertTrue(forced)
        self.assertEqual(code, 1)
        body = self.client.get("/api/update-now").get_json()
        self.assertEqual(body["state"], "error")
        self.assertEqual(body["error"], "Filter pass failed")
        again = self.client.post("/api/update-now")
        self.assertEqual(again.status_code, 429)

    def test_in_flight_pass_is_attached_not_stacked(self):
        self._alive(
            phase="running_filter",
            last_run_started="2026-10-27T02:10:00Z",
            last_run_finished=None,
            last_exit_code=None,
        )
        res = self.client.post("/api/update-now")
        self.assertEqual(res.status_code, 202)
        body = res.get_json()
        self.assertTrue(body["attached"])
        self.assertEqual(body["state"], "running")
        self.assertIsNone(manual_update.claim_queued())
        again = self.client.post("/api/update-now")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.get_json()["id"], body["id"])
        self._write_latest("2026-10-27T02:10:40Z")
        self._alive(
            phase="idle",
            last_run_started="2026-10-27T02:10:00Z",
            last_run_finished="2026-10-27T02:10:40Z",
            last_exit_code=0,
        )
        done = self.client.get("/api/update-now").get_json()
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["data_as_of"], "2026-10-27T02:10:40Z")
        cooled = self.client.post("/api/update-now")
        self.assertEqual(cooled.status_code, 429)

    def test_mid_pass_queue_does_not_schedule_a_second_run(self):
        self._alive()
        calls = []

        def fake():
            calls.append(1)
            manual_update._write(
                {
                    "id": "mid",
                    "state": "queued",
                    "attached": False,
                    "pipeline": "bot_loop",
                    "requested_at": "2026-10-27T02:20:00Z",
                }
            )
            return 0

        with mock.patch.object(bot, "run_filter", fake):
            _code, _cycles, forced = bot.iteration(
                skip_fresh=0,
                once=False,
                interval_min=5,
                mode="loop every 5 min",
                cycles=0,
            )
        self.assertFalse(forced)
        self.assertEqual(calls, [1])
        kick = self._kick()
        self.assertEqual(kick["id"], "mid")
        self.assertEqual(kick["state"], "done")
        self.assertFalse(manual_update.queued())

    def test_reconcile_finished_manual_pass_without_complete_call(self):
        self._alive(
            phase="idle",
            manual_id="abc",
            last_run_started="2026-10-27T02:40:00Z",
            last_run_finished="2026-10-27T02:40:30Z",
            last_exit_code=0,
        )
        manual_update.KICK_PATH.write_text(
            json.dumps(
                {
                    "id": "abc",
                    "state": "running",
                    "attached": False,
                    "requested_at": "2026-10-27T02:40:00Z",
                    "started_at": "2026-10-27T02:40:00Z",
                }
            ),
            encoding="utf-8",
        )
        body = self.client.get("/api/update-now").get_json()
        self.assertEqual(body["state"], "done")
        self.assertEqual(body["exit_code"], 0)
        self.assertGreater(body["retry_after_s"], 0)

    def test_sleep_wakes_for_a_kick(self):
        status = {
            "running": True,
            "pid": os.getpid(),
            "phase": "idle",
            "interval_min": 5,
        }

        def later():
            time.sleep(0.35)
            code, body = manual_update.request_update(status, {})
            self.assertEqual(code, 202, body)

        threading.Thread(target=later, daemon=True).start()
        t0 = time.time()
        why = bot.interruptible_sleep(8)
        self.assertEqual(why, "kick")
        self.assertLess(time.time() - t0, 3)


if __name__ == "__main__":
    unittest.main()
