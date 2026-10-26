# Nunu filter on Chromebook Linux (Crostini)

Quick caveat: this is how **this** project’s three layers work on Linux — paper research filter only.

## Your Linux environment

A Chromebook’s “Linux” is a Debian container (Crostini). Flask on **8765** is forwarded, so `http://127.0.0.1:8765/` in **your** Chrome hits the panel.

**Big gotcha:** the container pauses when the Chromebook sleeps or the Linux terminal fully closes. Keep Terminal/`tmux` open.

**Why Grok Bot’s localhost failed:** that server was on the remote agent box. Run Flask in *your* Linux terminal.

## The three layers

1. **Bot** (`bot.py` → `filter.py`) — DEX Screener + RugCheck → `flagged.json` / `latest.json` / `flag_history.json`
2. **Backend** (`server.py`) — APIs + panel on **8765** (ETag on flagged/latest)
3. **Frontend** (`static/index.html`) — images, score/12 trench band, sitting time, Pump / FOMO / GMGN / DEX links

Paper research only — no wallets, no orders.

## First install

```bash
cd ~
unzip -o nunu-filter.zip -d nunu-filter
cd nunu-filter
# Flat zip: bot.py / server.py should be HERE (not nunu-filter/nunu-filter/)
ls bot.py server.py static/index.html
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 bot.py --once
python3 server.py
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/) in Chrome.

## Update from a new zip (keep .venv)

1. In the terminal running the server: **Ctrl+C**
2. Replace project files (keep `.venv`):
   ```bash
   cd ~
   # backup sitting history if you want
   cp -f nunu-filter/flag_history.json /tmp/flag_history.json.bak 2>/dev/null || true
   unzip -o nunu-filter.zip -d nunu-filter-new
   # flat extract into place
   rsync -a --delete \
     --exclude '.venv' --exclude '.cache' --exclude '__pycache__' \
     --exclude '*.pid' --exclude 'server.log' --exclude 'bot.log' \
     nunu-filter-new/ nunu-filter/
   rm -rf nunu-filter-new
   cp -f /tmp/flag_history.json.bak nunu-filter/flag_history.json 2>/dev/null || true
   cd nunu-filter
   source .venv/bin/activate
   pip install -r requirements.txt
   python3 bot.py --once
   python3 server.py
   ```
3. Refresh Chrome at `http://127.0.0.1:8765/`

Simpler overwrite (if zip is flat and you’re in `~/nunu-filter`):

```bash
# Ctrl+C server first
cd ~/nunu-filter
unzip -o ~/nunu-filter.zip
source .venv/bin/activate
pip install -r requirements.txt
python3 bot.py --once
python3 server.py
```

## tmux

```bash
tmux new -s nunu
source .venv/bin/activate && python3 bot.py
# Ctrl+B " → second pane
source .venv/bin/activate && python3 server.py
# Detach: Ctrl+B D
```


## Viral · Elon / Trump (panel)

- After this update, the panel shows a **Viral · Elon / Trump** strip above the coin table (`GET /api/tweets` → `tweets.json`).
- Chat routine **"Elon Trump tweet alerts"** still pings new posts in chat; it does **not** automatically rewrite Chromebook `tweets.json` unless Grok syncs a new zip/installer or overwrites that file for you.
- Local refresh options: pull a new `nunu-panel-FINAL.zip` / run `install_nunu_v2.py`, or (on the agent box) `python3 tweets.py --once` / `--merge`. Offline Chromebook keeps the last cached posts until you update files.

## Disclaimer

Paper research only. Not financial advice. Tokens can be rugs.
