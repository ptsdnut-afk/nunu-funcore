# CANONICAL AC PACK — SHIP I Token Flow new-coin chime

Production target: the live Token Flow table on the Filter surface.
Cut: qa/cut-2026-10-27-ship-i-token-flow-new-sound.md
Eyes: qa/mock-filter-new-token-sound-v1.{html,png,md}
Baseline: panel_v 67 SHIP G game polish. This slice is panel_v 68.
Ops: `interval_min` / `INTERVAL_MIN` stays **5**. Do not retune Game `SOUND_SEQ.tick` / `SOUND_SEQ.down`.

## Constants
| Name | Value | Where |
|------|-------|--------|
| `panel_v` | **68** | `server.py` `GET /api/health`. Header reads `v68`. `PANEL_V` is 68. |
| feature | **`token_flow_new_sound`** | appended. `game_polish` and `delay_warning` stay. |
| Chime | `♪ NEW TOKEN` | `SOUND_SEQ.newtoken` sine `349.23 → 523.25 → 659.25`. One shot per new mint, staggered by 0.42s. Not a loop. |
| Seen set | mint strings | `tokenFlowAlertDecision`. `prev == null` seeds and plays nothing. Later diffs use mint keys, not DOM redraws. |
| Mute | shared topbar | `#gSoundToggle` **Sound on \| Mute**, visible on Filter. `state.mute` silences Game hits and this chime. |
| Hook | `data-flow-hook="new-token"` | Token Flow pane. Flash `#flowChime`. Mute note `#flowMuteNote`. |

## BLOCK
| ID | Expected |
|----|----------|
| N01 | When `/api/flagged` gains a mint that was not in the previous snapshot, the panel plays `newtoken` once for that mint and shows the arrival chrome: `.tok.arrive` green pulse, `.tag-arrive` NEW chip, `#flowChime` reading **♪ NEW TOKEN**, and toast `New token flagged · <ticker>`. Two new mints play two chimes. The first successful snapshot (`prev == null`) seeds `flowMints` and does not play, flash, or toast. |
| N02 | `SOUND_SEQ.newtoken` is a sine sequence and is not `tick`, `down`, `buy`, or `sell`. `beep("tick")` and `beep("down")` stay the Game hits. `beep("newtoken")` returns without playing and without `markSound`. Mute (`state.mute`) yields outcome `muted`: no tone, no `#flowChime`, dim NEW chip (`.tag-arrive.dim` / `.tok.arrive-muted`), `#flowMuteNote` **muted · no chime**, toast `New token flagged · silent`. Sound on clears mute so the next new mint chimes. If the audio context is not running, outcome is `blocked`: no tone, flash detail tells the user to tap Sound on. That tap resumes audio and does not replay the blocked mint. |
| N03 | Outcome `same` (empty list, same mint set in any order, or only removals) plays nothing and does not flash or toast. `render()` and `debounceRender()` do not call `noteTokenFlowFlags`. A min-vol / trench / hide-late / search redraw of an unchanged flagged set does not chime. |
| N04 | `GET /api/health` is `panel_v` **68**, `ok: true`, and `features` includes `token_flow_new_sound`, `game_polish`, and `delay_warning`. |

Coins already listed when the page opens are not “new this scan.” A mint that arrives while muted or while autoplay is blocked is marked seen so unmute does not dump a backlog.

Game chrome and Social Tap stay. No voiceover and no audio loop. SHIP G AC-S01…S03 still use one `beep("tick")` and `beep("down")`.

STATUS must be exactly PASS | FAIL | BLOCKED. No soft-pass.

Unit coverage: `python3 -m unittest test_token_flow_sound.py test_game_finish.py test_sitting_window.py test_viral.py`.
