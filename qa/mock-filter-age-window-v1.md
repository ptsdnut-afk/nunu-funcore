# Mock: Filter age-window (~5m drop) + Social Tap freshness · SHIP F Slice 1 v1

**Date:** 2026-10-26  
**Eyes only** — not a PR · locked cut `qa/cut-2026-10-26-ship-f-age-window-viral.md`  
**SoT metric:** `sitting_minutes` in `filter.py` (tunable drop threshold ≈ 5 min)

## Ask covered
Owner: Filter used to feel frozen (same names past the window). Drop coins that sit **~5m+**; Social Tap should feel current with the 5‑min feed — never invent posts. Freshness is already green (`interval_min` 5).

## Before → After
| Side | Board | Social Tap |
|------|--------|------------|
| **BEFORE** | EXAMPLE Maya-style sitter still on board at Sitting **14m** (sticky linger) | Grey idle chrome (~42m) — frozen feel |
| **AFTER** | Sitter **GONE** on next scan; remaining rows show Sitting; LUNEX **4m** amber near-window warn | Current-feeling viral-style EXAMPLE items (~2–4m) **or** honest empty — not stale idle chrome |

All tickers / viral lines labeled **EXAMPLE**. Accurate-or-omit — no live coin claims.

## Eng / PM pointer — ACs
| ID | Expected |
|----|----------|
| **AC-A01** | Sitting drop threshold ≈ 5 min (documented constant) |
| **AC-A02** | Coin with `sitting_minutes` past threshold **gone** after next scan |
| **AC-A03** | Sitting still visible; near-window rows readable (amber) |
| **AC-A04** | Score / quote_sync / game_finish chrome not regressed |
| **AC-A05** | Social Tap: current viral-style from real source **or** honest empty/loading/error — not stale idle chrome |
| **AC-A06** | Open/close Social Tap + Filter still snappy |

## Out of scope
- SHIP E Game finish (live 63 — QA gates close)
- Real money / wallets
- Score math / trench bands
- Widening `MAX_PAIR_AGE_HOURS` (72h pair age stays; this cut is **sitting** window)
- Full Social Tap rewrite (Slice 5 shipped — polish only)
- Opening a PR or editing production app code

## Files
- `/workspace/trading/nunu-filter/qa/mock-filter-age-window-v1.html`
- `/workspace/trading/nunu-filter/qa/mock-filter-age-window-v1.png`
- `/workspace/trading/nunu-filter/qa/mock-filter-age-window-v1.md` (this file)

## Binding cut
`/workspace/trading/nunu-filter/qa/cut-2026-10-26-ship-f-age-window-viral.md`
