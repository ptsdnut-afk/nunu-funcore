# LOCKED cut — SHIP I Token Flow new-coin chime

**Status:** ENG — Proto eyes SoT is `qa/mock-filter-new-token-sound-v1.{html,png,md}`. This slice is panel_v 68.
**Date:** 2026-10-27
**Why:** Owner: when Token Flow gets a fresh flagged coin, play a short “oh new token” cue. Live panel_v 67 can sit on “No tokens flagged this run.” The cue has to be obvious on that table and must not be a Game casino hit.

## In
1. **Arrival** — a mint that was not in the previous `/api/flagged` snapshot plays one short ♪ NEW TOKEN chime per new mint. The row gets a green pulse and a NEW chip. A ♪ NEW TOKEN flash and a “New token flagged” toast show above Token Flow. The first snapshot after load only seeds the seen set (coins already on the board stay quiet).
2. **Distinct chime** — sine “oh” ping (`SOUND_SEQ.newtoken`). Not the Game square ♪♪ WIN stack, not the sawtooth ♪ LOSS thud, not buy/sell. No voiceover and no looping bed.
3. **Shared Sound on | Mute** — the existing topbar toggle is visible on Filter. Mute kills the chime and the ♪ flash (dim NEW row, “muted · no chime”, silent toast). Sound on restores the chime for the next new mint. A browser autoplay block shows a tap cue on the flash; the tap unlocks later chimes and does not replay the blocked mint.
4. **No spam** — empty Token Flow, a same-mint refresh (any order), a coin leaving the board, and filter/search redraws do not chime.
5. **panel_v 68** — `GET /api/health` feature `token_flow_new_sound`. `game_polish` and `delay_warning` stay. Feed loop stays 5 minutes.

## Out
- Game SFX redesign (SHIP G AC-S01…S03 stay on `beep("tick")` / `beep("down")`)
- Social Tap sounds, Traders
- Changing `INTERVAL_MIN` / `interval_min`
- Music beds, long loops, voiceovers
- Real money / wallets
- EXAMPLE tickers from the mock (PEBBLE / NOMO / RIFT) on the live board

## Done when
- QA BLOCK on live for N01…N04
- Arrival / mute chrome matches `qa/mock-filter-new-token-sound-v1`
- `GET /api/health` is `panel_v` 68 with `token_flow_new_sound`
- Game win/loss hits and Social Tap still open

## BLOCK acceptance criteria
| ID | Expected |
|----|----------|
| N01 | A new flagged mint that was not in the last poll plays the ♪ NEW TOKEN chime once, with the green pulse, NEW chip, flash, and toast. Not on every redraw of the same set. The first snapshot seeds silently. |
| N02 | The chime is not the Game win, loss, buy, or sell hit. Topbar Sound on \| Mute is the shared control and is visible on Filter. Mute kills this chime. Sound on restores it for the next new mint. Autoplay block shows a tap cue and does not leave Token Flow silent after Sound on. |
| N03 | No chime, flash, or toast when Token Flow stays empty, when the same mint set refreshes, or when only the table redraws. |
| N04 | `GET /api/health` is `panel_v` **68** and `features` includes `token_flow_new_sound`. |

## Refs
- Eyes: `qa/mock-filter-new-token-sound-v1.{html,png,md}`
- Canonical pack: `qa/CANONICAL-AC-PACK-ship-i-token-flow-new-sound.md`
- Prior SFX: `qa/cut-2026-10-26-ship-g-game-animation.md` (do not retune)
- Live: https://nunu-funcore.onrender.com
- Repo: ptsdnut-afk/nunu-funcore
