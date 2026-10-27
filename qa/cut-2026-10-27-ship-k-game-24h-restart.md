# LOCKED cut — SHIP K Game 24h end → clean restart

**Status:** LOCKED — owner 2026-10-27: when the 24h is over, Game should start all over; fix rocket / session so it resets clean. Eng unlocked after SHIP I (ok with or after J). Live shot on v67; ship past current.
**Date:** 2026-10-27
**Why:** Owner shot `qa/owner-2026-10-27-game-24h-end-restart-ask-v67.png` — “game is over / 24h over → start all over; rocket fix.” Prior SHIP G residual: stale Session ended chrome / rocket not feeling like a fresh run.

## In
1. **24h end → ended state** — when clock hits 0 / force-sell, session clearly **ended** (not ACTIVE with dead clock or ghost open).
2. **Clean restart** — one obvious Start / Restart 24h that begins a fresh run: equity/cash baseline, empty open, rocket IDLE/FLAT fresh (no leftover OPEN chip / stuck mood from prior run).
3. **Rocket reset** — craft returns to calm IDLE on the new run; no leftover TICK+/DOWN / open-mint sticker from the finished session.

## Out
- Token Flow new-coin sound (SHIP I)
- Dismiss/Back chrome (SHIP J)
- Changing 24h length / scoring rules
- Auto-start without user tap (unless already product default — prefer explicit Start/Restart)

## Done when
- QA BLOCK PASS AC-E01…E03 on live past current
- Owner can finish a 24h (or force-end fixture) and start clean with rocket fresh

## BLOCK acceptance criteria
| ID | Proof | Expected |
|----|--------|----------|
| AC-E01 | Screenshot / note | At 24h end (or QA force-end): session shows ended, not ACTIVE-with-ghost |
| AC-E02 | Screenshot | Start/Restart begins new 24h; equity/cash/open reset to fresh run baseline |
| AC-E03 | Screenshot | Rocket IDLE/FLAT on new run; no prior OPEN mint / mood leftover |

## Unlock order
1. **Owner** — ask (done).
2. **Proto Bot** — optional eyes; not blocking.
3. **Engineering Lead** — unlocked after SHIP I merge/QA path clear; bump panel_v; Manual Deploy.
4. **QA** — BLOCK E01…E03 after health bumps (force-end fixture OK).
5. **PM** — close cut.

## Eng handoff
SoT `/workspace/trading/nunu-filter/` → game session end + Start/Restart + rocket-live reset. Reuse SHIP G force-tick / end paths if present. Do not claim PASS — QA gates.
