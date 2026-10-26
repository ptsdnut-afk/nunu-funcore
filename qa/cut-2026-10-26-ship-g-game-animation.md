# LOCKED cut — SHIP G Game polish (craft + chart + chrome + SFX)

**Status:** ENG — Proto eyes SoT is `qa/mock-game-polish-v2.{html,png,md}` (geometry over `qa/mock-game-rocket-anim-v1.*`). This slice is panel_v 67.
**Date:** 2026-10-26
**Why:** Owner: the live ship still read as a face-in-window blob, IDLE/TICK+/DOWN were hard to see with no position, Start was duplicated, the sound badge disagreed with the toggle, and a new run could leave “Session ended” on the status bar. Empty Quote must not open a 24h run.

## In
1. **Craft** — tapered hull, twin bells, plate seams, glass canopy. No face. Nebula (and other skins) tint hull + fins. Shop cards show a mini craft preview.
2. **Chart-space stage** — the ship rides a price trace. IDLE is a calm drift with a dim twin flame (visible with no open position). TICK+ noses up on a green spike with a longer flame and a +$ chip. DOWN noses down with grey smoke and a red slide. No green flame on DOWN.
3. **Honest chrome** — one Start 24h ($5) when idle or ended (Restart is that same control only while a run is ACTIVE). ACTIVE clock/equity never keep a “Session ended” bar. Last tick stays $0.00 until a real mark, then keeps the signed tick. PnL chip sign matches the direction (never ▼ DOWN +%).
4. **Quote guard** — Quote with no coin does not call start. It asks for a CA, ticker, or name.
5. **Casino hits** — ♪♪ WIN on a positive tick (short stacked chime). ♪ LOSS on a down tick (distinct thud, not the win hit). Topbar **Sound on | Mute**. Mute silences the hits; unmute restores them. No “NO SOUND” badge while Sound on.
6. **panel_v 67** — `GET /api/health` feature `game_polish`. Filter `delay_warning` and the 5-minute feed loop stay.

## Out
- Filter lag banner, age window, Social Tap
- Changing `INTERVAL_MIN` / `interval_min`
- Rewriting points, shop spend, or the paper book
- Real money / wallets
- Music beds, long loops, voiceovers
- Proto v2 numbers (EXAMPLE only on the mock)

## Done when
- QA BLOCK on live for AC-R01…R06, AC-F01…F04, AC-S01…S03
- Craft and chart match `qa/mock-game-polish-v2`
- `GET /api/health` is `panel_v` 67 with `game_polish` and `delay_warning`
- Filter delay banner unchanged

## BLOCK acceptance criteria
| ID | Expected |
|----|----------|
| AC-R01 | Craft silhouette (nose · body · fins · cockpit glass), not a face/oval blob |
| AC-R02 | IDLE calm readable (soft hover, dim twin thruster), including a chart cruise with no open position |
| AC-R03 | TICK+ thruster + nose lift + +$ chip on a positive mark |
| AC-R04 | DOWN nose-down + smoke; no green flame; never the win hit |
| AC-R05 | Equipped skin tints hull + fins; shop shows a mini craft preview |
| AC-R06 | Points, shop equip, Buy, and Sell still present |
| AC-F01 | Session ended → one Start 24h CTA. No second Start panel |
| AC-F02 | Active run: Last tick and equity update when a mark moves. $0.00 only when nothing has ticked |
| AC-F03 | PnL chip sign matches direction. Never ▼ DOWN +% |
| AC-F04 | Event tape stays readable. Dense QA footer is hidden on Game |
| AC-S01 | Tick+ plays a short punchy ♪♪ WIN hit, not a sparse beep |
| AC-S02 | DOWN plays a distinct ♪ LOSS thud. Not silent. Not the win hit |
| AC-S03 | Mute kills win and loss hits. Unmute restores them. Badge agrees with Sound on \| Mute |

## Refs
- Eyes: `qa/mock-game-polish-v2.{html,png,md}`
- Prior geometry: `qa/mock-game-rocket-anim-v1.*`
- Canonical pack: `qa/CANONICAL-AC-PACK-ship-g-game-animation.md`
- Live: https://nunu-funcore.onrender.com
- Repo: ptsdnut-afk/nunu-funcore
