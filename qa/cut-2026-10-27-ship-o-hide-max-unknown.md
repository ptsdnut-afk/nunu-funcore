# LOCKED cut — SHIP O Max known (prefer) / hide “Max unknown”

**Status:** LOCKED — Eng **unlocked now** (M CLOSED PASS on 73). Prefer real Max before hide; if still unknown, delete chrome (no “Max unknown” wall).
**Date:** 2026-10-27
**Why:** Owner shot `qa/owner-2026-10-27-max-unknown-ask-v68.png` (+ Bug pack `/workspace/repro-2026-10-27-max-unknown-filter/`) — every Token Flow row shows gray **Max unknown** under MCAP · % MAX. Owner: know it, or delete — taking space; none of them have it.

## In
1. **Prefer real Max** — when honest sources exist (bonding / ATH / true higher FDV per Slice 9 rules), show labeled Max / % max. Improve fill where free/safe; **never invent**.
2. **When still unknown** — do **not** render “Max unknown” (Filter rows, coin drawer, Trade/Game NOW stack). Keep NOW / mcap; leave max/% empty.
3. **No fake max** — Slice 9 honesty stands (no CEILING≈NOW → 100% gone theater).

## Out
- Gold Scar (SHIP N) / Manual Update (L) / Trade chart (M) / Game 24h (K)
- Reopening SHIP J dismiss
- Guaranteeing Max on every mint (many truly have no ceiling)

## Done when
- QA BLOCK PASS AC-X01…X04 on live
- Owner no longer sees a wall of “Max unknown”

## BLOCK acceptance criteria
| ID | Proof | Expected |
|----|--------|----------|
| AC-X01 | Screenshot / sample | Where a real higher ceiling exists, Max / % shown and labeled |
| AC-X02 | Screenshot | Filter rows still unknown → **no** “Max unknown” text under mcap |
| AC-X03 | Screenshot | Drawer / Trade unknown → omit “Max unknown” (NOW only) |
| AC-X04 | Screenshot | No regression: fake 100% gone / invented ceilings do not return |

## Unlock order
1. **Owner** — yes (done 2026-10-27: prefer fill, then hide).
2. **Proto Bot** — hide-when-unknown mock (cooking); not blocking.
3. **Engineering Lead** — after K (ok beside L/M); fill where honest + strip unknown chrome; bump panel_v; Manual Deploy.
4. **QA** — BLOCK X01…X04 after health bumps.
5. **PM** — close cut.

## Eng handoff
SoT `/workspace/trading/nunu-filter/` → ceiling sources + Filter mcap cell + drawer/Trade `max-unk`. Prefer real Max; omit node when unknown. Bug repro: `/workspace/repro-2026-10-27-max-unknown-filter/`. Owner shot: `qa/owner-2026-10-27-max-unknown-ask-v68.png`. Do not claim PASS — QA gates.

QA pack: `qa/CANONICAL-AC-PACK-ship-o-hide-max-unknown.md` (AC-X01…X04).

SoT mock (optional eyes): `qa/mock-filter-hide-max-unknown-v1.{html,png,md}` — fill Max first, else hide chip. Proto O01…O04 → cut X01…X04.
