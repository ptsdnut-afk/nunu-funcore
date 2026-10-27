# LOCKED cut — SHIP S Token Flow coins appear then vanish (sitting drop)

**Status:** LOCKED — owner 2026-10-27: “this thing is like broken coins aper then desapere look at it and fix it.” Live panel_v 70: `flagged_count=0` while `sitting_dropped_count=10` (scores up to 98, sitting ~5.1m). Eng **unlocked now** (hotfix — ok beside/after L; don’t wait for M…).
**Date:** 2026-10-27
**Why:** Owner shot `qa/owner-2026-10-27-coins-appear-disappear-ask-v70.png` — empty Token Flow (“No tokens flagged this run”) with bot on. `/api/latest` shows high-score mints present but **dropped by sitting_drop_min=5** so the board looks broken.

## In
1. **Stop empty-board theater** — coins must not flash in then vanish into a blank “0 flagged” wall while the bot is healthy and top_scores exist.
2. **Fix sitting-drop behavior (owner direction via PM)** — **show sitting coins, don’t hard-drop** off Token Flow. Keep them on the board with clear **Sitting** chrome after `sitting_drop_min`. Optional later retune of the minutes; do **not** leave filter “strict empty.”
3. **Transparent cue** — if something is aged/sitting, label it; don’t silently empty the list.
4. **Health honesty** — when bot ok + evaluated>0 + top_scores, never show a dead empty state that reads like the product is broken.

## Out
- Manual Update button (SHIP L — separate)
- Gold Scar / Trade chart / follow tab (N/M/R)
- Killing the sitting concept entirely without a replacement cue
- Inventing fake flagged rows

## Done when
- QA BLOCK PASS AC-V01…V04 on live
- Owner no longer sees coins pop then the whole board go empty for no clear reason

## BLOCK acceptance criteria
| ID | Proof | Expected |
|----|--------|----------|
| AC-V01 | `/api/latest` + screenshot | With bot healthy + top_scores, Token Flow is not a blank “0 flagged / No tokens…” wall |
| AC-V02 | Screenshot / note | Sitting / aged coins either stay visible with Sitting chrome **or** drop only under retuned rules that don’t wipe the board in ~5m |
| AC-V03 | Screenshot | Appear→vanish flash without explanation is gone (or labeled Sitting, not deleted) |
| AC-V04 | Note | SHIP F age-window intent preserved in spirit (no silent forever-stale spam) — document the new rule |

## Unlock order
1. **Owner** — fix ask (done 2026-10-27).
2. **Bug Reproduction** — flash path pack (appear→empty).
3. **Product Performance** — confirm bot/feed green vs empty UI.
4. **Engineering Lead** — hotfix unlocked now (ok with/after L); bump panel_v; Manual Deploy.
5. **QA** — BLOCK V01…V04 after health bumps.
6. **PM** — close cut.

## Eng handoff
SoT `/workspace/trading/nunu-filter/` → sitting_drop / Token Flow flagging. Live proof: `flagged_count=0`, `sitting_dropped_count=10`, sample TRUMP2028/CHICO score 98 @ ~5.1m sitting. Owner shot: `qa/owner-2026-10-27-coins-appear-disappear-ask-v70.png`. Bug pack: `/workspace/repro-2026-10-27-coins-appear-disappear/` (REPRO + live empty + `/api/latest` poll). Do keep-visible + Sitting chrome (don’t hard-drop). Don’t leave empty board. Do not claim PASS — QA gates.

QA pack: `qa/CANONICAL-AC-PACK-ship-s-sitting-drop-vanish.md` (AC-V01…V04).
