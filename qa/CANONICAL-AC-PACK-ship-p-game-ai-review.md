# Canonical AC pack — Nunu Memecoin Filter — SHIP P (Game 24h AI review, chess-style)

- **Cut:** 2026-10-27 SHIP P (binding, LOCKED) — `qa/cut-2026-10-27-ship-p-game-ai-review.md`
- **Product:** Nunu Memecoin Filter (paper research only)
- **Production under test:** https://nunu-funcore.onrender.com
- **SoT code:** `/workspace/trading/nunu-filter/`
- **Owner fixtures:** none (owner ask is verbal: chess-style post-game review). Force-end fixture OK.
- **Proto SoT (eyes):** `qa/mock-game-ai-review-v1.{html,png,md}` — proposed AC-R01…R05 (plain EN, dismissible, K Restart intact); PM: cut ACs now R01…R05. Not blocking Eng.
- **Repo:** ptsdnut-afk/nunu-funcore
- **Baseline live (pre-SHIP P):** Game 24h ends with Restart only (SHIP K). Gate only after deploy past the panel_v that carries SHIP P review (confirm `/api/health` panel_v + features). Do not BLOCK on a pre-P panel_v.
- **IDs:** AC-R01…R05 are **SHIP P Game AI review IDs only** — NOT SHIP G game polish AC-R01…R06. Never cross-map.
- **Severity:** all items below are **BLOCK** unless marked WARN
- **Rule:** Eng CI CLEAN ≠ AC PASS. Never soft-pass BLOCK. Local / PR proof ≠ success.
- **Order:** Separate from SHIP K (restart only). Gate after SHIP K STATUS. Eng takes time on AI quality.

## Scope

**In:** post-24h Review surface (clock 0 / force-sell) before or beside Restart · plain-language recap of *this* run (went well / poorly, notable trades, P&L arc, 1–3 takeaways) · grounded in this run's tape / closed trades / equity path · Restart (K) still works, review doesn't trap · honest paper / not-advice copy · optional soft review cue (Mute kills it)

**Out (from cut — do not gate):**
- Changing K restart mechanics (K stays its own cut)
- Live coaching mid-run / every-tick tips
- Gold Scar Filter lane (SHIP N)
- Rewriting score math / feed loop
- Hide Max unknown (SHIP O)
- Production PR / merging / claiming PASS by Eng

## BLOCK acceptance criteria (summary)

| ID | Proof | Expected |
|----|--------|----------|
| AC-R01 | Screenshot | On 24h finish (or force-end), AI run review offered (auto sheet or one-tap Review), not buried |
| AC-R02 | Note + Screenshot | Plain English review grounded in *this* run's trades / P&L / outcome — not raw logs or generic filler only |
| AC-R03 | Screenshot | Can dismiss (X/Back/Dismiss) and still Start / Restart 24h clean — K path intact; review doesn't trap |
| AC-R04 | Screenshot | Doesn't block sell / closed-history read; honest paper / not-advice copy; no stuck overlay |
| AC-R05 | Note | Optional soft "review ready / cooking" cue OK; Mute kills it (cue absent = PASS if no un-muteable noise) |

STATUS must be exactly `PASS` \| `FAIL` \| `BLOCKED`. No soft-pass.

---

## AC-R01 — Review appears at 24h end

**Title:** At 24h end, Review surface appears (or one-tap Review)

**Steps:**
1. Confirm deploy under test is past SHIP P baseline (`/api/health` panel_v + features).
2. Start a Game run; make ≥2 paper trades (≥1 closed).
3. End session: wait for clock 0 or use force-end / force-sell fixture.
4. Confirm a Review panel/modal/sheet appears, or a clearly visible one-tap Review control beside Restart.

**Pass criteria:**
- Review visible at session end without hunting (not buried).

**Evidence to capture:**
- Screenshot: session end state with Review visible

---

## AC-R02 — Grounded in this run

**Title:** Review references this run's trades / P&L / outcome (not generic filler only)

**Steps:**
1. Record the run's trades (tickers, entry/exit, P&L) and final equity before ending.
2. Read the review text.
3. Match ≥1 named trade/coin and P&L arc/outcome to the recorded run; confirm 1–3 takeaways.

**Pass criteria:**
- Review cites actual trades / P&L / outcome from this run; numbers consistent with session.
- Generic tips alone = FAIL. Invented trades or wrong P&L = FAIL.

**Evidence to capture:**
- Note: recorded run vs review text side-by-side
- Screenshot: review content

---

## AC-R03 — Leave review + Restart intact (K path)

**Title:** Can leave review and Start/Restart clean (K path intact)

**Steps:**
1. From review, Dismiss/close and/or tap Restart.
2. Confirm new run starts clean per SHIP K (fresh clock, fresh balance, no prior positions).

**Pass criteria:**
- Review not a trap; Restart works from/after review.
- K clean-restart behavior unchanged.

**Evidence to capture:**
- Screenshot: after leaving review
- Screenshot: fresh run after Restart

---

## AC-R04 — Honest copy, no stuck overlay

**Title:** Doesn't block sell / closed-history read; honest paper / not-advice; no stuck overlay

**Steps:**
1. Confirm sell controls and closed-trade history remain readable/usable while/after the review shows (scroll/close as needed).
2. Read review copy for paper-research / not-financial-advice label.
3. Check for fake certainty ("you would've moon'd if…").
4. After dismiss, confirm Game/Filter clickable (no stuck modal).

**Pass criteria:**
- Paper / not-advice labeling present; plain short recap (not an essay).
- No fake-certainty claims; no stuck overlay.
- Review blocking sell or closed-history read = FAIL.

**Evidence to capture:**
- Screenshot: review with disclaimer
- Note/screenshot: post-dismiss UI usable

---

## AC-R05 — Soft cue respects Mute

**Title:** Optional soft "review ready / cooking" cue OK; Mute kills it

**Steps:**
1. Unmuted: end a run; note any sound/visual "review cooking / ready" cue.
2. Toggle Mute; end another run (force-end OK).
3. Confirm no audible cue while muted (visual soft cue OK).

**Pass criteria:**
- Cue, if present, is soft (no alarm/loop); Mute silences it.
- No cue at all = PASS. Cue plays while muted = FAIL.

**Evidence to capture:**
- Note: unmuted vs muted behavior

---

## Gate notes

- AC-R01…R05 here ≠ SHIP G AC-R01…R06. Label all STATUS lines "SHIP P".
- Force-end fixture is acceptable for R01–R05; no need to wait a real 24h.
- If AI backend unavailable on live at test time and no review renders: R01 `FAIL` (or `BLOCKED` if the feature flag is not live yet).
- K restart regressions found here: FAIL R03 and flag to SHIP K owner; do not reopen K scope.
