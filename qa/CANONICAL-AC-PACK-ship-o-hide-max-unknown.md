# Canonical AC pack — Nunu Memecoin Filter — SHIP O (Prefer real Max / hide "Max unknown")

- **Cut:** 2026-10-27 SHIP O (binding, LOCKED) — `qa/cut-2026-10-27-ship-o-hide-max-unknown.md`
- **Product:** Nunu Memecoin Filter (paper research only)
- **Production under test:** https://nunu-funcore.onrender.com
- **SoT code:** `/workspace/trading/nunu-filter/`
- **Owner fixtures:** `qa/owner-2026-10-27-max-unknown-ask-v68.png`, `qa/owner-2026-10-27-max-unknown-mcap-v68.png` (also `qa/owner-2026-10-27-max-unknown-spam-v68.png`)
- **Bug pack:** `/workspace/repro-2026-10-27-max-unknown-filter/` (`REPRO.md`, `owner-max-unknown-all-rows-v68.png`)
- **Proto:** hide-when-unknown / fill-first mock — optional, not blocking Eng or gate
- **Repo:** ptsdnut-afk/nunu-funcore
- **Baseline live (pre-SHIP O):** panel_v 68/69 shows "Max unknown" on every Token Flow row. Gate only after deploy past the panel_v that carries SHIP O (confirm `/api/health` panel_v + features). Do not BLOCK on a pre-O panel_v.
- **IDs:** AC-X01…X04 for this cut only.
- **Severity:** all items below are **BLOCK** unless marked WARN
- **Rule:** Eng CI CLEAN ≠ AC PASS. Never soft-pass BLOCK. Local / PR proof ≠ success.
- **Order:** Gate after SHIP K STATUS (ok beside L/M). Owner preference: fill a real Max first; hide only when still unknown.

## Scope

**In:** prefer real labeled Max / % max where honest sources exist (bonding / ATH / true higher FDV per Slice 9) · when still unknown, no "Max unknown" text in Filter rows, coin drawer, Trade/Game NOW stack (keep NOW / mcap) · no fake max (Slice 9 honesty)

**Out (from cut — do not gate):**
- Gold Scar (SHIP N) / Manual Update (L) / Trade chart (M) / Game 24h (K)
- Reopening SHIP J dismiss
- Guaranteeing Max on every mint (many truly have no ceiling)

## BLOCK acceptance criteria (summary)

| ID | Proof | Expected |
|----|--------|----------|
| AC-X01 | Screenshot / sample | Where a real higher ceiling exists, Max / % shown and labeled |
| AC-X02 | Screenshot | Filter rows still unknown → **no** "Max unknown" text under mcap |
| AC-X03 | Screenshot | Drawer / Trade unknown → omit "Max unknown" (NOW only) |
| AC-X04 | Screenshot | No regression: fake 100% gone / invented ceilings do not return |

STATUS must be exactly `PASS` \| `FAIL` \| `BLOCKED`. No soft-pass.

---

## AC-X01 — Real Max shown where honest

**Title:** Where a real higher ceiling exists, Max / % shown and labeled

**Steps:**
1. Confirm deploy under test is past SHIP O baseline (`/api/health` panel_v + features).
2. On Filter Token Flow, find ≥1 row with an honest ceiling source (bonding / ATH / true higher FDV). Cross-check via `/api/feed/latest` or row data.
3. Confirm Max and % max render, labeled, with a value > NOW.

**Pass criteria:**
- Labeled Max / % max visible on rows that have a real ceiling.
- Value traceable to an honest source (not derived from NOW).
- If zero rows on live have a real ceiling at test time: STATUS `BLOCKED` (record sample + time), not PASS.

**Evidence to capture:**
- Screenshot: row with labeled Max / %
- Sample note: mint + source of ceiling

---

## AC-X02 — Filter rows: no "Max unknown"

**Title:** Filter rows still unknown → no "Max unknown" text under mcap

**Steps:**
1. Open Filter Token Flow (same view as owner fixture `qa/owner-2026-10-27-max-unknown-mcap-v68.png` / bug pack).
2. Scan rows without a ceiling.
3. Confirm MCAP / NOW still shown and no "Max unknown" text (or empty gray chip) under mcap.

**Pass criteria:**
- Zero occurrences of "Max unknown" on Filter rows (DOM text search OK).
- NOW / mcap still present; no wall of dead chrome.

**Evidence to capture:**
- Screenshot: Token Flow list after O (compare to owner fixture)
- Note: DOM search count for "Max unknown" = 0

---

## AC-X03 — Drawer / Trade: omit when unknown

**Title:** Drawer / Trade unknown → omit "Max unknown" (NOW only)

**Steps:**
1. Open coin drawer for a row with no ceiling.
2. Open Trade/Game NOW stack for the same (or another unknown) coin.
3. Confirm NOW shown, max/% empty, no "Max unknown" / `max-unk` node.

**Pass criteria:**
- No "Max unknown" in drawer or Trade/Game NOW stack.
- NOW / mcap still shown; layout not broken by removal.

**Evidence to capture:**
- Screenshot: drawer (unknown coin)
- Screenshot: Trade/Game NOW stack (unknown coin)

---

## AC-X04 — No fake max regression

**Title:** No regression: fake 100% gone / invented ceilings do not return

**Steps:**
1. Across Filter, drawer, Trade: look for CEILING≈NOW, "100% gone", or Max equal to NOW on coins lacking an honest source.
2. Spot-check ≥3 unknown coins against feed data.

**Pass criteria:**
- No invented ceilings; no fake 100% / CEILING≈NOW theater (Slice 9 honesty holds).

**Evidence to capture:**
- Screenshot(s): unknown coins showing NOW only, no fake %

---

## Gate notes

- Owner prefers fill-first; hide is the fallback. X02/X03 PASS does not excuse X01 if honest sources exist but are not shown.
- Do not gate Max coverage % — many mints truly have no ceiling.
- Do not reopen SHIP J dismiss here.
