"""SHIP T — Game Edit profile custom picture.

Not a QA PASS. AC-A01…A04 stay gated on a live panel_v 80 screenshot pass.
"""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import game
import server


PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG = "data:image/png;base64," + PNG_B64


def _data_url(mime: str, blob: bytes) -> str:
    return "data:" + mime + ";base64," + base64.b64encode(blob).decode("ascii")


def _jpeg() -> str:
    return _data_url("image/jpeg", b"\xff\xd8\xff" + b"\x00" * 16)


def _webp() -> str:
    return _data_url("image/webp", b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8)


class ProfileCustomPicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patch = mock.patch.object(game, "PROFILE_PATH", root / "profile.json")
        patch.start()
        self.addCleanup(patch.stop)

    def test_custom_still_saves_on_the_paper_profile_and_preset_clears_it(self):
        saved = game.update_profile(name="Pilot", pfp="custom", pfp_data=PNG)
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["profile"]["pfp"], "custom")
        self.assertEqual(saved["profile"]["pfp_data"], PNG)
        self.assertEqual(saved["profile"]["name"], "Pilot")
        self.assertEqual(saved["profile"]["pfps"], ["rocket", "star", "flame", "moon"])
        stored = game.load_profile()
        self.assertEqual(stored["pfp"], "custom")
        self.assertEqual(stored["pfp_data"], PNG)
        self.assertEqual(stored["points"], 0)

        for preset in ("rocket", "star", "flame", "moon"):
            again = game.update_profile(name="Pilot", pfp=preset)
            self.assertTrue(again["ok"], preset)
            self.assertEqual(again["profile"]["pfp"], preset)
            self.assertEqual(again["profile"]["pfp_data"], "")
            self.assertEqual(game.load_profile()["pfp_data"], "")
            game.update_profile(pfp="custom", pfp_data=PNG)

        jpeg = game.update_profile(pfp="custom", pfp_data="  " + _jpeg() + "\n")
        self.assertTrue(jpeg["ok"])
        self.assertTrue(jpeg["profile"]["pfp_data"].startswith("data:image/jpeg;base64,"))
        webp = game.update_profile(pfp="custom", pfp_data=_webp())
        self.assertTrue(webp["ok"])
        self.assertTrue(webp["profile"]["pfp_data"].startswith("data:image/webp;base64,"))

    def test_bad_or_oversized_file_keeps_the_last_good_avatar(self):
        first = game.update_profile(name="Ada", pfp="moon")
        self.assertTrue(first["ok"])
        cases = {
            "text": "data:text/plain;base64,aGVsbG8=",
            "gif": _data_url("image/gif", b"GIF89a" + b"\x00" * 8),
            "svg": "data:image/svg+xml;base64," + base64.b64encode(b"<svg></svg>").decode(),
            "html": "data:image/png;base64," + base64.b64encode(b"<html></html>").decode(),
            "empty": "data:image/png;base64,",
            "truncated": "not-a-data-url",
            "huge": "data:image/png;base64," + ("A" * game.PFP_DATA_MAX_CHARS),
        }
        for label, raw in cases.items():
            denied = game.update_profile(name="Bea", pfp="custom", pfp_data=raw)
            self.assertFalse(denied["ok"], label)
            self.assertIn("profile", denied)
            self.assertEqual(denied["profile"]["pfp"], "moon", label)
            self.assertEqual(denied["profile"]["name"], "Ada", label)
            self.assertEqual(game.load_profile()["pfp"], "moon", label)
            self.assertEqual(game.load_profile()["name"], "Ada", label)
            self.assertEqual(game.load_profile()["pfp_data"], "", label)
        self.assertIn("too large", cases and game.update_profile(pfp="custom", pfp_data=cases["huge"])["error"])
        self.assertIn("not a valid image", game.update_profile(pfp="custom", pfp_data=cases["text"])["error"])
        self.assertFalse(game.update_profile(name="Pilot", pfp="nope")["ok"])

        game.update_profile(pfp="custom", pfp_data=PNG)
        broken = {"name": "Ada", "pfp": "custom", "pfp_data": cases["text"], "points": 12}
        game._write_json(game.PROFILE_PATH, broken)
        loaded = game.load_profile()
        self.assertEqual(loaded["name"], "Ada")
        self.assertEqual(loaded["points"], 12)
        self.assertEqual(loaded["pfp"], "rocket")
        self.assertEqual(loaded["pfp_data"], "")
        self.assertEqual(game.public_profile()["pfp"], "rocket")
        self.assertEqual(game.public_profile()["pfp_data"], "")

    def test_profile_route_rejects_bad_file_and_accepts_png(self):
        client = server.app.test_client()
        bad = client.post("/api/game/profile", json={"name": "Ada", "pfp": "custom", "pfp_data": "data:text/plain;base64,aGVsbG8="})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("not a valid image", bad.get_json()["error"])
        self.assertNotEqual(game.load_profile()["pfp"], "custom")
        ok = client.post("/api/game/profile", json={"name": "Ada", "pfp": "custom", "pfp_data": PNG})
        self.assertEqual(ok.status_code, 200)
        body = ok.get_json()
        self.assertEqual(body["profile"]["pfp"], "custom")
        self.assertEqual(body["state"]["profile"]["pfp_data"], PNG)
        preset = client.post("/api/game/profile", json={"name": "Ada", "pfp": "star"})
        self.assertEqual(preset.status_code, 200)
        self.assertEqual(preset.get_json()["profile"]["pfp"], "star")
        self.assertEqual(game.load_profile()["pfp_data"], "")

    def test_health_is_v80_with_profile_custom_pic(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 86)
        self.assertIn("profile_custom_pic", data["features"])
        self.assertIn("filter_smart", data["features"])
        self.assertIn("game_follow_tab", data["features"])
        self.assertIn("8 MB", data["profile_custom_pic"])
        self.assertIn("Rocket", data["profile_custom_pic"])
        self.assertIn("Not synced", data["profile_custom_pic"])
        limits = game.public_profile()["pfp_limits"]
        self.assertEqual(limits["max_file_bytes"], 8 * 1024 * 1024)
        self.assertEqual(limits["max_stored_chars"], 120_000)
        self.assertEqual(limits["types"], ["image/png", "image/jpeg", "image/webp"])


