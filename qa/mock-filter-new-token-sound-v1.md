# Mock: Filter Token Flow new-token chime · v1

**Date:** 2026-10-27  
**Eyes only** — not a PR · EXAMPLE $ · no personal names  
**Live ref:** https://nunu-funcore.onrender.com panel_v 67 (owner Token Flow empty + Social Tap shot)  
**Prior SFX:** Game buy/sell + casino WIN HIT / LOSS HIT (SHIP B / SHIP G) — this mock is **Filter / Token Flow only**

## Ask covered
Owner (ship room): whenever there’s a **new coin** in Token Flow, play a short sound like “oh new token.”  
Mute must kill it. Distinct from Game tick+/win/loss. No spam on empty table or same-coin refresh. No voiceover / long loop.

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · BEFORE** | Existing EXAMPLE rows (NOMO, RIFT) | Topbar **Sound on \| Mute**; no ♪ flash; no NEW pulse; idle Token Flow |
| **2 · AFTER · ARRIVAL** | Fresh EXAMPLE **PEBBLE** row | Green pulse + **NEW** chip + **♪ NEW TOKEN** flash + toast “New token flagged”; Sound on |
| **3 · AFTER · MUTE** | Same PEBBLE arrival UI | **Mute** selected; banner **muted · no chime**; no ♪ flash; silent toast |
| **SFX strip** | Compare pack | Filter **♪ NEW TOKEN** vs Game **♪♪ WIN HIT** vs **♪ THUD LOSS HIT** — different wording/energy |

All $ / tickers are **EXAMPLE**. Banner: EYES ONLY · NOT A PR. Do not treat live mint from owner address bar as a claim.

## Eng / PM pointer — proposed ACs (for PM cut)
| ID | Expected |
|----|----------|
| **AC-N01** | When a new coin appears in Token Flow (new flagged row this scan), play a short distinct “new token” chime |
| **AC-N02** | Chime is clearly different from Game buy/sell and win/loss SFX |
| **AC-N03** | Mute kills the new-token chime; unmute restores after user gesture if needed |
| **AC-N04** | Empty Token Flow / no new rows → no spam chime; do not fire on refresh of same coins |
| **AC-N05** | Game chrome / Social Tap still work; no voiceover / long loop |

## Out of scope
- Production PR / merging
- Speeding the bot / scan interval
- Changing Game sounds (SHIP B / SHIP G)
- Real money / wallets
- Music beds / long loops / voiceovers

## Files
- `qa/mock-filter-new-token-sound-v1.html`
- `qa/mock-filter-new-token-sound-v1.png`
- `qa/mock-filter-new-token-sound-v1.md` (this file)

## Pattern refs
- `qa/mock-filter-lag-warning-v1.{html,md,png}`
- `qa/mock-game-polish-v2.md` (Sound on \| Mute + WIN/LOSS hit wording to avoid)
- `taste.md`
