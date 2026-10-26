# CANONICAL AC PACK — SHIP F Slice 1 Filter age-window

Production target: the live Filter board and Social Tap.
Cut: qa/cut-2026-10-26-ship-f-age-window-viral.md
Eyes: qa/mock-filter-age-window-v1.{html,md}
Baseline: panel_v 64 SHIP E PASS. This slice is panel_v 65.

## Constants
| Name | Value | Where |
|------|-------|--------|
| `SITTING_DROP_MIN` | **5.0** | `filter.py`. Health field `sitting_drop_min`. A row leaves the board when rounded `sitting_minutes` **>** 5.0. 5.0 stays. |
| `SITTING_NEAR_LEAD_MIN` | **1.0** | Amber when `sitting_minutes` ≥ 4.0 and still on the board (`sitting_band: near`). |
| `SITTING_FRESH_MIN` | **2.0** | Green Sitting value under 2 minutes. Display only. |
| `SOCIAL_TAP_FRESH_MIN` | **5.0** | `viral.py`. Payload `fresh_sec` is 300. Past that, old cards are omitted. |
| `MAX_PAIR_AGE_HOURS` | **72** | Unchanged. This cut is the sitting window. |

## BLOCK
| ID | Expected |
|----|----------|
| AC-A01 | `GET /api/health` is `panel_v` 65 with `sitting_drop_min` 5, features `sitting_window` and `social_tap_fresh`. The Filter header reads `sitting drop 5m`. |
| AC-A02 | After the next filter scan, a coin whose `sitting_minutes` exceeds 5 is absent from `GET /api/flagged`. `GET /api/latest` lists it under `sitting_dropped` (ticker, mint, sitting_minutes, score) and `thresholds.sitting_drop_min` is 5. The same mint stays off the following scan. |
| AC-A03 | Rows that remain show Sitting (`1.8m` fresh, `3.6m` inside, `4.0m` amber `near ~5m drop`). |
| AC-A04 | Score /12, mcap progress, quote sync, and Game finish chrome unchanged. |
| AC-A05 | Social Tap shows copied public items while the feed `updated_at` is inside 5 minutes (pill `current`, subtitle includes `feed loop 5m`) or an honest loading, empty, or error. A feed older than 5 minutes does not keep the previous cards. Nothing invented. Bot idle does not grey the pane. |
| AC-A06 | Open/close Social Tap and switching Filter / Traders stays snappy. |

## Prove AC-A02 on live
1. Deploy past panel_v 64. Confirm `GET /api/health` → `panel_v` 65 and `sitting_drop_min` 5.
2. From `GET /api/flagged`, note a mint and its `sitting_minutes`.
3. Wait for the next bot scan after that sit would exceed 5 minutes (the loop is `INTERVAL_MIN`, default 5). One scan is enough when the coin has already sat past 5.
4. `GET /api/flagged` no longer contains that mint. `GET /api/latest` → `sitting_dropped` includes it, and `thresholds.sitting_drop_min` is 5.
5. The following scan still omits that mint while `first_flagged_at` is inside the 24h history TTL.

Unit coverage: `python3 -m unittest test_sitting_window.py test_viral.py`.
