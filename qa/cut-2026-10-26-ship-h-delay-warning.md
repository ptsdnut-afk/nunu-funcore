# LOCKED cut — SHIP H Filter delay warning + lag estimate

**Status:** ENG — Proto eyes SoT is `qa/mock-filter-lag-warning-v1.{html,png,md}`. This slice is panel_v 66.
**Date:** 2026-10-26
**Why:** Owner compared Filter vs Dex on the same coin (KANE). Filter still showed ~$52k / +9.6% while Dex had already dumped to ~$37k. The header data-age chip (~4m) is easy to miss, and “refreshed a few seconds ago” is the panel poll, not a Dex tick. Feed loop stays `interval_min` 5. Speed-up vs Dex stays parked.

## In
1. **Loud Filter warning** — not only the quiet data-age chip. Amber banner on the Filter surface: **Behind live · not Dex 1s**, with the current data age and a worst-case **≤ ~5m** loop bound.
2. **Fresh state** — under ~1 minute the same banner is calm green: **Near live · just scanned**, still showing age and **next ≤ ~5m**. It does not say Dex 1s or live parity.
3. **Louder near the loop bound** — at ≥~4m (≥~80% of the 5-minute loop) the amber banner reads louder than the fresh state.
4. **panel_v 66** — `GET /api/health` feature `delay_warning`. Feed loop stays 5 minutes.

## Out
- Speeding up the bot / changing `INTERVAL_MIN`
- Dex websocket or a tick-level feed
- SHIP G Game rocket animation (`qa/mock-game-rocket-anim-v1.*` is eyes only)
- Score math / trench bands / sitting-drop threshold
- Hardcoding the KANE example into the live board

## Done when
- QA BLOCK on live for AC-L01…L04
- Banner matches `qa/mock-filter-lag-warning-v1`
- `GET /api/health` is `panel_v` 66 with `delay_warning`
- Game tab and Social Tap still open; no rocket-animation diff

## BLOCK acceptance criteria
| ID | Proof | Expected |
|----|--------|----------|
| AC-L01 | Screenshot | Filter shows a clear delay / not-live-Dex warning (not only a tiny age chip) |
| AC-L02 | Screenshot | Lag estimate visible: current data age + ~up to 5m / loop bound |
| AC-L03 | Screenshot | Near-stale (≥~4m or ≥~80% of loop) reads louder than fresh (<1m) |
| AC-L04 | Screenshot + health | Game chrome / Social Tap not broken; no false “1s live” claim · no fake live prices |

## Refs
- Eyes: `qa/mock-filter-lag-warning-v1.{html,png,md}`
- Canonical pack: `qa/CANONICAL-AC-PACK-ship-h-delay-warning.md`
- Owner shots (motivation only): Filter KANE ~$52k vs Dex ~$37k
- Live: https://nunu-funcore.onrender.com
- Repo: ptsdnut-afk/nunu-funcore
