"""SHIP P — post-24h paper review grounded in this run (AC-R01…R05)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import game


BANNED = (
    "this is the one",
    "to the moon",
    "moon'd",
    "mooned",
    "guaranteed",
    "you'd have",
    "you would have",
    "would've",
    "sure thing",
    "you would've",
)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _visible(review: dict) -> str:
    parts = [
        review.get("headline"),
        review.get("summary"),
        review.get("arc"),
        review.get("disclaimer"),
        review.get("humility"),
        review.get("grade_note"),
        review.get("label"),
    ]
    for bullet in review.get("bullets") or []:
        if isinstance(bullet, dict):
            parts.append(bullet.get("tag"))
            parts.append(bullet.get("text"))
    for line in review.get("takeaways") or []:
        parts.append(line)
    return "\n".join(str(p) for p in parts if p)


def _pebble_book() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "session_id": "runpebble",
        "status": "ended",
        "started_at": _utc(now - timedelta(hours=24)),
        "ends_at": _utc(now - timedelta(seconds=5)),
        "ended_at": _utc(now - timedelta(seconds=5)),
        "cash_usd": 5.94,
        "final_cash": 5.94,
        "start_bankroll": 5.0,
        "peak_equity": 6.40,
        "positions": [],
        "closed": [
            {
                "id": "p1",
                "symbol": "PEBBLE",
                "name": "Pebble",
                "pnl_usd": 1.42,
                "pnl_pct": 71.0,
                "close_reason": "tp",
                "stake_usd": 2.0,
                "opened_at": "2026-10-27T14:00:00Z",
                "closed_at": "2026-10-27T14:40:00Z",
            },
            {
                "id": "a1",
                "symbol": "AURUM",
                "name": "Aurum",
                "pnl_usd": -0.48,
                "pnl_pct": -48.0,
                "close_reason": "stop",
                "stake_usd": 1.0,
                "opened_at": "2026-10-27T14:04:00Z",
                "closed_at": "2026-10-27T14:18:00Z",
            },
        ],
        "events": [
            {"at": _utc(now), "kind": "buy", "msg": "DOGE buy · not on this tape"},
        ],
        "disclaimer": "Paper research only. Not financial advice. No real trades.",
    }


class ShipPReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for patch in (
            mock.patch.object(game, "PROFILE_PATH", root / "profile.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
            mock.patch.object(game, "STATE_PATH", root / "state.json"),
            mock.patch.object(game, "fetch_dex_token", return_value=None),
            mock.patch.object(game, "fetch_live_tick", return_value=None),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_grade_bands_label_the_paper_result_only(self):
        self.assertEqual(game.review_grade(36.8), "B+")
        self.assertEqual(game.review_grade(18.8), "B")
        self.assertEqual(game.review_grade(0), "C+")
        self.assertEqual(game.review_grade(-50), "F")
        self.assertIn("not a promise", game.REVIEW_HUMILITY)
        self.assertIn("1 in 58", game.REVIEW_HUMILITY)
        self.assertIn("Fed and CPI", game.REVIEW_HUMILITY)
        self.assertNotIn("this is the one", game.REVIEW_HUMILITY.lower())

    def test_ended_review_cites_this_runs_trades_and_pnl(self):
        game.save_state(_pebble_book())
        out = game.public_state()
        review = out["review"]
        text = _visible(review)
        self.assertEqual(review["status"], "ready")
        self.assertEqual(review["feature"], "game_ai_review")
        self.assertFalse(review["advice"])
        self.assertTrue(review["grounded"])
        self.assertEqual(review["grade"], "B")
        self.assertEqual(review["headline"], "Small paper edge — PEBBLE carried, AURUM leaked")
        self.assertEqual(review["pnl_usd"], out["pnl_usd"])
        self.assertEqual(review["pnl_pct"], out["pnl_pct"])
        self.assertEqual(review["final_equity"], out["equity_usd"])
        self.assertAlmostEqual(review["pnl_usd"], 0.94, places=2)
        self.assertAlmostEqual(review["pnl_pct"], 18.8, places=2)
        self.assertEqual(review["symbols"], ["PEBBLE", "AURUM"])
        cited = {row["symbol"]: row for row in review["cited"]}
        self.assertEqual(cited["PEBBLE"]["pnl_usd"], 1.42)
        self.assertEqual(cited["PEBBLE"]["close_reason"], "tp")
        self.assertEqual(cited["AURUM"]["pnl_usd"], -0.48)
        self.assertEqual(cited["AURUM"]["close_reason"], "stop")
        self.assertIn("PEBBLE", text)
        self.assertIn("AURUM", text)
        self.assertIn("1.42", text)
        self.assertIn("0.48", text)
        self.assertIn("+18.8%", text)
        self.assertIn("20m", text)
        self.assertNotIn("DOGE", text)
        self.assertNotIn("under 5 minutes", text)
        self.assertEqual(review["disclaimer"], game.REVIEW_DISCLAIMER)
        self.assertEqual(review["humility"], game.REVIEW_HUMILITY)
        self.assertIn("Not financial advice", text)
        self.assertIn("Paper research", text)
        self.assertGreaterEqual(len(review["takeaways"]), 1)
        self.assertLessEqual(len(review["takeaways"]), 3)
        self.assertLessEqual(len(review["bullets"]), 5)
        self.assertIn("NOTE", [b["tag"] for b in review["bullets"]])
        low = text.lower()
        for phrase in BANNED:
            self.assertNotIn(phrase, low)
        self.assertLess(len(text), 1600)

    def test_active_run_has_no_review_even_with_closed_trades(self):
        book = _pebble_book()
        book["status"] = "active"
        book["ends_at"] = _utc(datetime.now(timezone.utc) + timedelta(hours=10))
        book.pop("ended_at", None)
        game.save_state(book)
        review = game.public_state()["review"]
        self.assertEqual(review["status"], "none")
        self.assertEqual(review["advice"], False)
        self.assertNotIn("headline", review)
        self.assertNotIn("PEBBLE", json.dumps(review))
        self.assertNotIn("humility", review)

    def test_empty_tape_does_not_invent_a_trade(self):
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "quiet",
            "status": "ended",
            "started_at": _utc(now - timedelta(hours=24)),
            "ends_at": _utc(now),
            "ended_at": _utc(now),
            "cash_usd": 5.0,
            "final_cash": 5.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [],
            "closed": [],
            "events": [{"kind": "end", "msg": "HACHI would have mooned"}],
        })
        review = game.public_state()["review"]
        text = _visible(review)
        self.assertEqual(review["status"], "ready")
        self.assertEqual(review["headline"], "Quiet book — no closed trades")
        self.assertEqual(review["trade_count"], 0)
        self.assertEqual(review["cited"], [])
        self.assertEqual(review["pnl_usd"], 0.0)
        self.assertNotIn("HACHI", text)
        self.assertNotIn("moon", text.lower())
        self.assertIn("No closed trades", text)

    def test_force_end_review_matches_the_close_then_restart_clears_it(self):
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "ghost",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [{
                "id": "poshachi",
                "mint": "DMnMwUVo1a9HbSnixbdNaTsoHczJ8cdamUVRM1Qpump",
                "symbol": "HACHI",
                "name": "HACHI",
                "stake_usd": 1.0,
                "entry_price_usd": 1.0,
                "qty_tokens": 1.0,
                "last_price_usd": 1.0,
                "opened_at": _utc(now - timedelta(minutes=30)),
            }],
            "closed": [],
            "events": [],
        })
        ended = game.force_end_session()
        self.assertEqual(ended["status"], "ended")
        view = game.public_state()
        review = view["review"]
        self.assertEqual(review["status"], "ready")
        self.assertEqual(review["session_id"], "ghost")
        self.assertEqual(review["symbols"], ["HACHI"])
        self.assertEqual(review["cited"][0]["close_reason"], "force_24h")
        self.assertEqual(review["cited"][0]["pnl_usd"], 0.0)
        self.assertEqual(review["pnl_usd"], view["pnl_usd"])
        self.assertEqual(review["pnl_usd"], 0.0)
        self.assertIn("HACHI", review["headline"])
        self.assertNotIn("PEBBLE", _visible(review))
        for phrase in BANNED:
            self.assertNotIn(phrase, _visible(review).lower())

        started = game.start_session(force=False)
        fresh = game.public_state(started)
        self.assertEqual(fresh["status"], "active")
        self.assertNotEqual(fresh["session_id"], "ghost")
        self.assertEqual(fresh["positions"], [])
        self.assertEqual(fresh["closed"], [])
        self.assertEqual(fresh["cash_usd"], 5.0)
        self.assertEqual(fresh["review"]["status"], "none")
        self.assertNotIn("HACHI", json.dumps(fresh["review"]))

    def test_qa_end_route_returns_review_and_does_not_autostart(self):
        import server

        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "ghost",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [{
                "id": "poshachi",
                "mint": "DMnMwUVo1a9HbSnixbdNaTsoHczJ8cdamUVRM1Qpump",
                "symbol": "HACHI",
                "stake_usd": 1.0,
                "entry_price_usd": 1.0,
                "qty_tokens": 1.0,
                "last_price_usd": 1.5,
                "opened_at": _utc(now - timedelta(minutes=10)),
            }],
            "closed": [],
            "events": [],
        })
        client = server.app.test_client()
        res = client.post("/api/game/state?qa_end=1", json={})
        body = res.get_json()
        self.assertEqual(body["status"], "ended")
        self.assertEqual(body["session_id"], "ghost")
        review = body["review"]
        self.assertEqual(review["status"], "ready")
        self.assertEqual(review["symbols"], ["HACHI"])
        self.assertAlmostEqual(review["cited"][0]["pnl_usd"], 0.5, places=2)
        self.assertAlmostEqual(review["pnl_usd"], body["pnl_usd"], places=4)
        self.assertIn("HACHI", _visible(review))
        self.assertIn("0.50", _visible(review))
        follow = client.get("/api/game/state")
        self.assertEqual(follow.get_json()["status"], "ended")
        self.assertEqual(follow.get_json()["review"]["status"], "ready")
        started = client.post("/api/game/start", json={"force": False})
        fresh = started.get_json()["state"]
        self.assertEqual(fresh["status"], "active")
        self.assertNotEqual(fresh["session_id"], "ghost")
        self.assertEqual(fresh["review"]["status"], "none")
        self.assertEqual(fresh["positions"], [])
        self.assertEqual(fresh["cash_usd"], 5.0)

    def test_health_is_v77_with_game_ai_review(self):
        import server

        data = server.app.test_client().get("/api/health").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 90)
        for feature in (
            "game_ai_review",
            "game_24h_restart",
            "hide_max_unknown",
            "qa_max_fixture",
            "trade_chart",
            "sitting_keep",
            "manual_update",
            "dismiss_back",
            "game_polish",
        ):
            self.assertIn(feature, data["features"])
        self.assertIn("qa_end=1", data["qa_end"])


class ShipPPanelTests(unittest.TestCase):
    def test_panel_offers_review_without_trapping_restart(self):
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 90", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("paintPanelVersion", html)
        self.assertNotIn("?v=76", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("data-panel-v", html)
        self.assertIn('id="gReview"', html)
        self.assertIn('id="gReviewSheet"', html)
        self.assertIn('data-feature="game_ai_review"', html)
        self.assertIn('data-blocks-trade="0"', html)
        self.assertIn('aria-modal="false"', html)
        self.assertIn('id="gReviewDismiss"', html)
        self.assertIn('id="gReviewClose"', html)
        self.assertIn('id="gReviewRestart"', html)
        self.assertEqual(html.count('id="gStart"'), 1)
        self.assertIn('gReviewRestart.addEventListener("click", gameStart)', html)
        self.assertIn("Restart and wipe current 24h run + open positions?", html)
        sheet = html[html.index('id="gReviewSheet"'):html.index('id="gRocket"')]
        self.assertNotIn('id="gClosed"', sheet)
        self.assertNotIn('id="gSellAll"', sheet)
        self.assertNotIn('id="gStart"', sheet)
        pure = html[html.index("/* ship-p-pure-start */"):html.index("/* ship-p-pure-end */")]
        script = pure + r"""
