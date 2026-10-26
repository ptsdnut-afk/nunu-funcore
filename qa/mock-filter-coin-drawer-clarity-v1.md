# Filter · Slice 11 drawer clarity mock v1

**Date:** 2026-10-26  
**Eyes only** - not a PR.  
**Locked cut:** `qa/cut-2026-10-26-ship-d-slice11-drawer-clarity.md`  
**AC pack:** `qa/CANONICAL-AC-PACK-ship-d-slice11-drawer-clarity.md`  
**Repro:** `/workspace/repro-2026-10-26-mooncoin-drawer/`

## Ask covered
Owner MoonCoin shots: drawer is ugly slam, hard to read, jargon BS (`Heuristic risk index`, `buys_h1`, twin +651% 1h=24h under "Profit Outlook", yellow FDV essay + dashes theater when max unknown). Filter behind the overlay goes mush.

## Before -> after
| Surface | BEFORE (live dump) | AFTER (this mock) |
|---------|--------------------|-------------------|
| Chrome | Hard opaque blackout slam | Calmer right sheet, soft dim, Filter list still readable |
| Ceiling unknown | CEILING `-`, `% gone -`, yellow FDV essay | **NOW only** + quiet "Max unknown." |
| Past move | "Profit Outlook" twin +651% / +651% | **Last hour print** · one honest **1h +651%** (too young for 24h) |
| Vitality | Method essay + `buys_h1` / `sells_h1` / `volume_h1_usd` / `pair_age_hours` | Active pill + `~7 min old · 3.3k buys / 2.7k sells · $375k vol` |
| Rug / LP | Lawyer hedges under each % | Big % + one short line + chips |
| Signals | Missing / buried | Early whale top ~56% (EXAMPLE) or FOMO **Unknown** |
| Verdict | None | Human line: hot + new + LP locked + past hour ripped (not a prediction) |

## 2-state ceiling board (top of mock)
1. **HAS MAX** (TRUMP-style EXAMPLE) - Now / Ceiling / % gone with real headroom  
2. **MAX UNKNOWN** (MoonCoin-style EXAMPLE) - Now $331.6k only + "Max unknown."

## Eng pointer - AC-D01…D07
| ID | Expected |
|----|----------|
| **AC-D01** | Calmer coin sheet - not a hard opaque slam; page behind readable; bigger type; scannable |
| **AC-D02** | Plain English default; no lawyer wall; no raw keys like `buys_h1` on default view |
| **AC-D03** | Twin 1h=24h (or too young for 24h) -> **one** honest print only |
| **AC-D04** | Unknown ceiling: hide CEILING, % gone, yellow FDV essay; NOW only + optional "Max unknown" |
| **AC-D05** | Real labeled ceiling still shows Max + % gone |
| **AC-D06** | Rug / LP: one clear number each + ≤ one short plain line; no essay |
| **AC-D07** | Snappy; Filter/Game mcap unknown path matches; no fake forecast language |

## Out of scope
- Slice 10 quote ±5% band  
- SHIP E Game finish (tick+ / rocket / points)  
- Feed bot Start Command (ops)  
- Opening a PR or merging  

## Files
- `qa/mock-filter-coin-drawer-clarity-v1.html`
- `qa/mock-filter-coin-drawer-clarity-v1.png`
- `qa/mock-filter-coin-drawer-clarity-v1.md` (this file)
