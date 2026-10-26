# Mock: Game rocket redesign (blob → craft) v1

**Date:** 2026-10-26  
**Eyes only** — not a PR · visual-only cut draft for Eng/PM.  
**Extends (does not overwrite):** `qa/mock-game-finish-tick-v1.{html,png,md}` state language (IDLE / TICK+ / DOWN).  
**Owner ask:** live v65 Game sprite “lwk is ass” — replace lo-fi purple oval blob.

## Ask covered
Live Game tab shows a purple oval + smiley + green bubbles. Owner wants a finished rocket character that still reacts on tick+/down/idle. This mock keeps SHIP E behavior language and swaps the hero silhouette for a real craft (nose · body · fins · cockpit glow · Nebula tint).

## Storyboard panels
| Panel | State | Surfaces |
|-------|--------|----------|
| **1 · BEFORE** | Current live blob | Purple oval + smiley + bubble trail · labeled CURRENT · ASS |
| **2 · AFTER IDLE** | Calm / flat | Craft hover, dim green flame, Nebula hull, **no sound** |
| **3 · AFTER TICK+** | EXAMPLE gain **+$0.12** | ♪ tick+, nose lift, long thruster, floating +$ chip, equity flash |
| **4 · AFTER DOWN** | EXAMPLE session red | Nose-down + grey smoke, **no ♪ tick+**, no green flame |

Thin shop strip under storyboard shows skins tinting the craft (Nebula equipped). All $ / % / pts figures are **EXAMPLE**. Banner: EYES ONLY · NOT A PR.

## What changed vs current blob
- Silhouette is a horizontal pilot craft (pointed nose, tapered hull, twin fins, rear engine ring, glowing cockpit) — not an oval fish.
- Motion stills: IDLE soft hover + dim flame; TICK+ pitched lift + long green flame + +$ chip; DOWN pitched down + smoke puffs.
- Nebula Frame purple/magenta gradient still paints hull + fins (AC-R05).

## Eng / PM pointer — draft ACs
| ID | Expected |
|----|----------|
| **AC-R01** | Rocket silhouette clearly a craft (nose · body · fins · cockpit), not oval blob |
| **AC-R02** | IDLE calm readable (soft hover, dim thruster, no scream) |
| **AC-R03** | TICK+ thruster pulse + lift readable (longer flame, nose up, optional +$ chip) |
| **AC-R04** | DOWN nose-down + smoke readable (no green flame, never ♪ tick+) |
| **AC-R05** | Skin tint (Nebula purple/magenta) still applies to hull + fins |
| **AC-R06** | No regression to tick+ / points / shop wiring — **visual-only this cut** |

## Out of scope
- Rewiring tick+ sound, points, or shop spend logic
- Filter age-window / Social Tap
- Real money / wallets
- Opening a PR or editing production app code
- Inventing accurate live numbers (accurate-only elsewhere)

## Files
- `qa/mock-game-rocket-anim-v1.html`
- `qa/mock-game-rocket-anim-v1.png`
- `qa/mock-game-rocket-anim-v1.md` (this file)

## Owner / prior refs
- Owner shot: `qa/owner-shot-2026-10-26-game-anim-ass-v65.png`
- Prior state storyboard: `qa/mock-game-finish-tick-v1.{html,png,md}`
- Live: https://nunu-funcore.onrender.com/?v=65
