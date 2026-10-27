# CANONICAL AC PACK — SHIP J X / Back dismiss

Production target: score factors, the coin drawer, and Game Trade select on this panel.
Cut: qa/cut-2026-10-27-ship-j-dismiss-back.md
Eyes: qa/mock-filter-dismiss-chrome-v1.{html,png,md} (eyes only — EXAMPLE tickers stay off the live board)
Baseline: panel_v 68 SHIP I Token Flow new-coin chime. This slice is panel_v 69.
Ops: `interval_min` / `INTERVAL_MIN` stays **5**. Do not retune `SOUND_SEQ` or Game rocket / SFX. Do not touch Traders, 24h restart, or scoring math.

## Constants
| Name | Value | Where |
|------|-------|--------|
| `panel_v` | **69** | `server.py` `GET /api/health`. Header reads `v69`. `PANEL_V` is 69. |
| feature | **`dismiss_back`** | appended. `token_flow_new_sound`, `game_polish`, and `delay_warning` stay. |
| Control | top-right **✕** | `button.dismiss-x`. 28×28, green border `#2a5a40` on `#102018`. `aria-label` says where it goes. |
| Score | `data-dismiss="score"` | Inside `.score-detail` (Token Flow row and pinned scan card). Optional `button.dismiss-back` reads **← Back to Token Flow**. |
| Coin | `data-dismiss="coin"` | `#coinSheetClose` in the sticky sheet bar. Optional **← Back to Token Flow** on the sheet. |
| Trade | `data-dismiss="trade"` | `#gTradeBack` in the Trade pane head, hidden until a coin is selected (`#gTradeBody[data-selected="1"]`). Quote card optional link reads **← Back / clear selection**. |
| Idle quote | unchanged copy | Clear puts `#gQuoteBox` back to `Type CA / ticker / name → Quote (or Enter).` and drops `state.lastQuoteMint`. |

## BLOCK
| ID | Expected |
|----|----------|
| AC-D01 | Coin detail / score-factors shows X or Back; click closes it. The ✕ on an open `.tok` removes `.open` and clears `state.scoreOpenMint`. The ✕ on a scan score sheet clears `#fScanBox` and `#fPickBox`. The ✕ on `#coinSheet` runs `closeCoinDetail` (sheet and backdrop no longer take clicks). |
| AC-D02 | Trade with a selected coin shows X or Back; click clears / exits selection. `#gTradeBack` is visible for a quote card, a quoting line, a pick list, or a quote error, and hidden when Trade is idle. The click clears the quote card, the pick list, and the mint field without `location.reload`. Open positions, the session, points, and shop stay. A quote response that returns after the click does not put the card back. |
| AC-D03 | After dismiss, Token Flow / Filter still usable (no stuck overlay). `body` does not keep `coin-open`. The backdrop does not keep pointer events. A later click on a Token Flow row or score still opens. No full page reload. |

Esc closes an open score sheet or a Trade selection when the key target is not an input. Profile modal and the coin sheet still take Esc first. The visible ✕ is the required control; Esc is a bonus.

STATUS must be exactly PASS | FAIL | BLOCKED. No soft-pass.

Unit coverage: `python3 -m unittest test_dismiss_back.py test_token_flow_sound.py test_game_finish.py test_sitting_window.py test_viral.py`.
