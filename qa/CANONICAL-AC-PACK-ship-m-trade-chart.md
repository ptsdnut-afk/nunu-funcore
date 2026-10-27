# Canonical AC pack — Nunu Memecoin Filter — SHIP M (Trade chart + $ label clarity)

- **Cut:** 2026-10-27 SHIP M (binding, LOCKED) — `qa/cut-2026-10-27-ship-m-trade-chart.md`
- **Product:** Nunu Memecoin Filter (paper research only)
- **Production under test:** https://nunu-funcore.onrender.com
- **SoT code:** `/workspace/trading/nunu-filter/`
- **Owner fixtures:** `qa/owner-2026-10-27-trade-chart-missing-ask-v67.png`, `qa/owner-2026-10-27-trade-dollar-labels-ask.png`
- **Bug repro (folds into AC-C03, not a separate cut):** `qa/repro-2026-10-27-game-trade-dollar-units/` (`REPRO.md`, `owner-trade-dollar-sense-v67.png`) — HACHI open trade: NOW $367.4k (DEX FDV/mcap) beside STAKE $1 / +$0.19 paper reads as position worth $367k.
- **Proto:** Trade chart + $ label mock — optional, not blocking Eng or gate
- **Repo:** ptsdnut-afk/nunu-funcore
- **Unlock order:** Eng builds after SHIP J → SHIP K (with/after SHIP L). Baseline = whatever panel_v is live when M ships; gate only after Eng deploys **past** that panel_v with chart + label changes (confirm `/api/health` panel_v + features). Do not BLOCK on a deploy that doesn't carry SHIP M.
- **IDs:** AC-C01…C04 for this cut only — **not** SHIP D Slice 9 ceiling-math AC-C01…C05.
- **Severity:** all items below are **BLOCK** unless marked WARN
- **Rule:** Eng CI CLEAN ≠ AC PASS. Never soft-pass BLOCK. Local / PR proof ≠ success.
- **Scope of this pack:** AC-C01…C04 only (IDs match cut table exactly)
- **Order:** Finish SHIP J AC-D01…D03 and SHIP K AC-E01…E03 STATUS (and SHIP L if shipped first) before BLOCKing this pack.

## Scope

**In:** visible price/mcap chart in Trade right pane for selected coin (Dex embed or lightest honest path) · chart readable vs LIVE / NOW · $ families clearly labeled — NOW = market cap (MCAP), STAKE / MARK / P&L = paper position $, ENTRY / LAST / LIVE USD = token price · behind cue OK (Dex-live chart vs bot-lagged NOW; no fake sync; optional source note) · empty / fail state message

**Out (from cut — do not gate):**
- Manual Update / force scan (SHIP L)
- Dismiss/Back chrome (SHIP J)
- Game 24h restart (SHIP K)
- Full TradingView terminal / multi-TF research suite
- Changing score math / feed interval / stake defaults

## BLOCK acceptance criteria (summary)

| ID | Proof | Expected |
|----|--------|----------|
| AC-C01 | Screenshot | Trade with selected coin shows a chart in the main empty pane (not blank) |
| AC-C02 | Screenshot | Chart matches the selected mint; LIVE/NOW still visible beside it |
| AC-C03 | Screenshot | $ labels clearly separate mcap (NOW) vs position $ (stake/mark/P&L) vs token price (entry/last/live) |
| AC-C04 | Note | No coin / load fail → clear empty/error state (not stuck black panel) |

STATUS must be exactly `PASS` \| `FAIL` \| `BLOCKED`. No soft-pass.

---

## AC-C01 — Chart in Trade pane

**Title:** Trade with selected coin shows a chart in the main empty pane (not blank)

**Steps:**
1. Confirm deploy under test carries SHIP M past baseline (`/api/health` panel_v + features).
2. Open Trade and select a coin (same shape as `qa/owner-2026-10-27-trade-chart-missing-ask-v67.png`).
3. Wait for load; inspect the right/main pane.

**Pass criteria:**
- A real price/mcap chart renders in the previously blank pane (not decorative/placeholder).

**Evidence to capture:**
- Screenshot: Trade selected coin with chart rendered (compare to owner fixture blank pane)

---

## AC-C02 — Chart matches mint; LIVE/NOW visible

**Title:** Chart matches the selected mint; LIVE/NOW still visible beside it

**Steps:**
1. With chart from C01, note selected coin's mint / CA.
2. Verify chart source (embed URL, pair, or ticker/label) corresponds to that mint.
3. Select a second coin; confirm chart switches to the new mint (no stale chart).
4. Confirm LIVE / NOW values remain visible alongside the chart.

**Pass criteria:**
- Chart is for the selected mint, and updates on reselect.
- LIVE / NOW visible next to chart. Chart Dex-live vs bot-lagged NOW drift is OK (behind cue); faked sync is not required.

**Evidence to capture:**
- Screenshot: chart + LIVE/NOW with mint identifiable
- Note: mint ↔ chart source match; second-coin switch result

---

## AC-C03 — $ label families separated

**Title:** $ labels clearly separate mcap (NOW) vs position $ (stake/mark/P&L) vs token price (entry/last/live)

**Steps:**
1. Open Trade on an open paper position (repro shape: HACHI — `qa/repro-2026-10-27-game-trade-dollar-units/REPRO.md`; fixture `qa/owner-2026-10-27-trade-dollar-labels-ask.png`).
2. Read every `$` value on Trade chrome.
3. Classify each into: market cap (NOW / where it's at), paper position $ (STAKE / MARK / P&L), token price (ENTRY / LAST / LIVE USD).

**Pass criteria:**
- NOW is explicitly labeled MCAP / market cap.
- STAKE / MARK / P&L explicitly read as position dollars.
- ENTRY / LAST / LIVE USD explicitly read as token price (not mcap).
- Repro no longer reproduces: a $…k mcap beside $1 stake does not read as the position's value.

**Evidence to capture:**
- Screenshot: open position Trade with all three labeled families visible
- Note: repro pack re-run result (FAIL if $367k-style mcap still unlabeled next to stake)

---

## AC-C04 — Empty / fail state

**Title:** No coin / load fail → clear empty/error state (not stuck black panel)

**Steps:**
1. Open Trade with no coin selected; observe pane.
2. Force chart load fail (e.g. coin without Dex pair, or block embed domain in DevTools) and observe pane.

**Pass criteria:**
- Both cases show a short clear message; no infinite black / blank pane or endless spinner.

**Evidence to capture:**
- Note (+ screenshot if possible): no-coin state and fail state text

---

## Gate notes

- Do not confuse these AC-C01…C04 with SHIP D Slice 9 ceiling-math AC-C01…C05.
- Bug Repro `qa/repro-2026-10-27-game-trade-dollar-units/` is gated under AC-C03 only — no separate cut/STATUS.
- Chart lagging vs NOW is acceptable (behind cue); optional source note missing = WARN, not FAIL.
- BLOCK only after Eng deploys past current live panel_v with SHIP M; if not live → `BLOCKED`.
- Score math / feed interval / stake default changes are out of scope; flag regressions separately.

## STATUS

| ID | STATUS | Evidence |
|----|--------|----------|
| AC-C01 | | |
| AC-C02 | | |
| AC-C03 | | |
| AC-C04 | | |
