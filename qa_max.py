"""QA-only Token Flow Max row.

Opt-in via ``?qa_max=1``. This module does not write ``flagged.json`` and does
not rewrite any live row. The synthetic row carries a bonding target clearly
above NOW. Its FDV is equal to NOW on purpose, so the Slice 9 gate cannot
treat FDV≈NOW as Max / 100% gone.
"""

from __future__ import annotations

from typing import Any

from mcap_progress import apply_mcap

QA_MAX_MINT = "QAMaxFixtureBonding11111111111111111111"
QA_MAX_TICKER = "QAMAX"
QA_MAX_NOW_USD = 120_000.0
QA_MAX_BONDING_USD = 690_000.0
QA_MAX_LABEL = "QA only · synthetic bonding ceiling · not a live mint"


def is_fixture_mint(mint: str | None) -> bool:
    return (mint or "").strip() == QA_MAX_MINT


def source_row() -> dict[str, Any]:
    """Inputs only. Ceiling math stays in ``mcap_progress`` (Slice 9)."""
    now = QA_MAX_NOW_USD
    bonding = QA_MAX_BONDING_USD
    return {
        "ticker": QA_MAX_TICKER,
        "name": "QA Max fixture · bonding ceiling",
        "mint": QA_MAX_MINT,
        "qa_max_fixture": True,
        "qa_label": QA_MAX_LABEL,
        "score": 100.0,
        "score_12": 12.0,
        "trench_band": True,
        "flagged": True,
        "late_chase": False,
        "hard_fails": [],
        "red_flags": [],
        "metrics": {
            "liquidity_usd": 42_000.0,
            "volume_h1_usd": 88_000.0,
            "volume_h24_usd": 88_000.0,
            "market_cap_usd": now,
            "fdv_usd": now,
            "bonding_mcap_usd": bonding,
            "price_usd": "0.00012",
            "price_change_h1": 4.2,
            "buys_h1": 120,
            "sells_h1": 80,
            "buy_sell_ratio_h1": 1.5,
            "pair_age_hours": 0.2,
            "market_cap_from_fdv": False,
        },
        "liquidity_usd": 42_000.0,
        "volume_h1_usd": 88_000.0,
        "pair_age_hours": 0.2,
        "market_cap_usd": now,
        "fdv_usd": now,
        "bonding_mcap_usd": bonding,
        "market_cap_from_fdv": False,
        "why": [
            "QA fixture only. Bonding ceiling is synthetic and labeled.",
            "FDV equals NOW on this row and is not used as Max.",
        ],
    }


def prepared_row() -> dict[str, Any]:
    """Same shape as a flagged feed row, including an honest bonding ceiling."""
    from research import research_card

    row = apply_mcap(source_row())
    row["research"] = research_card(row)
    row["qa_max_fixture"] = True
    row["qa_label"] = QA_MAX_LABEL
    return row
