# Canonical AC pack — Nunu Memecoin Filter — SHIP S (Token Flow sitting-drop vanish)

- **Cut:** 2026-10-27 SHIP S (LOCKED, binding) — `qa/cut-2026-10-27-ship-s-sitting-drop-vanish.md`
- **Product:** Nunu Memecoin Filter (paper research only)
- **Production under test:** https://nunu-funcore.onrender.com
- **SoT code:** `/workspace/trading/nunu-filter/`
- **Owner fixtures:** `qa/owner-2026-10-27-coins-appear-disappear-ask-v70.png` · `qa/owner-2026-10-27-token-flow-vanish-v68.png`
- **Live proof at cut (panel_v 70):** `flagged_count=0`, `sitting_dropped_count=10`, top_scores up to 98 (e.g. TRUMP2028/CHICO) @ ~5.1m sitting, `sitting_drop_min=5`
- **Repo:** ptsdnut-afk/nunu-funcore
- **Baseline:** Eng hotfix unlocked now (ok beside/after SHIP L). BLOCK only when `/api/health` panel_v/features carry SHIP S (past 70 / sitting-keep feature flag).
- **IDs:** AC-V01…V04 are SHIP S only — never cross-map to any other V-prefixed pack.
- **Severity:** all items below are **BLOCK** unless marked WARN
- **Rule:** Eng CI CLEAN ≠ AC PASS. Never soft-pass BLOCK. No fake flagged rows.
- **Scope of this pack:** AC-V01…V04 only

## Scope

**In:** no blank “0 flagged” wall when bot healthy + top_scores · show sitting coins, don’t hard-drop (Sitting chrome) · labeled cue instead of silent vanish · documented rule preserving SHIP F age-window intent

**Out (from cut — do not gate):**
- Manual Update button (SHIP L)
- Gold Scar / Trade chart / follow tab (SHIP N/M/R)
- Killing sitting concept without a replacement cue
- Inventing fake flagged rows

## BLOCK acceptance criteria (summary)

| ID | Proof | Expected |
|----|--------|----------|
| AC-V01 | `/api/latest` + screenshot | Bot healthy + top_scores → Token Flow not a blank “0 flagged / No tokens…” wall |
| AC-V02 | Screenshot / note | Sitting coins stay visible with Sitting chrome (or drop only under retuned rules that don’t wipe the board in ~5m) |
| AC-V03 | Screenshot | No unexplained appear→vanish flash; aged rows labeled Sitting, not deleted |
| AC-V04 | Note | SHIP F age-window intent preserved (no silent forever-stale spam); new rule documented |

STATUS must be exactly `PASS` \| `FAIL` \| `BLOCKED`. No soft-pass.

---

## AC-V01 — No blank wall while healthy

**Steps:**
1. Confirm `/api/health` carries SHIP S (panel_v/features). Else `BLOCKED`.
2. Pull `/api/latest`: record `bot` ok, `evaluated`, `flagged_count`, `sitting_dropped_count`, `top_scores`.
3. Hard refresh bare https://nunu-funcore.onrender.com (no `?v=` param); open Token Flow.

**Pass criteria:**
- When bot ok + evaluated>0 + top_scores non-empty, Token Flow shows rows — not “No tokens flagged this run” / 0-flagged wall.

**Evidence:** `/api/latest` JSON + Token Flow screenshot (same minute)

---

## AC-V02 — Sitting chrome, no hard-drop

**Steps:**
1. Watch a coin past `sitting_drop_min` (≥ one bot loop, ~5m+).
2. Confirm it stays on Token Flow with visible **Sitting** label/chrome, or (if Eng retuned) the documented rule doesn’t empty the board within ~5m.

**Pass criteria:**
- Aged coin visible with Sitting chrome, or removal follows retuned rule without wiping the board.

**Evidence:** screenshot of Sitting row + note of minutes sitting

---

## AC-V03 — Appear→vanish flash gone

**Steps:**
1. Observe Token Flow across ≥2 consecutive bot passes (loop 5m).
2. Compare to owner fixtures (coins flash in → board empties).

**Pass criteria:**
- No coin set appears then disappears with no explanation; aged rows labeled, not deleted.
- Board never goes fully empty between passes while healthy + top_scores.

**Evidence:** screenshots pass N and N+1 with timestamps (`data_as_of`)

---

## AC-V04 — Rule documented, SHIP F intent kept

**Steps:**
1. Read Eng PR / SoT notes for the new sitting rule (minutes, keep vs drop, cap).
2. Confirm stale coins don’t pile up forever silently (cap/age-out/label per SHIP F spirit).

**Pass criteria:**
- New rule written down (PR or SoT doc) and matches live behavior.
- No silent forever-stale spam.

**Evidence:** note quoting rule + link/path

---

## Gate notes

- Owner fixtures were taken on `?v=68` and v70 — retest on bare URL only.
- Do not confuse AC-V01…V04 with any other V-ID pack.
- L FAIL/PASS does not affect this pack.