class ProfileCustomPicPanelTests(unittest.TestCase):
    def test_edit_profile_offers_file_picker_preview_and_presets(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 86", html)
        self.assertIn("paper only · v85", html)
        self.assertIn("?v=86", html)
        self.assertIn('data-feature="profile_custom_pic"', html)
        self.assertIn('id="pfPhoto"', html)
        self.assertIn(">Use photo<", html)
        self.assertIn('id="pfFileBtn"', html)
        self.assertIn(">Choose from files<", html)
        self.assertIn('id="pfFile"', html)
        self.assertIn('type="file"', html)
        self.assertIn('accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"', html)
        self.assertIn('id="pfPreview"', html)
        self.assertIn('id="pfPicError"', html)
        self.assertIn("Up to 8 MB", html)
        self.assertIn("not synced to other devices", html)
        self.assertIn('data-pfp="rocket"', html)
        self.assertIn('data-pfp="star"', html)
        self.assertIn('data-pfp="flame"', html)
        self.assertIn('data-pfp="moon"', html)
        self.assertIn("const PFP_MAX_FILE_BYTES = 8 * 1024 * 1024", html)
        self.assertIn("const PFP_MAX_DATA_CHARS = 120000", html)
        close = html[html.index("function closeProfile"):html.index("async function saveProfile")]
        self.assertNotIn("fetch(", close)
        self.assertNotIn("/api/game/profile", close)
        picker = html[html.index("async function onProfileFile"):html.index("function paintFinish")]
        self.assertNotIn("/api/game/profile", picker)
        self.assertIn("The current avatar is unchanged", picker)
        save = html[html.index("async function saveProfile"):html.index("async function gameShop")]
        self.assertIn('"/api/game/profile"', save)
        self.assertIn("pfp_data", save)
        self.assertIn("The current avatar is unchanged", save)
