# CANONICAL AC PACK — SHIP H Filter delay warning

Production target: the live Filter surface on this panel.
Cut: qa/cut-2026-10-26-ship-h-delay-warning.md
Eyes: qa/mock-filter-lag-warning-v1.{html,png,md}
Baseline: panel_v 65 SHIP F age-window. This slice is panel_v 66.
Ops: `interval_min` / `INTERVAL_MIN` stays **5**. Do not speed the feed loop.

## Constants
| Name | Value | Where |
|------|-------|--------|
| `FEED_LOOP_MIN` | **5** | `static/index.html`. Worst-case lag copy is `≤ ~5m` / `up to ~5m`. Not an `INTERVAL_MIN` change. |
| `NEAR_LIVE_SEC` | **60** | Under 1 minute the banner is green **Near live · just scanned**. |
| `LOUD_LAG_SEC` | **240** | ≥4 minutes (≥~80% of the 5-minute loop) the banner is the loud amber **Behind live · not Dex 1s**. |
| Between | 1m–<4m | Same “Behind live” copy, quieter amber (no loud glow). Still not a tiny age chip. |

## BLOCK
| ID | Expected |
|----|----------|
| AC-L01 | Filter shows a clear delay / not-live-Dex warning (not only a tiny age chip). Banner title is **Behind live · not Dex 1s** when the scan is not fresh, directly under the header on Filter. |
| AC-L02 | Lag estimate is visible: current **data age** (for example `4m 3s` or `0.4m`) and a loop bound **≤ ~5m** (`up to ~5m lag (5-min scan)` when behind; `next scan ≤ 5m` when fresh). |
| AC-L03 | Near-stale (≥~4m or ≥~80% of the loop) reads louder (amber glow) than fresh (<1m), which is calm green **Near live · just scanned**. |
| AC-L04 | Game chrome and Social Tap are not broken. Copy does not claim Dex 1s or a live Dex price. `GET /api/health` is `panel_v` **66** and `features` includes `delay_warning`. Feed loop stays 5 minutes. No SHIP G rocket animation in this slice. |

An age older than one loop still says **Behind live · not Dex 1s** and shows that data age. It does not pretend the print is only ≤5m old.

STATUS must be exactly PASS | FAIL | BLOCKED. No soft-pass.

Unit coverage: `python3 -m unittest test_viral.py test_sitting_window.py test_game_finish.py`.