const ended = reviewSheetDecision({
  status: "ended",
  session_id: "runpebble",
  review: { status: "ready" },
}, "");
if (!ended.offer || !ended.open || ended.trap || ended.dismissed) process.exit(2);
const dismissed = reviewSheetDecision({
  status: "ended",
  session_id: "runpebble",
  review: { status: "ready" },
}, "runpebble");
if (!dismissed.offer || dismissed.open || dismissed.trap || !dismissed.dismissed) process.exit(3);
const live = reviewSheetDecision({
  status: "active",
  session_id: "runpebble",
  review: { status: "none" },
}, "");
if (live.offer || live.open || live.trap) process.exit(4);
const quiet = reviewCueDecision(true);
if (quiet.play || !quiet.visual || quiet.loop || quiet.kind !== "review") process.exit(5);
const loud = reviewCueDecision(false);
if (!loud.play || !loud.visual || loud.loop) process.exit(6);
const blob = [REVIEW_COOKING.title, REVIEW_COOKING.summary, REVIEW_COOKING.disclaimer, REVIEW_COOKING.note].join(" ");
if (!reviewCopyAllowed(blob)) process.exit(7);
if (reviewCopyAllowed("this is the one")) process.exit(8);
if (reviewCopyAllowed("you would've mooned")) process.exit(9);
if (REVIEW_COOK_MS < 400 || REVIEW_COOK_MS > 1500) process.exit(10);
"""
        subprocess.check_call(["node", "-e", script])


if __name__ == "__main__":
    unittest.main()
