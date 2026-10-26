# Nunu Memecoin Filter

Paper-research Solana memecoin screener for Joao / Nunu. Three layers (APEX-style, research-only):

1. **Bot** — `bot.py` loops `filter.py` (DEX Screener + RugCheck → score → JSON)
2. **Backend** — `server.py` Flask serves JSON APIs + panel on port **8765**
3. **Frontend** — `static/index.html` calls `/api/*`

**No trading. No wallets. No private keys. No broker/order execution.**

Shared state is JSON files only (`flagged.json`, `latest.json`, `runs.log`, `bot_status.json`, `flag_history.json`). Bot and Flask do not import each other.

## What’s on the panel

- Token **image**, name/ticker, short CA
- Score **0–100** plus **score/12** pill (trench band = ≥ 10 / 12)
- Raw **LP**, **vol 1h/24h**, **pair age**, **data_as_of** freshness
- **Sitting time** (how long flagged) + **score-stable** minutes
- **Late chase** badge (older pair + huge 1h % gain)
- Client filters: min vol 1h, trench-only, hide late chase
- Links: **Pump** (`pump.fun/coin/{mint}`) · **FOMO** (`fomo.family/coin/{mint}?chainId=1399811149`) · **GMGN** · **DEX Screener**

## Files

| File | Purpose |
|------|---------|
| `filter.py` | Core fetch / score / write (cache + sitting history) |
| `bot.py` | Loop wrapper (`INTERVAL_MIN` default 15; `--once`) |
| `server.py` | Flask — `/api/*` with ETag + static panel |
| `static/index.html` | Browser panel |
| `flag_history.json` | Sitting-time persistence (local) |
| `.cache/` | Short TTL DEX / RugCheck cache (local) |
| `start.sh` | Foreground supervisor: bot loop + Flask (Render Start Command) |
| `RENDER.md` | Render Start Command and env vars |
| `CHROMEBOOK.md` | Crostini / localhost + update steps |

## Run on the Grok Bot box

```bash
cd /workspace/trading/nunu-filter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 bot.py --once
python3 server.py              # http://127.0.0.1:8765/
```

**Note:** The box’s `127.0.0.1` is not Joao’s Chromebook. For his Chrome, install on Crostini — see **CHROMEBOOK.md**.

## Render

Start Command: `bash start.sh`. That process stays in the foreground and runs both `bot.py --interval` (`INTERVAL_MIN`, default 15) and `server.py` (`HOST` / `PORT`). If either child dies, the supervisor exits so Render restarts the service. Full env table and the post-deploy checks are in **RENDER.md**. `panel_v` stays 60.

Local equivalent (one terminal, Ctrl+C stops both):

```bash
bash start.sh
```

## APIs

| Endpoint | Returns |
|----------|---------|
| `GET /api/health` | Liveness |
| `GET /api/flagged` | Flagged array (ETag / 304) |
| `GET /api/latest` | Last run summary (ETag / 304) |
| `GET /api/status` | Bot status + files + recent runs |
| `GET /` | Panel |

## Tunables

Edit constants at the top of `filter.py`: floors, weights, `LATE_CHASE_*`, cache TTLs. Re-run `bot.py --once`.

## Disclaimer

Paper research only. Not financial advice. Tokens can be rugs.
