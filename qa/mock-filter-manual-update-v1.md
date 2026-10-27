# Mock: Filter Token Flow · Manual Update now · v1

**Date:** 2026-10-27  
**Eyes only** — not a PR · EXAMPLE $ · Game near-black + green/amber  
**Proposed cut (not locked):** separate from SHIP J / K — do not reopen dismiss or 24h restart  
**Live ref:** panel_v **68** · header Refresh `#btnRefresh` → `loadData(true)` only

## Ask covered
Owner: “is it possible to add like a manual update button so it don’t get like 2 3 m behind”

## Product truth (important)
- Live panel_v 68 already has header **Refresh** (`#btnRefresh`) → only `loadData(true)` (reload last `/api/flagged` JSON). Does **not** re-run `bot.py`.
- Feature `on_demand_scan` + `/api/scan` scores one mint/name — **not** a full Token Flow bot pass.
- Bot loop ~5m (`sitting_drop_min` / interval). Owner wants a manual kick so they aren’t waiting for the next loop.
- SHIP H already shipped the delay warning. This is a **control** to pull fresh, not just warn.
- Do **not** reopen J (dismiss) / K (24h restart). Separate cut.

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · BEFORE** | Quiet Refresh + Behind live / age ~2m 40s | Refresh exists but does **not** unstick bot lag (call-out) |
| **2 · AFTER · IDLE** | Loud **Update now** next to Refresh | Helper: “Pull fresh · may take ~30s” |
| **3 · AFTER · RUNNING** | Button disabled / spinner **Updating…** | Lag chip holds until new pass lands |
| **4 · AFTER · DONE** | Near live / age ~0m 12s | Toast “Token Flow updated”; Update now re-enabled (cooldown OK) |

Banner: **EYES ONLY · NOT A PR · EXAMPLE $**.

## Eng / PM pointer — proposed ACs
| ID | Expected |
|----|----------|
| **AC-U01** | Obvious Update now / Scan now on Filter Token Flow (not buried) |
| **AC-U02** | Click kicks a fresh bot/filter pass (or equivalent) that refreshes flagged Token Flow — not only client reload of stale JSON |
| **AC-U03** | Busy state while running; no double-fire spam; cooldown or disable until done |
| **AC-U04** | After success, data age / lag banner updates; Token Flow usable; Refresh can stay for light reload |
| **AC-U05 WARN** | Rate-limit / don’t melt Render (cooldown OK) |

## Out of scope
- Speeding the default bot loop permanently / Dex websocket
- Replacing SHIP H lag warning (this adds a control beside it)
- SHIP J dismiss / SHIP K 24h restart
- Single-mint `/api/scan` as the only “update” (wrong product surface)
- Production PR / merging

## Files
- `qa/mock-filter-manual-update-v1.html`
- `qa/mock-filter-manual-update-v1.png`
- `qa/mock-filter-manual-update-v1.md` (this file)

## Pattern refs
- `qa/mock-filter-lag-warning-v1.*` (SHIP H — warn, no kick)
- `qa/mock-filter-new-token-sound-v1.*`
- `taste.md`
