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

# Ensure project dir is on path when launched from elsewhere
if str(DIR) not in sys.path:
    sys.path.insert(0, str(DIR))

from filter import main as run_filter  # noqa: E402


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
    path = DIR / "latest.json"
    if not path.exists():
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


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
        started = _utc()
        write_status(
            mode=mode,
            interval_min=None if args.once else interval_min,
            running=True,
            last_run_started=started,
            last_run_finished=None,
            last_exit_code=None,
            cycles=cycles,
            phase="running_filter",
        )
        try:
            age = latest_age_sec()
            if (
                not args.once
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
                write_status(
                    mode=mode,
                    interval_min=None if args.once else interval_min,
                    running=True,
                    last_run_started=started,
                    last_run_finished=_utc(),
                    last_exit_code=0,
                    cycles=cycles,
                    phase="skipped_fresh",
                    latest_age_sec=round(age, 1),
                )
            else:
                code = int(run_filter() or 0)
        except Exception as e:  # keep loop alive on unexpected errors
            print(f"ERROR filter raised: {e}", file=sys.stderr, flush=True)
            code = 1
        finished = _utc()
        cycles += 1
        write_status(
            mode=mode,
            interval_min=None if args.once else interval_min,
            running=not args.once and not _stop,
            last_run_started=started,
            last_run_finished=finished,
            last_exit_code=code,
            cycles=cycles,
            phase="idle" if not args.once else "done",
        )
        print(f"[{finished}] filter exit={code} cycle={cycles}", flush=True)

        if args.once or _stop:
            break

        sleep_s = interval_min * 60.0
        # Interruptible sleep (0.5s ticks — snappier stop)
        end = time.time() + sleep_s
        while not _stop and time.time() < end:
            time.sleep(min(0.5, end - time.time()))

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
