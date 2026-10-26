# CANONICAL AC PACK — SHIP E Slice 1 Game finish

Production target: the live Game tab on this panel.
Cut: qa/cut-2026-10-26-ship-e-game-finish.md
Eyes: qa/mock-game-finish-tick-v1.{html,png,md}
Base palette / shop: qa/mock-game-dopamine-v2b (extended, not overwritten)
Baseline: panel_v 62 quote sync + drawer clarity. This slice is panel_v 63.

| ID | BLOCK expected |
|----|----------------|
| AC-T01 | Every positive paper PnL mark plays a short ♪ tick+ **once** (not only on buy/sell) |
| AC-T02 | That same tick pulses the rocket thrusters and lifts it. Floating gain chip is on the strip |
| AC-T03 | Flat / zero Δ leaves the rocket calm and plays **no** up-sound |
| AC-T04 | A negative tick / loss puts the rocket nose-down with smoke and **never** plays ♪ tick+ |
| AC-T05 | Profile chip opens edit for name + picture. Points balance changes on settle events |
| AC-T06 | Equip an owned skin and the rocket tint follows. Locked skins show a price. Spend points when the balance covers it |
| AC-T07 | Sounds, motion, and points on the live Game tab feel finished (same bar as Filter polish) |

Also in this slice (not a new AC id): coin-drawer Flags chips use plain English. `late_chase` renders as “Late chase”. The same helper covers the other snake_case flags on that strip.

Points table (real, not proposed): finish in profit +80, new personal peak +120, board #1 at settle +250, early sell under 5 minutes −25, blow up under $1 −100, daily earn cap 400.

STATUS must be exactly PASS | FAIL | BLOCKED. No soft-pass.
