# LOCKED cut — SHIP J X / Back dismiss

**Status:** ENG — Proto eyes SoT is `qa/mock-filter-dismiss-chrome-v1.{html,png,md}` (eyes only). This slice is panel_v 69.
**Date:** 2026-10-27
**Why:** Owner drills into score factors, coin detail, or a Game Trade select and cannot leave. The score sheet and the selected-coin Trade panel have no dismiss. Browser Back is the only way out. Live baseline is panel_v 68 (`token_flow_new_sound`).

## In
1. **Score factors** — expanded Token Flow score sheet and the pinned scan score sheet get a top-right **✕** (28px, green Game chrome). Optional text **← Back to Token Flow** sits under the sheet. One click collapses the row, or clears the scan result, and the Filter list is usable again.
2. **Coin drawer** — the sheet’s corner control is that same top-right **✕**, not a quiet “Close” word. Optional **← Back to Token Flow** sits on the sheet. One click removes the overlay (`body.coin-open` and the backdrop) so Token Flow takes clicks again. No reload.
3. **Trade select** — while a coin is quoted, quoting, picked, or showing a quote error, the Trade header shows the same top-right **✕**. The quote card also offers **← Back / clear selection**. One click clears the quote, the pick list, and the mint field, and hides the ✕. Open positions, the 24h run, points, and shop stay. No reload.
4. **panel_v 69** — `GET /api/health` feature `dismiss_back`. `token_flow_new_sound`, `game_polish`, and `delay_warning` stay. Feed loop stays 5 minutes.
5. **Esc** — bonus, same key already used for the coin sheet and the profile modal. Esc closes an open score sheet or a Trade selection when focus is not in a field. The visible ✕ is the required control.

## Out
- Token Flow new-coin sound (SHIP I) and Game win/loss hits
- Social Tap, Traders rewrite, Game rocket / SFX
- Game meta, 24h restart, points, shop, paper book
- Scoring math and the sitting window
- New navigation or a multi-page router
- Real money / wallets
- EXAMPLE tickers from the mock (HACHI-shaped / wiener / PEBBLE) on the live board

## Done when
- QA BLOCK on live for AC-D01…AC-D03
- Top-right ✕ matches `qa/mock-filter-dismiss-chrome-v1`
- After dismiss, Token Flow / Filter still takes clicks. No stuck overlay. No full reload.
- `GET /api/health` is `panel_v` 69 with `dismiss_back`

## BLOCK acceptance criteria
| ID | Expected |
|----|----------|
| AC-D01 | Coin detail / score-factors shows X or Back; click closes it |
| AC-D02 | Trade with a selected coin shows X or Back; click clears / exits selection |
| AC-D03 | After dismiss, Token Flow / Filter still usable (no stuck overlay) |

## Refs
- Eyes: `qa/mock-filter-dismiss-chrome-v1.{html,png,md}`
- Canonical pack: `qa/CANONICAL-AC-PACK-ship-j-dismiss-back.md`
- Prior sound (do not retune): `qa/cut-2026-10-27-ship-i-token-flow-new-sound.md`
- Live: https://nunu-funcore.onrender.com
- Repo: ptsdnut-afk/nunu-funcore
