"""Social Tap viral feed: real parsed items only, never the tweet seed."""

from __future__ import annotations

import json
import subprocess
import unittest
from datetime import datetime, timezone
from unittest import mock

import viral

NOW = datetime(2026, 10, 26, 8, 45, tzinfo=timezone.utc)

TRENDS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
  <channel>
    <title>Daily Search Trends</title>
    <item>
      <title>winter storm forecast</title>
      <ht:approx_traffic>500+</ht:approx_traffic>
      <pubDate>Mon, 26 Oct 2026 01:40:00 -0700</pubDate>
      <ht:news_item>
        <ht:news_item_title>First Big Winter Storm Forecast for 7 US States</ht:news_item_title>
        <ht:news_item_url>https://example.test/winter</ht:news_item_url>
        <ht:news_item_source>The Weather Channel</ht:news_item_source>
      </ht:news_item>
      <ht:news_item>
        <ht:news_item_title>Guangzhou es amable</ht:news_item_title>
        <ht:news_item_url>https://example.test/es</ht:news_item_url>
        <ht:news_item_source>Diario AS</ht:news_item_source>
      </ht:news_item>
    </item>
    <item>
      <title>old trend</title>
      <pubDate>Mon, 20 Oct 2026 01:40:00 -0700</pubDate>
      <ht:news_item>
        <ht:news_item_title>Too old</ht:news_item_title>
        <ht:news_item_url>https://example.test/old</ht:news_item_url>
        <ht:news_item_source>Old Paper</ht:news_item_source>
      </ht:news_item>
    </item>
  </channel>
</rss>
"""

REDDIT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <author><name>/u/AutoModerator</name></author>
    <id>t3_sticky</id>
    <title>Making friends monday</title>
    <published>2026-10-26T08:00:00+00:00</published>
    <link href="https://www.reddit.com/r/gaming/comments/sticky/"/>
  </entry>
  <entry>
    <author><name>/u/Laylow_chips</name></author>
    <id>t3_fresh</id>
    <title>Thank god they changed it</title>
    <published>2026-10-26T08:09:29+00:00</published>
    <link href="https://www.reddit.com/r/memes/comments/fresh/"/>
  </entry>
  <entry>
    <author><name>/u/OldPoster</name></author>
    <id>t3_old</id>
    <title>Pinned from February</title>
    <published>2026-02-14T12:40:35+00:00</published>
    <link href="https://www.reddit.com/r/memes/comments/old/"/>
  </entry>
</feed>
"""

NEWS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Lula is re-elected Brazil's president - Financial Times</title>
      <link>https://news.google.com/rss/articles/fixture-lula</link>
      <pubDate>Mon, 26 Oct 2026 07:41:15 GMT</pubDate>
      <source url="https://www.ft.com">Financial Times</source>
    </item>
    <item>
      <title>Ancient headline - Reuters</title>
      <link>https://news.google.com/rss/articles/old</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <source url="https://www.reuters.com">Reuters</source>
    </item>
  </channel>
