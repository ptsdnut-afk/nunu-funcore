# SHIP S sitting rule — keep listed, do not hard-drop

Supersedes the SHIP F **removal** at `sitting_minutes` > 5. SHIP F’s aim (the board must not fill with unlabeled stale names) stays. The 5-minute hard-drop does not: it matched the bot loop, so the next pass set `flagged_count` to 0 and `sitting_dropped_count` to every coin that had just appeared (live 2026-10-27, panel_v 70, TRUMP2028 / CHICO score 98 at 5.1m).

Code: `filter.py` `apply_sitting_rule`. Health: `panel_v` 72, feature `sitting_keep` (SHIP L `manual_update` stays on the same health payload).

## What stays on Token Flow

A coin that cleared the flag line (score, liquidity, 1h volume, pair age) stays on Token Flow for as long as it still clears that line.

| Rounded `sitting_minutes` | `sitting_band` | Board | Chrome |
| --- | --- | --- | --- |
| none | `unknown` | stays | Sitting time blank |
| < 2.0 | `fresh` | stays | green time, “inside window” |
| 2.0 up to just under 4.0 | `inside` | stays | “inside window” |
| ≥ 4.0 and ≤ 5.0 | `near` | stays | amber, “near ~5m · stays” |
| > 5.0 and ≤ 180.0 | `over` | stays, `sitting_kept` | **Sitting** badge, red time, “still listed” |
| > 180.0 | `over` | stays, `sitting_aged_hold` | **Sitting** badge, “aged · still listed” |

`SITTING_DROP_MIN` is **5.0**. `SITTING_AGED_MIN` is **180.0**. Both compare the rounded one-decimal `sitting_minutes` already stored on the row. 5.0 stays `near`. 5.1 is Sitting and still listed. 180.0 is Sitting. 180.1 is aged and still listed.

`flagged` is not cleared for sitting time. `sitting_dropped_count` is **0**. `sitting_aged_out_count` is **0**. There is no second list of coins removed for sitting.

## What still leaves (SHIP F spirit — not silent, not forever)

Sitting time does not delete a row. A coin leaves Token Flow only when it **misses the normal flag gates** on a later scan:

- score under `MIN_SCORE_TO_FLAG` (55)
- liquidity under `MIN_LIQUIDITY_USD`
- 1h volume under `MIN_VOLUME_H1_USD`
- pair age over `MAX_PAIR_AGE_HOURS` (72)

That is the anti-spam bound. Names do not sit forever once they stop qualifying, and they do not sit past 5 minutes **unlabeled**. Past 5 minutes the row wears Sitting chrome. Past 180 minutes the hint escalates to “aged · still listed” so a long sit is not the same cue as a fresh one. Flag history TTL (`FLAG_HISTORY_TTL_H`, 24h) still drops mints that have already left the board.

## Empty board

Token Flow uses “No tokens flagged this run. Re-run python3 bot.py --once.” only when the last scan has no evaluated coins with `top_scores`.

When `/api/flagged` is empty but `/api/latest` still has real `sitting_kept`, `sitting_dropped` (pre-SHIP S payloads), or `top_scores` with `flagged: true`, the panel shows those real rows with Sitting chrome. It does not invent a row for a top score that never cleared the flag line. That scan says how many coins were evaluated and names the top score: “None cleared the flag line. Nothing invented.”

## Live proof this rule replaces

`qa/repro-2026-10-27-coins-appear-disappear/` — panel_v 70, `evaluated` 45, `flagged_count` 0, `sitting_dropped_count` 10, scores up to 98 at 5.1m. Under this rule those ten coins stay flagged with `sitting_band: over`.
