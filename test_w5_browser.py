"""SHIP W5 — headless checks for the tick paint rate and the score pill.

Last and the chart dot are counted in a real browser against a local server
whose tick changes every 500ms. The score pill is measured with bounding
boxes at 1280, 1024, 768, and 395.
"""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import game
import server
import trade_stream

MINT = "3KpyR7uM657Bbcw64gLHQpJfPYByJEiVhu4ED3cnpump"
TICKERS = (
    ("发发", "发发火星", "MintAgenC1111111111111111111111111111111", False),
    ("FĀFĀ", "发发", "MintFafa11111111111111111111111111111111", True),
    ("CRAMERSAN", "CRAMERSAN the long one", "MintCramer111111111111111111111111111111", False),
    ("SUPERLONGTOKEN", "超级长的中文代币名字发发发", "MintLong1111111111111111111111111111111", False),
)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _flow_rows() -> list[dict]:
    rows = []
    for ticker, name, mint, holding in TICKERS:
        rows.append({
            "mint": mint,
            "ticker": ticker,
            "name": name,
            "score_12": 11,
            "trench_band": True,
            "pair_age_hours": 2,
            "sitting_minutes": 12.4,
            "sitting_band": "over",
            "holding": holding,
            "metrics": {
                "liquidity_usd": 20000,
                "volume_h1_usd": 9000,
                "price_change_h1": 12,
                "buys_h1": 40,
                "sells_h1": 20,
                "pair_age_hours": 2,
            },
        })
    return rows


