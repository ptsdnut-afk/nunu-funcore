# LOCKED cut — SHIP M Trade chart + $ label clarity

**Status:** LOCKED — Eng **unlocked now** (L + S CLOSED PASS on 72). Chart on Trade + fix colliding $ families. QA hold C01–C04 until past 72.
**Date:** 2026-10-27
**Why:** Owner shot `qa/owner-2026-10-27-trade-chart-missing-ask-v67.png` — Trade right pane blank; wants chart to see where it’s at / if behind. Owner follow-up `qa/owner-2026-10-27-trade-dollar-labels-ask.png` — **$367.4k NOW** (mcap) next to **stake $1 / mark ~$1.19 / +$0.19** reads like nonsense; labels mix mcap vs position $ vs price.

## In
1. **Visible chart on Trade** when a coin is selected — fill the empty right pane with a real price/mcap chart for that mint (Dex embed or lightest honest path).
2. **Where it’s at** — chart readable vs LIVE / NOW (not a decorative blank).
3. **$ family clarity** — make units obvious so they don’t collide:
   - **NOW / where it’s at** = market cap (keep $…k, label “MCAP” or “market cap”)
   - **STAKE / MARK / P&L (+$)** = paper position dollars (stake size / mark value / P&L)
   - **ENTRY / LAST / LIVE USD** = token price (not mcap)
4. **Behind cue OK** — chart may be Dex-live while NOW is bot-lagged; don’t fake sync. Optional tiny source note.
5. **Empty / fail state** — if chart can’t load, short message (not infinite black).

## Out
- Manual Update / force scan (SHIP L — separate)
- Dismiss/Back chrome (SHIP J)
- Game 24h restart (SHIP K)
- Full TradingView terminal / multi-TF research suite
- Changing score math / feed interval / stake defaults

## Done when
- QA BLOCK PASS AC-C01…C04 on live
- Owner opens Trade and sees a chart + dollars that don’t look like nonsense

## BLOCK acceptance criteria
| ID | Proof | Expected |
|----|--------|----------|
| AC-C01 | Screenshot | Trade with selected coin shows a chart in the main empty pane (not blank) |
| AC-C02 | Screenshot | Chart matches the selected mint; LIVE/NOW still visible beside it |
| AC-C03 | Screenshot | $ labels clearly separate mcap (NOW) vs position $ (stake/mark/P&L) vs token price (entry/last/live) |
| AC-C04 | Note | No coin / load fail → clear empty/error state (not stuck black panel) |

## Unlock order
1. **Owner** — yes (done 2026-10-27: L + M after J→K).
2. **Proto Bot** — Trade chart + $ label mock (folding in); not blocking once Eng unlocked.
3. **Engineering Lead** — after J→K (with/after L); ship chart + label clarity; bump panel_v; Manual Deploy.
4. **QA** — BLOCK C01…C04 after health bumps.
5. **PM** — close cut.

## Eng handoff
SoT `/workspace/trading/nunu-filter/` → Trade selected-coin layout (right pane) + Trade $ chrome. Prefer Dex embed or lightest chart path. Proof shots: `qa/owner-2026-10-27-trade-chart-missing-ask-v67.png`, `qa/owner-2026-10-27-trade-dollar-labels-ask.png`. Bug repro: `qa/repro-2026-10-27-game-trade-dollar-units/` (AC-C03 — not a separate cut). Do not claim PASS — QA gates.

SoT mock (optional eyes): `qa/mock-game-position-chart-v1.{html,png,md}` — Trade chart + plain $ labels. Proto mock ACs C01…C05 (C05 ↔ cut AC-C03 $ units).

QA pack: `qa/CANONICAL-AC-PACK-ship-m-trade-chart.md` (AC-C01…C04; $ units under C03).
