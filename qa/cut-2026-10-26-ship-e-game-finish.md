# LOCKED cut — SHIP E Slice 1 Game finish (rocket / points / tick+)

**Status:** LOCKED (scope frozen). Eng unlocks after SHIP D Slice 10 Quote sync QA PASS. Proto mock ready.
**Date:** 2026-10-26
**Why:** Owner: Game was half-finished — rocket, points, and positive tick feedback skipped after SHIP C Slice 1. Full AC, no half-pass.

## In
1. **Positive tick feedback** — every time paper PnL ticks up (gain), play a short up-sound + rocket/dude thruster lift. No spam on flat; DOWN = nose-down, no up-sound.
2. **Rocket character** — live strip tied to open position / session PnL (UP / DOWN). From finish mock.
3. **Points economy (real)** — balance visible; earn/lose on settle; profile chip → edit name + pfp.
4. **Shop cosmetics** — equip owned skins that tint rocket; spend points to unlock.
5. **Polish bar** — sounds, motion, points finished on live Game tab. No thin ship.

## Out
- Filter age-window / Social Tap viral (behind freshness)
- Quote sync (Slice 10 — ahead of this)
- Real money / wallets / trading
- New game modes beyond paper 24h + current board

## Done when
- QA BLOCK PASS on live with AC-T01…T07 (Proto pointer)
- Proto design eyes vs `qa/mock-game-finish-tick-v1.*`
- Owner hears/sees gain ticks + rocket without asking “where’s the rocket”

## Refs
- Finish mock: `qa/mock-game-finish-tick-v1.{html,png,md}` (IDLE / TICK+ / DOWN)
- Base: `qa/mock-game-dopamine-v2b.*`
- Draft trail: `qa/cut-draft-2026-10-26-ship-e-game-finish.md`