</rss>
"""


class ViralParseTests(unittest.TestCase):
    def test_trends_keep_fresh_english_headline_and_drop_old(self):
        posts = viral.parse_google_trends(TRENDS_XML, NOW)
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post["kind"], "trend")
        self.assertEqual(post["title"], "winter storm forecast")
        self.assertEqual(post["text"], "First Big Winter Storm Forecast for 7 US States")
        self.assertEqual(post["source_label"], "The Weather Channel")
        self.assertEqual(post["url"], "https://example.test/winter")
        self.assertEqual(post["traffic"], "500+")
        self.assertTrue(post["created_at"].startswith("2026-10-26"))
        self.assertNotIn("Guangzhou", json.dumps(post))

    def test_reddit_drops_automod_and_stale(self):
        posts = viral.parse_reddit_atom(
            REDDIT_XML, kind="meme", source="reddit_memes", label="r/memes", now_dt=NOW
        )
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["title"], "Thank god they changed it")
        self.assertEqual(posts[0]["username"], "Laylow_chips")
        self.assertEqual(posts[0]["source_label"], "r/memes")
        self.assertEqual(posts[0]["url"], "https://www.reddit.com/r/memes/comments/fresh/")
        self.assertEqual(posts[0]["text"], "")

    def test_news_strips_source_suffix_and_drops_old(self):
        posts = viral.parse_google_news(NEWS_XML, NOW)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["title"], "Lula is re-elected Brazil's president")
        self.assertEqual(posts[0]["kind"], "event")
        self.assertEqual(posts[0]["source_label"], "Financial Times")
        self.assertTrue(posts[0]["created_at"])

    def test_coin_lexicon_keeps_coins_and_drops_unrelated(self):
        self.assertTrue(viral.coin_relevant_text("Pump.fun memecoin revenue hits a daily high"))
        self.assertTrue(viral.coin_relevant_text("Bitcoin holds near $80,000 ahead of the Fed"))
        self.assertTrue(viral.coin_relevant_text("Canada opens consultation on draft stablecoin rules"))
        self.assertTrue(viral.coin_relevant_text("tokenized share class on XRP Ledger"))
        self.assertFalse(viral.coin_relevant_text("Lula is re-elected Brazil's president"))
        self.assertFalse(viral.coin_relevant_text("First Big Winter Storm Forecast for 7 US States"))
        self.assertFalse(viral.coin_relevant_text("trump accounts"))
        self.assertFalse(viral.coin_relevant_text("walmart stock"))
        self.assertTrue(viral.coin_relevant_text("Copy trading vs finding coins yourself"))
        self.assertFalse(viral.coin_relevant_text("PHP flaw shows AI vulnerability apocalypse"))
        self.assertFalse(viral.coin_relevant_text("The opening coin toss was delayed"))
        self.assertFalse(viral.coin_relevant_text("A gold coin sold at auction"))

    def test_assemble_biases_coin_and_meme_and_invents_nothing(self):
        meme = viral.parse_reddit_atom(
            REDDIT_XML, kind="meme", source="reddit_memes", label="r/memes", now_dt=NOW
        )
        meme.append(
            {
                "id": "reddit_memes:pepe",
                "kind": "meme",
                "title": "Pepe meme is everywhere again",
                "text": "",
                "username": "Laylow_chips",
                "name": "r/memes",
                "source": "reddit_memes",
                "source_label": "r/memes",
                "via": "r/memes",
                "url": "https://www.reddit.com/r/memes/comments/pepe/",
                "created_at": "2026-10-26T08:12:00Z",
                "traffic": "",
            }
        )
        politics = viral.parse_google_news(NEWS_XML, NOW)
        weather = viral.parse_google_trends(TRENDS_XML, NOW)
        coin_news = [
            {
                "id": "news:pump",
                "kind": "coin",
                "title": "Pump.fun memecoin revenue hits a daily high",
                "text": "",
                "username": "Google News",
                "name": "Cointelegraph",
                "source": "google_news_coin",
                "source_label": "Cointelegraph",
                "via": "Google News",
                "url": "https://news.google.com/rss/articles/fixture-pump",
                "created_at": "2026-10-26T07:41:15Z",
                "traffic": "",
            },
            politics[0],
        ]
        wire = [
            {
                "id": "ct:php",
                "kind": "coin",
                "title": "PHP flaw shows AI vulnerability apocalypse",
                "text": "",
                "username": "Cointelegraph",
                "name": "Cointelegraph",
                "source": "cointelegraph",
                "source_label": "Cointelegraph",
                "via": "Cointelegraph",
                "url": "https://cointelegraph.com/php-flaw",
                "created_at": "2026-10-26T14:25:22Z",
                "traffic": "",
            },
            {
                "id": "ct:btc",
                "kind": "coin",
                "title": "Bitcoin holds near $80,000 as oil retreats",
                "text": "",
                "username": "Cointelegraph",
                "name": "Cointelegraph",
                "source": "cointelegraph",
                "source_label": "Cointelegraph",
                "via": "Cointelegraph",
                "url": "https://cointelegraph.com/bitcoin-holds",
                "created_at": "2026-10-26T11:24:33Z",
                "traffic": "",
            },
        ]
        payload = viral.assemble(
            [
                {"id": "google_trends", "label": "Google Trends", "ok": True, "items": weather},
                {"id": "reddit_memes", "label": "r/memes", "ok": True, "items": meme},
                {
                    "id": "reddit_memecoins",
                    "label": "r/memecoins",
                    "ok": False,
                    "error": "HTTP Error 429: Too Many Requests",
                    "items": [],
                },
                {"id": "google_news_coin", "label": "Google News", "ok": True, "items": coin_news},
                {"id": "cointelegraph", "label": "Cointelegraph", "ok": True, "items": wire},
                # General news id is not a live source. Its rows must not leak in.
                {"id": "google_news", "label": "Google News", "ok": True, "items": politics},
            ]
        )
        titles = [p["title"] for p in payload["posts"]]
        self.assertEqual(
            titles,
            [
                "Pump.fun memecoin revenue hits a daily high",
                "Bitcoin holds near $80,000 as oil retreats",
                "Pepe meme is everywhere again",
            ],
        )
        kinds = [p["kind"] for p in payload["posts"]]
        self.assertEqual(kinds, ["coin", "coin", "meme"])
        self.assertNotIn("Thank god they changed it", titles)
        self.assertGreaterEqual(kinds[:4].count("coin"), 2)
        first = payload["posts"][0]
        self.assertEqual(first["source_label"], "Cointelegraph")
        self.assertEqual(first["created_at"], "2026-10-26T07:41:15Z")
        self.assertTrue(first["url"])
        self.assertEqual(payload["bias"], "coin_relevant")
        self.assertIsNone(payload["error"])
        self.assertIsNone(payload["note"])
        self.assertTrue(payload["ok"])
        blob = json.dumps(payload)
        self.assertNotIn("Lula", blob)
        self.assertNotIn("Winter", blob)
        self.assertNotIn("PHP flaw", blob)
        self.assertNotIn("elonmusk", blob)
        self.assertNotIn("meme_note", blob)
        self.assertNotIn("ticker", blob.lower())
        self.assertNotIn("Guangzhou", blob)
        reddit = next(s for s in payload["sources"] if s["id"] == "reddit_memecoins")
        self.assertFalse(reddit["ok"])
        self.assertIn("429", reddit["error"])
        trends = next(s for s in payload["sources"] if s["id"] == "google_trends")
        self.assertTrue(trends["ok"])
        self.assertEqual(trends["count"], 0)

    def test_irrelevant_only_is_honest_empty(self):
        payload = viral.assemble(
            [
                {
                    "id": "google_trends",
                    "label": "Google Trends",
                    "ok": True,
                    "items": viral.parse_google_trends(TRENDS_XML, NOW),
                },
                {
                    "id": "google_news_coin",
                    "label": "Google News",
                    "ok": True,
                    "items": viral.parse_google_news(NEWS_XML, NOW),
                },
                {"id": "reddit_memes", "label": "r/memes", "ok": True, "items": []},
                {"id": "cointelegraph", "label": "Cointelegraph", "ok": True, "items": []},
                {"id": "reddit_memecoins", "label": "r/memecoins", "ok": True, "items": []},
            ]
        )
        self.assertEqual(payload["posts"], [])
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["error"])
        self.assertIn("Nothing invented", payload["note"])
        self.assertIn("coin-relevant", payload["note"].lower())
        self.assertNotIn("elonmusk", json.dumps(payload))

    def test_megathread_is_dropped_not_rewritten(self):
        payload = viral.assemble(
            [
                {
                    "id": "reddit_memecoins",
                    "label": "r/memecoins",
                    "ok": True,
                    "items": [
                        {
                            "id": "reddit_memecoins:daily",
                            "kind": "coin",
                            "title": "Daily Discussion Thread - October 26",
                            "text": "",
                            "username": "AutoPoster",
                            "name": "r/memecoins",
                            "source": "reddit_memecoins",
                            "source_label": "r/memecoins",
                            "via": "r/memecoins",
                            "url": "https://www.reddit.com/r/memecoins/comments/daily/",
                            "created_at": "2026-10-26T08:00:00Z",
                            "traffic": "",
                        },
                        {
                            "id": "reddit_memecoins:live",
                            "kind": "coin",
                            "title": "New solana memecoin just listed",
                            "text": "",
                            "username": "trader",
                            "name": "r/memecoins",
                            "source": "reddit_memecoins",
                            "source_label": "r/memecoins",
                            "via": "r/memecoins",
                            "url": "https://www.reddit.com/r/memecoins/comments/live/",
                            "created_at": "2026-10-26T08:10:00Z",
                            "traffic": "",
                        },
                    ],
                }
            ]
        )
        self.assertEqual([p["title"] for p in payload["posts"]], ["New solana memecoin just listed"])
        self.assertNotIn("Daily Discussion", json.dumps(payload["posts"]))

    def test_total_failure_is_empty_error_not_seed(self):
        payload = viral.assemble(
            [
                {"id": spec["id"], "label": spec["label"], "ok": False, "error": "down", "items": []}
                for spec in viral._SOURCES
            ]
        )
        self.assertEqual(payload["posts"], [])
        self.assertFalse(payload["ok"])
        self.assertIn("Live sources failed", payload["error"])
        self.assertNotIn("elonmusk", json.dumps(payload))
        self.assertNotIn("2114170724130734195", json.dumps(payload))

    def test_feed_payload_does_not_call_network_or_seed(self):
        viral.reset_cache()

        def fetch(_url):
            raise OSError("network disabled in test")

        payload = viral.feed_payload(fetch=fetch, use_cache=False)
        self.assertEqual(payload["posts"], [])
        self.assertTrue(payload["error"])
        self.assertEqual(payload["feed"], "viral_live")
        self.assertNotIn("elonmusk", json.dumps(payload))


class SocialTapFreshnessTests(unittest.TestCase):
    def setUp(self):
        viral.reset_cache()

    def tearDown(self):
        viral.reset_cache()

    def _card(self, title: str) -> dict:
        return {
            "ok": True,
            "feed": "viral_live",
            "bias": "coin_relevant",
            "live": True,
            "stale": False,
            "updated_at": "2026-10-26T08:00:00Z",
            "posts": [
                {
                    "id": "live:" + title,
                    "kind": "coin",
                    "title": title,
                    "text": title,
                    "url": "https://example.test/" + title.replace(" ", "-"),
                    "source_label": "Example",
                    "created_at": "2026-10-26T08:00:00Z",
                }
            ],
            "sources": [],
            "error": None,
            "note": None,
        }

    def test_fresh_window_constant_is_five_minutes(self):
        self.assertEqual(viral.SOCIAL_TAP_FRESH_MIN, 5.0)
        self.assertEqual(viral.SOCIAL_TAP_FRESH_SEC, 300.0)

    def test_present_feed_keeps_posts_through_five_minutes(self):
        card = self._card("fresh memecoin headline")
        held = viral.present_feed(card, age_sec=viral.SOCIAL_TAP_FRESH_SEC)
        self.assertFalse(held["stale"])
        self.assertEqual(held["posts"][0]["title"], "fresh memecoin headline")
        self.assertEqual(held["fresh_sec"], 300.0)
        self.assertEqual(held["feed_loop_min"], 5.0)
        self.assertEqual(card["posts"][0]["title"], "fresh memecoin headline")

    def test_present_feed_past_loop_omits_idle_cards(self):
        card = self._card("idle card from asleep bot")
        held = viral.present_feed(card, age_sec=42 * 60)
        self.assertTrue(held["stale"])
        self.assertEqual(held["posts"], [])
        self.assertIn("Nothing invented", held["note"])
        self.assertNotIn("idle card", json.dumps(held["posts"]))
        self.assertEqual(card["posts"][0]["title"], "idle card from asleep bot")

    def test_cache_hit_past_loop_does_not_serve_idle_cards(self):
        now = 2_000_000.0
        with viral._lock:
            viral._cache["payload"] = self._card("idle card from asleep bot")
            viral._cache["at"] = now - 42 * 60
            viral._cache["until"] = now + 30
            viral._cache["refreshing"] = False

        def fetch(_url):
            raise AssertionError("fresh ttl hit must not fetch")

        out = viral.feed_payload(fetch=fetch, now=now)
        self.assertEqual(out["posts"], [])
        self.assertTrue(out["stale"])
        self.assertIn("Nothing invented", out["note"])

    def test_cache_inside_loop_returns_copied_posts(self):
        now = 2_000_000.0
        with viral._lock:
            viral._cache["payload"] = self._card("solana memecoin listing")
            viral._cache["at"] = now - 90
            viral._cache["until"] = now + 30
            viral._cache["refreshing"] = False

        def fetch(_url):
            raise AssertionError("inside the loop a warm cache must not fetch")

        out = viral.feed_payload(fetch=fetch, now=now)
        self.assertFalse(out["stale"])
        self.assertEqual(out["posts"][0]["title"], "solana memecoin listing")
        self.assertEqual(out["feed_loop_min"], 5.0)

    def test_expired_cache_past_loop_refresh_failure_is_honest(self):
        now = 3_000_000.0
        with viral._lock:
            viral._cache["payload"] = self._card("old idle card")
            viral._cache["at"] = now - 42 * 60
            viral._cache["until"] = now - 1
            viral._cache["refreshing"] = False

        def fetch(_url):
            raise OSError("sources down")

        out = viral.feed_payload(fetch=fetch, now=now)
        self.assertEqual(out["posts"], [])
        self.assertTrue(out.get("error"))
        self.assertNotIn("old idle card", json.dumps(out["posts"]))
        self.assertNotIn("elonmusk", json.dumps(out))

    def test_inflight_past_loop_withholds_cards(self):
        now = 4_000_000.0
        with viral._lock:
            viral._cache["payload"] = self._card("still painting idle")
            viral._cache["at"] = now - 42 * 60
            viral._cache["until"] = now - 1
            viral._cache["refreshing"] = True

        def fetch(_url):
            raise AssertionError("in-flight refresh must not be fetched again")

        out = viral.feed_payload(fetch=fetch, now=now)
        self.assertEqual(out["posts"], [])
        self.assertTrue(out["refreshing"])
        self.assertIn("Refreshing", out["note"])


class PanelRouteTests(unittest.TestCase):
    def test_health_is_v66_with_delay_warning(self):
        import server

        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["panel_v"], 90)
        self.assertIn("delay_warning", data["features"])
        self.assertIn("game_polish", data["features"])
        self.assertEqual(data["qa_tick"], "page ?qa_tick=both|up|down")
        self.assertIn("game_finish", data["features"])
        self.assertIn("viral_live", data["features"])
        self.assertIn("coin_detail", data["features"])
        self.assertIn("social_tap", data["features"])
        self.assertIn("filter_theme", data["features"])
        self.assertIn("coin_relevant", data["features"])
        self.assertIn("mcap_progress", data["features"])
        self.assertIn("quote_sync", data["features"])
        self.assertEqual(data["quote_sla_s"], 5.0)
        self.assertEqual(data["quote_band"], 0.05)

    def test_tweets_route_serves_viral_payload_not_file(self):
        import server

        payload = {
            "ok": True,
            "feed": "viral_live",
            "live": True,
            "stale": False,
            "updated_at": "2026-10-26T08:45:00Z",
            "posts": [
                {
                    "id": "trend:fixture",
                    "kind": "trend",
                    "title": "fixture trend",
                    "text": "fixture headline",
                    "url": "https://example.test/item",
                    "source_label": "Example",
                    "created_at": "2026-10-26T08:40:00Z",
                }
            ],
            "sources": [{"id": "google_trends", "label": "Google Trends", "ok": True, "count": 1, "error": None}],
            "error": None,
            "disclaimer": viral.DISCLAIMER,
        }
        with mock.patch.object(server.viral_mod, "feed_payload", return_value=payload):
            client = server.app.test_client()
            res = client.get("/api/tweets")
            self.assertEqual(res.status_code, 200)
            body = res.get_json()
            self.assertEqual(body["feed"], "viral_live")
            self.assertEqual(body["posts"][0]["title"], "fixture trend")
            self.assertNotIn("elonmusk", json.dumps(body))
            etag = res.headers["ETag"]
            again = client.get("/api/tweets", headers={"If-None-Match": etag})
            self.assertEqual(again.status_code, 304)

    def test_panel_labels_social_tap_v66(self):
        with open("static/index.html", encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn(">Social Tap<", html)
        self.assertIn("const PANEL_V = 90", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("Mcap · % max", html)
        self.assertIn("Market cap progress · paper research", html)
        self.assertIn("Ceiling math:", html)
        self.assertNotIn("Max unknown", html)
        self.assertIn("realPrints", html)
        self.assertIn("Last hour print", html)
        self.assertIn("Not a prediction.", html)
        self.assertIn("Not a forecast.", html)
        self.assertIn("class=\"mcap-hero", html)
        self.assertIn("class=\"thin-bar", html)
        self.assertIn(">Mcap now<", html)
        self.assertNotIn("guaranteed", html.lower())
        self.assertIn("Coin-relevant viral", html)
        self.assertIn("nothing invented", html)
        self.assertIn('id="viralToggle"', html)
        self.assertIn("Collapse", html)
        self.assertNotIn("meme_note", html)
        self.assertNotIn("?v=56", html)
        self.assertNotIn("?v=57", html)
        self.assertNotIn("?v=58", html)
        self.assertNotIn("?v=59", html)
        self.assertNotIn("?v=60", html)
        self.assertNotIn("?v=61", html)
        self.assertNotIn("?v=62", html)
        self.assertNotIn("?v=63", html)
        self.assertNotIn("?v=64", html)
        self.assertNotIn("?v=65", html)
        self.assertNotIn("?v=66", html)
        self.assertNotIn("?v=67", html)
        self.assertNotIn("?v=68", html)
        self.assertNotIn("?v=69", html)
        self.assertNotIn("?v=70", html)
        self.assertNotIn("?v=71", html)
        self.assertNotIn("?v=72", html)
        self.assertNotIn("?v=73", html)
        self.assertNotIn("?v=76", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("Where it’s at · DEX Screener", html)
        self.assertIn("data-quote-live", html)
        self.assertIn("quoteAgeOk", html)
        self.assertNotIn("at FDV · no upside", html)
        self.assertNotIn("No research ceiling", html)
        self.assertIn("tweets-gone", html)
        self.assertIn("Social Tap hidden", html)
        self.assertIn(">Coin</span>", html)
        self.assertIn("does not recalculate the score", html)
        self.assertIn("score-btn.win", html)
        self.assertIn("coin-hero", html)
        self.assertIn("engine weights", html)

    def test_lag_banner_matches_ship_h_mock(self):
        with open("static/index.html", encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="lagBanner"', html)
        self.assertIn("Behind live · not Dex 1s", html)
        self.assertIn("Near live · just scanned", html)
        self.assertIn("≤ ~5m", html)
        self.assertIn("up to ~<b>5m</b> lag (5‑min scan)", html)
        self.assertIn("paper scores can trail mid-dump", html)
        self.assertIn("still not Dex 1s — paper research loop", html)
        self.assertIn("next scan ≤ <b>5m</b>", html)
        self.assertIn("const FEED_LOOP_MIN = 5", html)
        self.assertIn("const NEAR_LIVE_SEC = 60", html)
        self.assertIn("const LOUD_LAG_SEC = 4 * 60", html)
        self.assertIn("sitting kept past 5m", html)
        self.assertIn("On-chain · filtered · sitting kept past 5m", html)
        self.assertNotIn('id="dataAge"', html)
        self.assertNotIn("Live · on-chain · filtered", html)
        self.assertIn("body.mode-game #lagBanner { display: none; }", html)
        consts = html[html.index("const FEED_LOOP_MIN"):html.index("function lagAgeText")]
        fns = html[html.index("function lagAgeText"):html.index("function paintLag")]
        script = consts + "\n" + fns + r"""
