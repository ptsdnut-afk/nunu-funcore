# Canonical AC pack — Nunu Memecoin Filter — SHIP K (Game 24h end → clean restart)

- **Cut:** 2026-10-27 SHIP K (binding) — `qa/cut-2026-10-27-ship-k-game-24h-restart.md`
- **Product:** Nunu Memecoin Filter (paper research only)
- **Production under test:** https://nunu-funcore.onrender.com
- **SoT code:** `/workspace/trading/nunu-filter/`
- **Owner / repro:** `qa/owner-2026-10-27-game-24h-end-restart-ask-v67.png` · Bug pack `/workspace/repro-2026-10-27-game-24h-reset-rocket/`
- **Proto:** optional eyes — not blocking
- **Repo:** ptsdnut-afk/nunu-funcore
- **Baseline:** ship after SHIP I (ok with/after J). BLOCK only when restart chrome is live past current panel_v (`/api/health`).
- **IDs:** AC-E01…E03 for this cut only (not SHIP E Game finish AC-T01…T07).
- **Severity:** all items below are **BLOCK** unless marked WARN
- **Rule:** Eng CI CLEAN ≠ AC PASS. Never soft-pass BLOCK. Force-end fixture OK.
- **Scope of this pack:** AC-E01…E03 only
- **Order:** Finish SHIP I (N) then SHIP J (D) then this pack (E).

## Scope

**In:** 24h end → clear ended state (not ACTIVE-with-ghost) · obvious Start/Restart for fresh 24h (equity/cash/open baseline) · rocket IDLE/FLAT on new run with no leftover OPEN/mood

**Out (from cut — do not gate):**
- Token Flow new-coin sound (SHIP I)
- Dismiss/Back chrome (SHIP J)
- Changing 24h length / scoring rules
- Auto-start without user tap (prefer explicit Start/Restart)

## BLOCK acceptance criteria (summary)

| ID | Proof | Expected |
|----|--------|----------|
| AC-E01 | Screenshot / note | At 24h end (or QA force-end): session shows ended, not ACTIVE-with-ghost |
| AC-E02 | Screenshot | Start/Restart begins new 24h; equity/cash/open reset to fresh run baseline |
| AC-E03 | Screenshot | Rocket IDLE/FLAT on new run; no prior OPEN mint / mood leftover |

STATUS must be exactly `PASS` \| `FAIL` \| `BLOCKED`. No soft-pass.

---

## AC-E01 — Ended state at 24h end

**Title:** At 24h end (or QA force-end): session shows ended, not ACTIVE-with-ghost

**Steps:**
1. Confirm deploy under test has SHIP K (`/api/health`).
2. Reach 24h end via natural end or Eng/QA force-end fixture.
3. Confirm UI shows **ended** (Session ended / equivalent) — not ACTIVE with dead clock or ghost open.

**Pass criteria:**
- Ended state is clear.
- Not ACTIVE-with-ghost open/clock.

**Evidence:** screenshot of ended chrome + health JSON

---

## AC-E02 — Start/Restart fresh run

**Title:** Start/Restart begins new 24h; equity/cash/open reset to fresh run baseline

**Steps:**
1. From ended state, click obvious Start / Restart 24h.
2. Confirm new run begins: equity/cash baseline, empty open positions.

**Pass criteria:**
- Start/Restart control works.
- Fresh run baseline (cash/equity/open) — not leftover prior session numbers as active.

**Evidence:** before/after screenshots

---

## AC-E03 — Rocket reset on new run

**Title:** Rocket IDLE/FLAT on new run; no prior OPEN mint / mood leftover

**Steps:**
1. On the new run after E02, inspect rocket craft state.
2. Confirm IDLE/FLAT calm; no leftover OPEN mint sticker / TICK+ / DOWN mood from finished session.

**Pass criteria:**
- Rocket reads fresh IDLE/FLAT.
- No prior-session OPEN/mood leftover.

**Evidence:** screenshot of rocket on new run

---

## Gate notes

- Force-end fixture OK (cut).
- Do not confuse AC-E01…E03 with SHIP E Game finish AC-T01…T07.
