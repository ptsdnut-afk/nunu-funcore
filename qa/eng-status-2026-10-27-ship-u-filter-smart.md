# Eng STATUS — SHIP U Filter smart / efficient

**Not a QA PASS.** Constants below are what `filter.py` does now. Examples are the unit-tested maxed book, not a live rescan.

Paper research only. Not a buy. Not a promise.

## Before → after

| Rule | Before (panel_v 78) | After (panel_v 79) |
|---|---|---|
| Flag bar | score >= 55, hard-fail liq <$15k / vol h1 <$5k / age >72h | Same floors. New hard-fail: h24 >= +500% and age >= 3h (`already_ran_h24>=500`) |
| Late chase | age >= 2h AND h1 >= +100%, red flag only, score unchanged | age >= 1h AND h1 >= +180%, **−12** after the 0–100 cap, blocks trench |
| Already-ran | none | h24 >= +250% AND age >= 2h → **−18**. h24 >= +500% AND age >= 3h → hard-fail (the −18 still shows on the score) |
| Crowded narrative | not on Token Flow | shared `gold_scar._crowded_hit`, **−12**, blocks trench, still flaggable if score stays >= 55 |
| Clone pack | not on Token Flow (gold fails the pack at 3) | same `family_key`, count >= 3 → **−8** each. Not a hard ban |
| Socials | X-only = 9.0 (0.45×20). A profile URL was enough | one link kind and no description: cap **6.0**. Second link or a description keeps full weight |
| Trench | `score_12 >= 10` | `score_12 >= 10` AND age known AND age < 6h AND not late-chase AND not crowded |
| Penalties | flow adj only (±) | crowded / late / already-ran apply **after** the 0–100 cap, so a maxed book loses the full points |
| RugCheck | summary if liq >= $7.5k AND vol h1 >= $2.5k (half floors) | half floors still the lower bound. Also skip hard-fails (liq <$15k, vol h1 <$5k, age >72h) and already-ran. No full `/report` on the scan |
| Mint universe | boosts + profiles | unchanged |
| Gold annotate | every `/api/flagged` and `/api/gold` read | cached on flagged mtime_ns + size + `qa_max` + `qa_gold`. Manual Update busts it by rewriting the file |
| Gold narrative | stem equals token or token startswith stem | also a stem of length >= 4 with >= 3 characters in front (`elon` in `gtaelon`). `melon`, `advance`/`vance`, `xxelon` do not match. No gold bar loosened |
| Sitting | keep past 5.0m | unchanged. No hard sitting-drop |
| Cadence | `start.sh` interval 5m, `SKIP_FRESH_SEC=0` | unchanged. Wall-time target stays under 15s for ~50–80 mints. Stalls show up as `rug_fail` / `elapsed_s` on the runs.log line, which now also has `rug_skipped` and `rug_skipped_past_half_floor` |

`score_before_demotion` is the 0–100 book after socials (including the profile cap) and before crowded / late / already-ran. Clone −8 is a second pass on the cohort.

Top-holder % demotion was not added. RugCheck summary has no per-wallet percents, and fetching `/report` on the scan would spend the slow call this ship is cutting.

## Worked demotions (maxed book)

Book = liquidity 30 + volume 25 + holders 25 + socials 20 + buy-heavy flow +3 = 103, capped to **100.0 / 12.0**, trench, flagged. Tests in `test_filter_smart.py`.

| Example | Inputs | Before | After |
|---|---|---|---|
| ELON2028 | maxed book, age 1.2h, h1 +20%, h24 +40% | 100.0 / 12.0, trench | **88.0 / 10.6**, crowded −12, still flagged, **trench false**. A live-style 98.3 (11.8/12) would be **86.3 / 10.4**, trench false |
| GTAELON | same book, ticker `GTAELON` | TF: same 100 / trench. Gold narrative could pass (`gtaelon` does not start with `elon`) | TF: **88.0 / 10.6**, trench false. Gold narrative **fails** on stem `elon` |
| CHICO-class | maxed book, age 4h, h1 +40%, h24 +400% | 100.0 / 12.0, trench. Old late-chase did not fire (h1 +40% < +100%) | **82.0 / 9.8**, already-ran −18, still flagged, trench false. A live-style 95.0 (11.4/12) would be **77.0 / 9.2** |
| Aged extreme | age 4h, h24 +800%, h1 +40% | would flag at 100 | hard-fail `already_ran_h24>=500`, score 82.0, **not flagged** |
| Late chase | age 1.5h, h1 +200%, h24 +40% | not late (age < 2h), 100 / trench | **88.0 / 10.6**, late −12, trench false, still flagged |
| Retired cosmetic mark | age 2.5h, h1 +120%, h24 +40% | late_chase flag, score still 100, trench | **not** late_chase (h1 < +180), score 100, trench. Large 24h prints are the already-ran rule |
| Stack | age 3.5h, h1 +200%, h24 +300% | cosmetic late only if it met the old mark; score unchanged | late −12 and already-ran −18 → **70.0 / 8.4**, flagged, trench false |
| Clone ×3 | DOGE / DOGE2 / DOGECOIN, each maxed | each 100, no pack penalty | each **92.0 / 11.0**, clone −8, trench kept |
| Crowded pack ×3 | TRUMP / TRUMP2028 / TRUMPCOIN | each 100 | crowded −12 then clone −8 → **80.0 / 9.6**, trench false, still flagged |
| X-only socials | twitter URL, no description, no second link, same liq/vol/holders/flow | socials 9 → book **92.0 / 11.0** | socials **6** → book **89.0 / 10.7** |
| Fresh joke | FROG, age 0.4h, h1 +12%, h24 +18%, full socials + description | 100 / trench | **100.0 / 12.0**, flagged, trench. Early winners are not taxed |

Panel trench sections follow `trench_band`. An explicit `false` no longer gets pulled back in just because score/12 is still >= 10. Rows with no `trench_band` field keep the old score/12 >= 10 fallback.

## Efficiency

- Annotate cache: second `/api/flagged` with the same flagged mtime does not call `gold_scar.annotate`. Touching the file does. `?qa_gold=1` is a separate key.
- RugCheck: a $10k liq / $8k vol coin clears the old half-floor and is now skipped. Dust below half-floor was already skipped. Age 4h and h24 +400% on a liquid pair is skipped.
- `runs.log` line adds `rug_skipped`, `rug_skipped_past_half_floor`, `crowded`, `already_ran`, `already_ran_hard`, `clone_pack` next to the existing `rug_ok` / `rug_fail` / `elapsed_s`.

## Left alone

Game sell (`POST /api/game/sell`), finish (`POST /api/game/end`), follow tab, AI review, sitting-keep, gold score/tape/on-chain/overheard/macro bars. No FOMO, TikTok, or copy-wallet buy path. Empty gold is still the expected live result.
