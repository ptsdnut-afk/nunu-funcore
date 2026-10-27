# REPRO — Token Flow coins appear then disappear (empty board)

**When:** 2026-10-27 ~02:37–02:40 UTC  
**Live:** https://nunu-funcore.onrender.com · `/api/health` **panel_v 70** (`game_24h_restart`) · bot on  
**Owner ask:** “this thing is like broken coins aper then desapere look at it and fix it pl thnx”  
**Owner shot:** `owner-2026-10-27-coins-appear-disappear-ask-v70.png` (URL had `?v=68` cache query — ignore; health is 70)  
**Cut:** `qa/cut-2026-10-27-ship-s-sitting-drop-vanish.md` (**SHIP S** LOCKED) · QA `qa/CANONICAL-AC-PACK-ship-s-sitting-drop-vanish.md` AC-V01…V04

## Steps
1. Open https://nunu-funcore.onrender.com/ (Filter / Token Flow).
2. Leave defaults: LIST Min vol 1h $ **5000**, Trench band unchecked, Hide late chase unchecked.
3. Watch Token Flow header: **sitting drop 5m** · Flagged / showing / trench counts · “bot on”.
4. Optionally poll GET `/api/latest` and GET `/api/flagged` while the UI Auto-refreshes (~22s).

## What you see
- UI empty wall: **“No tokens flagged this run. Re-run python3 bot.py --once.”**
- Counts: **Flagged 0 · showing 0 · trench 0** with green **bot on** and fresh Auto/ETag timestamps.
- Live BR shots (panel_v 70): `01-live-v70-empty.png`, `02-empty-stable-after-2m.png` — stayed empty ~2m+.
- Contrast: Min vol lowered to **1000** still empty (`03-empty-minvol-1000.png`) → not a vol-threshold UI glitch alone.
- Owner path: coins flash in, then the board goes blank (appear→vanish).

## Network / API (same window)
- GET `/api/health` → `ok: true`, `panel_v: 70`, `sitting_drop_min: 5.0` (`health.json`).
- GET `/api/latest` (poll ×3 ~22s, `data_as_of` stuck on one scan `2026-10-27T02:36:50Z`):
  - `evaluated: 45`, **`flagged_count: 0`**, `trench_band_count: 0`
  - **`sitting_dropped_count: 10`** with `sitting_minutes: 5.1` (threshold `sitting_drop_min: 5.0`)
  - High scores still in `top_scores` / `sitting_dropped` but **`flagged: false`**
- Sample dropped (score → sitting_minutes):
  - TRUMP2028 **98.0** @ 5.1m
  - CHICO **98.0** @ 5.1m
  - ███WIF **93.5** @ 5.1m
  - ELON2028 **86.8** @ 5.1m
  - HACHI **77.0** @ 5.1m
  - (+ N64, Wifoween, XCAT, NETWORK, PEAK)
- GET `/api/flagged` → `[]` / empty (`api-flagged.json`).
- Evidence: `api-latest.json`, `api-latest-poll.json`, `api-latest-summary.json`.

## Why it’s fucked
- Sitting-drop (**SHIP F**, 5m) **hard-removes** rows from flagged/Token Flow once `sitting_minutes` clears the threshold.
- Bot is healthy and just evaluated 45 mints; top scores up to **98** exist — but the board reads as a broken empty product.
- Match owner: coins **appear**, then **disappear** into “0 flagged / No tokens…” with no Sitting chrome left behind.

## Expected (per LOCKED SHIP S)
- Stop empty-board theater while bot ok + `top_scores` / sitting set exist.
- **Show sitting coins** with clear Sitting chrome after `sitting_drop_min` — don’t hard-drop off Token Flow.
- Transparent cue; don’t silently wipe the list in ~5m.

## Out of scope
- SHIP L Manual Update (separate, cooking).
- M / O / N / P / R / Q queue polish items.

## Pack
`/workspace/repro-2026-10-27-coins-appear-disappear/`  
Hand to **@Engineering Lead** for SHIP S hotfix (ok beside/after L).
