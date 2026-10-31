#!/usr/bin/env python3
"""
Nunu filter Flask backend (paper research only).

Reads shared JSON state written by filter.py / bot.py. Does not import them.
Serves API + static panel on port 8765 by default.
Also: paper-log CSV append + 24h $5 paper game.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, g, jsonify, make_response, request, send_from_directory
from flask_cors import CORS

import game as game_mod
import gold_scar
import manual_update
import qa_gold
import qa_max
import viral as viral_mod
from mcap_progress import apply_mcap

DIR = Path(__file__).resolve().parent
STATIC_DIR = DIR / "static"
FLAGGED_PATH = DIR / "flagged.json"
LATEST_PATH = DIR / "latest.json"
STATUS_PATH = DIR / "bot_status.json"
TWEETS_PATH = DIR / "tweets.json"
RUNS_LOG = DIR / "runs.log"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
CORS(app)

_QA_ARM_PATHS = ("/api/game/tick", "/api/game/buy", "/api/game/sell", "/api/game/quote")
_QA_CLEAR = ("off", "0", "up")


def _qa_walk_mint() -> str:
    """Mint this arming request is about. Sell resolves the open position."""
    mint = (request.args.get("mint") or request.args.get("ca") or "").strip()
    if game_mod.looks_like_mint(mint):
        return mint
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        body = {}
    mint = str(body.get("mint") or body.get("ca") or "").strip()
    if game_mod.looks_like_mint(mint):
        return mint
    pid = str(body.get("position_id") or body.get("id") or "").strip()
    if not pid:
        return ""
    st = game_mod.load_state()
    for pos in st.get("positions") or []:
        if isinstance(pos, dict) and pos.get("id") == pid:
            found = str(pos.get("mint") or "")
            return found if game_mod.looks_like_mint(found) else ""
    return ""


@app.before_request
def _bind_qa_walk():
    """Arm a one-mint price walk from ?qa_tick=down. off, 0, and up clear it.

    /api/game/state?qa_tick=down stays the display-only equity nudge. up on
    that route still clears a walk cookie so a later buy is not the walked price.
    The walk covers only the mint it was armed on, and it stops after the span.
    """
    client = (request.cookies.get(game_mod.CLIENT_COOKIE) or "").strip()
    if not client:
        client = uuid.uuid4().hex
        g.client_set_cookie = client
    game_mod.set_client_sid(client)
    cookie = (request.cookies.get(game_mod.QA_WALK_COOKIE) or "").strip()
    query = (request.args.get("qa_tick") or "").strip().lower()
    path = request.path or ""
    sid = None
    if query in _QA_CLEAR:
        g.qa_walk_clear_cookie = True
        if cookie:
            game_mod.clear_qa_walk(cookie)
    elif query == "down" and path in _QA_ARM_PATHS:
        sid = cookie or uuid.uuid4().hex
        g.qa_walk_set_cookie = sid
        game_mod.arm_qa_walk(sid, _qa_walk_mint())
    elif cookie and game_mod.qa_walk_armed(cookie):
        sid = cookie
    game_mod.set_qa_walk_sid(sid)

_json_cache: dict[str, tuple[int, object]] = {}
# Gold annotate is pure CPU. Pollers hit /api/flagged often. Keyed by the
# flagged file mtime+size and the qa_max / qa_gold query so a Manual Update
# write (new mtime) and the fixture queries cannot serve a stale board.
_prepare_cache: dict[tuple, list] = {}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        st = path.stat()
        key = str(path)
        hit = _json_cache.get(key)
        if hit and hit[0] == st.st_mtime_ns:
            return hit[1]
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _json_cache[key] = (st.st_mtime_ns, data)
        return data
    except (OSError, json.JSONDecodeError):
        return default


def _file_etag(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        st = path.stat()
        raw = f"{path.name}:{st.st_mtime_ns}:{st.st_size}"
        return '"' + hashlib.md5(raw.encode("utf-8")).hexdigest() + '"'
    except OSError:
        return None


def _http_date(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
    except OSError:
        return None


def _with_research_rows(data):
    """Attach the paper-research card without rewriting flagged.json."""
    if not isinstance(data, list):
        return data
    from research import research_card

    out = []
    for row in data:
        if not isinstance(row, dict):
            out.append(row)
            continue
        cloned = apply_mcap(dict(row))
        cloned["research"] = research_card(cloned)
        out.append(cloned)
    return out


def _with_qa_max_rows(data):
    """Prepend the synthetic bonding row. Other rows are not rewritten."""
    rows = _with_research_rows(data)
    if not isinstance(rows, list):
        rows = []
    kept = [row for row in rows if not (isinstance(row, dict) and row.get("qa_max_fixture"))]
    return [qa_max.prepared_row()] + kept


def _wants_qa_max() -> bool:
    """Opt-in Max fixture. Absent or any other value leaves live rows alone."""
    raw = (request.args.get("qa_max") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _wants_qa_gold() -> bool:
    """Opt-in Gold card. ``arrive`` is the same row; the panel plays the cue once."""
    raw = (request.args.get("qa_gold") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "arrive"}


def _prepare_cache_key() -> tuple:
    qa_max = _wants_qa_max()
    qa_gold = _wants_qa_gold()
    try:
        st = FLAGGED_PATH.stat()
        return (st.st_mtime_ns, st.st_size, qa_max, qa_gold)
    except OSError:
        return (None, 0, qa_max, qa_gold)


def _prepare_flagged(data):
    """Research card, optional QA rows, then a Gold Scar verdict on every row.

    Gold is recomputed when flagged.json changes. A ``gold`` stamp inside
    flagged.json does not promote. Repeat reads with the same mtime and the
    same qa_max / qa_gold query reuse the annotated payload.
    """
    key = _prepare_cache_key()
    cached = _prepare_cache.get(key)
    if cached is not None:
        return cached
    rows = _with_research_rows(data)
    if not isinstance(rows, list):
        rows = []
    if _wants_qa_max():
        kept = [row for row in rows if not (isinstance(row, dict) and row.get("qa_max_fixture"))]
        rows = [qa_max.prepared_row()] + kept
    rows = gold_scar.annotate(rows)
    if _wants_qa_gold():
        rows = qa_gold.prepend(rows)
    _prepare_cache.clear()
    _prepare_cache[key] = rows
    return rows


def _flagged_etag_suffix() -> str:
    parts = ["gold_scar"]
    if _wants_qa_max():
        parts.append("qa_max")
    if _wants_qa_gold():
        parts.append("qa_gold")
    return ":" + "+".join(parts)


def _json_with_validators(path: Path, default, transform=None, etag_suffix: str = "") -> Response:
    etag = _file_etag(path)
    if etag and etag_suffix:
        etag = '"' + etag.strip('"') + etag_suffix + '"'
    last_mod = _http_date(path)
    inm = request.headers.get("If-None-Match")
    if etag and inm and inm.strip() == etag:
        resp = make_response("", 304)
        resp.headers["ETag"] = etag
        if last_mod:
            resp.headers["Last-Modified"] = last_mod
        resp.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
        return resp

    data = _read_json(path, default)
    if transform is not None:
        data = transform(data)
    resp = make_response(jsonify(data))
    if etag:
        resp.headers["ETag"] = etag
    if last_mod:
        resp.headers["Last-Modified"] = last_mod
    resp.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return resp


@app.get("/api/health")
def api_health():
    import trade_stream as trade_stream_mod

    return jsonify(
        {
            "ok": True,
            "service": "nunu-filter",
            "panel_v": 95,
            "time": _utc(),
            "qa_tick": "page ?qa_tick=both|up|down",
            "qa_end": "page ?qa_end=1 (one-shot) or POST /api/game/end",
            "qa_max": "page ?qa_max=1 or GET /api/flagged?qa_max=1 (also GET /api/qa/max-fixture?qa_max=1)",
            "qa_gold": (
                "page ?qa_gold=1 shows one synthetic rare card in the Gold section "
                "(first load seeds, so no chime). ?qa_gold=arrive plays ♪ GOLD once for that card. "
                "Mute silences it. The cue is not the Token Flow new-token chime and not Game win/loss. "
                "GET /api/flagged?qa_gold=1 and GET /api/gold?qa_gold=1. "
                "Absent query leaves live rows only. Live gold stays empty unless the stricter 7-gate stack clears. "
                "Do not combine with ?fixture=."
            ),
            "gold_scar": (
                "Separate Gold picks section. Promotion recomputed when flagged.json changes, from the "
                "owner 7-gate stack, stricter: score/12 >= 11, copy pack at 3, crowded politics out "
                "(including a stem inside a compound ticker such as elon in GTAELON), "
                "1h under +180 with a required 5m print, clean on-chain dig including top holders, "
                "overheard posts only (a profile URL is not enough; live context passes none), "
                "macro veto unless gold_macro.json explicitly says Fed-quiet and Solana-ok. "
                "Gate evidence is on each row as gold_scar and on GET /api/gold. Empty is expected. "
                "No gold bar was loosened."
            ),
            "filter_smart": (
                "Token Flow demotions, paper only. Crowded narrative −12 and no trench. "
                "Late chase is age>=1h and h1>=+180% costing −12 (was a cosmetic flag at age>=2h and h1>=+100%). "
                "Already-ran is h24>=+250% and age>=2h costing −18; h24>=+500% and age>=3h hard-fails. "
                "Clone pack >=3 costs −8. A profile URL with no second link and no description caps socials at 6 "
                "(X-only was 9). Trench needs score/12>=10, age under 6h, and not late-chase or crowded. "
                "Gold annotate is cached on flagged mtime. RugCheck summaries skip hard-fails and already-ran. "
                "Mint universe stays boosts+profiles. Not a buy."
            ),
            "profile_custom_pic": (
                "Game Edit profile can take a still PNG, JPEG, or WebP from files or photos "
                "(upload max 8 MB). Preview, then Save stores a small crop on this paper profile "
                "with the name and points. Cancel, a non-image, or an oversized file keeps the last "
                "good avatar. Rocket, Star, Flame, and Moon presets stay. Not an account. "
                "Not synced across devices. GIF and video animation are not saved."
            ),
            "final_polish": (
                "Chrome pass. Buy toasts sit above the Filter Game follow chip. "
                "Narrow widths keep that chip and Token Flow in frame. "
                "A rejected profile file cannot flip the avatar to Star."
            ),
            "live_tick": (
                "Game price and the rocket chart share one tick cache. "
                "The displayed price is the last real swap on the coin's main pool. "
                "PumpSwap and pump.fun curves use logsSubscribe (mentions the pool, commitment "
                "confirmed) and parse Buy/Sell Program data into the executed fill. "
                "processed logsSubscribe acks and then pushes nothing on the public socket. "
                "api.mainnet-beta pushes every logsSubscribe on one socket and answers 429 "
                "around 10 connections, so open positions and the coin being viewed share one "
                "logs socket (at most 8 subscriptions). A 429 backs off with jitter. "
                "A pool without that live socket is priced from the 1s vault poll. "
                "Vault accountSubscribe is the second source. "
                "Orca keeps accountSubscribe. PumpPortal trade sockets are not used: "
                "they require a funded API key. "
                "Jupiter Price API usdPrice is one batched call per second for the SOL anchor. "
                "It is not the steady-state coin price. A pool that stays unreachable for more than "
                "10s may return src jupiter_fallback, and that tick is stale so it cannot fill a buy, "
                "sell, or stop. The first tick resolves the pool and reads vaults synchronously "
                "(base64, within about 3s). A non-SOL quote is the pool ratio times that quote token's "
                "own SOL or USDC pool (PumpSwap, Orca, pump.fun, Raydium CLMM, or Meteora DLMM). That pool is resolved, "
                "cached, and subscribed even when it is not the quote token's busiest pair, so an "
                "SPCX-quoted coin and BABA (发发) both stay off Jupiter in steady state. "
                "Every watched PumpSwap pool gets logsSubscribe before vault "
                "accounts take subscription room. A coin with an open position, or viewed in the last "
                "5 minutes, keeps those subscriptions. A pool with no vault subscription is HTTP-polled "
                "at least once a second. The socket thread does not do that HTTP, so acks and "
                "notifications are not stuck behind a poll. /api/health stream reports ws_state, "
                "ws_error, subscriptions (logs: and vault: ids), and last_event. "
                "Browsers poll /api/game/tick every 500ms and never call Jupiter or RPC. "
                "stale is true exactly when quote_age_s > quote_sla_s (5s). Past that, Buy, Sell, and stops "
                "will not fill. A fresh tick stores price_ts, trade_ts, render_ts, src, driver, and fetched_at. "
                "src is pool_swap, pool_vault, jupiter, or jupiter_fallback. "
                "For 5 seconds after a swap the mark is that executed fill (pool_swap). Between swaps "
                "the mark is the vault ratio plus virtual_quote_reserves. The raw vault ratio runs "
                "about 7% under the fill on these pools; the fee schedule (20 bps LP, 5 bps protocol) "
                "is not that gap. "
                "driver is swap, or sol_usd when only the SOL price moved. "
                "A single print more than 15% from the last published price is held. A second "
                "independent read publishes immediately when the two agree within 5%. "
                "The hold is at most 3.0s from hold start. At that deadline the held price "
                "publishes only if a swap and a vault (or two independent on-chain reads) agree "
                "within 5%. Otherwise the coin is stale: Buy, Sell, and a new stop stay blocked, "
                "and an open stop is not filled at an unconfirmed price. When the executed swap and the "
                "vault disagree by more than 5%, the coin is stale immediately and Buy is refused. "
                "The stop still fills at that non-dust executed swap when both legs are below it, or "
                "when that swap is below it. A quiet vault with no disagreeing swap keeps the last "
                "confirmed price fresh. A real crash whose reads "
                "agree still publishes within 3s. A one-off bounce is rejected when a later read "
                "disagrees. A dust buy of a few lamports is not a price; the mark stays on the "
                "effective reserves. "
                "There is no real baseline when none has been accepted, or baseline_age is huge. "
                "The guard does not publish an override in that case. It publishes only once a swap "
                "price and a vault price agree within 5%. Until then the tick is stale and Buy stays "
                "off. Two agreeing on-chain reads replace a stuck price. "
                "A pool with no logs subscription, or a silent one, gets that swap from the chain: "
                "on first watch, in parallel with the vault read, and at most every 10s while still "
                "unconfirmed, getSignaturesForAddress (a short limit) and one getTransaction decode "
                "the newest swap. The confirming price is the swap's post-trade vault state, times "
                "the quote token's USD when the quote is not SOL. An old swap still counts. If those "
                "signatures hold no decodable swap, two vault reads within 1% are the baseline, so a "
                "working vault is not left blank. After that, vault reads keep the price fresh. "
                "Spike rows log hold_start and publish. "
                "That pair is accepted even when it is far from the baseline. A baseline older than 5s cannot veto them. "
                "Held spikes and overrides are stream.spike_rejects and are not a stop or a take-profit "
                "until the override publishes."
            ),
            "qa_tick_down": (
                "Server ?qa_tick=down on /api/game/tick (also buy, sell, and quote) walks this browser "
                "session's one armed mint down 27% over about 60s so a 20% stop can fire (the "
                "cross is inside that minute). Other mints stay on the real feed. When the 60s ends "
                "the walk stops and a later buy fills at the real price. "
                "?qa_tick=off, ?qa_tick=0, and ?qa_tick=up clear the walk cookie. "
                "/api/game/state?qa_tick=down stays the display-only equity nudge. "
                "The shared tick cache, the trade stream, and any other browser's stop are not changed. "
                "A position is stopped on the walked price only for the browser that armed it."
            ),
            "qa_stale": (
                "Page ?qa_stale=1 is this browser only. The client treats live ticks as older than "
                "quote_sla_s, shows stale Ns, and blocks Buy and Sell. It does not change the server feed. "
                "Absent the query, ticks behave normally."
            ),
            "live_tick_ttl_s": game_mod.LIVE_TICK_TTL_S,
            "stream": trade_stream_mod.stream_health(),
            "features": ["filter", "viral", "social_tap", "traders", "paper_log", "game_24h", "name_search", "on_demand_scan", "coin_detail", "viral_live", "filter_theme", "coin_relevant", "mcap_progress", "quote_sync", "game_finish", "sitting_window", "social_tap_fresh", "delay_warning", "game_polish", "token_flow_new_sound", "dismiss_back", "game_24h_restart", "manual_update", "sitting_keep", "trade_chart", "hide_max_unknown", "qa_max_fixture", "game_ai_review", "gold_scar", "game_follow_tab", "filter_smart", "profile_custom_pic", "final_polish", "live_tick", "trade_stream"],
            "sitting_drop_min": 5.0,
            "sitting_aged_min": 180.0,
            "sitting_keep": True,
            "sitting_near_lead_min": 1.0,
            "sitting_fresh_min": 2.0,
            "social_tap_fresh_min": 5.0,
            "quote_sla_s": game_mod.QUOTE_FRESH_SLA_S,
            "quote_band": game_mod.QUOTE_BAND,
            "disclaimer": "Paper research only. Not financial advice. No trades placed.",
        }
    )


@app.get("/api/flagged")
def api_flagged():
    return _json_with_validators(
        FLAGGED_PATH,
        [],
        transform=_prepare_flagged,
        etag_suffix=_flagged_etag_suffix(),
    )


@app.get("/api/gold")
def api_gold():
    """Gold lane only. Empty most of the time is the expected result."""
    rows = _prepare_flagged(_read_json(FLAGGED_PATH, []))
    picks = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("gold_scar"), dict)
        and row["gold_scar"].get("gold") is True
    ]
    return jsonify(
        {
            "ok": True,
            "panel_v": 95,
            "feature": "gold_scar",
            "count": len(picks),
            "empty_ok": True,
            "fixture": _wants_qa_gold(),
            "paper": gold_scar.PAPER,
            "strictness": gold_scar.STRICTNESS,
            "gates": [gate_id for gate_id, _title in gold_scar.GATE_ORDER],
            "note": (
                "Empty most of the time is expected. A high Token Flow score is not gold. "
                "False gold is worse than an empty lane."
            ),
            "picks": picks,
        }
    )


@app.get("/api/qa/gold-fixture")
def api_qa_gold_fixture():
    """QA hook for the synthetic rare card. Does nothing to the live feed by itself."""
    if not _wants_qa_gold():
        return jsonify(
            {
                "ok": True,
                "enabled": False,
                "qa_gold_fixture": False,
                "note": "Opt-in only. Add ?qa_gold=1 or ?qa_gold=arrive. Default Gold section stays empty unless a live row clears the stack.",
            }
        )
    row = qa_gold.prepared_row()
    return jsonify(
        {
            "ok": True,
            "enabled": True,
            "qa_gold_fixture": True,
            "label": row.get("qa_label"),
            "ticker": row.get("ticker"),
            "mint": row.get("mint"),
            "why": (row.get("gold_scar") or {}).get("why"),
            "paper": gold_scar.PAPER,
            "note": "Synthetic rare card for AC-G02/G03/G06. Not a live mint. Other rows are not promoted.",
            "row": row,
        }
    )


@app.get("/api/qa/max-fixture")
def api_qa_max_fixture():
    """QA hook for the synthetic bonding Max. Does nothing to the live feed by itself."""
    if not _wants_qa_max():
        return jsonify(
            {
                "ok": True,
                "enabled": False,
                "qa_max_fixture": False,
                "note": "Opt-in only. Add ?qa_max=1. Default Token Flow is unchanged.",
            }
        )
    row = qa_max.prepared_row()
    prog = row.get("mcap_progress") if isinstance(row.get("mcap_progress"), dict) else {}
    return jsonify(
        {
            "ok": True,
            "enabled": True,
            "qa_max_fixture": True,
            "label": row.get("qa_label"),
            "ticker": row.get("ticker"),
            "mint": row.get("mint"),
            "ceiling_kind": prog.get("ceiling_kind"),
            "ceiling_label": prog.get("ceiling_label"),
            "note": "Synthetic bonding ceiling for AC-X01. Not a live mint. Other rows are not rewritten.",
            "row": row,
        }
    )


@app.get("/api/tweets")
def api_tweets():
    """Social Tap: live public viral items. Does not serve the tweets.json cache."""
    payload = viral_mod.feed_payload()
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    etag = '"' + hashlib.md5(body.encode("utf-8")).hexdigest() + '"'
    inm = request.headers.get("If-None-Match")
    if inm and inm.strip() == etag:
        resp = make_response("", 304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
        return resp
    resp = make_response(jsonify(payload))
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return resp


@app.get("/api/latest")
def api_latest():
    return _json_with_validators(LATEST_PATH, {})


@app.get("/api/status")
def api_status():
    bot = _read_json(STATUS_PATH, {})
    latest = _read_json(LATEST_PATH, {})
    flagged = _read_json(FLAGGED_PATH, [])
    runs_tail = []
    if RUNS_LOG.exists():
        try:
            lines = RUNS_LOG.read_text(encoding="utf-8").strip().splitlines()
            runs_tail = lines[-10:]
        except OSError:
            runs_tail = []
    return jsonify(
        {
            "time": _utc(),
            "bot": bot if isinstance(bot, dict) else {},
            "latest_summary": {
                "run_finished": latest.get("run_finished") if isinstance(latest, dict) else None,
                "data_as_of": latest.get("data_as_of") if isinstance(latest, dict) else None,
                "scanned_mints": latest.get("scanned_mints") if isinstance(latest, dict) else None,
                "evaluated": latest.get("evaluated") if isinstance(latest, dict) else None,
                "flagged_count": latest.get("flagged_count")
                if isinstance(latest, dict)
                else (len(flagged) if isinstance(flagged, list) else 0),
                "trench_band_count": latest.get("trench_band_count")
                if isinstance(latest, dict)
                else None,
            },
            "flagged_rows": len(flagged) if isinstance(flagged, list) else 0,
            "files": {
                "flagged": FLAGGED_PATH.exists(),
                "latest": LATEST_PATH.exists(),
                "bot_status": STATUS_PATH.exists(),
                "tweets": TWEETS_PATH.exists(),
                "runs_log": RUNS_LOG.exists(),
                "game_state": game_mod.STATE_PATH.exists(),
                "game_scores": game_mod.SCORES_PATH.exists(),
            },
            "recent_runs": runs_tail,
            "disclaimer": "Paper research only. Not financial advice. No trades placed.",
        }
    )


def _bot_and_latest() -> tuple[dict, dict]:
    bot = _read_json(STATUS_PATH, {})
    latest = _read_json(LATEST_PATH, {})
    if not isinstance(bot, dict):
        bot = {}
    if not isinstance(latest, dict):
        latest = {}
    return bot, latest


@app.get("/api/update-now")
def api_update_now_status():
    """Kick status. Does not start a pass and does not score a single mint."""
    bot, latest = _bot_and_latest()
    return jsonify(manual_update.public_status(bot, latest))


@app.post("/api/update-now")
def api_update_now():
    """Queue one filter.main() pass on the existing bot loop.

    Not a reload of /api/flagged and not /api/scan. Busy and cooldown reject
    extra taps so passes do not stack.
    """
    bot, latest = _bot_and_latest()
    code, body = manual_update.request_update(bot, latest)
    resp = make_response(jsonify(body), code)
    if code == 429:
        resp.headers["Retry-After"] = str(int(body.get("retry_after_s") or 1))
    print(
        f"[update-now] {code} state={body.get('state')} id={body.get('id')} "
        f"error={body.get('error')}",
        flush=True,
    )
    return resp


# —— Name / ticker search (Game + Filter) ——


@app.get("/api/search")
def api_search():
    q = (request.args.get("q") or request.args.get("query") or request.args.get("mint") or "").strip()
    try:
        limit = int(request.args.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    payload = game_mod.search_payload(q, limit=limit)
    code = 200 if payload.get("ok") else 404
    return jsonify(payload), code


# —— On-demand Filter score for any mint/name ——


@app.get("/api/scan")
def api_scan():
    """Resolve q → mint, then run filter.evaluate stack once (not a full bot run)."""
    import filter as filter_mod

    q = (request.args.get("q") or request.args.get("query") or request.args.get("mint") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "q required — name, ticker, or CA"}), 400
    fresh = (request.args.get("fresh") or "").lower() in ("1", "true", "yes")

    # optional pick=mint when UI already chose from picker
    pick = (request.args.get("mint") or request.args.get("ca") or "").strip()
    resolved = None
    src = None
    err = None
    if pick and game_mod.looks_like_mint(pick):
        resolved, err, src = pick, None, "mint"
    else:
        # if ambiguous name, surface picker instead of scoring wrong coin
        if not game_mod.looks_like_mint(q):
            hits = game_mod.search_candidates(q, limit=8)
            if not hits:
                return jsonify({"ok": False, "error": f"No Solana matches for {q!r}", "query": q}), 404
            if len(hits) > 1:
                # still score best, but return candidates so UI can re-pick
                resolved = hits[0]["mint"]
                src = hits[0].get("source") or "dex_search"
                result = filter_mod.scan_mint(resolved, bypass_cache=fresh)
                result["query"] = q
                result["resolved_mint"] = resolved
                result["resolve_source"] = src
                result["candidates"] = hits
                result["need_pick"] = True
                code = 200 if result.get("ok") else 400
                return jsonify(result), code
            resolved = hits[0]["mint"]
            src = hits[0].get("source") or "dex_search"
        else:
            resolved, err, src = game_mod.resolve_mint(q)
    if not resolved:
        return jsonify({"ok": False, "error": err or "Could not resolve", "query": q}), 404
    result = filter_mod.scan_mint(resolved, bypass_cache=fresh)
    result["query"] = q
    result["resolved_mint"] = resolved
    result["resolve_source"] = src
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.get("/api/coin")
def api_coin():
    """Coin detail: snapshot row when flagged, else one scan, plus RugCheck report when it returns."""
    import filter as filter_mod

    mint = (request.args.get("mint") or request.args.get("ca") or request.args.get("q") or "").strip()
    if not mint:
        return jsonify({"ok": False, "error": "mint required"}), 400
    if game_mod.looks_like_mint(mint):
        game_mod.watch_mint(mint)
    if qa_gold.is_fixture_mint(mint):
        # Never scan this synthetic mint. The card exists only while qa_gold is on.
        if not _wants_qa_gold():
            return jsonify(
                {
                    "ok": False,
                    "error": "QA Gold fixture is opt-in. Open the panel with ?qa_gold=1.",
                    "mint": mint,
                    "qa_gold_fixture": False,
                }
            ), 400
        row = qa_gold.prepared_row()
        return jsonify(
            {
                "ok": True,
                "mint": mint,
                "source": "qa_gold_fixture",
                "qa_gold_fixture": True,
                "row": row,
                "research": row.get("research"),
            }
        )
    if qa_max.is_fixture_mint(mint):
        # Never scan this synthetic mint. The row exists only while qa_max is on.
        if not _wants_qa_max():
            return jsonify(
                {
                    "ok": False,
                    "error": "QA Max fixture is opt-in. Open the panel with ?qa_max=1.",
                    "mint": mint,
                    "qa_max_fixture": False,
                }
            ), 400
        row = qa_max.prepared_row()
        return jsonify(
            {
                "ok": True,
                "mint": mint,
                "source": "qa_max_fixture",
                "qa_max_fixture": True,
                "row": row,
                "research": row.get("research"),
            }
        )
    fresh = (request.args.get("fresh") or "").lower() in ("1", "true", "yes")
    result = filter_mod.coin_payload(mint, bypass_cache=fresh)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


# —— Paper log (research CSV) ——


@app.post("/api/paper")
def api_paper():
    body = request.get_json(silent=True) or {}
    mint = (body.get("mint") or body.get("address") or body.get("ca") or "").strip()
    if body.get("stake_usd") is None or body.get("stop_loss_pct") is None or body.get("take_profit_pct") is None:
        # also accept short keys
        if body.get("stake") is not None and body.get("sl_pct") is not None and body.get("tp_pct") is not None:
            result = game_mod.append_paper_trade(
                mint=mint,
                stake_usd=body.get("stake"),
                sl_pct=body.get("sl_pct"),
                tp_pct=body.get("tp_pct"),
                user_note=body.get("note") or body.get("user_note"),
            )
        else:
            return jsonify(
                {
                    "ok": False,
                    "error": "Require mint + stake_usd + stop_loss_pct + take_profit_pct (will not invent values)",
                }
            ), 400
    else:
        result = game_mod.append_paper_trade(
            mint=mint,
            stake_usd=body["stake_usd"],
            sl_pct=body["stop_loss_pct"],
            tp_pct=body["take_profit_pct"],
            user_note=body.get("note") or body.get("user_note"),
        )
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


# —— 24h $5 game ——


def _wants_qa_end() -> bool:
    """One-shot 24h force-end. Persists the close. Does not start a new run."""
    raw = (request.args.get("qa_end") or "").strip().lower()
    if raw in ("1", "true", "yes", "end"):
        return True
    if request.method != "POST":
        return False
    body = request.get_json(silent=True) or {}
    flag = body.get("qa_end")
    if flag is True or flag == 1:
        return True
    return str(flag or "").strip().lower() in ("1", "true", "yes", "end")


@app.get("/api/game/state")
def api_game_state():
    if _wants_qa_end():
        game_mod.force_end_session()
    return jsonify(game_mod.qa_tick_view(game_mod.public_state(), request.args.get("qa_tick")))


@app.post("/api/game/state")
def api_game_state_tick():
    """Client-driven refresh / SL-TP / force-close tick.

    Optional ?qa_tick=up|down|flat nudges this response only. The paper book
    is saved by public_state before that view, and the nudge is not written back.
    Optional ?qa_end=1 force-ends an active run (persisted) and does not start another.
    """
    if _wants_qa_end():
        game_mod.force_end_session()
    return jsonify(game_mod.qa_tick_view(game_mod.public_state(), request.args.get("qa_tick")))


@app.post("/api/game/end")
def api_game_end():
    """Force-end the active 24h paper run. Same close as the clock. No auto-start.

    Body session_id, when present, must be the active run. A stale end for an
    older session does not close the current one.
    """
    body = request.get_json(silent=True) or {}
    sid = str(body.get("session_id") or "").strip() or None
    st = game_mod.force_end_session(session_id=sid)
    return jsonify({"ok": True, "state": game_mod.public_state(st), "sound": "end"})


@app.post("/api/game/start")
def api_game_start():
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    st = game_mod.start_session(force=force)
    return jsonify({"ok": True, "state": game_mod.public_state(st), "sound": "start"})


@app.post("/api/game/buy")
def api_game_buy():
    body = request.get_json(silent=True) or {}
    mint = (body.get("mint") or body.get("address") or body.get("ca") or "").strip()
    if not mint:
        return jsonify({"ok": False, "error": "mint required", "sound": "fail"}), 400
    if body.get("stake_usd") is None and body.get("stake") is None:
        return jsonify({"ok": False, "error": "stake_usd required", "sound": "fail"}), 400
    stake = body.get("stake_usd", body.get("stake"))
    sl = body.get("sl_pct", body.get("stop_loss_pct"))
    tp = body.get("tp_pct", body.get("take_profit_pct"))
    rid = body.get("client_request_id") or body.get("request_id")
    shown = body.get("displayed_price", body.get("seen_price"))
    result = game_mod.buy(
        mint, stake, sl_pct=sl, tp_pct=tp, client_request_id=rid, displayed_price=shown,
    )
    code = 200 if result.get("ok") else 400
    if result.get("state") is not None:
        result["state"] = game_mod.public_state(result.get("state"))
    return jsonify(result), code


@app.post("/api/game/sell")
def api_game_sell():
    body = request.get_json(silent=True) or {}
    if body.get("all") or body.get("sell_all"):
        result = game_mod.sell_all()
        code = 200 if result.get("ok") else 400
        result["state"] = game_mod.public_state(result.get("state"))
        return jsonify(result), code
    pid = (body.get("position_id") or body.get("id") or "").strip()
    if not pid:
        return jsonify({"ok": False, "error": "position_id required", "sound": "fail"}), 400
    result = game_mod.sell(pid)
    code = 200 if result.get("ok") else 400
    result["state"] = game_mod.public_state(result.get("state"))
    return jsonify(result), code


@app.get("/api/game/tick")
def api_game_tick():
    """Lightweight price for the open Game chart. Cached <=2s on the server."""
    mint = (request.args.get("mint") or request.args.get("ca") or "").strip()
    if not mint:
        return jsonify({"ok": False, "error": "mint required", "quote_live": False}), 400
    if not game_mod.looks_like_mint(mint):
        resolved, err, _src = game_mod.resolve_mint(mint)
        if not resolved:
            return jsonify({"ok": False, "error": err or "mint required", "quote_live": False}), 400
        mint = resolved
    import trade_stream
    trade_stream.note_view(mint)
    tick = game_mod.fetch_live_tick(mint)
    if not isinstance(tick, dict):
        reason = "no_baseline" if trade_stream.awaiting_baseline(mint) else "cold"
        return jsonify({
            "ok": False,
            "mint": mint,
            "price_usd": None,
            "quote_live": False,
            "stale": True,
            "stale_label": "STALE",
            "stale_reason": reason,
            "driver": reason,
            "quote_sla_s": game_mod.QUOTE_FRESH_SLA_S,
            "error": "no price",
        })
    age = tick.get("quote_age_s")
    sla = game_mod.QUOTE_FRESH_SLA_S
    # stale is exactly age > quote_sla_s, even if a caller marked quote_live.
    stale = game_mod.tick_is_stale(age, sla)
    px = tick.get("price_usd")
    try:
        px_ok = px is not None and float(px) > 0
    except (TypeError, ValueError):
        px_ok = False
    # A missing price is never a live tick. Cold and no-baseline say why.
    missing_reason = ""
    if not px_ok:
        stale = True
        if trade_stream.awaiting_baseline(mint) or tick.get("stale_reason") == "no_baseline":
            missing_reason = "no_baseline"
        else:
            missing_reason = "cold"
        px = None
    body = {
        "ok": True if px_ok else False,
        "mint": mint,
        "price_usd": px,
        "price_ts": tick.get("price_ts"),
        "quoted_at": tick.get("quoted_at") or tick.get("price_ts"),
        "quoted_epoch": tick.get("quoted_epoch"),
        "quote_age_s": age,
        "quote_live": bool(px_ok and not stale and age is not None),
        "quote_sla_s": sla,
        "source": tick.get("source"),
        "src": tick.get("src") or game_mod.public_src(tick.get("source")),
        "driver": missing_reason or tick.get("driver") or "swap",
        "fetched_at": tick.get("fetched_at") or tick.get("price_ts"),
        "trade_ts": tick.get("trade_ts") or tick.get("price_ts"),
        "render_ts": tick.get("render_ts") or _utc(),
        "stale": stale,
        "stale_label": (
            "STALE" if missing_reason else (("stale %ss" % int(round(float(age)))) if stale and age is not None else ("STALE" if stale else ""))
        ),
    }
    if missing_reason:
        body["stale_reason"] = missing_reason
        body["error"] = "no price"
    if tick.get("qa_tick"):
        body["qa_tick"] = tick.get("qa_tick")
        body["qa_walk_drop"] = tick.get("qa_walk_drop")
    return jsonify(body)


@app.get("/api/game/quote")
def api_game_quote():
    mint = (request.args.get("mint") or request.args.get("ca") or request.args.get("q") or "").strip()
    if not mint:
        return jsonify({"ok": False, "error": "Enter a coin — CA, ticker, or name", "sound": "fail", "started": False}), 400
    fresh = (request.args.get("fresh") or "").lower() in ("1", "true", "yes")
    # multi-hit name: return picker instead of silent wrong coin
    if not game_mod.looks_like_mint(mint):
        hits = game_mod.search_candidates(mint, limit=8)
        if len(hits) > 1:
            return jsonify({
                "ok": False,
                "need_pick": True,
                "error": f"{len(hits)} Solana matches — pick one",
                "query": mint,
                "candidates": hits,
            })
        if len(hits) == 1:
            mint = hits[0]["mint"]
    return jsonify(game_mod.quote_payload(mint, fresh=fresh))


@app.get("/api/game/scores")
def api_game_scores():
    return jsonify({"ok": True, "scores": game_mod.load_scores()})


@app.post("/api/game/profile")
def api_game_profile():
    body = request.get_json(silent=True) or {}
    kwargs = {}
    if "name" in body:
        kwargs["name"] = body.get("name")
    if "pfp" in body:
        kwargs["pfp"] = body.get("pfp")
    if "pfp_data" in body:
        kwargs["pfp_data"] = body.get("pfp_data")
    result = game_mod.update_profile(**kwargs)
    code = 200 if result.get("ok") else 400
    if result.get("ok"):
        result["state"] = game_mod.public_state()
    return jsonify(result), code


@app.post("/api/game/shop")
def api_game_shop():
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip().lower()
    skin = str(body.get("id") or body.get("skin") or "").strip()
    if action == "buy":
        result = game_mod.buy_cosmetic(skin)
    elif action == "equip":
        result = game_mod.equip_cosmetic(skin)
    else:
        return jsonify({"ok": False, "error": "action must be buy or equip"}), 400
    code = 200 if result.get("ok") else 400
    if result.get("ok"):
        result["state"] = game_mod.public_state()
    return jsonify(result), code


@app.after_request
def _qa_walk_cookie(resp: Response):
    if getattr(g, "qa_walk_clear_cookie", False):
        # Max-Age=0 so the browser drops nunu_qa_walk. Same path and flags as the set.
        resp.set_cookie(
            game_mod.QA_WALK_COOKIE,
            "",
            max_age=0,
            expires=0,
            path="/",
            httponly=True,
            samesite="Lax",
        )
        _set_client_cookie(resp)
        return resp
    sid = getattr(g, "qa_walk_set_cookie", None)
    if sid:
        resp.set_cookie(
            game_mod.QA_WALK_COOKIE,
            sid,
            httponly=True,
            samesite="Lax",
            path="/",
        )
    _set_client_cookie(resp)
    return resp


def _set_client_cookie(resp: Response) -> None:
    client = getattr(g, "client_set_cookie", None)
    if not client:
        return
    resp.set_cookie(
        game_mod.CLIENT_COOKIE,
        client,
        max_age=60 * 60 * 24 * 400,
        httponly=True,
        samesite="Lax",
        path="/",
    )


@app.after_request
def _fast_headers(resp: Response):
    path = request.path or ""
    if path.startswith("/static") or path.endswith((".js", ".css", ".png", ".jpg", ".webp", ".svg")):
        resp.headers.setdefault("Cache-Control", "public, max-age=300")
    try:
        accept = request.headers.get("Accept-Encoding", "")
        if (
            "gzip" in accept
            and resp.status_code == 200
            and not resp.direct_passthrough
            and resp.headers.get("Content-Encoding") is None
        ):
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype in (
                "application/json",
                "text/html",
                "text/css",
                "application/javascript",
                "text/javascript",
            ):
                import gzip as _gzip

                data = resp.get_data()
                if data and len(data) >= 512:
                    resp.set_data(_gzip.compress(data, compresslevel=5))
                    resp.headers["Content-Encoding"] = "gzip"
                    resp.headers["Vary"] = "Accept-Encoding"
                    resp.headers["Content-Length"] = str(len(resp.get_data()))
    except Exception:
        pass
    return resp


@app.get("/")
def index():
    resp = send_from_directory(STATIC_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.get("/favicon.ico")
def favicon():
    return ("", 204)


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "8765"))
    except ValueError:
        port = 8765
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    print(f"nunu-filter server on http://{host}:{port}/ (panel + /api/*)", flush=True)
    print("Paper research only — no wallets, no order execution.", flush=True)
    import trade_stream
    trade_stream.ensure_started()
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()
