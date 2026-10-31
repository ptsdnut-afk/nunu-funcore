"""Session-scoped checklist fixtures. Off unless ``?qa_x=`` is set.

``buy`` maps to BUY-WATCH. ``skip`` prepends two coins: one rug-history dev
and one bundled coin. Both are forced SKIP for that reason. Live rows are
not rewritten.
"""

from __future__ import annotations

from typing import Any

import coin_checklist as cc

QA_BUY_MINT = "QAXBuyWatch11111111111111111111111111111"
QA_WATCH_MINT = "QAXWatch1111111111111111111111111111111"
QA_RUG_MINT = "QAXRugDev11111111111111111111111111111111"
QA_BUNDLE_MINT = "QAXBundled111111111111111111111111111111"

_MODES = {
    "buy": "buy-watch",
    "buy-watch": "buy-watch",
    "buywatch": "buy-watch",
    "watch": "watch",
    "skip": "skip",
}


def mode_from_query(raw: str | None) -> str | None:
    return _MODES.get((raw or "").strip().lower().replace("_", "-"))


def is_fixture_mint(mint: str | None) -> bool:
    return (mint or "").strip() in {QA_BUY_MINT, QA_WATCH_MINT, QA_RUG_MINT, QA_BUNDLE_MINT}


def _base(**overrides: Any) -> dict[str, Any]:
    mint = overrides.pop("mint", QA_BUY_MINT)
    sources: dict[str, Any] = {
        "pump": {
            "mint": mint,
            "creator": "QACreator1111111111111111111111111111111",
            "description": "A hedge narrative for the fixture.",
            "created_timestamp": 1_700_000_000_000,
            "complete": True,
            "twitter": "https://x.com/example",
            "website": "https://example.com",
        },
        "dex": {
            "priceUsd": "0.0001",
            "marketCap": 100_000,
            "priceChange": {"h1": 4},
            "txns": {"h1": {"buys": 80, "sells": 30}},
            "info": {
                "websites": [{"url": "https://example.com"}],
                "socials": [{"type": "twitter", "url": "https://x.com/example"}],
            },
        },
        "rug": {"lpLockedPct": 100, "totalHolders": 400},
        "creator_coins": {"count": 1, "coins": [{"mint": mint}]},
        "holders": [
            {"owner": "QAHolderA111111111111111111111111111111", "pct": 4, "excluded": False},
            {"owner": "QAHolderB111111111111111111111111111111", "pct": 3, "excluded": False},
        ],
        "holder_source": "Solana RPC",
        "authorities": {
            "known": True,
            "mint_authority": None,
            "freeze_authority": None,
            "source": "Solana RPC",
        },
        "traces": [
            {"owner": "QAHolderA111111111111111111111111111111", "pct": 4, "funder": "solo-a", "source": "public RPC"},
            {"owner": "QAHolderB111111111111111111111111111111", "pct": 3, "funder": "solo-b", "source": "public RPC"},
        ],
        "organic": {"known": True, "unique_traders": 140, "max_wallet_pct": 4.0, "source": "GeckoTerminal"},
        "social_checks": {
            "status": "pass",
            "detail": "X + website loaded · X is older than the coin",
            "source": "X + Dexscreener",
        },
        "live_narratives": ["hedge"],
    }
    sources.update(overrides)
    if "mint" in sources.get("pump", {}):
        sources["pump"]["mint"] = mint
    return sources


def checklist_for(mint: str) -> dict[str, Any] | None:
    mint = (mint or "").strip()
    if mint == QA_BUY_MINT:
        payload = cc.assemble(mint, _base(mint=mint), now=1_700_000_500)
    elif mint == QA_WATCH_MINT:
        payload = cc.assemble(mint, _base(mint=mint, live_narratives=["other"]), now=1_700_000_500)
    elif mint == QA_RUG_MINT:
        payload = cc.assemble(
            mint,
            _base(
                mint=mint,
                creator_coins={
                    "count": 2,
                    "coins": [
                        {"mint": mint},
                        {
                            "mint": "QAPrior111111111111111111111111111111111",
                            "created_timestamp": 1_600_000_000_000,
                            "last_trade_timestamp": 1_600_000_100_000,
                            "complete": False,
                        },
                    ],
                },
                prior_rugs=1,
                prior_rugs_checked=1,
            ),
            now=1_700_000_500,
        )
    elif mint == QA_BUNDLE_MINT:
        payload = cc.assemble(
            mint,
            _base(
                mint=mint,
                holders=[
                    {"owner": "QAHolderA111111111111111111111111111111", "pct": 6, "excluded": False},
                    {"owner": "QAHolderB111111111111111111111111111111", "pct": 4, "excluded": False},
                ],
                traces=[
                    {"owner": "QAHolderA111111111111111111111111111111", "pct": 6, "funder": "same-funder", "source": "public RPC"},
                    {"owner": "QAHolderB111111111111111111111111111111", "pct": 4, "funder": "same-funder", "source": "public RPC"},
                ],
            ),
            now=1_700_000_500,
        )
    else:
        return None
    payload["fixture"] = True
    payload["qa_x_fixture"] = True
    return payload


def _list_row(mint: str, ticker: str, name: str, checklist: dict[str, Any]) -> dict[str, Any]:
    reason = ", ".join(checklist.get("forced_reasons") or [])
    label = reason or str(checklist.get("verdict") or "")
    return {
        "ticker": ticker,
        "name": name,
        "mint": mint,
        "qa_x_fixture": True,
        "qa_label": "QA only · " + label,
        "checklist_verdict": checklist.get("verdict"),
        "forced_skip": checklist.get("forced_skip"),
        "forced_reasons": checklist.get("forced_reasons") or [],
        "score": 60.0,
        "score_12": 7.0,
        "flagged": True,
        "trench_band": False,
        "late_chase": False,
        "hard_fails": [],
        "red_flags": [],
        "metrics": {
            "price_usd": "0.0001",
            "market_cap_usd": 100_000,
            "liquidity_usd": 20_000,
            "volume_h1_usd": 8_000,
            "price_change_h1": 4.0,
            "pair_age_hours": 2.0,
            "buys_h1": 40,
            "sells_h1": 20,
        },
        "why": ["QA checklist fixture. Not a live mint."],
        "breakdown": {"liquidity": 10, "volume": 10, "holders": 10, "socials": 10, "flow_adj": 0},
    }


def rows_for(mode: str | None) -> list[dict[str, Any]]:
    if mode == "buy-watch":
        specs = [(QA_BUY_MINT, "QAXBUY", "QA buy maps to BUY-WATCH")]
    elif mode == "watch":
        specs = [(QA_WATCH_MINT, "QAXWATCH", "QA WATCH")]
    elif mode == "skip":
        specs = [
            (QA_RUG_MINT, "QAXRUG", "QA rug-history dev"),
            (QA_BUNDLE_MINT, "QAXBUNDLE", "QA bundled coin"),
        ]
    else:
        return []
    out = []
    for mint, ticker, name in specs:
        checklist = checklist_for(mint) or {}
        out.append(_list_row(mint, ticker, name, checklist))
    return out
