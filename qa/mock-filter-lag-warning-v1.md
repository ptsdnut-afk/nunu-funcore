# Mock: Filter delay warning + lag estimate · SHIP H v1

**Date:** 2026-10-26  
**Eyes only** — not a PR · visual SoT for locked cut `qa/cut-2026-10-26-ship-h-delay-warning.md`.  
**Ops truth:** `interval_min: 5` → lag ≈ data age now / worst-case ~5m behind Dex. Speed-up parked.

## Ask covered
Owner: Filter doesn’t update as fast as Dex — ok for now, but wants a **clear delay warning** + **how much lag** (estimate). Header already has a quiet data-age chip (~4m) that is easy to miss. Owner proof: Filter KANE MCAP NOW ~$52.0k vs Dex 1s ~$37k mid-dump (shots on disk).

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · BEFORE** | Live-style quiet chip | Tiny `data age 4m 3s` next to Refresh — easy to miss |
| **2 · AFTER · STALE** | Age ~4m (≥~80% of 5m loop) | Amber banner: **Behind live · not Dex 1s** + age + worst ≤ ~5m · EXAMPLE KANE row shows Filter vs Dex pattern |
| **3 · AFTER · FRESH** | Age ~0.4m | Calm green **Near live · just scanned** + age + next scan ≤ ~5m — not alarmist, not “1s live” |

All $ figures are **EXAMPLE**. Banner: EYES ONLY · NOT A PR. Never claim second-by-second like Dex.

## Eng / PM pointer — ACs (aligned to locked cut)
| ID | Expected |
|----|----------|
| **AC-L01** | Filter shows clear delay / not-live-Dex warning (not only a tiny age chip) |
| **AC-L02** | Lag estimate visible: current data age + ~up to 5m / loop bound |
| **AC-L03** | Near-stale (≥~4m or ≥~80% of loop) reads louder than fresh (&lt;1m) |
| **AC-L04** | Game chrome / Social Tap not broken; no false “1s live” claim · no fake live prices |

## Owner / ops refs
- `qa/owner-shot-2026-10-26-filter-data-age-4m-v65.png`
- `qa/owner-shot-2026-10-26-dex-nomo-1s.png`
- `qa/owner-shot-2026-10-26-filter-kane-52k-v65.png`
- `qa/owner-shot-2026-10-26-dex-kane-37k-1s.png`
- Locked cut: `qa/cut-2026-10-26-ship-h-delay-warning.md`

## Out of scope
- Speeding up the bot / sub-minute scans
- Dex websocket / tick-level feed
- Game rocket (SHIP G)
- Score math / trench bands
- Opening a PR or editing production app code

## Files
- `qa/mock-filter-lag-warning-v1.html`
- `qa/mock-filter-lag-warning-v1.png`
- `qa/mock-filter-lag-warning-v1.md` (this file)
