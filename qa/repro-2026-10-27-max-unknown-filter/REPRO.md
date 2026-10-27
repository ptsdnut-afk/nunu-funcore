# REPRO — Token Flow “Max unknown” on every row (dead space)

**When:** 2026-10-27  
**Owner shot:** `?v=68` · live health at pack time: panel_v **69** `dismiss_back`  
**URL:** https://nunu-funcore.onrender.com  
**Owner ask:** “I need this knoowed fix if not just delete taking space all them don't have”  
**Evidence:** `owner-max-unknown-all-rows-v68.png` (owner selected/highlighted **Max unknown**)

## Steps
1. Open Filter / Token Flow (Trench 10–12 list).
2. Look at column **MCAP - % MAX** under each coin’s mcap line.
3. Scan the visible trench rows.

## What you see
Every listed coin shows a gray **Max unknown** chip under mcap:
- ELON2028 $1.53M → Max unknown
- CHICO $5.20M → Max unknown
- TRUMP2028 $826.3k → Max unknown
- WIF $1.70M → Max unknown
- LMAO $78.7k → Max unknown
- HACHI $381.3k → Max unknown

No row in the shot has a known max / % of max. The chip is repeated dead copy taking vertical space in the densest column.

## Why it’s fucked
- `% MAX` / ceiling progress is useless when every row is “unknown”.
- Owner: either **know the max** (fix data) or **delete / hide** the unknown chip so it stops eating space when missing.
- Related prior: ceiling-math / Game “Max unknown” packs — this is Filter list spam, not Game Trade `$` units (SHIP M).

## Expected (product cut)
- Prefer: show real max / % when we have it.
- Else: omit the “Max unknown” chip (and maybe the % MAX affordance) so blank ≠ clutter.
- Don’t leave a gray unknown on every trench row.

## Out of scope
- SHIP J CLOSED PASS 69 · queue K → L → M · N Gold Scar pending lock.
- Game Trade `$` / chart → SHIP M.

## Pack
`/workspace/repro-2026-10-27-max-unknown-filter/`
