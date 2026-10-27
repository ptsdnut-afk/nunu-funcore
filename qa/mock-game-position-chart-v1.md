# Mock: Game open-position chart + plain $ labels · v1 (SHIP M draft)

**Date:** 2026-10-27  
**Eyes only** — not a PR · EXAMPLE $ · ticker **PEBBLE** (not live HACHI)  
**Cut (draft):** `qa/cut-2026-10-27-ship-m-trade-chart.md`  
**Owner proof:** `qa/owner-2026-10-27-trade-chart-missing-ask-v67.png`  
**Optional SoT** — Proto eyes; does **not** block SHIP J / K / L

## Ask covered
1. Owner: want to **see the chart** on Game open position / Trade so they know where price/mcap is at — especially if **behind** Dex. Today the right pane is a black void; only a DEX Screener text link.
2. Owner follow-up: **“$ makes no sense look at those numbers.”** Math can be fine (stake $1 → +18.8% → position ~$1.19) but bare **MARK** next to **ENTRY / LAST** coin prices + unlabeled **NOW** mcap reads as nonsense.

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · BEFORE** | Open position like owner shot | Right pane **empty**; DEX Screener text only; confusing $ row (Stake / Mark / Entry / Last / TP) |
| **2 · AFTER · LIVE** | Chart + plain $ | Right pane spark/line with **ENTRY** + **LAST** marks; **Mcap NOW** aligned; **Near live** chip; labels: Entry price · Last price · Position value ($mark) · Mcap NOW; Sell + DEX intact; light X (SHIP J) |
| **3 · AFTER · BEHIND** | Same + lag honesty | Amber **Behind live · ~3.4m** chip; “Mark may be behind Dex” note; dashed Dex tip ahead of stale mark — no fake 1s live |

Banner: **EYES ONLY · NOT A PR · EXAMPLE $ · PEBBLE**.

## Eng / PM pointer — proposed ACs
| ID | Expected |
|----|----------|
| **AC-C01** | Open Game position / Trade select shows a chart (not empty right pane) |
| **AC-C02** | Chart shows entry vs last (or clear “where it’s at” mark) so owner can see up/down vs entry |
| **AC-C03** | If quote/data may lag Dex, surface a short behind/near-live note (no false 1s claim) |
| **AC-C04** | DEX Screener link still works; Sell/Buy not broken |
| **AC-C05** | Readable $ labels — Entry price · Last price · Position value ($mark) · Mcap NOW; no bare “MARK” next to coin prices |

## Out of scope
- Manual Update / force scan (SHIP L)
- Dismiss/Back chrome redesign (SHIP J — keep X if it fits; don’t redesign)
- Game 24h restart (SHIP K)
- Full TradingView / multi-TF research suite
- Production PR / merging / claiming PASS
- Real money / wallets / live HACHI numbers

## Files
- `qa/mock-game-position-chart-v1.html`
- `qa/mock-game-position-chart-v1.png`
- `qa/mock-game-position-chart-v1.md` (this file)

## Pattern refs
- `qa/mock-game-polish-v2.{html,md,png}`
- `qa/mock-filter-dismiss-chrome-v1.{html,md,png}`
- `taste.md`
