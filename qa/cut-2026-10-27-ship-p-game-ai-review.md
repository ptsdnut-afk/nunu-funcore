# DRAFT cut — SHIP P Game 24h AI review (chess-style)

**Status:** LOCKED — Eng **unlocked now** (O CLOSED PASS on 75). Take time on AI. **Separate from SHIP K** (K = clean restart only).
**Date:** 2026-10-27
**Why:** Owner: “when 24h finish can I get an ai analize like when you play chess and you finish game it reviews … but with this.”

## In
1. **Post-24h review surface** — when session ends (clock 0 / force-sell), show a clear **Review** (panel/modal/sheet) before or beside Restart — not buried.
2. **Run summary** — plain-language AI (or structured) review of *this* 24h: what went well / poorly, notable trades, rough P&L arc, 1–3 takeaways. Chess-recap energy, not a novel.
3. **Grounded in this run** — uses actual session tape / closed trades / equity path from *this* Game run (not generic tips).
4. **Restart still works** — SHIP K clean Start/Restart remains; review doesn’t trap you (Dismiss / Restart after review).
5. **Honest limits** — paper research only; no “you would’ve moon’d if…” fake certainty; label as review not financial advice.

## Out
- Changing K restart mechanics (K stays its own cut)
- Live coaching mid-run / every-tick tips
- Gold Scar Filter lane (SHIP N)
- Rewriting score math / feed loop

## Done when
- QA BLOCK PASS AC-R01…R05 on live (after Eng ships)
- Owner finishes a 24h (or force-end) and gets a readable run review

## BLOCK acceptance criteria
| ID | Proof | Expected |
|----|--------|----------|
| AC-R01 | Screenshot | At 24h end, Review surface appears (or one-tap Review) |
| AC-R02 | Note | Review references this run’s trades / P&L / outcome (not generic filler only) |
| AC-R03 | Screenshot | Can leave review and Start/Restart clean (K path intact) |
| AC-R04 | Screenshot | Copy is plain + honest (paper / not advice); no stuck overlay |
| AC-R05 | Note | Optional soft “review ready” cue OK; Mute kills it |

## Unlock order
1. **Owner** — yes (done 2026-10-27: after K; take time on AI).
2. **Proto Bot** — optional post-game review mock; can cook while K ships.
3. **Engineering Lead** — after K (don’t block K); take time on review quality; bump panel_v; Manual Deploy.
4. **QA** — BLOCK R01…R05 after health bumps (force-end fixture OK).
5. **PM** — close cut.

## Eng handoff
SoT `/workspace/trading/nunu-filter/` → Game session end + tape/closed trades + new review UI. Keep K restart path. Prefer short grounded recap over long LLM essay. Do not claim PASS — QA gates. Owner: chess-style post-game review.

QA pack: `qa/CANONICAL-AC-PACK-ship-p-game-ai-review.md` (AC-R01…R04 — not SHIP G Rs).

SoT mock (optional eyes): `qa/mock-game-ai-review-v1.{html,png,md}` — chess-style AI run review. Proto proposed AC-R01…R05 (plain EN, dismissible, K Restart intact).
