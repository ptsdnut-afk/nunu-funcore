#!/usr/bin/env python3
"""Cross-process kick for one filter pass.

The bot loop and the Flask panel do not import each other. They share this
file. POST /api/update-now writes state "queued". The sleeping bot notices
within one tick and runs filter.main() (the same pass as the 5-minute loop).
A second tap while that pass is queued or running is rejected. After it
finishes, a cooldown rejects another kick so a mash cannot stack passes.

This is not /api/scan. scan_mint scores one mint and does not refresh Token Flow.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent
KICK_PATH = DIR / "manual_update.json"
LOCK_PATH = DIR / "manual_update.lock"

# After a manual pass settles, ignore new kicks this long. The 5-minute
# loop is unchanged; this only spaces on-demand passes.
COOLDOWN_SEC = 60
# Queued but the loop never claimed it (idle, not inside a pass).
STALE_QUEUED_SEC = 90
# Claimed or attached, and the bot is not in running_filter.
STALE_RUNNING_SEC = 45

PIPELINE = "bot_loop"


def _utc(ts: float | None = None) -> str:
    when = datetime.now(timezone.utc) if ts is None else datetime.fromtimestamp(ts, timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: object) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def pid_alive(pid: object) -> bool:
    try:
        pid_i = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if pid_i <= 0:
        return False
    try:
        os.kill(pid_i, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def bot_alive(bot: dict | None) -> bool:
    if not isinstance(bot, dict) or not bot.get("running"):
        return False
    return pid_alive(bot.get("pid"))


@contextmanager
def _locked():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.touch(exist_ok=True)
    import fcntl

    with LOCK_PATH.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read() -> dict | None:
    if not KICK_PATH.exists():
        return None
    try:
        data = json.loads(KICK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write(data: dict) -> None:
    KICK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = KICK_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(KICK_PATH)


def _retry_after(kick: dict | None, now: float | None = None) -> int:
    if not kick:
        return 0
    end = _parse(kick.get("cooldown_until"))
    if end is None:
        return 0
    remain = end - (time.time() if now is None else now)
    if remain <= 0:
        return 0
    return int(remain + 0.999)


def _mark_finished_unlocked(
    kick: dict,
    code: int,
    finished: str,
    error: str | None = None,
) -> dict:
    if kick.get("state") in ("done", "error"):
        return kick
    code_i = int(code)
    fail = error or (None if code_i == 0 else "Filter pass failed")
    kick = dict(kick)
    kick["state"] = "error" if fail else "done"
    kick["exit_code"] = code_i
    kick["finished_at"] = finished or _utc()
    kick["error"] = fail
    kick["cooldown_until"] = _utc(time.time() + COOLDOWN_SEC)
    _write(kick)
    return kick


def _stale(kick: dict, seconds: float) -> bool:
    anchor = _parse(kick.get("started_at") or kick.get("requested_at"))
    if anchor is None:
        return False
    return (time.time() - anchor) > seconds


def _reconcile_unlocked(bot: dict) -> dict | None:
    """Settle a kick the bot already finished, or one that will never start."""
    kick = _read()
    if not kick:
        return None
    state = kick.get("state")
    phase = bot.get("phase")
    if state == "queued":
        if phase != "running_filter" and _stale(kick, STALE_QUEUED_SEC):
            return _mark_finished_unlocked(
                kick, 1, _utc(), error="Bot did not start the update"
            )
        return kick
    if state != "running":
        return kick

    if kick.get("attached"):
        watch = kick.get("watch_started")
        same = bool(watch) and bot.get("last_run_started") == watch
        if phase == "running_filter" and same:
            return kick
        if (
            same
            and phase != "running_filter"
            and bot.get("last_run_finished")
            and bot.get("last_exit_code") is not None
        ):
            return _mark_finished_unlocked(
                kick, int(bot["last_exit_code"]), str(bot["last_run_finished"])
            )
        if phase != "running_filter" and _stale(kick, STALE_RUNNING_SEC):
            return _mark_finished_unlocked(kick, 1, _utc(), error="Update stalled")
        return kick

    manual_id = bot.get("manual_id") or ""
    if manual_id == kick.get("id") and phase == "running_filter":
        return kick
    if (
        manual_id == kick.get("id")
        and phase != "running_filter"
        and bot.get("last_run_finished")
        and bot.get("last_exit_code") is not None
    ):
        return _mark_finished_unlocked(
            kick, int(bot["last_exit_code"]), str(bot["last_run_finished"])
        )
    if phase != "running_filter" and _stale(kick, STALE_RUNNING_SEC):
        return _mark_finished_unlocked(kick, 1, _utc(), error="Update stalled")
    return kick


def _view(
    kick: dict | None,
    bot: dict | None,
    latest: dict | None,
    *,
    ok: bool,
    error: str | None = None,
    force_state: str | None = None,
) -> dict:
    bot = bot if isinstance(bot, dict) else {}
    latest = latest if isinstance(latest, dict) else {}
    kick = kick or {}
    state = force_state or kick.get("state") or "idle"
    if error is None and state == "error":
        error = kick.get("error") or "Filter pass failed"
    retry = _retry_after(kick)
    return {
        "ok": ok,
        "feature": "manual_update",
        "pipeline": PIPELINE,
        "state": state,
        "id": kick.get("id"),
        "attached": bool(kick.get("attached")),
        "requested_at": kick.get("requested_at"),
        "started_at": kick.get("started_at") or kick.get("watch_started"),
        "finished_at": kick.get("finished_at"),
        "exit_code": kick.get("exit_code"),
        "cooldown_s": COOLDOWN_SEC,
        "cooldown_until": kick.get("cooldown_until"),
        "retry_after_s": retry,
        "bot_running": bot_alive(bot),
        "bot_phase": bot.get("phase"),
        "interval_min": bot.get("interval_min"),
        "pass_started": bot.get("last_run_started"),
        "pass_finished": bot.get("last_run_finished"),
        "data_as_of": latest.get("data_as_of"),
        "run_finished": latest.get("run_finished"),
        "error": error,
    }


def queued() -> bool:
    with _locked():
        kick = _read()
        return bool(kick and kick.get("state") == "queued" and not kick.get("attached"))


def claim_queued() -> str | None:
    """Mark one queued kick running. The caller must run filter.main()."""
    with _locked():
        kick = _read()
        if not kick or kick.get("state") != "queued" or kick.get("attached"):
            return None
        kick["state"] = "running"
        kick["attached"] = False
        kick["started_at"] = _utc()
        _write(kick)
        return str(kick.get("id") or "")


def complete(kick_id: str, code: int) -> dict | None:
    with _locked():
        kick = _read()
        if not kick or kick.get("id") != kick_id:
            return kick
        if kick.get("state") in ("done", "error"):
            return kick
        fail = None if int(code) == 0 else "Filter pass failed"
        return _mark_finished_unlocked(kick, int(code), _utc(), error=fail)


def cover_queued(finished: str, code: int) -> dict | None:
    """A pass already in flight covers a kick that landed mid-run.

    Stops a stale idle read from stacking a second filter.main() immediately
    after the one that just wrote fresh data_as_of.
    """
    with _locked():
        kick = _read()
        if not kick or kick.get("state") != "queued" or kick.get("attached"):
            return kick
        kick["covered_by"] = "in_flight_pass"
        fail = None if int(code) == 0 else "Filter pass failed"
        return _mark_finished_unlocked(kick, int(code), finished or _utc(), error=fail)


def public_status(bot: dict | None, latest: dict | None = None) -> dict:
    bot = bot if isinstance(bot, dict) else {}
    with _locked():
        kick = _reconcile_unlocked(bot)
        err = None
        if kick and kick.get("state") == "error":
            err = kick.get("error") or "Filter pass failed"
        return _view(kick, bot, latest, ok=True, error=err)


def request_update(bot: dict | None, latest: dict | None = None) -> tuple[int, dict]:
    """Queue one bot-loop pass, or reject when one is already going / cooling down."""
    bot = bot if isinstance(bot, dict) else {}
    with _locked():
        kick = _reconcile_unlocked(bot)
        if not bot_alive(bot):
            return 503, _view(kick, bot, latest, ok=False, error="Bot loop is not running")
        if kick and kick.get("state") in ("queued", "running"):
            return 409, _view(
                kick,
                bot,
                latest,
                ok=False,
                error="Filter pass already running",
            )
        retry = _retry_after(kick)
        if retry > 0:
            return 429, _view(
                kick,
                bot,
                latest,
                ok=False,
                error=f"Wait {retry}s before another update",
                force_state="cooldown",
            )
        now = _utc()
        if bot.get("phase") == "running_filter":
            kick = {
                "id": uuid.uuid4().hex,
                "state": "running",
                "attached": True,
                "pipeline": PIPELINE,
                "requested_at": now,
                "watch_started": bot.get("last_run_started"),
                "started_at": bot.get("last_run_started"),
                "finished_at": None,
                "exit_code": None,
                "error": None,
                "cooldown_until": None,
            }
            _write(kick)
            return 202, _view(kick, bot, latest, ok=True)
        kick = {
            "id": uuid.uuid4().hex,
            "state": "queued",
            "attached": False,
            "pipeline": PIPELINE,
            "requested_at": now,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "error": None,
            "cooldown_until": None,
        }
        _write(kick)
        return 202, _view(kick, bot, latest, ok=True)
