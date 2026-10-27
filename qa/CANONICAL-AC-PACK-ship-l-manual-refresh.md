# Canonical AC pack — Nunu Memecoin Filter — SHIP L (Manual Update now)

- **Cut:** 2026-10-27 SHIP L (binding, LOCKED) — `qa/cut-2026-10-27-ship-l-manual-refresh.md`
- **Product:** Nunu Memecoin Filter (paper research only)
- **Production under test:** https://nunu-funcore.onrender.com
- **SoT code:** `/workspace/trading/nunu-filter/`
- **Proto SoT (eyes):** `qa/mock-filter-manual-update-v1.{html,png,md}` — **Update now** = force bot/filter pass, not panel Refresh
- **Owner ask:** “add like a manual update button so it don’t get like 2 3 m behind” (loop ~5m; SHIP H delay warning already live)
- **Repo:** ptsdnut-afk/nunu-funcore
- **Order / unlock:** Eng after SHIP J (AC-D) → SHIP K (AC-E). QA does not BLOCK before J and K STATUS land unless owner pulls L forward.
- **Baseline:** live pre-L header Refresh `#btnRefresh` → `loadData(true)` only (reloads last `/api/flagged`; does **not** re-run `bot.py`). BLOCK only when Update now deploy is live past current panel_v (`/api/health`).
- **Severity:** all items below are **BLOCK** (cut table) — Proto tagged U05 WARN; cut wins.
- **Rule:** Eng CI CLEAN ≠ AC PASS. Never soft-pass BLOCK. Client reload / re-paint of same snapshot ≠ U02 PASS. Single-mint `/api/scan` ≠ U02 PASS.
- **Scope of this pack:** AC-U01…U05 (IDs match cut + Proto MD)

## Scope

**In:** loud **Update now** on Filter Token Flow beside Refresh · tap forces a fresh bot/filter pass that refreshes flagged Token Flow · busy (disabled / “Updating…”) + done cues (age drops, toast) · short error on failure · cooldown / rate-limit

**Out (from cut — do not gate):**
- Permanent faster `interval_min` / always-on sub-minute bot
- Dex websocket / tick-level live
- Replacing SHIP H lag warning (control sits beside it)
- SHIP J dismiss / SHIP K 24h restart
- Fake “live 1s” claims
- Single-mint `/api/scan` as the “update”

## BLOCK acceptance criteria (summary)

| ID | Proof | Expected |
|----|--------|----------|
| AC-U01 | Screenshot | Obvious **Update now** on Filter Token Flow (beside/near Refresh; not buried) |
| AC-U02 | Note + age | Tap kicks a **fresh bot/filter pass** — not only client reload of stale `/api/flagged` |
| AC-U03 | Screenshot | Busy state while running; control disabled until done; no double-fire |
| AC-U04 | Note + age | On success, data age / lag banner updates; Token Flow usable; light Refresh can remain |
| AC-U05 | Note | Cooldown / rate-limit so mash doesn’t melt Render |

STATUS must be exactly `PASS` \| `FAIL` \| `BLOCKED`. No soft-pass.

---

## AC-U01 — Update now visible

**Title:** Obvious Update now on Filter Token Flow (not buried)

**Steps:**
1. Confirm deploy under test has SHIP L (`/api/health` panel_v + features).
2. Open Filter Token Flow; locate **Update now** (or Scan now) beside/near header Refresh / data-age chip.
3. Confirm it is distinct from Refresh and obvious.

**Pass criteria:** Update now visible, labeled, near Refresh / lag chip. Refresh alone = FAIL.

**Evidence:** screenshot of control + health JSON

---

## AC-U02 — Forces bot/filter pass (not Refresh)

**Title:** Tap kicks a fresh bot/filter pass — not only client reload of stale `/api/flagged`

**Steps:**
1. Note data age / `data_as_of` (and `/api/status` or feed meta last-pass time if exposed) before tap.
2. Tap **Update now**; watch network (expect server-side trigger, not just `GET /api/flagged`).
3. After done, confirm last-pass timestamp advanced ahead of next scheduled loop and flagged rows reflect the new pass.

**Pass criteria:** A new bot/filter pass runs on demand. Same-snapshot reload or single-mint `/api/scan` only = FAIL.

**Evidence:** before/after age + pass timestamp notes, network note, screenshots

---

## AC-U03 — Busy state + no double-fire

**Title:** Busy while running; control disabled until done

**Steps:**
1. Tap Update now; capture disabled / spinner / “Updating…”.
2. While in flight, tap again — confirm no second request fires.
3. Wait until done (or failure); confirm control re-enables (cooldown OK) or short error shown.

**Pass criteria:** Busy cue in flight; no double-fire; re-enable or visible error — no silent noop.

**Evidence:** screenshots busy + done (+ error if hit)

---

## AC-U04 — Done: age/lag updates, Token Flow usable

**Title:** On success, data age / lag banner updates; Token Flow usable; Refresh can remain

**Steps:**
1. After U02 pass lands, read data-age chip / SHIP H lag banner.
2. Confirm age dropped (e.g. Behind live → Near live) and banner honest.
3. Interact with Token Flow (scroll / open row); confirm Refresh still works for light reload.

**Pass criteria:** Age/lag reflects fresh pass; Token Flow usable; lag warning still honest.

**Evidence:** before/after age screenshots + note

---

## AC-U05 — Rate-limit / Render safety

**Title:** Cooldown / rate-limit so mash doesn’t melt Render

**Steps:**
1. Mash Update now repeatedly right after a pass completes.
2. Confirm cooldown (UI and/or server reject, e.g. 429 / “wait Ns”) — passes don’t stack.
3. Optional: Product Performance confirms no overlapping scans / Render spike.

**Pass criteria:** Server- or client-enforced cooldown prevents stacked passes.

**Evidence:** note of mash behavior + network/status codes

---

## Gate notes

- Cut file already lists AC-U01…U05 (no lag vs PM). Proto MD tags U05 WARN; cut table treats it BLOCK — this pack follows the cut.
- Pre-L header Refresh (`#btnRefresh` → `loadData(true)`) does not re-run the bot; do not credit it toward U01/U02.
- Product Performance may confirm feed path / age move — QA still owns STATUS.
- Do not BLOCK until SHIP J and SHIP K STATUS land (unless owner pulls L forward).
