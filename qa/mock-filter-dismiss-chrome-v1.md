# Mock: Filter dismiss / Back chrome · v1 (SHIP J)

**Date:** 2026-10-27  
**Eyes only** — not a PR · EXAMPLE $ · no personal names  
**Cut (locked):** `qa/cut-2026-10-27-ship-j-dismiss-back.md`  
**Live ref:** owner stuck sheets `qa/owner-2026-10-27-dismiss-score-factors-v67.png` + `qa/owner-2026-10-27-dismiss-trade-wiener-v67.png`  
**Optional SoT** — Proto eyes; not blocking Eng

## Ask covered
Owner: need an **X / Back** when clicking into score factors / Trade / coin detail so they can leave without browser Back.

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · BEFORE score** | Stuck score-factors sheet | Top-right empty; no X / Back; browser Back only |
| **2 · AFTER score** | Same sheet + dismiss | Clear top-right **X** + optional **Back to Token Flow** |
| **3 · BEFORE Trade** | Trade “Buy this” select | No X / Back on selected coin |
| **4 · AFTER Trade** | Same Trade select | Top-right **X** + **Back / clear selection** |
| **5 · AFTER coin drawer** | Optional drawer | Top-right **X** + Back → Token Flow (AC-D03) |

Banner: **EYES ONLY · NOT A PR · EXAMPLE $**. Prefer top-right X matching existing Game chrome. Esc nice; button required.

## Eng / PM pointer — locked ACs (exact IDs)
| ID | Expected |
|----|----------|
| **AC-D01** | Coin detail / score-factors shows X or Back; click closes it |
| **AC-D02** | Trade with a selected coin shows X or Back; click clears / exits selection |
| **AC-D03** | After dismiss, Token Flow / Filter still usable (no stuck overlay) |

## Out of scope
- Token Flow new-coin sound (SHIP I)
- New navigation IA / multi-page router
- Game rocket / SFX work
- Production PR / merging
- Real money / wallets

## Files
- `qa/mock-filter-dismiss-chrome-v1.html`
- `qa/mock-filter-dismiss-chrome-v1.png`
- `qa/mock-filter-dismiss-chrome-v1.md` (this file)

## Pattern refs
- `qa/mock-filter-new-token-sound-v1.{html,md,png}`
- `qa/mock-filter-lag-warning-v1.{html,md,png}`
- `taste.md`
