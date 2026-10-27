#!/usr/bin/env python3
"""
Nunu filter bot loop (paper research only).

Runs filter.main() once or on an interval. Does not import server.py.
Shares state only through JSON files written by filter.py.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent
STATUS_PATH = DIR / "bot_status.json"
DATA_DIR = DIR

# Ensure project dir is on path when launched from elsewhere
if str(DIR) not in sys.path:
    sys.path.insert(0, str(DIR))

from filter import main as run_filter  # noqa: E402

import manual_update  # noqa: E402


_stop = False


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _stop
    _stop = True
    print(f"\n[{_utc()}] bot received signal {signum}; stopping after current cycle…", flush=True)


def write_status(**kwargs) -> None:
    payload = {
        "updated_at": _utc(),
        "pid": os.getpid(),
        "disclaimer": "Paper research only. Not financial advice. No trades placed.",
    }
    payload.update(kwargs)
    try:
        STATUS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"WARN could not write {STATUS_PATH}: {e}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nunu memecoin filter bot (paper research)")
    p.add_argument(
        "--once",
        action="store_true",
        help="Run a single filter pass and exit",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Minutes between runs (default: INTERVAL_MIN env or 5)",
    )
    p.add_argument(
        "--skip-fresh-sec",
        type=float,
        default=None,
        help="Skip filter if latest.json newer than this many seconds "
             "(default: SKIP_FRESH_SEC env or 240; 0=always run)",
    )
    return p.parse_args()


def latest_age_sec() -> float | None:
    path = DATA_DIR / "latest.json"
    if not path.exists():
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def iteration(
    *,
    skip_fresh: float,
    once: bool,
    interval_min: float,
    mode: str,
    cycles: int,
) -> tuple[int, int, bool]:
    """One filter cycle. A queued manual kick bypasses the fresh-file skip."""
    kick_id = manual_update.claim_queued()
    forced = kick_id is not None
    started = _utc()
    if forced:
        print(f"[{started}] manual update — running filter pass now", flush=True)
    write_status(
        mode=mode,
        interval_min=None if once else interval_min,
        running=True,
        last_run_started=started,
        last_run_finished=None,
        last_exit_code=None,
        cycles=cycles,
        phase="running_filter",
        trigger="manual" if forced else "loop",
        manual_id=kick_id or "",
    )
    code = 1
    phase = "idle"
    skipped = False
    try:
        age = latest_age_sec()
        if (
            not forced
            and not once
            and skip_fresh
            and skip_fresh > 0
            and age is not None
            and age < skip_fresh
        ):
            print(
                f"[{started}] skip filter — latest.json age {age:.0f}s "
                f"< {skip_fresh:.0f}s (scheduled routine likely ran)",
                flush=True,
            )
            code = 0
            phase = "skipped_fresh"
            skipped = True
        else:
            code = int(run_filter() or 0)
            phase = "idle"
    except Exception as e:  # keep loop alive on unexpected errors
        print(f"ERROR filter raised: {e}", file=sys.stderr, flush=True)
        code = 1
        phase = "idle"
    finally:
        finished = _utc()
        cycles += 1
        write_status(
            mode=mode,
            interval_min=None if once else interval_min,
            running=not once and not _stop,
            last_run_started=started,
            last_run_finished=finished,
            last_exit_code=code,
            cycles=cycles,
            phase="done" if once else phase,
            trigger="manual" if forced else "loop",
            manual_id=kick_id or "",
        )
        print(
            f"[{finished}] filter exit={code} cycle={cycles} "
            f"trigger={'manual' if forced else 'loop'}",
            flush=True,
        )
        if kick_id:
            manual_update.complete(kick_id, code)
        elif not skipped:
            # A kick that raced a pass already in flight is satisfied by it.
            manual_update.cover_queued(finished, code)
    return code, cycles, forced


def interruptible_sleep(seconds: float) -> str:
    """Sleep until the interval elapses, stop is requested, or a kick is queued."""
    end = time.time() + max(0.0, seconds)
    while not _stop and time.time() < end:
        if manual_update.queued():
            return "kick"
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(0.5, remaining))
    if _stop:
        return "stop"
    return "elapsed"


def main() -> int:
    args = parse_args()
    interval_min = args.interval
    if interval_min is None:
        try:
            interval_min = float(os.environ.get("INTERVAL_MIN", "5"))
        except ValueError:
            interval_min = 5.0
    if interval_min < 1:
        interval_min = 1.0

    skip_fresh = args.skip_fresh_sec
    if skip_fresh is None:
        try:
            skip_fresh = float(os.environ.get("SKIP_FRESH_SEC", "240"))
        except ValueError:
            skip_fresh = 240.0

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    mode = "once" if args.once else f"loop every {interval_min:g} min"
    print(f"[{_utc()}] nunu-bot starting ({mode})", flush=True)
    write_status(
        mode=mode,
        interval_min=interval_min if not args.once else None,
        running=True,
        last_run_started=None,
        last_run_finished=None,
        last_exit_code=None,
        cycles=0,
    )

    cycles = 0
    while not _stop:
        _code, cycles, _forced = iteration(
            skip_fresh=skip_fresh,
            once=bool(args.once),
            interval_min=interval_min,
            mode=mode,
            cycles=cycles,
        )
        if args.once or _stop:
            break

        sleep_s = interval_min * 60.0
        why = interruptible_sleep(sleep_s)
        if why == "kick":
            print(f"[{_utc()}] manual update queued — ending sleep early", flush=True)
        if why == "stop":
            break

    write_status(
        mode=mode,
        interval_min=None if args.once else interval_min,
        running=False,
        last_run_finished=_utc(),
        cycles=cycles,
        phase="stopped",
    )
    print(f"[{_utc()}] nunu-bot stopped after {cycles} cycle(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
