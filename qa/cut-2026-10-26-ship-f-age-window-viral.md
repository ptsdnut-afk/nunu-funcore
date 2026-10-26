# LOCKED cut — SHIP F Slice 1 Filter age-window (~5m drop) + Social Tap freshness

**Status:** UNLOCKED — SHIP E Game finish QA PASS on live panel_v 64. Eng implements this cut. Residual SHIP E DOWN chip sign nit stays out.
**Date:** 2026-10-26
**Why:** Owner finish mandate. Filter used to feel frozen (same names past the window). Freshness is green (`interval_min` 5). Drop coins that sit too long; keep Social Tap feeling current with the feed.

## In
1. **Age-window drop (~5m)** — once a flagged coin’s `sitting_minutes` exceeds ~5, it leaves the Filter board on the next scan (no sticky Maya-style linger). Tunable constant; default 5.
2. **Honest Sitting** — Sitting metric still shown; coins near the window read clearly before they drop.
3. **Social Tap freshness polish** — with feed on a 5-min loop, Social Tap shows current viral-style items or honest empty/loading/error — not stale chrome from an idle bot. No inventing posts. No full Social Tap rewrite (Slice 5 already shipped).

## Out
- SHIP E Game finish (already on live 64 — QA gates close) and the DOWN chip sign nit
- Real money / wallets
- Changing score math / trench bands
- Multi-pair BRUZIL UX residual (non-blocking)
- Widening MAX_PAIR_AGE_HOURS (72h pair age stays; this cut is **sitting** window)

## Done when
- QA BLOCK PASS on live for AC-A01…A06
- Proto eyes vs `qa/mock-filter-age-window-v1.*`
- Owner sees old sitters drop within ~one scan after 5m; Social Tap not frozen

## BLOCK acceptance criteria
| ID | Proof | Expected |
|----|--------|----------|
| AC-A01 | API + screenshot | Config / behavior: sitting drop threshold ≈ 5 min (documented constant) |
| AC-A02 | Screenshot or timed evidence | Coin with `sitting_minutes` past threshold is **gone** from Filter after next scan |
| AC-A03 | Screenshot | Sitting still visible on remaining rows; near-window rows readable |
| AC-A04 | Screenshot | Score / quote_sync / game_finish chrome not regressed |
| AC-A05 | Screenshot | Social Tap open shows current viral-style items from real source **or** honest empty/loading/error — not obviously stale idle chrome |
| AC-A06 | Timing | Open/close Social Tap + Filter still snappy |

## Unlock order
1. **PM** — this cut locked.
2. **QA** — SHIP E T01–T07 PASS on panel_v 64 (done; this cut is unlocked).
3. **Proto** — light eyes (mock only if needed).
4. **Engineering Lead** — implement; merge when cut-clean.
5. **QA** — BLOCK on production after deploy past 64. This slice is panel_v 65.

## Refs
- Mock: `qa/mock-filter-age-window-v1.{html,md}` (Proto 2026-10-26 — before sticky linger → after ~5m drop)
- SoT: `filter.py` `sitting_minutes`, constant `SITTING_DROP_MIN`
- Live: https://nunu-funcore.onrender.com
- Repo: ptsdnut-afk/nunu-funcore
- Canonical pack: `qa/CANONICAL-AC-PACK-ship-f-slice1-age-window.md`
