# Mock: Game AI post-game review · v1 (SHIP P draft)

**Date:** 2026-10-27  
**Eyes only** — not a PR · EXAMPLE $ · tickers **PEBBLE** / **AURUM** (not live HACHI)  
**Cut (draft):** `qa/cut-2026-10-27-ship-p-game-ai-review.md`  
**Optional SoT** — Proto eyes; does **not** reopen SHIP K; separate from N / O / J→K→L→M

## Ask covered
Owner: “also when 24h finish can I get an ai analize like when you play chess and you finish game it reviews sum like that but with this.”

Take-our-time AI: UI can show **Review cooking…** then a short readable recap — not a wall of jargon. Review overlays / follows the finish state; K restart chrome stays.

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · BEFORE** | 24h ends | Equity / closed trades / calm rocket / Restart 24h — **no** review |
| **2 · AFTER · LOADING** | Cooking | Dim Game + sheet “AI reviewing your run…” · chess-engine vibe · soft cue · dismissible |
| **3 · AFTER · REVIEW** | Recap | Grade **B+** · headline · 3–5 bullets (best / worst / timing / try next) · **Start next 24h** + Dismiss · top-right **×** · paper / not-advice |

Banner: **EYES ONLY · NOT A PR · EXAMPLE $ · PEBBLE / AURUM**.

## Eng / PM pointer — proposed ACs
| ID | Expected |
|----|----------|
| **AC-R01** | On 24h finish (or force-end), offer AI run review (auto sheet or one-tap Review) |
| **AC-R02** | Review is plain English grounded in *this* run’s trades / P&L — not raw logs or generic filler only |
| **AC-R03** | Can dismiss (X/Back) and still Start / Restart 24h — K path intact; review doesn’t trap |
| **AC-R04** | Doesn’t block sell / closed-history read; honest paper / not-advice; no stuck overlay |
| **AC-R05** | Optional soft “review ready / cooking” cue; Mute kills it |

## Out of scope
- Changing K restart mechanics (K stays its own cut)
- Live mid-run coaching / every-tick tips
- Gold Scar Filter lane (SHIP N)
- Hide Max unknown (SHIP O)
- Rewriting score math / feed loop
- Production PR / merging / claiming PASS
- Real money / wallets / live HACHI as hero
- Personal names

## Files
- `qa/mock-game-ai-review-v1.html`
- `qa/mock-game-ai-review-v1.png`
- `qa/mock-game-ai-review-v1.md` (this file)

## Pattern refs
- `qa/mock-game-finish-tick-v1.{html,md,png}`
- `qa/mock-game-polish-v2.{html,md,png}`
- `qa/cut-2026-10-27-ship-k-game-24h-restart.md`
- `taste.md`
