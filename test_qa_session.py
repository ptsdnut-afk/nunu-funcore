"""QA requests use a separate paper book and do not touch the owner's cash or points."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import game
import server


class QaBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for patch in (
            mock.patch.object(game, "STATE_PATH", root / "game_state.json"),
            mock.patch.object(game, "PROFILE_PATH", root / "game_profile.json"),
            mock.patch.object(game, "SCORES_PATH", root / "game_scores.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        game.start_session(force=True)
        st = game.load_state()
        st["cash_usd"] = 4.99
        game.save_state(st)
        prof = game.load_profile()
        prof["points"] = -750
        game.save_profile(prof)

    def test_qa_header_and_query_leave_the_owner_book_alone(self):
        client = server.app.test_client()
        for call in (
            lambda: client.get("/api/game/state?qa=1"),
            lambda: client.get("/api/game/state", headers={"X-Nunu-QA": "1"}),
            lambda: client.get("/api/game/state?qa_tick=up"),
        ):
            res = call()
            self.assertEqual(res.status_code, 200)
            body = res.get_json()
            self.assertEqual(body.get("cash_usd"), game.START_BANKROLL)
            self.assertNotEqual(body.get("session_id"), game.load_state().get("session_id"))
        owner = game.load_state()
        self.assertEqual(owner["cash_usd"], 4.99)
        self.assertEqual(game.load_profile()["points"], -750)
        token = game._book.set("qa")
        try:
            qa = game.load_state()
            qa["cash_usd"] = 1.25
            game.save_state(qa)
            prof = game.load_profile()
            prof["points"] = 40
            game.save_profile(prof)
        finally:
            game._book.reset(token)
        self.assertEqual(game.load_state()["cash_usd"], 4.99)
        self.assertEqual(game.load_profile()["points"], -750)


if __name__ == "__main__":
    unittest.main()
