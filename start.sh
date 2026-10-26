#!/usr/bin/env bash
# Foreground supervisor for the Nunu filter bot + Flask panel.
#
# Render web Start Command:
#   bash start.sh
#
# Stays in the foreground for the life of both children (do not nohup-and-exit).
# Forwards SIGINT/SIGTERM. If either child exits on its own, stops the other
# and exits non-zero so Render restarts the service.
#
# Requires bash 4.3+ (`wait -n`). Do not run this with dash/`sh`.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

INTERVAL_MIN="${INTERVAL_MIN:-5}"
PORT="${PORT:-8765}"
HOST="${HOST:-0.0.0.0}"
# bot.py's own default skip is 240s based on latest.json mtime. A fresh
# checkout makes that mtime "now" while data_as_of is still the git snapshot,
# so the first loop would skip and then sleep the whole interval. Default 0
# runs a real pass on boot. Set SKIP_FRESH_SEC=240 to restore the skip.
SKIP_FRESH_SEC="${SKIP_FRESH_SEC:-0}"

is_number() {
  [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

if ! is_number "$INTERVAL_MIN"; then
  echo "[start] INTERVAL_MIN must be a number of minutes (got ${INTERVAL_MIN})" >&2
  exit 1
fi
if ! is_number "$SKIP_FRESH_SEC"; then
  echo "[start] SKIP_FRESH_SEC must be a number of seconds (got ${SKIP_FRESH_SEC})" >&2
  exit 1
fi
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  echo "[start] PORT must be an integer (got ${PORT})" >&2
  exit 1
fi

export HOST PORT INTERVAL_MIN SKIP_FRESH_SEC
export PYTHONUNBUFFERED=1

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -f venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[start] python3 not found" >&2
  exit 127
fi

if [[ ! -f bot.py || ! -f server.py ]]; then
  echo "[start] bot.py and server.py must sit next to start.sh (dir=${DIR})" >&2
  exit 1
fi

bot_pid=""
server_pid=""
shutting_down=0

kill_one() {
  local pid="$1"
  local sig="$2"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill -s "$sig" "$pid" 2>/dev/null || true
  fi
}

reap() {
  local pid
  for pid in "$bot_pid" "$server_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}

# Stop both children. TERM first so bot.py can mark itself stopped; KILL if
# a filter pass is still running when the platform's stop grace is ticking.
shutdown() {
  if [[ "$shutting_down" -eq 1 ]]; then
    return
  fi
  shutting_down=1
  echo "[start] stopping bot and server" >&2
  kill_one "$bot_pid" TERM
  kill_one "$server_pid" TERM
  local i alive pid
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    alive=0
    for pid in "$bot_pid" "$server_pid"; do
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        alive=1
      fi
    done
    if [[ "$alive" -eq 0 ]]; then
      break
    fi
    sleep 0.5
  done
  if [[ "$alive" -eq 1 ]]; then
    echo "[start] children still running after 10s; sending SIGKILL" >&2
    kill_one "$bot_pid" KILL
    kill_one "$server_pid" KILL
  fi
  reap
}

trap 'shutdown; echo "[start] stopped"; exit 0' INT TERM

echo "[start] python=$("$PY" -V 2>&1) interval=${INTERVAL_MIN}min skip_fresh=${SKIP_FRESH_SEC}s bind=${HOST}:${PORT}"
echo "[start] launching bot loop"
"$PY" bot.py --interval "$INTERVAL_MIN" --skip-fresh-sec "$SKIP_FRESH_SEC" &
bot_pid=$!

echo "[start] launching Flask"
"$PY" server.py &
server_pid=$!

echo "[start] bot pid=${bot_pid} server pid=${server_pid} (foreground; this process exits if either stops)"
echo "[start] Paper research only — no wallets / no orders."

set +e
wait -n "$bot_pid" "$server_pid"
status=$?
set -e

if [[ "$shutting_down" -eq 1 ]]; then
  exit 0
fi

if ! kill -0 "$bot_pid" 2>/dev/null; then
  echo "[start] bot exited (status=${status}); stopping server so the service restarts" >&2
else
  echo "[start] server exited (status=${status}); stopping bot so the service restarts" >&2
fi
shutdown
exit 1
