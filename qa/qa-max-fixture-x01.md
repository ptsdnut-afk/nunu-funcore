# QA Max fixture — re-BLOCK AC-X01

Not a PASS claim. X02–X04 (hide “Max unknown”) already PASS on panel_v 74. X01 stays **BLOCKED** until QA sees a labeled Max / % gone on a ceiling that is honestly above NOW. This fixture does not invent ceilings on live unknown rows.

## Click path

1. After deploy, `GET /api/health` shows `panel_v` **76**, feature `qa_max_fixture`, and still `hide_max_unknown`, `trade_chart`, `sitting_keep`, `manual_update`. `game_ai_review` is on the same health payload (SHIP P).
2. Open Token Flow with the fixture on: `/?qa_max=1` (the panel adds `&v=76`). Banner: **QA Max fixture — synthetic bonding row QAMAX**.
3. The **QAMAX** row (badge **QA MAX**) shows labeled **Max** and **% gone** from a **Bonding** ceiling above NOW (NOW $120.0k → Max $690.0k · 17.4% gone). FDV on that row equals NOW and is not the Max.
4. Every other row stays Max-empty. No “Max unknown” chrome.
5. Hard-refresh **without** `qa_max` (`/?v=76`). QAMAX is gone. Unknown rows still have no Max.

## API cross-check

- `GET /api/flagged?qa_max=1` — first row `qa_max_fixture: true`, `mcap_progress.ceiling_kind: bonding`, `ceiling_label: Bonding`, `pct_gone` set, ceiling more than ~2% above NOW. Later rows match the default feed.
- `GET /api/flagged` — no QAMAX row.
- `GET /api/qa/max-fixture` — disabled, no row.
- `GET /api/qa/max-fixture?qa_max=1` — the same synthetic row.

Do not combine `?qa_max=1` with `?fixture=`. QAMAX is not a live mint.
