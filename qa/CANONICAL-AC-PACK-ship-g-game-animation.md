# CANONICAL AC PACK — SHIP G Game polish

Production target: the live Game tab on this panel.
Cut: qa/cut-2026-10-26-ship-g-game-animation.md
Eyes: qa/mock-game-polish-v2.{html,png,md}
Base craft note: qa/mock-game-rocket-anim-v1.* (v2 wins on geometry, chart stage, chrome, SFX)
Baseline: panel_v 66 SHIP H delay warning. This slice is panel_v 67.
Ops: `interval_min` / `INTERVAL_MIN` stays **5**. Do not touch Filter `delay_warning` copy or thresholds.

## Constants
| Name | Value | Where |
|------|-------|--------|
| `panel_v` | **67** | `server.py` `GET /api/health` |
| feature | **`game_polish`** | appended; `delay_warning` stays |
| Win hit | `♪♪ WIN` | `SOUND_SEQ.tick` square stacked chime. Label only. Not reused for DOWN |
| Loss hit | `♪ LOSS` | `SOUND_SEQ.down` sawtooth thud. Different sequence from tick |
| Quote empty | no start | `emptyQuoteDecision` and `GET /api/game/quote` with a blank mint return 400 `Enter a coin — CA, ticker, or name` and do not call `start_session` |

## BLOCK
| ID | Expected |
|----|----------|
| AC-R01 | The ship is a horizontal craft: tapered hull, twin engine bells, plate seams, glass canopy. No eyes, no smile, no oval face blob. |
| AC-R02 | IDLE is a soft hover with a dim twin flame on the chart stage. With no open position the stage is a demo cruise and does not play ♪♪ WIN. |
| AC-R03 | A positive paper mark plays the lift: nose up, longer green flame, floating +$ chip, chart spike. |
| AC-R04 | A negative mark pitches the nose down, shows grey smoke, hides the green flame, and never plays ♪♪ WIN. |
| AC-R05 | The equipped skin paints hull and fins (Nebula is purple/magenta). Locked and owned shop cards show a mini craft in that tint plus Preview. |
| AC-R06 | Points balance, shop equip/buy, Buy, and Sell All stay wired. This slice does not rewrite the points table. |
| AC-F01 | Idle and ended show one `Start 24h ($5)` control. The old second Start panel is gone. ACTIVE changes that same control to Restart. |
| AC-F02 | While status is active, the status bar is not `Session ended · final cash`. Last tick is `$0.00` / waiting mark until a real tick, then the signed amount stays. Equity on the ship matches the session. |
| AC-F03 | The direction chip is `▲ UP +%` or `▼ DOWN −%` or `— FLAT`. DOWN never renders a plus percent. |
| AC-F04 | The event tape and high-score pane stay. The dense QA fixture footer is hidden while Game is open (`body.mode-game .qa-foot`). |
| AC-S01 | A positive tick calls `beep("tick")` once and the badge/flash reads ♪♪ WIN. The tone is a stacked chime, not the old single sine beep. |
| AC-S02 | A loss calls `beep("down")` (not `beep("tick")`). The badge/flash reads ♪ LOSS. The down sequence is not the win sequence. |
| AC-S03 | Topbar toggle is `Sound on` or `Mute` and the ship badge uses the same words when idle. Mute sets the hits to silent. Sound on restores them. There is no `No sound` badge while Sound on. |

An active run that just started must clear a previous `Session ended` line even if the poll that painted it already finished.

STATUS must be exactly PASS | FAIL | BLOCKED. No soft-pass.

Unit coverage: `python3 -m unittest test_viral.py test_sitting_window.py test_game_finish.py`.
