# REPRO — Game Trade `$` units don't make sense

**When:** 2026-10-27  
**Live health:** panel_v **68** (`token_flow_new_sound`); owner shot on `?v=67`  
**URL:** https://nunu-funcore.onrender.com  
**Mint:** `DMnMwUvo1a9HbSnixbdNaTsoHczJ8cdamUwVRM1Qpump` (HACHI)  
**Owner ask:** “can we fix the $ makes no scneces look at does numbers”  
**Evidence:** `owner-trade-dollar-sense-v67.png`

## Steps
1. Open Game with an active paper position (HACHI open).
2. Drill into the open trade panel (left stack + empty right pane).
3. Read the `$` numbers together.

## What you see (owner shot)
| Label | Value | Apparent unit |
|-------|-------|----------------|
| LIVE | 0.000387 USD | price |
| delta | +$0.19 · +18.8% | paper P&L dollars |
| **NOW** | **$367.4k** | **FDV / mcap** (“Where it’s at · DEX Screener”) |
| STAKE | $1.00 | paper dollars |
| MARK | $1.188 | paper dollars |
| ENTRY / LAST | 0.000325 / 0.000387 | price (no $) |
| CLOSED rows | ±$0.xx | paper dollars |

## Why it looks fucked
- Same `$` prefix on **NOW $367.4k** (DEX Screener FDV) and **STAKE $1.00** / **+$0.19** (paper stake + P&L).
- Math on paper is fine: ~18.8% on $1 → MARK ~$1.19 / +$0.19.
- The big green **NOW** reads like “my position is worth $367k” next to a $1 stake — it isn’t; it’s market cap.
- Empty right pane (chart ask → SHIP M) makes the FDV number the only “where it’s at” cue, so the unit clash is louder.

## Expected (product cut)
- Don’t use bare `$` for FDV next to paper `$` without a clear unit (e.g. FDV / mcap label, or `$367.4k FDV`, or drop `$` on mcap).
- Or put “where it’s at” on a chart (SHIP M) and keep paper `$` only for stake/mark/P&L.

## Out of scope / related
- Chart-on-Trade empty right pane → SHIP M (separate).
- Manual Update → SHIP L (separate).
- Queue still J → K first.

## Pack
`qa/repro-2026-10-27-game-trade-dollar-units/`