class W5BrowserTests(unittest.TestCase):
    def test_score_pill_does_not_overlap_name_or_ticker(self):
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server

        port = _free_port()
        httpd = make_server("127.0.0.1", port, server.app, threaded=True)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        rows = _flow_rows()

        def fulfill(route):
            url = route.request.url
            if "/api/flagged" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(rows))
            elif "/api/latest" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"run_finished": _utc(datetime.now(timezone.utc))}))
            elif "/api/tweets" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"posts": [], "accounts": []}))
            elif "/api/status" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"bot": {"running": False}}))
            elif "/api/game/" in url or "/api/update-now" in url or "/api/gold" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "status": "idle", "positions": [], "state": {"status": "idle", "positions": []}}))
            else:
                route.continue_()

        overlaps = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.route("**/api/**", fulfill)
            for width in (1280, 1024, 768, 395):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(f"http://127.0.0.1:{port}/?v=91", wait_until="domcontentloaded")
                page.wait_for_selector("#modeFilter .tok .score-btn", timeout=10000)
                page.wait_for_timeout(200)
                found = page.evaluate(
                    """() => {
                      const out = [];
                      function size(el) {
                        if (!el) return null;
                        const b = el.getBoundingClientRect();
                        return {w: b.width, h: b.height, text: (el.textContent || "").trim().slice(0, 24)};
                      }
                      document.querySelectorAll("#modeFilter .tok").forEach((tok) => {
                        const pill = tok.querySelector(".score-btn");
                        const ticker = tok.querySelector(".ticker");
                        const name = tok.querySelector(".name");
                        const ident = tok.querySelector(".ident");
                        const sym = tok.querySelector(".ticker .sym");
                        function hit(el) {
                          if (!pill || !el) return null;
                          const a = pill.getBoundingClientRect();
                          const b = el.getBoundingClientRect();
                          const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
                          const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
                          return {w: Math.max(0, w), h: Math.max(0, h), text: (el.textContent || "").trim().slice(0, 24)};
                        }
                        const badges = [];
                        tok.querySelectorAll(".badge").forEach((el) => {
                          const box = hit(el);
                          if (box) badges.push(box);
                        });
                        out.push({
                          ticker: hit(ticker),
                          name: hit(name),
                          badges: badges,
                          ident: size(ident),
                          sym: size(sym),
                          texts: (tok.innerText || "").slice(0, 80),
                        });
                      });
                      return out;
                    }"""
                )
                self.assertGreaterEqual(len(found), 3, found)
                saw_sitting = False
                saw_holding = False
                for row in found:
                    ident = row["ident"]
                    sym = row["sym"]
                    self.assertIsNotNone(ident, row)
                    self.assertGreaterEqual(ident["w"], 60, (width, row))
                    self.assertGreaterEqual(ident["h"], 1, (width, row))
                    self.assertIsNotNone(sym, row)
                    self.assertGreater(sym["w"], 0, (width, row))
                    self.assertGreater(sym["h"], 0, (width, row))
                    self.assertTrue(sym["text"], (width, row))
                    for kind in ("ticker", "name"):
                        box = row[kind]
                        self.assertIsNotNone(box)
                        if box["w"] >= 1 and box["h"] >= 1:
                            overlaps.append((width, kind, box))
                    for box in row["badges"]:
                        if "Sitting" in box["text"] or "SITTING" in box["text"]:
                            saw_sitting = True
                        if "HOLDING" in box["text"]:
                            saw_holding = True
                        if box["w"] >= 1 and box["h"] >= 1:
                            overlaps.append((width, "badge", box))
                self.assertTrue(saw_sitting, found)
                self.assertTrue(saw_holding, found)
            browser.close()
        self.assertEqual(overlaps, [], overlaps)

    def test_last_and_chart_dot_follow_a_half_second_tick(self):
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server

        trade_stream.reset_for_tests()
        game._live_cache.clear()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(game, "STATE_PATH", Path(folder.name) / "state.json")
        patch.start()
        self.addCleanup(patch.stop)
        now = datetime.now(timezone.utc)
        game.save_state({
            "session_id": "w5-browser",
            "status": "active",
            "started_at": _utc(now - timedelta(hours=1)),
            "ends_at": _utc(now + timedelta(hours=20)),
            "cash_usd": 4.0,
            "start_bankroll": 5.0,
            "peak_equity": 5.0,
            "positions": [{
                "id": "pos1",
                "mint": MINT,
                "symbol": "AGEN",
                "name": "AgenC",
                "stake_usd": 1.0,
                "entry_price_usd": 0.001,
                "qty_tokens": 1000.0,
                "last_price_usd": 0.001,
                "opened_at": _utc(now - timedelta(minutes=1)),
                "sl_pct": 20,
                "tp_pct": 100,
            }],
            "closed": [],
            "events": [],
        })
        price = {"v": 0.001}
        stop = threading.Event()

        def pump():
            while not stop.is_set():
                price["v"] *= 1.004
                trade_stream.ingest_swap(
                    MINT, price["v"], anchor=price["v"], seed_ok=True, sol=100.0, source="pool_swap",
                )
                trade_stream.note_mid(MINT, price["v"])
                stop.wait(0.5)

        trade_stream.ingest_swap(MINT, price["v"], anchor=price["v"], seed_ok=True, sol=100.0, source="pool_vault")
        trade_stream.note_mid(MINT, price["v"])
        worker = threading.Thread(target=pump, daemon=True)
        worker.start()
        self.addCleanup(stop.set)

        port = _free_port()
        httpd = make_server("127.0.0.1", port, server.app, threaded=True)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/?v=91&mode=game", wait_until="domcontentloaded")
            page.wait_for_selector("[data-role=last-px]", timeout=10000)
            page.fill("#gMint", MINT)
            page.click("#gQuote")
            page.wait_for_selector("#gQuoteBox [data-role=live-usd]", timeout=10000)
            page.wait_for_function(
                """() => {
                  const el = document.querySelector("[data-role=last-px]");
                  return el && el.textContent && el.textContent !== "—" && el.textContent !== "";
                }""",
                timeout=10000,
            )
            stats = page.evaluate(
                """() => new Promise((resolve) => {
                  const lastEl = () => document.querySelector("[data-role=last-px]");
                  const dotEl = () => document.getElementById("gChartDot");
                  const usdEl = () => document.querySelector("#gQuoteBox [data-role=live-usd]");
                  const lasts = [];
                  const dots = [];
                  const usds = [];
                  let prevLast = "";
                  let prevDot = "";
                  let prevUsd = "";
                  let lastAt = 0;
                  let dotAt = 0;
                  const lastGaps = [];
                  const dotGaps = [];
                  const started = performance.now();
                  const timer = setInterval(() => {
                    const now = performance.now();
                    const last = lastEl();
                    const dot = dotEl();
                    const usd = usdEl();
                    const text = last ? (last.textContent || "") : "";
                    const usdText = usd ? (usd.textContent || "") : "";
                    const cy = dot ? (dot.getAttribute("data-px") || "") + "@" + (dot.getAttribute("cy") || "") : "";
                    if (text && text !== prevLast) {
                      if (prevLast) lastGaps.push((now - lastAt) / 1000);
                      lastAt = now;
                      prevLast = text;
                      lasts.push(text);
                    }
                    if (usdText && usdText !== prevUsd && usdText !== "—") {
                      prevUsd = usdText;
                      usds.push(usdText);
                    }
                    if (cy && cy !== prevDot && !cy.startsWith("@")) {
                      if (prevDot) dotGaps.push((now - dotAt) / 1000);
                      dotAt = now;
                      prevDot = cy;
                      dots.push(cy);
                    }
                    if (now - started >= 30000) {
                      clearInterval(timer);
                      resolve({
                        lastChanges: lasts.length,
                        dotChanges: dots.length,
                        usdChanges: usds.length,
                        maxLastGap: lastGaps.length ? Math.max.apply(null, lastGaps) : 999,
                        maxDotGap: dotGaps.length ? Math.max.apply(null, dotGaps) : 999,
                        sample: lasts.slice(0, 3).concat(lasts.slice(-2)),
                        usdSample: usds.slice(0, 2).concat(usds.slice(-2)),
                      });
                    }
                  }, 100);
                })"""
            )
            browser.close()
        print("W5_TICK", json.dumps(stats))
        self.assertGreaterEqual(stats["lastChanges"], 20, stats)
        self.assertLessEqual(stats["maxLastGap"], 3.0, stats)
        self.assertGreaterEqual(stats["dotChanges"], 20, stats)
        self.assertLessEqual(stats["maxDotGap"], 3.0, stats)
        self.assertGreaterEqual(stats["usdChanges"], 50, stats)


if __name__ == "__main__":
    unittest.main()
