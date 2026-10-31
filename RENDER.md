# Render

Live panel: https://nunu-funcore.onrender.com

The web process has to keep the filter loop and Flask alive together. `server.py` alone serves the git copy of `latest.json`, `/api/status` shows an empty bot, and the panel stays on **bot idle** and red **stale … — run bot**.

## Start Command

In the Render service (**Settings → Start Command**):

```bash
bash start.sh
```

`start.sh` is a foreground supervisor (bash 4.3+). It does not `nohup` and exit.

- `bot.py --interval` with `INTERVAL_MIN` (default **15** minutes)
- `server.py` on `HOST` / `PORT` (Render injects `PORT`)

SIGINT and SIGTERM are forwarded to both children. If either child exits on its own, the supervisor stops the other and exits non-zero so Render restarts the service.

Do not use `python server.py` / `python3 server.py` as the Start Command. Do not use a shell that backgrounds both processes and returns — Render treats that exit as the service stopping and can kill the children.

## Build command

```bash
pip install -r requirements.txt
```

Render’s native Python runtime already does this when `requirements.txt` is present.

## Environment

| Variable | Default in `start.sh` | Role |
|---|---|---|
| `PORT` | `8765` | Flask port. Render sets this on web services. Leave it. |
| `HOST` | `0.0.0.0` | Flask bind address. |
| `INTERVAL_MIN` | `15` | Minutes between filter passes. Optional. |
| `SKIP_FRESH_SEC` | `0` | Passed through to `bot.py`. `0` runs a real pass on boot, so a deploy does not treat the git `latest.json` (new file mtime, old `data_as_of`) as fresh and then sleep the whole interval. Set `240` only if something else is also writing `latest.json`. |
| `X_BEARER_TOKEN` | unset | X API bearer token for the X Tracker feed. The owner must set this on the web service. If it is missing, the X Tracker tab says **X not configured** and does not invent posts. |
| `X_WATCHLIST_PATH` | `x_watchlist.json` next to the app | Optional. The watchlist file. Point it at a persistent disk if the list should survive a new deploy. A process restart keeps the file on the same disk. |

No panel change. `panel_v` stays **60**.

## After deploy

Logs should include `nunu-bot starting (loop every 15 min)` and `[start] bot pid=… server pid=…`.

1. `GET /api/health` stays `panel_v` 60 and `ok: true`.
2. `GET /api/status` shows `bot.running: true` (panel pill **bot on**).
3. When that first pass finishes, `GET /api/latest` `data_as_of` is minutes old at most, and the red stale badge clears (it turns on at 10 minutes).

A Shell one-off `python bot.py --once` rewrites `latest.json` a single time and does not keep the loop running. It is only a temporary unstick until this Start Command is what the service actually runs. After that, a new deploy starts the loop again by itself.

Run **one** instance. A second instance would scrape DexScreener / RugCheck in parallel and each instance would serve its own files. A free web service that spins down stops the bot until the next wake; `start.sh` starts it again on boot. An always-on instance is what keeps `data_as_of` moving while nobody is loading the panel.
