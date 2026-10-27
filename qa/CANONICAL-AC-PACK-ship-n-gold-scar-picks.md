# Canonical AC pack — Nunu Memecoin Filter — SHIP N (Gold Scar picks)

- **Cut:** 2026-10-27 SHIP N (LOCKED, binding) — `qa/cut-2026-10-27-ship-n-gold-scar-picks.md`
- **Product:** Nunu Memecoin Filter (paper research only — not buy advice)
- **Production under test:** https://nunu-funcore.onrender.com
- **Gate SoT:** `qa/owner-2026-10-27-gold-scar-gate-process.md` (owner 7-gate stack). Eng code `gold_scar.py` is stricter. Looser = FAIL.
- **Proto:** `qa/mock-filter-gold-ai-pick-v1.md` — eyes reference, not blocking
- **Fixture:** `qa/qa-gold-fixture.md` — `?qa_gold=1` (chrome, why, links) and `?qa_gold=arrive` (one ♪ GOLD). Not a PASS.
- **Repo:** ptsdnut-afk/nunu-funcore
- **Baseline:** BLOCK only when `/api/health` carries SHIP N (`panel_v` 77, feature `gold_scar`).
- **IDs:** AC-G01…G06 for this cut only. **ID collision guard:** these G IDs are SHIP N only — never cross-map to SHIP G Game polish AC-R/F/S packs.
- **Severity:** all items below are **BLOCK** unless marked WARN
- **Rule:** Eng CI CLEAN ≠ AC PASS. Never soft-pass BLOCK. Eng does not claim PASS on this pack.
- **Scope of this pack:** AC-G01…G06 only
- **Empty gold most of the time is PASS** (not a bug) — never stuff mediocre coins to prove chrome.

## Scope

**In:** strict multi-gate promotion (Filter→Clone→Narrative→Tape→On-chain→Overheard→macro veto, or stricter) · separate Gold section on Filter · gold chrome distinct · special gold sound (≠ SHIP I new-token; Mute kills) · honest empty state · short why + contract/links path, paper copy

**Out (from cut — do not gate):**
- Sitting drop (SHIP S), Manual Update (SHIP L), custom profile pic (SHIP T)
- Gold on every high Token Flow score
- Price guarantees / buy signals
- Replacing specialist bots
- Traders meta, Game changes, sticky tab, copytrade, FOMO-style wallet following

## BLOCK acceptance criteria (summary)

| ID | Proof | Expected |
|----|--------|----------|
| AC-G01 | Screenshot | Separate Gold section on Filter |
| AC-G02 | Screenshot | Gold chrome clearly distinct |
| AC-G03 | Note / ♪ | New gold → special sound (mute ok; ≠ SHIP I) |
| AC-G04 | Note | Normal flags do **not** enter gold; bar uses multi-gate SoT |
| AC-G05 | Screenshot | Empty state when no gold |
| AC-G06 | Note / screenshot | Gold row shows short why + contract/links path; paper/not advice |

STATUS must be exactly `PASS` \| `FAIL` \| `BLOCKED`. No soft-pass.

---

## AC-G01 — Separate Gold section

**Title:** Separate Gold section on Filter

**Steps:**
1. Confirm deploy under test has SHIP N (`/api/health` panel_v 77 and feature `gold_scar`); else STATUS `BLOCKED`.
2. Hard refresh bare https://nunu-funcore.onrender.com (no stale `?v=`).
3. Open Filter; locate Gold / Top picks section.

**Pass criteria:**
- Dedicated Gold section exists, separate from Token Flow (not mixed anonymously into the flow list).

**Evidence:** screenshot of Filter showing Gold section + health JSON

---

## AC-G02 — Gold chrome distinct

**Title:** Gold chrome clearly distinct

**Steps:**
1. With a live gold pick or `/?qa_gold=1`, view the Gold section and Token Flow together.
2. Compare against the eyes storyboard `qa/mock-filter-gold-ai-pick-v1.md`.

**Pass criteria:**
- Gold pick reads as gold-SCAR stand-out — distinct from normal flagged rows at a glance.
- Readable on dark theme.

**Evidence:** screenshot gold row beside a normal flag row

---

## AC-G03 — Special gold sound

**Title:** New gold → special sound (mute ok; ≠ SHIP I)

**Steps:**
1. Sound on. Open `/?qa_gold=arrive` (or a real new gold mint).
2. Confirm one distinct ♪ GOLD — not the SHIP I new-token sound nor Game casino sound.
3. Toggle Mute; open arrive again → no sound.

**Pass criteria:**
- Distinct gold cue, once per new gold mint (no repeat spam on poll).
- Mute kills it.
- `/?qa_gold=1` shows the card and seeds quietly (no chime on that first load).

**Evidence:** note (+ UI/console or short capture)

---

## AC-G04 — Normal flags stay out; multi-gate bar

**Title:** Normal flags do not enter gold; bar uses multi-gate SoT

**Steps:**
1. Observe Token Flow flagged rows (incl. high scores) over several polls.
2. Confirm none appear in Gold unless they clear the gate stack.
3. Check `gold_scar` on `/api/flagged` or `/api/gold`. Gates are the seven in `qa/owner-2026-10-27-gold-scar-gate-process.md`. Stricter OK; looser = FAIL. `?qa_max=1` (QAMAX, score 12) must not enter Gold.

**Pass criteria:**
- High Token Flow score alone does not promote to gold.
- Gate evidence matches owner stack or stricter.

**Evidence:** note with sampled mints + gate outcome

---

## AC-G05 — Honest empty state

**Title:** Empty state when no gold

**Steps:**
1. When no coin clears gates (common; the default page), view Gold section.

**Pass criteria:**
- Readable empty state (“No gold picks yet — rare on purpose”) — not broken/blank, not stuffed with mediocre coins.
- Empty gold most of the time = PASS, not a bug.

**Evidence:** screenshot of empty Gold section

---

## AC-G06 — Why + contract/links; paper copy

**Title:** Gold row shows short why + contract/links path; paper/not advice

**Steps:**
1. Open a gold row (`/?qa_gold=1` or live).
2. Confirm short plain-language why.
3. Confirm path to full contract + links (copy CA / chart / X).
4. Confirm paper research / not buy advice copy; no "will moon" / price guarantee.

**Pass criteria:**
- Why line present and short.
- Contract/links reachable.
- Paper/not-advice framing; no buy signal language.

**Evidence:** screenshot / note

---

## Gate notes

- BLOCK only once `/api/health` carries SHIP N; before that STATUS `BLOCKED`.
- Fixture gold OK for G02/G03/G06; G04/G05 on live.
- Do not confuse AC-G01…G06 (SHIP N) with SHIP G Game polish AC-R/F/S.
- Sound ≠ SHIP I new-token; Mute kills.
- Eng must not mark these PASS.