if (lagPhase(24) !== "fresh") process.exit(2);
if (lagPhase(59) !== "fresh") process.exit(3);
if (lagPhase(60) !== "mid") process.exit(4);
if (lagPhase(239) !== "mid") process.exit(5);
if (lagPhase(240) !== "loud") process.exit(6);
if (lagPhase(243) !== "loud") process.exit(7);
if (lagPhase(null) !== "loud") process.exit(8);
if (lagAgeText(24) !== "0.4m") process.exit(9);
if (lagAgeText(4 * 60 + 3) !== "4m 3s") process.exit(10);
if (lagAgeText(null) !== "—") process.exit(11);
const stale = lagDetailHtml("loud", "4m 3s", 243);
if (stale.indexOf("data age <b>4m 3s</b>") < 0) process.exit(12);
if (stale.indexOf("up to ~<b>5m</b> lag") < 0) process.exit(13);
if (stale.indexOf("paper scores can trail mid-dump") < 0) process.exit(14);
const fresh = lagDetailHtml("fresh", "0.4m", 24);
if (fresh.indexOf("next scan ≤ <b>5m</b>") < 0) process.exit(15);
if (fresh.indexOf("still not Dex 1s") < 0) process.exit(16);
if (fresh.indexOf("1s live") >= 0) process.exit(17);
const old = lagDetailHtml("loud", "12m 4s", 724);
if (old.indexOf("older than the ~<b>5m</b> loop") < 0) process.exit(18);
if (old.indexOf("not Dex 1s") < 0) process.exit(19);
"""
        subprocess.check_call(["node", "-e", script])


if __name__ == "__main__":
    unittest.main()
