# LOCKED cut — SHIP D Slice 10 Quote sync (same-second NOW / LIVE vs labeled DEX)

**Status:** LOCKED — Eng unlocked (narrowed 2026-10-26 after Bug re-pass).
**Date:** 2026-10-26
**Why:** Owner accurate-data rule. BRUZIL owner shots ($591.1k vs DEX ~$566k) were **cross-tab tick skew**, not a stuck wrong quote — Bug same-minute re-pass matched (LIVE 0.000585 / NOW $566.8k ≈ DEX). Still ship: when we label DEX Screener, the number must be a fresh same-path quote or Unknown — never a stale print presented as live.

## In
1. Game/paper NOW + LIVE USD use the same live DEX quote path as the DEX link for that mint.
2. If fetch fails or quote is older than the refresh SLA, show **Unknown / —** (don’t keep an old number labeled live DEX).
3. QA: open Game drawer + DexScreener for same mint **same minute** → NOW mcap and LIVE USD within **±5%** of Dex, **or** Unknown if outside / fetch fail.
4. No Eng work treating the owner’s $591 vs $566 pair as a stuck bug — that was tab skew.

## Out
- Ceiling / % gone (Slice 9 CLOSED PASS)
- Feed list bot Start Command / INTERVAL_MIN (ops)
- Game rocket / points / tick+ (SHIP E — next)
- Age-window / viral Social Tap (behind freshness)

## Done when
- QA BLOCK PASS on live past v61 (Q01–Q05 same-second band or Unknown)
- Bug pack `/workspace/repro-2026-10-26-bruzil-game-vs-dex/` referenced as non-FAIL baseline

## Refs
- Bug pack findings 2026-10-26
- Owner BRUZIL shots (cross-tab skew)
