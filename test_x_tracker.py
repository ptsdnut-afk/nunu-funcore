"""SHIP AC — X Tracker (AC-AC01…AC-AC04).

The X API is mocked. panel_v stays 84. Game price ticks are not part of this cut.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import x_tracker

MINT = "5G3pzMiNQxiFctE1aZRTctYJLz5zGdfA1S65QMT5pump"
USER_ID = "18670001"


def _panel() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


class XTrackerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "x_watchlist.json"
        self._saved_token = os.environ.get("X_BEARER_TOKEN")
        self._saved_path = os.environ.get("X_WATCHLIST_PATH")
        os.environ.pop("X_BEARER_TOKEN", None)
        os.environ["X_WATCHLIST_PATH"] = str(self.path)
        self.clock = {"t": 1_000.0}
        x_tracker.clear_memory()
        x_tracker.set_transport(None)
        x_tracker.set_clock(lambda: self.clock["t"])
        import server

        self.client = server.app.test_client()

    def tearDown(self):
        x_tracker.set_transport(None)
        x_tracker.set_clock(None)
        x_tracker.clear_memory()
        if self._saved_token is None:
            os.environ.pop("X_BEARER_TOKEN", None)
        else:
            os.environ["X_BEARER_TOKEN"] = self._saved_token
        if self._saved_path is None:
            os.environ.pop("X_WATCHLIST_PATH", None)
        else:
            os.environ["X_WATCHLIST_PATH"] = self._saved_path
        self._tmp.cleanup()

    def _handles(self) -> list[str]:
        res = self.client.get("/api/x/watchlist")
        self.assertEqual(res.status_code, 200)
        return [row["handle"] for row in res.get_json()["handles"]]

    def _fresh_process_handles(self) -> list[str]:
        env = os.environ.copy()
        env["X_WATCHLIST_PATH"] = str(self.path)
        env.pop("X_BEARER_TOKEN", None)
        out = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import json,os,x_tracker; print(json.dumps(x_tracker.load_handles()))",
            ],
            cwd=str(Path(__file__).resolve().parent),
            env=env,
        )
        return json.loads(out.decode("utf-8"))


class WatchlistPersistenceTests(XTrackerCase):
    def test_ac_ac01_add_and_remove_persist_across_reload_and_restart(self):
        seeded = self._handles()
        self.assertEqual(seeded, ["slingoorio"])
        self.assertTrue(self.path.exists())

        added = self.client.post("/api/x/watchlist", json={"handle": "@SomeTrader"})
        self.assertEqual(added.status_code, 200)
        body = added.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["handle"], "sometrader")
        self.assertFalse(body["already"])

        # Reload: a new GET in this process reads the file, not a private copy.
        x_tracker.clear_memory()
        self.assertEqual(self._handles(), ["slingoorio", "sometrader"])
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))["handles"]
        self.assertEqual(on_disk, ["slingoorio", "sometrader"])

        # Restart: a new interpreter loads the same file.
        self.assertEqual(self._fresh_process_handles(), ["slingoorio", "sometrader"])

        removed = self.client.delete("/api/x/watchlist", query_string={"handle": "@SomeTrader"})
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.get_json()["removed"])
        x_tracker.clear_memory()
        self.assertEqual(self._handles(), ["slingoorio"])
        self.assertEqual(self._fresh_process_handles(), ["slingoorio"])
        self.assertNotIn("sometrader", self.path.read_text(encoding="utf-8"))

        # Removing the seed sticks too. A restart must not put it back.
        dropped = self.client.delete("/api/x/watchlist", query_string={"handle": "slingoorio"})
        self.assertEqual(dropped.status_code, 200)
        self.assertEqual(self._fresh_process_handles(), [])
        self.assertEqual(self._handles(), [])

    def test_seed_add_is_idempotent_and_case_insensitive(self):
        again = self.client.post("/api/x/watchlist", json={"handle": "@Slingoorio"})
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.get_json()["already"])
        self.assertEqual(self._handles(), ["slingoorio"])


class FeedTests(XTrackerCase):
    def _install(self, tweets, spaces=None, users=None, missing=()):
        os.environ["X_BEARER_TOKEN"] = "test-bearer-not-a-real-token"
        spaces = [] if spaces is None else spaces
        users = users or [
            {
                "id": USER_ID,
                "name": "Slingoor",
                "username": "slingoorio",
                "profile_image_url": "https://example.test/a.png",
            }
        ]
        calls = []

        def transport(path, params):
            calls.append((path, dict(params)))
            if path == "users/by":
                errors = [
                    {
                        "value": handle,
                        "title": "Not Found Error",
                        "detail": f"Could not find user with usernames: [{handle}].",
                        "type": "https://api.twitter.com/2/problems/resource-not-found",
                    }
                    for handle in missing
                ]
                body = {"data": list(users)}
                if errors:
                    body["errors"] = errors
                return 200, body
            if path.startswith("users/") and path.endswith("/tweets"):
                return 200, {"data": list(tweets)}
            if path == "spaces/by/creator_ids":
                return 200, {"data": list(spaces)}
            if path.startswith("users/by/username/"):
                handle = path.rsplit("/", 1)[-1]
                for user in users:
                    if user["username"].lower() == handle.lower():
                        return 200, {"data": user}
                return 404, {
                    "errors": [
                        {
                            "value": handle,
                            "title": "Not Found Error",
                            "detail": f"Could not find user with username: [{handle}].",
                            "type": "https://api.twitter.com/2/problems/resource-not-found",
                        }
                    ]
                }
            return 500, {"errors": [{"detail": "unexpected " + path}]}

        x_tracker.set_transport(transport)
        return calls

    def test_ac_ac02_feed_shows_a_new_post_inside_the_two_minute_window(self):
        html = _panel()
        match_at = html.index("const X_POLL_MS = ")
        poll_line = html[match_at:html.index("\n", match_at)]
        poll_ms = int(poll_line.split("=", 1)[1].strip().rstrip(";"))
        self.assertGreater(poll_ms, 0)
        self.assertLessEqual(x_tracker.REFRESH_SEC * 1000 + poll_ms, 120_000)
        self.assertLessEqual(x_tracker.REFRESH_SEC, 120)
        self.assertIn("setInterval(loadXTracker, X_POLL_MS)", html)

        tweets = [
            {
                "id": "100",
                "text": "older note",
                "created_at": "2026-10-31T06:00:00.000Z",
                "public_metrics": {"like_count": 1, "retweet_count": 0, "reply_count": 0},
            }
        ]
        calls = self._install(tweets)
        first = self.client.get("/api/x/feed")
        self.assertEqual(first.status_code, 200)
        payload = first.get_json()
        self.assertTrue(payload["configured"])
        self.assertEqual([post["id"] for post in payload["posts"]], ["100"])
        self.assertEqual(payload["refresh_sec"], x_tracker.REFRESH_SEC)
        self.assertNotIn("test-bearer-not-a-real-token", first.get_data(as_text=True))
        tweet_calls = [path for path, _params in calls if path.endswith("/tweets")]
        self.assertEqual(len(tweet_calls), 1)

        tweets.append(
            {
                "id": "200",
                "text": "just posted",
                "created_at": "2026-10-31T06:01:30.000Z",
                "public_metrics": {"like_count": 3, "retweet_count": 1, "reply_count": 0},
            }
        )
        # Still inside the server cache: the new post is not due yet.
        self.clock["t"] += x_tracker.REFRESH_SEC - 1
        cached = self.client.get("/api/x/feed").get_json()
        self.assertEqual([post["id"] for post in cached["posts"]], ["100"])
        self.assertEqual(len([path for path, _params in calls if path.endswith("/tweets")]), 1)

        # Cache expires at REFRESH_SEC. The next read shows the new post.
        self.clock["t"] += 1
        fresh = self.client.get("/api/x/feed").get_json()
        self.assertEqual([post["id"] for post in fresh["posts"]], ["200", "100"])
        self.assertEqual(fresh["posts"][0]["text"], "just posted")
        self.assertEqual(len([path for path, _params in calls if path.endswith("/tweets")]), 2)

    def test_missing_token_is_x_not_configured_and_never_fakes_posts(self):
        called = []

        def transport(path, params):
            called.append(path)
            return 200, {"data": [{"id": "999", "text": "invented post", "created_at": "2026-10-31T06:00:00.000Z"}]}

        x_tracker.set_transport(transport)
        res = self.client.get("/api/x/feed")
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["error"], "X not configured")
        self.assertEqual(payload["posts"], [])
        self.assertIn("X_BEARER_TOKEN", payload["detail"])
        self.assertEqual(called, [])
        self.assertNotIn("invented post", res.get_data(as_text=True))
        self.assertIn("No posts are invented", res.get_data(as_text=True))

    def test_ac_ac03_a_post_with_a_ca_is_marked_for_the_drawer_chip(self):
        text = f"ape $Bonk now {MINT} before it moves"
        self._install(
            [
                {
                    "id": "300",
                    "text": text,
                    "created_at": "2026-10-31T06:02:00.000Z",
                    "public_metrics": {"like_count": 4, "retweet_count": 0, "reply_count": 1},
                },
                {
                    "id": "301",
                    "text": "no coin talk here",
                    "created_at": "2026-10-31T06:03:00.000Z",
                },
            ]
        )
        posts = self.client.get("/api/x/feed").get_json()["posts"]
        by_id = {post["id"]: post for post in posts}
        hit = by_id["300"]
        self.assertEqual(hit["cas"], [MINT])
        self.assertEqual(hit["tickers"], ["BONK"])
        self.assertTrue(hit["highlight"])
        quiet = by_id["301"]
        self.assertEqual(quiet["cas"], [])
        self.assertEqual(quiet["tickers"], [])
        self.assertFalse(quiet["highlight"])

        html = _panel()
        chip = html[html.index("function xChips"):html.index("function xLiveBadge")]
        self.assertIn('class="x-ca-chip"', chip)
        self.assertIn('data-mint="', chip)
        self.assertIn('data-opens="coin-drawer"', chip)
        click = html[html.index('closest(".x-ca-chip")'):html.index('closest(".x-remove")')]
        self.assertIn("openCoinFromMint(chip.getAttribute(\"data-mint\")", click)
        self.assertIn('fetch("/api/coin/checklist?mint="', html)

    def test_ticker_only_highlights_without_a_chip_source(self):
        marks = x_tracker.annotate("watching $WIF and $sol today")
        self.assertEqual(marks["tickers"], ["WIF", "SOL"])
        self.assertEqual(marks["cas"], [])
        self.assertTrue(marks["highlight"])
        self.assertFalse(x_tracker.annotate("plain sentence").get("highlight"))

    def test_live_space_badge_only_when_the_handle_is_hosting(self):
        self._install(
            tweets=[
                {
                    "id": "400",
                    "text": "we are live",
                    "created_at": "2026-10-31T06:04:00.000Z",
                }
            ],
            spaces=[
                {
                    "id": "sp-live",
                    "state": "live",
                    "title": "Chart talk",
                    "host_ids": [USER_ID],
                    "creator_id": USER_ID,
                }
            ],
        )
        live = self.client.get("/api/x/feed").get_json()
        space = live["handles"][0]["space"]
        self.assertEqual(space["state"], "live")
        self.assertTrue(space["live"])
        self.assertEqual(space["title"], "Chart talk")

        x_tracker.expire_feed_cache()
        self._install(
            tweets=[
                {
                    "id": "401",
                    "text": "space ended",
                    "created_at": "2026-10-31T06:05:00.000Z",
                }
            ],
            spaces=[
                {
                    "id": "sp-end",
                    "state": "ended",
                    "title": "Old",
                    "host_ids": [USER_ID],
                    "creator_id": USER_ID,
                }
            ],
        )
        ended = self.client.get("/api/x/feed").get_json()
        self.assertIsNone(ended["handles"][0]["space"])
        self.assertEqual(ended["posts"][0]["id"], "401")

    def test_spaces_failure_leaves_the_badge_out_and_keeps_posts(self):
        os.environ["X_BEARER_TOKEN"] = "test-bearer-not-a-real-token"

        def transport(path, params):
            if path == "users/by":
                return 200, {
                    "data": [{"id": USER_ID, "name": "Slingoor", "username": "slingoorio"}]
                }
            if path.endswith("/tweets"):
                return 200, {
                    "data": [
                        {
                            "id": "500",
                            "text": "still here",
                            "created_at": "2026-10-31T06:06:00.000Z",
                        }
                    ]
                }
            if path == "spaces/by/creator_ids":
                return 403, {"title": "Forbidden"}
            return 500, None

        x_tracker.set_transport(transport)
        payload = self.client.get("/api/x/feed").get_json()
        self.assertIsNone(payload["handles"][0]["space"])
        self.assertEqual(payload["posts"][0]["text"], "still here")
        self.assertIsNone(payload["error"])

    def test_referenced_tweet_ca_still_becomes_a_chip(self):
        self._install([])
        # Replace the tweets response with a quote that carries the CA on the included post.
        def transport(path, params):
            if path == "users/by":
                return 200, {"data": [{"id": USER_ID, "name": "Slingoor", "username": "slingoorio"}]}
            if path.endswith("/tweets"):
                return 200, {
                    "data": [
                        {
                            "id": "600",
                            "text": "this",
                            "created_at": "2026-10-31T06:07:00.000Z",
                            "referenced_tweets": [{"type": "quoted", "id": "601"}],
                        }
                    ],
                    "includes": {
                        "tweets": [
                            {"id": "601", "text": f"the mint is {MINT}"},
                        ]
                    },
                }
            if path == "spaces/by/creator_ids":
                return 200, {"data": []}
            return 500, None

        x_tracker.set_transport(transport)
        x_tracker.expire_feed_cache()
        post = self.client.get("/api/x/feed").get_json()["posts"][0]
        self.assertEqual(post["cas"], [MINT])
        self.assertTrue(post["highlight"])
        self.assertIn(MINT, post["text"])


class BadHandleTests(XTrackerCase):
    def test_ac_ac04_bad_handle_is_a_clear_error_and_is_not_saved(self):
        before = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        # Touch the seed file first so a rejected add cannot be confused with seeding.
        self.assertEqual(self._handles(), ["slingoorio"])
        before = self.path.read_text(encoding="utf-8")
        for raw in ("", "@", "bad handle", "has.dot", "waytoolongname12", "nope!", "@@@@"):
            res = self.client.post("/api/x/watchlist", json={"handle": raw})
            self.assertEqual(res.status_code, 400, raw)
            body = res.get_json()
            self.assertFalse(body["ok"])
            self.assertEqual(body["error"], x_tracker.BAD_HANDLE_ERROR)
            self.assertIn("@name", body["error"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self._handles(), ["slingoorio"])

        os.environ["X_BEARER_TOKEN"] = "test-bearer-not-a-real-token"

        def transport(path, params):
            handle = path.rsplit("/", 1)[-1]
            return 404, {
                "errors": [
                    {
                        "value": handle,
                        "title": "Not Found Error",
                        "detail": f"Could not find user with username: [{handle}].",
                        "type": "https://api.twitter.com/2/problems/resource-not-found",
                    }
                ]
            }

        x_tracker.set_transport(transport)
        missing = self.client.post("/api/x/watchlist", json={"handle": "@nopeuser"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json()["error"], "No X account for @nopeuser.")
        self.assertEqual(self._handles(), ["slingoorio"])
        self.assertNotIn("nopeuser", self.path.read_text(encoding="utf-8"))

    def test_a_bad_handle_already_on_the_list_shows_on_the_feed(self):
        self.client.post("/api/x/watchlist", json={"handle": "@ghostuser"})
        self.assertIn("ghostuser", self._handles())
        os.environ["X_BEARER_TOKEN"] = "test-bearer-not-a-real-token"

        def transport(path, params):
            if path == "users/by":
                return 200, {
                    "data": [{"id": USER_ID, "name": "Slingoor", "username": "slingoorio"}],
                    "errors": [
                        {
                            "value": "ghostuser",
                            "title": "Not Found Error",
                            "detail": "Could not find user with usernames: [ghostuser].",
                            "type": "https://api.twitter.com/2/problems/resource-not-found",
                        }
                    ],
                }
            if path.endswith("/tweets"):
                return 200, {
                    "data": [
                        {
                            "id": "700",
                            "text": "real post",
                            "created_at": "2026-10-31T06:08:00.000Z",
                        }
                    ]
                }
            if path == "spaces/by/creator_ids":
                return 200, {"meta": {"result_count": 0}}
            return 500, None

        x_tracker.set_transport(transport)
        payload = self.client.get("/api/x/feed").get_json()
        by_handle = {row["handle"]: row for row in payload["handles"]}
        self.assertEqual(by_handle["ghostuser"]["error"], "No X account for @ghostuser.")
        self.assertIsNone(by_handle["slingoorio"]["error"])
        self.assertEqual(payload["posts"][0]["text"], "real post")


class PanelContractTests(XTrackerCase):
    def test_health_keeps_panel_v_and_names_the_token(self):
        import server

        health = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(health["panel_v"], 84)
        self.assertIn("x_tracker", health["features"])
        self.assertIn("coin_checklist", health["features"])
        self.assertIn("live_tick", health["features"])
        self.assertIn("X_BEARER_TOKEN", health["x_tracker"])
        self.assertIn("X not configured", health["x_tracker"])
        self.assertNotIn("posting", health["x_tracker"].split("Read only")[0])

    def test_panel_tab_unconfigured_state_and_no_fake_seed_post(self):
        html = _panel()
        self.assertIn('id="tabX"', html)
        self.assertIn(">X Tracker<", html)
        self.assertIn('id="modeX"', html)
        self.assertIn("X not configured", html)
        self.assertIn("No posts are invented", html)
        self.assertIn('id="xHandleError"', html)
        self.assertIn("showXHandleError", html)
        self.assertIn("configured === false", html)
        self.assertIn('const PANEL_V = 84', html)
        self.assertNotIn("invented post", html)
        badge = html[html.index("function xLiveBadge"):html.index("function renderXText")]
        self.assertIn("space.live !== true", badge)
        self.assertIn('class="x-live"', badge)
        self.assertIn('return ""', badge)


if __name__ == "__main__":
    unittest.main()
