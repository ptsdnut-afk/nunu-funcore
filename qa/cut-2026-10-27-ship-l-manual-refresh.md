# LOCKED cut — SHIP L Manual Update / Refresh

**Status:** LOCKED — Eng **unlocked now** (SHIP K CLOSED PASS on 70). Owner yes 2026-10-27 after J→K. Manual Update / force fresh feed pull. QA hold U01…U05 until past 70.
**Date:** 2026-10-27
**Why:** Owner in product room: “add like a manual update button so it don’t get like 2 3 m behind.” SHIP H already shipped lag warning; speeding the always-on loop was parked — this cut is **on-demand refresh**, not a permanent faster interval.

## In
1. **Manual Update control** — obvious button/control on Filter (near data-age / delay warning) labeled Update / Refresh (or equivalent).
2. **Fresh pull now** — tap kicks a **server-side feed refresh** (or equivalent path that actually advances data age), not only a client re-render of the same snapshot.
3. **Busy + done cues** — while running: disabled / spinner / “Updating…”; on finish: data age drops / rows refresh; failures show a short error (no silent noop).
4. **Safe spam** — debounce / cooldown so mash doesn’t stack overlapping scans.

## Out
- Permanent `interval_min` drop / always-on sub-minute bot (still parked unless owner widens later)
- Dex websocket / tick-level live
- SHIP J dismiss chrome / SHIP K Game 24h restart
- Fake “live 1s” claims

## Done when
- QA BLOCK PASS AC-U01…U05 on live
- Owner can tap Update and see fresher age without waiting for the next loop

## BLOCK acceptance criteria
| ID | Proof | Expected |
|----|--------|----------|
| AC-U01 | Screenshot | Obvious **Update now** on Filter Token Flow (beside/near Refresh; not buried) |
| AC-U02 | Note + age | Tap kicks a **fresh bot/filter pass** — not only client reload of stale `/api/flagged` |
| AC-U03 | Screenshot | Busy state while running; control disabled until done |
| AC-U04 | Note + age | On success, data age / lag banner updates; Token Flow usable; light Refresh can remain |
| AC-U05 | Note | Cooldown / rate-limit so mash doesn’t melt Render |

## Unlock order
1. **Owner** — yes (done 2026-10-27).
2. **Proto Bot** — optional 1-panel chrome mock; not blocking once Eng unlocked.
3. **Engineering Lead** — after J→K (or when owner says pull forward); ship control + refresh path; bump panel_v; Manual Deploy.
4. **Product Performance** — confirm refresh actually hits the feed path / age moves.
5. **QA** — BLOCK U01…U05 after health bumps.
6. **PM** — close cut.

## Eng handoff
SoT `/workspace/trading/nunu-filter/` → Filter header / delay-warning chrome + feed worker / refresh endpoint. Prefer kicking the existing scan loop early over inventing a second pipeline. Keep lag warning honest. Do not claim PASS — QA gates.

SoT mock (optional eyes): `qa/mock-filter-manual-update-v1.{html,png,md}` — Update now / force bot pass (not panel Reload). Proto proposed AC-U01…U05 in MD.
