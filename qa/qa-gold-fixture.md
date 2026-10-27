# QA Gold fixture — AC-G02 / G03 / G06 when live gold is empty

Not a PASS claim. G01 and G05 are on the normal Filter page. G04 is the live feed: high Token Flow scores stay out of Gold. This fixture does not promote any live row.

## Click path

1. After deploy, `GET /api/health` shows `panel_v` **77**, feature `gold_scar`, and `qa_gold`. `game_ai_review`, `qa_max_fixture`, `hide_max_unknown`, `sitting_keep`, `trade_chart`, `manual_update`, and `token_flow_new_sound` stay on the same payload.
2. Hard-refresh bare `/?v=77` (the panel adds `v=77`). Gold picks sits above Token Flow. Copy: **No gold picks yet — rare on purpose**. Token Flow still lists normal flags. Empty gold is the normal state.
3. Chrome / why / links: `/?qa_gold=1`. Banner: **QA Gold fixture — synthetic rare card QAGOLD**. One gold card, badge **QA GOLD**, short why, Copy CA, Chart, Pump, X. Paper line on the card. First load seeds the mint, so it does **not** chime. QAGOLD is not mixed into Token Flow.
4. Sound: `/?qa_gold=arrive`. Same card. One **♪ GOLD** on that load (tap Sound on first if the browser blocks audio). The cue is not the Token Flow ♪ NEW TOKEN chime and not Game win/loss. A second poll of the same card does not chime again.
5. Mute, then open `/?qa_gold=arrive` again. No gold cue. The muted note can show. Unmute does not replay that same mint.
6. Hard-refresh without `qa_gold`. The synthetic card is gone.

## API cross-check

- `GET /api/flagged` — every row has `gold_scar.gold: false`. No `QAGOLD`.
- `GET /api/gold` — `count: 0`. Empty is expected.
- `GET /api/flagged?qa_gold=1` — first row `qa_gold_fixture: true`, `gold_scar.gold: true`, seven gates, why + links. Later rows are not gold.
- `GET /api/qa/gold-fixture` — disabled, no row.
- `GET /api/qa/gold-fixture?qa_gold=1` — the same synthetic row.
- `GET /api/flagged?qa_max=1` — QAMAX can show in Token Flow and does **not** enter Gold. A high score is not a gold pick.

Do not combine `?qa_gold=1` with `?fixture=`. QAGOLD is not a live mint.

## Why live gold is empty

`gold_scar.py` recomputes promotion on each read. It does not trust a `gold` stamp in `flagged.json`. Stricter than the owner stack in `qa/owner-2026-10-27-gold-scar-gate-process.md`:

1. Filter score/12 at least **11** (owner skip line is ~10), plus pool, volume, buy pressure, LP lock, mint/freeze checked, and a top-holder percent.
2. Copy pack of **3** (owner ~5) fails the whole name family.
3. Elon / Trump / politics stems fail, including `TRUMP2028`.
4. 1h move of **+180%** fails (owner line is +300). A 5m print is required. A large 24h run fails.
5. On-chain dig needs RugCheck ≤ 8, zero risks, top holder ≤ 12%, top-10 ≤ 40%.
6. Overheard needs two different people in a caller-supplied sample. A profile URL is not enough. The live server passes no sample.
7. Macro veto holds unless `gold_macro.json` explicitly says Fed-quiet and Solana-ok. That file is not shipped.

False gold is worse than an empty lane.
