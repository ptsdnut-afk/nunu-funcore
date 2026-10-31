"""SHIP AB — VECTOR and LEDGER paper race (AC-AB01…AB07). panel_v is not bumped."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import checklist_adapter
import game
import game_ai

_REAL_CHECKLIST = checklist_adapter.checklist_for_mint


SKIP_MINT = "3KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
GOOD_MINT = "4KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
WEAK_MINT = "5KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
UNKNOWN_MINT = "6KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"


def _tick(mint: str, price: float, age: float) -> dict:
    return {
        "mint": mint,
        "price_usd": price,
        "quote_age_s": age,
        "quote_live": age <= game.QUOTE_FRESH_SLA_S,
        "price_ts": "2026-10-31T18:00:00Z",
        "quoted_epoch": time.time() - age,
        "source": "jup",
    }


def _row(mint: str, symbol: str, m5: float) -> dict:
    return {
        "mint": mint,
        "ticker": symbol,
        "name": symbol,
        "metrics": {"price_change_m5": m5},
    }


def _card(verdict: str, passes, checks: int = 8) -> dict:
    return {
        "ok": verdict != "unknown" and isinstance(passes, int),
        "verdict": verdict,
        "pass_count": passes,
        "check_count": checks,
        "source": "test",
    }


def _pattern(epoch: float, mint: str, dip: float) -> list[dict]:
    """Entry, a dip, then a rip. Only the entry is a signal."""
    base = {"mint": mint, "symbol": "PATH", "verdict": "SKIP", "pass_count": 4, "check_count": 8}
    return [
        {**base, "epoch": epoch, "price": 1.0, "m5": 12.0, "signal": True},
        {**base, "epoch": epoch + 10, "price": dip, "m5": None, "signal": False},
        {**base, "epoch": epoch + 20, "price": 1.50, "m5": None, "signal": False},
    ]


class ShipABTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.flagged = root / "flagged.json"
        self.flagged.write_text("[]", encoding="utf-8")
        for patch in (
            mock.patch.object(game, "PROFILE_PATH", root / "profile.json"),
            mock.patch.object(game, "SCORES_PATH", root / "scores.json"),
            mock.patch.object(game, "STATE_PATH", root / "state.json"),
            mock.patch.object(game, "FLAGGED_PATH", self.flagged),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.prices = {game.SOL_MINT: (150.0, 0.2)}
        self.cards = {}

        def fake_tick(mint, bypass_cache=False, qa_sid=None):
            spec = self.prices.get(mint)
            if not spec:
                return None
            price, age = spec
            return _tick(mint, price, age)

        def fake_card(mint):
            return dict(self.cards.get(mint) or _card("unknown", None))

        tick_patch = mock.patch.object(game, "fetch_live_tick", side_effect=fake_tick)
        card_patch = mock.patch.object(checklist_adapter, "checklist_for_mint", side_effect=fake_card)
        tick_patch.start()
        card_patch.start()
        self.addCleanup(tick_patch.stop)
        self.addCleanup(card_patch.stop)

    def _write_rows(self, rows: list[dict]) -> None:
        self.flagged.write_text(json.dumps(rows), encoding="utf-8")

    def _start(self) -> dict:
        return game.start_session(force=True)

    def _step(self, now: float) -> dict:
        st = game.load_state()
        game_ai.step(st, now=now)
        game.save_state(st)
        return st

    def test_adapter_is_unknown_when_checklist_is_missing(self):
        got = _REAL_CHECKLIST(UNKNOWN_MINT)
        self.assertEqual(got["verdict"], "unknown")
        self.assertIsNone(got["pass_count"])
        self.assertEqual(got["source"], "missing")
        self.assertFalse(game_ai.ledger_allows(got, 7))

    def test_ledger_bars(self):
        self.assertFalse(game_ai.ledger_allows(_card("unknown", None), 7))
        self.assertFalse(game_ai.ledger_allows(_card("SKIP", 8), 7))
        self.assertFalse(game_ai.ledger_allows(_card("WATCH", 6), 7))
        self.assertFalse(game_ai.ledger_allows(_card("WATCH", 7), 8))
        self.assertTrue(game_ai.ledger_allows(_card("WATCH", 7), 7))
        self.assertTrue(game_ai.ledger_allows(_card("BUY-WATCH", 8), 7))
        clamped = game_ai._clamp_settings("LEDGER", {"min_verdict": 0, "stop_pct": 12})
        self.assertGreaterEqual(clamped["min_verdict"], 7)

    def test_both_ais_fill_filter_coins_with_receipts(self):
        self._write_rows([
            _row(SKIP_MINT, "SKIPR", 30),
            _row(GOOD_MINT, "LEDGR", 9),
        ])
        self.cards[SKIP_MINT] = _card("SKIP", 4)
        self.cards[GOOD_MINT] = _card("BUY-WATCH", 8)
        self._start()
        armed = self._step(1_000)
        self.assertIsNotNone(armed["ai_books"]["VECTOR"]["pending"])
        self.assertIsNotNone(armed["ai_books"]["LEDGER"]["pending"])
        self.assertEqual(armed["ai_books"]["VECTOR"]["positions"], [])
        self.assertEqual(armed["ai_books"]["LEDGER"]["pending"]["mint"], GOOD_MINT)
        self.assertEqual(armed["ai_books"]["VECTOR"]["pending"]["mint"], SKIP_MINT)

        early = self._step(1_002)
        self.assertEqual(early["ai_books"]["VECTOR"]["positions"], [])
        self.assertEqual(early["ai_books"]["LEDGER"]["positions"], [])

        self.prices[SKIP_MINT] = (1.0, 1.25)
        self.prices[GOOD_MINT] = (2.0, 0.4)
        filled = self._step(1_003)
        vector = filled["ai_books"]["VECTOR"]["positions"]
        ledger = filled["ai_books"]["LEDGER"]["positions"]
        self.assertEqual(len(vector), 1)
        self.assertEqual(len(ledger), 1)
        self.assertEqual(vector[0]["mint"], SKIP_MINT)
        self.assertEqual(ledger[0]["mint"], GOOD_MINT)
        receipts = {rec["trader"]: rec for rec in filled["receipts"] if rec.get("trader")}
        self.assertEqual(receipts["VECTOR"]["verdict"], "SKIP")
        self.assertTrue(receipts["VECTOR"]["skip_flag"])
        self.assertEqual(receipts["VECTOR"]["quote_age_s"], 1.25)
        self.assertIn("momentum", receipts["VECTOR"]["entry_reason"])
        self.assertEqual(receipts["LEDGER"]["verdict"], "BUY-WATCH")
        self.assertEqual(receipts["LEDGER"]["pass_count"], 8)
        self.assertEqual(receipts["LEDGER"]["quote_age_s"], 0.4)
        self.assertLessEqual(receipts["VECTOR"]["quote_age_s"], game.QUOTE_FRESH_SLA_S)
        self.assertLessEqual(receipts["LEDGER"]["quote_age_s"], game.QUOTE_FRESH_SLA_S)
        fills = [row for row in filled["ai_log"] if row.get("kind") == "fill"]
        self.assertGreaterEqual(len(fills), 2)
        for row in fills:
            self.assertLessEqual(row["quote_age_s"], 5)
            self.assertGreaterEqual(row["delay_s"], 3)
            self.assertLessEqual(row["trades_this_hour"], 6)
            self.assertIn("5s", row["msg"])
            self.assertIn("delay", row["msg"])

    def test_stale_quote_and_hour_cap_are_logged(self):
        self._write_rows([_row(SKIP_MINT, "SKIPR", 30)])
        self.cards[SKIP_MINT] = _card("SKIP", 4)
        self._start()
        self._step(2_000)
        self.prices[SKIP_MINT] = (1.0, 9.0)
        stale = self._step(2_003)
        self.assertEqual(stale["ai_books"]["VECTOR"]["positions"], [])
        kinds = [row.get("kind") for row in stale["ai_log"]]
        self.assertIn("stale", kinds)
        stale_row = next(row for row in stale["ai_log"] if row.get("kind") == "stale")
        self.assertGreater(stale_row["quote_age_s"], 5)
        self.assertIn("5s", stale_row["msg"])

        st = game.load_state()
        st["ai_books"]["VECTOR"]["trade_epochs"] = [3_000 - 5] * 6
        st["ai_books"]["VECTOR"]["pending"] = None
        game.save_state(st)
        capped = self._step(3_000)
        self.assertIsNone(capped["ai_books"]["VECTOR"]["pending"])
        self.assertEqual(capped["ai_books"]["VECTOR"]["positions"], [])
        rate = next(row for row in capped["ai_log"] if row.get("kind") == "rate")
        self.assertGreaterEqual(rate["trades_this_hour"], 6)
        self.assertIn("6", rate["msg"])

    def test_ledger_never_buys_skip_or_under_seven(self):
        self._write_rows([
            _row(SKIP_MINT, "SKIPR", 40),
            _row(WEAK_MINT, "WEAK", 20),
            _row(UNKNOWN_MINT, "UNK", 12),
        ])
        self.cards[SKIP_MINT] = _card("SKIP", 8)
        self.cards[WEAK_MINT] = _card("WATCH", 6)
        self.cards[UNKNOWN_MINT] = _card("unknown", None)
        self._start()
        self.prices[SKIP_MINT] = (1.0, 0.2)
        self.prices[WEAK_MINT] = (1.0, 0.2)
        self.prices[UNKNOWN_MINT] = (1.0, 0.2)
        self._step(4_000)
        filled = self._step(4_003)
        ledger = filled["ai_books"]["LEDGER"]
        self.assertEqual(ledger["positions"], [])
        self.assertIsNone(ledger["pending"])
        vector_mints = [p["mint"] for p in filled["ai_books"]["VECTOR"]["positions"]]
        self.assertEqual(vector_mints, [SKIP_MINT])
        self.assertEqual(filled["receipts"][0]["verdict"], "SKIP")
        for rec in filled["receipts"]:
            if rec.get("trader") == "LEDGER":
                self.fail("LEDGER wrote a receipt")

    def test_scoreboard_ranks_by_percent_and_race_grows(self):
        self._start()
        first = self._step(5_000)
        second = self._step(5_002)
        self.assertGreaterEqual(len(second["ai_race"]), 2)
        self.assertGreater(len(second["ai_race"]), len(first["ai_race"]))
        st = game.load_state()
        st["ai_books"]["VECTOR"]["cash_usd"] = 6.0
        st["ai_books"]["VECTOR"]["max_drawdown_pct"] = 4.5
        game.save_state(st)
        view = game.public_state(game.load_state())
        board = view["scoreboard"]
        self.assertEqual(board["rank_by"], "pnl_pct")
        self.assertEqual(board["rows"][0]["id"], "VECTOR")
        for row in board["rows"]:
            self.assertIn("pnl_pct", row)
            self.assertIn("pnl_usd", row)
            self.assertIn("max_drawdown_pct", row)
        self.assertAlmostEqual(board["rows"][0]["pnl_pct"], 20.0, places=2)
        self.assertAlmostEqual(board["rows"][0]["pnl_usd"], 1.0, places=2)
        self.assertAlmostEqual(board["rows"][0]["max_drawdown_pct"], 4.5, places=2)
        self.assertGreaterEqual(len(board["race"]), 2)
        hooked = game.tick_session()
        self.assertGreater(len(hooked["ai_race"]), len(second["ai_race"]))

    def test_learning_keeps_a_winner_and_logs_the_split(self):
        self._start()
        st = game.load_state()
        tape = []
        for i, epoch in enumerate((10_000, 10_100, 10_200)):
            tape.extend(_pattern(epoch, SKIP_MINT, 0.815))
        st["ai_feed"] = tape
        trade = {
            "symbol": "SKIPR",
            "exit_reason": "stop",
            "close_reason": "stop",
            "pnl_usd": -0.18,
            "pnl_pct": -18.0,
            "verdict": "SKIP",
            "lesson": "SKIPR stop. Verdict SKIP (4/8). Entry $1. P/L $-0.18 (-18.0%).",
            "closed_at": "2026-10-31T18:04:00Z",
        }
        row = game_ai.apply_learning(st, "VECTOR", trade)
        game.save_state(st)
        self.assertTrue(row["applied"])
        self.assertEqual(row["field"], "stop_pct")
        self.assertAlmostEqual(row["before"], 18.0)
        self.assertAlmostEqual(row["after"], 19.0)
        self.assertIn("widen stop", row["reason"])
        train = row["backtest"]["train"]
        validate = row["backtest"]["validate"]
        self.assertGreater(train["n"], 0)
        self.assertGreater(validate["n"], 0)
        self.assertGreater(validate["proposed_pnl_pct"], validate["current_pnl_pct"])
        self.assertLessEqual(validate["proposed_dd_pct"], 20)
        self.assertIn("current_pnl_pct", train)
        self.assertIn("proposed_pnl_pct", train)
        self.assertEqual(st["ai_settings"]["VECTOR"]["stop_pct"], 19.0)
        saved = game.load_state()
        self.assertEqual(saved["ai_settings_log"][0]["applied"], True)
        self.assertEqual(saved["ai_settings_log"][0]["backtest"]["split"], "2/3 train, 1/3 validate")

    def test_learning_rejects_a_deep_drawdown_and_logs_it(self):
        self._start()
        st = game.load_state()
        st["ai_settings"]["VECTOR"]["stop_pct"] = 25.0
        tape = []
        pattern = []
        for n in range(5):
            mint = f"{n}KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
            pattern.append({
                "epoch": 0, "mint": mint, "price": 1.0, "m5": 12.0, "signal": True,
                "verdict": "SKIP", "pass_count": 4, "check_count": 8, "symbol": "P",
            })
        for n in range(5):
            mint = f"{n}KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
            pattern.append({
                "epoch": 50, "mint": mint, "price": 0.745, "m5": None, "signal": False,
                "verdict": "SKIP", "pass_count": 4, "check_count": 8, "symbol": "P",
            })
        for n in range(5):
            mint = f"{n}KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
            pattern.append({
                "epoch": 80, "mint": mint, "price": 1.5, "m5": None, "signal": False,
                "verdict": "SKIP", "pass_count": 4, "check_count": 8, "symbol": "P",
            })
        for shift in (0, 1_000, 2_000):
            for sample in pattern:
                row = dict(sample)
                row["epoch"] = sample["epoch"] + shift
                tape.append(row)
        st["ai_feed"] = tape
        trade = {
            "symbol": "P",
            "exit_reason": "stop",
            "pnl_usd": -0.2,
            "verdict": "SKIP",
            "lesson": "P stop. Verdict SKIP (4/8). Entry $1. P/L $-0.20 (-20.0%).",
            "closed_at": "2026-10-31T18:05:00Z",
        }
        row = game_ai.apply_learning(st, "VECTOR", trade)
        self.assertFalse(row["applied"])
        self.assertEqual(st["ai_settings"]["VECTOR"]["stop_pct"], 25.0)
        self.assertGreater(row["backtest"]["validate"]["proposed_dd_pct"], 20)
        self.assertIn("drawdown", row["backtest"]["note"])
        self.assertIn("train", row["backtest"])
        self.assertIn("validate", row["backtest"])
        self.assertGreater(row["backtest"]["train"]["proposed_pnl_pct"], row["backtest"]["train"]["current_pnl_pct"])

    def test_short_tape_is_rejected_with_a_split_log(self):
        self._start()
        st = game.load_state()
        st["ai_feed"] = []
        row = game_ai.apply_learning(st, "LEDGER", {
            "symbol": "X",
            "exit_reason": "tp",
            "pnl_usd": 0.2,
            "verdict": "BUY-WATCH",
            "lesson": "X tp. Verdict BUY-WATCH (8/8). Entry $1. P/L $+0.20 (+20.0%).",
            "closed_at": "2026-10-31T18:06:00Z",
        })
        self.assertFalse(row["applied"])
        self.assertEqual(row["backtest"]["validate"]["n"], 0)
        self.assertIn("not enough history", row["backtest"]["note"])
        self.assertEqual(st["ai_settings"]["LEDGER"]["tp_pct"], 30.0)

    def test_settings_persist_and_balances_reset(self):
        self._start()
        st = game.load_state()
        st["ai_feed"] = []
        for epoch in (10_000, 10_100, 10_200):
            st["ai_feed"].extend(_pattern(epoch, SKIP_MINT, 0.815))
        game_ai.apply_learning(st, "VECTOR", {
            "exit_reason": "stop",
            "pnl_usd": -0.1,
            "verdict": "SKIP",
            "lesson": "kept",
            "closed_at": "2026-10-31T18:07:00Z",
            "symbol": "SKIPR",
        })
        st["ai_books"]["VECTOR"]["cash_usd"] = 3.5
        st["ai_books"]["VECTOR"]["positions"] = [{"id": "open1", "mint": SKIP_MINT}]
        st["cash_usd"] = 2.0
        game.save_state(st)
        fresh = game.start_session(force=True)
        self.assertEqual(fresh["cash_usd"], 5.0)
        self.assertEqual(fresh["positions"], [])
        self.assertAlmostEqual(fresh["ai_settings"]["VECTOR"]["stop_pct"], 19.0)
        self.assertEqual(fresh["ai_books"]["VECTOR"]["cash_usd"], 5.0)
        self.assertEqual(fresh["ai_books"]["VECTOR"]["positions"], [])
        self.assertEqual(fresh["ai_books"]["LEDGER"]["cash_usd"], 5.0)
        self.assertTrue(fresh["ai_settings_log"])
        self.assertTrue(fresh["ai_settings_log"][0]["applied"])
        self.assertTrue(fresh["ai_feed"])

    def test_study_tabs_carry_reason_verdict_and_lesson(self):
        self._write_rows([_row(SKIP_MINT, "SKIPR", 30), _row(GOOD_MINT, "LEDGR", 9)])
        self.cards[SKIP_MINT] = _card("SKIP", 4)
        self.cards[GOOD_MINT] = _card("WATCH", 7)
        self._start()
        self.prices[SKIP_MINT] = (1.0, 0.5)
        self.prices[GOOD_MINT] = (1.0, 0.5)
        self._step(8_000)
        self._step(8_003)
        self.prices[SKIP_MINT] = (0.8, 0.4)
        closed = self._step(8_010)
        vector = closed["ai_books"]["VECTOR"]["closed"]
        self.assertEqual(len(vector), 1)
        self.assertEqual(vector[0]["exit_reason"], "stop")
        self.assertEqual(vector[0]["verdict"], "SKIP")
        self.assertIn("SKIP", vector[0]["lesson"])
        self.assertTrue(closed["ai_settings_log"])
        log = closed["ai_settings_log"][0]
        self.assertIn("reason", log)
        self.assertIn("train", log["backtest"])
        self.assertIn("validate", log["backtest"])
        closed["status"] = "ended"
        review = game.build_run_review(closed)
        self.assertEqual(review["status"], "ready")
        study = review["study"]
        self.assertEqual(study["tabs"], ["you", "VECTOR", "LEDGER"])
        trade = study["VECTOR"]["trades"][0]
        self.assertIn("momentum", trade["entry_reason"])
        self.assertEqual(trade["exit_reason"], "stop")
        self.assertEqual(trade["verdict"], "SKIP")
        self.assertIn("Verdict SKIP", trade["lesson"])
        ledger_open = study["LEDGER"]["trades"][0]
        self.assertIn("checklist 7/8", ledger_open["entry_reason"])
        self.assertEqual(ledger_open["verdict"], "WATCH")

    def test_owner_buy_still_uses_the_fill_and_panel_v_stays(self):
        self.prices[SKIP_MINT] = (1.0, 0.3)
        self._start()
        out = game.buy(SKIP_MINT, 1.0, sl_pct=20, tp_pct=40)
        self.assertTrue(out["ok"], out)
        self.assertAlmostEqual(out["position"]["entry_price_usd"], 1.0)
        self.assertEqual(out["receipt"]["quote_age_s"], 0.3)
        self.assertNotIn("trader", out["receipt"])
        import server
        data = server.app.test_client().get("/api/health").get_json()
        self.assertEqual(data["panel_v"], 84)
        self.assertIn("ship_ab", data["features"])
        self.assertIn("game_receipts", data["features"])
        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("const PANEL_V = 84", html)
        self.assertIn('id="gRaceBoard"', html)
        self.assertIn('id="gAiSettings"', html)
        self.assertIn("function renderAiBoard", html)
        self.assertIn("function raceChartSvg", html)
        self.assertIn('["you", "You"]', html)
        self.assertIn('["VECTOR", "VECTOR"]', html)
        self.assertIn('["LEDGER", "LEDGER"]', html)
        self.assertIn("data-study-tab", html)
        self.assertIn(">Entry</span>", html)
        self.assertIn(">Exit</span>", html)
        self.assertIn(">Verdict</span>", html)
        self.assertIn(">Lesson</span>", html)
        self.assertIn("train ", html)
        self.assertIn("validate ", html)
