"""QA-only Gold Scar card.

Opt-in via ``?qa_gold=1`` (chrome, no forced cue) or ``?qa_gold=arrive``
(same card; the panel treats it as a new gold mint and plays ♪ GOLD once).

This module does not write ``flagged.json`` and does not mark any live row
gold. The card is synthetic and labeled. Live promotion stays in
``gold_scar.evaluate``, which fails closed.
"""

from __future__ import annotations

from typing import Any

from gold_scar import PAPER, fixture_verdict
from mcap_progress import apply_mcap

QA_GOLD_MINT = "QAGoldScarFixture111111111111111111111"
QA_GOLD_TICKER = "QAGOLD"
QA_GOLD_LABEL = "QA only · synthetic rare gold card · not a live mint"
FIXTURE_WHY_LINE = "Synthetic stand-in. Not a live clearance and not a buy."


def is_fixture_mint(mint: str | None) -> bool:
    return (mint or "").strip() == QA_GOLD_MINT


def prepared_row() -> dict[str, Any]:
    """Shape of a flagged row plus an explicit fixture verdict."""
    from research import research_card

    row: dict[str, Any] = {
        "ticker": QA_GOLD_TICKER,
        "name": "QA gold fixture · rare lane example",
        "mint": QA_GOLD_MINT,
        "ca_short": "QAGo…1111",
        "qa_gold_fixture": True,
        "qa_label": QA_GOLD_LABEL,
        "score": 100.0,
        "score_12": 12.0,
        "trench_band": True,
        "flagged": True,
        "late_chase": False,
        "hard_fails": [],
        "red_flags": [],
        "description": "Synthetic example for the gold lane. Paper research only. Not a live coin.",
        "metrics": {
            "liquidity_usd": 80_000.0,
            "volume_h1_usd": 40_000.0,
            "volume_h24_usd": 40_000.0,
            "market_cap_usd": 90_000.0,
            "fdv_usd": 90_000.0,
            "price_usd": "0.00009",
            "price_change_h1": 12.0,
            "price_change_m5": 4.0,
            "price_change_h24": 12.0,
            "buys_h1": 220,
            "sells_h1": 140,
            "buy_sell_ratio_h1": 1.57,
            "pair_age_hours": 0.6,
            "lp_locked_pct": 100.0,
        },
        "liquidity_usd": 80_000.0,
        "volume_h1_usd": 40_000.0,
        "pair_age_hours": 0.6,
        "market_cap_usd": 90_000.0,
        "fdv_usd": 90_000.0,
        "lp_locked_pct": 100.0,
        "holders": {
            "rugcheck_available": True,
            "score_normalised": 1.0,
            "lp_locked_pct": 100.0,
            "risk_count": 0,
            "risks": [],
            "top1_pct": 4.5,
            "top10_pct": 22.0,
        },
        "socials": {
            "twitter": True,
            "telegram": False,
            "website": False,
            "link_count": 1,
            "twitter_url": "https://x.com/search?q=QAGOLD&src=typed_query",
        },
        "links": {
            "pump": "https://pump.fun/coin/" + QA_GOLD_MINT,
            "dex": "https://dexscreener.com/solana/" + QA_GOLD_MINT,
            "gmgn": "https://gmgn.ai/sol/token/" + QA_GOLD_MINT,
            "x": "https://x.com/search?q=QAGOLD&src=typed_query",
        },
        "dex_url": "https://dexscreener.com/solana/" + QA_GOLD_MINT,
        "why": [FIXTURE_WHY_LINE],
        "image_url": None,
    }
    row = apply_mcap(row)
    row["research"] = research_card(row)
    row["qa_gold_fixture"] = True
    row["qa_label"] = QA_GOLD_LABEL
    row["gold_scar"] = fixture_verdict()
    row["gold_scar"]["paper"] = PAPER
    return row


def prepend(rows: list[Any] | None) -> list[Any]:
    kept: list[Any] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and (row.get("qa_gold_fixture") or row.get("mint") == QA_GOLD_MINT):
            continue
        kept.append(row)
    return [prepared_row()] + kept
