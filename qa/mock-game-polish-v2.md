# Mock: Game polish craft+chrome+SFX v2 (SHIP G)

**Date:** 2026-10-26  
**Eyes only** — not a PR · EXAMPLE $ · profile **Pilot**  
**Locked cut:** `qa/cut-2026-10-26-ship-g-game-animation.md`  
**Base:** `qa/mock-game-rocket-anim-v1.{html,md,png}`  
**Gap scan:** `qa/eyes-2026-10-26-game-polish-gaps-v66/`

## Ask covered
Owner widened SHIP G: polish Game + interactive rocket-in-chart + **addictive casino-like sounds**. This v2 storyboard covers craft redesign, chart-space stage, functional ACTIVE chrome, and visual stand-ins for punchy win/loss SFX (AC-S01…S03).

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · BEFORE** | Face blob + **Session ended** | CLOCK 00:00:00 · last tick $0 · duplicate Start · NO SOUND vs Sound on · dense QA footer · no chart-space |
| **2 · AFTER IDLE** | Finished ship · **ACTIVE** mid-run | Glass canopy (no face) · twin thrusters · Nebula · **in** price/space chart · open EXAMPLE · CLOCK live · Sound on \| Mute · Buy/Sell/tape |
| **3 · AFTER TICK+** | Open mark **+$0.14** | Lift + thruster + +$ chip · equity flash · **♪♪ WIN HIT** flash (casino, not beep) · ACTIVE clock · chart spike |
| **4 · AFTER DOWN** | Session red · still **ACTIVE** | Nose-down + smoke · chip **▼ DOWN −12.4%** (never +%) · **♪ THUD LOSS HIT** (distinct, not tick+ reuse) · CLOCK still live |

## What changed vs v1
- Craft: tapered hull, twin bells, plate seams, glass canopy (**no face**).
- Rocket lives **in** interactive chart-space stage (green spike / red slide).
- Functional chrome: one Start CTA when ended; AFTER panels show **ACTIVE** + live CLOCK (never Session ended while equity/clock live).
- Shop: mini craft preview per skin.
- Dense QA footer only on BEFORE.
- SFX eyes: punchy WIN HIT / LOSS HIT flash labels + AC-S strip; topbar **Sound on | Mute** toggle (no contradictory NO SOUND badge on AFTER).

## ACs mapped (locked cut)
| ID | Expected | Mock proof |
|----|----------|------------|
| **AC-R01** | Craft silhouette, not oval/face blob | Panels 2–4 |
| **AC-R02** | IDLE calm drift in chart stage | Panel 2 |
| **AC-R03** | TICK+ thruster + lift | Panel 3 |
| **AC-R04** | DOWN nose-down + smoke; no ♪ tick+ reuse | Panel 4 |
| **AC-R05** | Nebula/skin tints hull + fins; shop preview | Panels 2–4 + shop |
| **AC-R06** | Points / shop / Buy / Sell still present | Chrome + shop |
| **AC-F01** | Session ended → one obvious Start/Restart 24h | Panel 1 + CTA strip |
| **AC-F02** | Active run: Last tick / equity update on marks | Panels 2–4 |
| **AC-F03** | PnL chip sign matches (no DOWN +%) | Panel 4 |
| **AC-F04** | Event tape readable; hide dense QA footer on AFTER | Panels 2–4 |
| **AC-S01** | Tick+/win hit short, punchy, casino-energy | Panel 3 WIN HIT + SFX strip |
| **AC-S02** | Loss/DOWN distinct hit (not silent, not tick+ reuse) | Panel 4 LOSS HIT + SFX strip |
| **AC-S03** | Mute kills all new SFX; unmute restores | Topbar Sound on \| Mute |

## Out of scope
- Production merges / PR
- Filter / lag / age-window
- Real money / wallets
- Music beds / long loops / voiceovers
- Accurate live numbers (EXAMPLE only)

## Files
- `qa/mock-game-polish-v2.html`
- `qa/mock-game-polish-v2.png`
- `qa/mock-game-polish-v2.md` (this file)

## Eng / PM pointer
Prefer this geometry + chart-space + SFX pack notes over live face-window craft. Fold into `static/index.html` rocket-live + game loop + audio. QA gates R01…R06 + F01…F04 + S01…S03 past panel_v 66 — Proto does not claim PASS · eyes only · do not merge.
