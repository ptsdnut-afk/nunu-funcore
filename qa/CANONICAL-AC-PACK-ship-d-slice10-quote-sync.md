# CANONICAL AC PACK — SHIP D Slice 10 Quote sync (NOW / LIVE vs labeled DEX)

Production: https://nunu-funcore.onrender.com
Cut: qa/cut-2026-10-26-ship-d-slice10-quote-sync.md
Repro: /workspace/repro-2026-10-26-bruzil-game-vs-dex/
Baseline: panel_v 61 Slice 9 ceiling PASS
Band: labeled DEX NOW mcap / LIVE USD within **±5%** of DexScreener at check time, else **Unknown / —** (never stale invent)

| ID | BLOCK expected |
|----|----------------|
| AC-Q01 | Paper/Game (or Filter) surface that labels **DEX Screener** shows NOW mcap within **±5%** of DexScreener market cap for that mint at check time, **or** Unknown / — if outside band / fetch fail |
| AC-Q02 | Same surface LIVE USD (or price) within **±5%** of DexScreener price, **or** Unknown / — |
| AC-Q03 | If quote refresh fails or is outside band: do **not** keep printing the old number under a live “DEX Screener” label — drop to Unknown / — or relabel honestly |
| AC-Q04 | BRUZIL (or equiv open position / Game quote) clears Q01–Q03; Slice 9 ceiling honesty still holds (no 100% invent) |
| AC-Q05 | Snappy; Social Tap / Traders / ceiling path no regression; feed Start Command / SHIP E out of scope |

STATUS must be exactly PASS | FAIL | BLOCKED. No soft-pass.
