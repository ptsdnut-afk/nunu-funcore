# Mock: Game finish · tick+ / rocket (SHIP E draft) v1

**Date:** 2026-10-26  
**Eyes only** — not a PR · draft cut `qa/cut-draft-2026-10-26-ship-e-game-finish.md` (unlock after Slice 9 QA PASS).  
**Extends (does not overwrite):** `qa/mock-game-dopamine-v2b.{html,png,md}`

## Ask covered
Owner: Game was half-finished — rocket, points, shop parked after SHIP C. Need **every positive paper PnL tick** → short up-sound (♪ tick+) + rocket dude action (thruster pulse / lift). Finish the loop.

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · IDLE / flat** | Mark hold, Δ = 0 | Calm hover, dim thrusters, **no sound badge** |
| **2 · TICK+** | EXAMPLE gain **+$0.12** | ♪ tick+ badge, floating +$0.12, equity green flash, thruster pulse + lift |
| **3 · DOWN / loss** | EXAMPLE session red | Nose-down + smoke, **no ♪ tick+** (optional dull thump only) |

Profile chip (name + pts + Edit), **real** points balance (not proposed-only), and shop strip (equip / buy / locked) stay readable under the storyboard — finished, not half UI.

All dollar / % / tick figures are **EXAMPLE**. Banner: EYES ONLY · SHIP E draft · NOT A PR.

## Eng / PM pointer — ACs
| ID | Expected |
|----|----------|
| **AC-T01** | Every positive paper PnL mark → short ♪ tick+ **once** (not only buy/sell) |
| **AC-T02** | Same tick → rocket thruster pulse + lift; floating gain chip optional but recommended |
| **AC-T03** | Flat / zero Δ → calm rocket, **no** up-sound |
| **AC-T04** | Negative tick / loss → nose-down + smoke; **never** play ♪ tick+ |
| **AC-T05** | Profile chip opens edit (name + pfp); points balance updates on settle events |
| **AC-T06** | Shop: equip owned skin tints rocket; locked shows price; spend pts if balance allows |
| **AC-T07** | Polish bar: sounds + motion + points feel finished on live Game tab (same bar as Filter polish) |

## Out of scope
- Filter age-window / Social Tap viral
- Real money / wallets
- Opening a PR or merging
- Inventing accurate Filter numbers (accurate-only elsewhere)

## Files
- `qa/mock-game-finish-tick-v1.html`
- `qa/mock-game-finish-tick-v1.png`
- `qa/mock-game-finish-tick-v1.md` (this file)

## Binding draft cut
`qa/cut-draft-2026-10-26-ship-e-game-finish.md`
