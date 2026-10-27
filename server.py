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
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, request, send_from_directory
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

_json_cache: dict[str, tuple[int, object]] = {}


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


def _prepare_flagged(data):
    """Research card, optional QA rows, then a fresh Gold Scar verdict on every row.

    Gold is recomputed here. A ``gold`` stamp inside flagged.json does not promote.
    """
    rows = _with_research_rows(data)
    if not isinstance(rows, list):
        rows = []
    if _wants_qa_max():
        kept = [row for row in rows if not (isinstance(row, dict) and row.get("qa_max_fixture"))]
        rows = [qa_max.prepared_row()] + kept
    rows = gold_scar.annotate(rows)
    if _wants_qa_gold():
        rows = qa_gold.prepend(rows)
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
    return jsonify(
        {
            "ok": True,
            "service": "nunu-filter",
            "panel_v": 77,
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
                "Separate Gold picks section. Promotion recomputed on each flagged read from the "
                "owner 7-gate stack, stricter: score/12 >= 11, copy pack at 3, crowded politics out, "
                "1h under +180 with a required 5m print, clean on-chain dig including top holders, "
                "overheard posts only (a profile URL is not enough; live context passes none), "
                "macro veto unless gold_macro.json explicitly says Fed-quiet and Solana-ok. "
                "Gate evidence is on each row as gold_scar and on GET /api/gold. Empty is expected."
            ),
            "features": ["filter", "viral", "social_tap", "traders", "paper_log", "game_24h", "name_search", "on_demand_scan", "coin_detail", "viral_live", "filter_theme", "coin_relevant", "mcap_progress", "quote_sync", "game_finish", "sitting_window", "social_tap_fresh", "delay_warning", "game_polish", "token_flow_new_sound", "dismiss_back", "game_24h_restart", "manual_update", "sitting_keep", "trade_chart", "hide_max_unknown", "qa_max_fixture", "game_ai_review", "gold_scar"],
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
            "panel_v": 77,
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
    """Force-end the active 24h paper run. Same close as the clock. No auto-start."""
    st = game_mod.force_end_session()
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
    result = game_mod.buy(mint, stake, sl_pct=sl, tp_pct=tp)
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
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()
