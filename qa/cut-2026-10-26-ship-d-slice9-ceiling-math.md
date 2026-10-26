# Binding cut — 2026-10-26 SHIP D Slice 9 (Ceiling math honesty)

**Decision-needed:** none — standing owner consent; unlocked after owner shot Phook NOW=CEILING=$86.8k FDV → **100% gone** (“math way off”).

- **Product:** Nunu Memecoin Filter (paper research only)
- **Production under test:** https://nunu-funcore.onrender.com
- **Repo:** snookcomback/nunu-funcore
- **SoT code:** `/workspace/trading/nunu-filter/`
- **Baseline live:** PANEL v60 (SHIP D Slice 8 PASS @ e001199)
- **Owner evidence:** Phook drawer — NOW $86.8k · CEILING $86.8k (FDV) · 100% gone
- **Severity:** all AC below are **BLOCK** unless marked WARN
- **Rule:** Eng CI CLEAN ≠ AC PASS. Never soft-pass BLOCK. Never invent ceilings/ATH/FDV. Do not address owner as Joao / Nunu / Snook.

## Problem

Using FDV as “max the coin can go” when **FDV ≈ current mcap** (fully circulating) makes `% gone = 100%`. Math is literally now/FDV, but it reads as “can’t go higher / max already hit,” which is wrong for research upside.

## Binding ceiling rules (replaces Slice 8 ambiguity)

Keep source order: **bonding/graduation target → else FDV → else ATH**, always labeled.

**New honesty gate:**
1. If a candidate ceiling is missing, ≤ 0, or **≤ NOW × 1.02** (no meaningful upside vs now), **skip it** and try the next source.
2. If every source is skipped → show **honest unavailable** for ceiling + % (keep NOW). Optional note: e.g. `FDV ≈ now (fully circulating)` / `no upside ceiling` — not “100% gone.”
3. Never show **100% gone** solely because FDV equals NOW.
4. Still never invent ATH/bonding numbers.

## Scope — Slice 9 (this cut)

**In:** Filter rows + coin drawer + paper/Game mcap progress — apply honesty gate above; copy so “100% gone” only when a **real higher** labeled ceiling exists and now ≥ that ceiling.

**Out:** inventing max price · Social Tap rewrite · feed/bot ops · Filter age-window (parked) · viral Social Tap (parked)

## BLOCK acceptance criteria

| ID | Proof | Expected |
|----|--------|----------|
| AC-C01 | Screenshot (Phook or equiv FDV≈NOW) | Does **not** show CEILING = NOW with **100% gone** |
| AC-C02 | Screenshot | When FDV≈NOW and no higher bonding/ATH: honest empty ceiling/% **or** clear “at FDV / no upside ceiling” — not max-hit |
| AC-C03 | Screenshot | When a real higher ceiling exists (bonding or ATH > NOW): still shows labeled ceiling + sensible % < 100% (unless now truly ≥ ceiling) |
| AC-C04 | Screenshot | Filter + paper/Game same rules; sources still labeled; no invented numbers |
| AC-C05 | Screenshot \| timing | Snappy; Social Tap / Traders / coin detail no regression |

## Unlock order

1. **PM** — this cut locked (done).
2. **Proto** — optional eyes-only mock of “FDV≈now → no 100%” if Eng wants it.
3. **Engineering Lead** — Slice 9 only on snookcomback; Eng merges when cut-clean.
4. **QA** — BLOCK past v60; STATUS PASS \| FAIL \| BLOCKED. @Bug Reproduction on FAIL.

## Done when

QA posts STATUS PASS for AC-C01…C05 on live panel past v60.
