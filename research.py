"""Honest paper-research card for one coin.

The rug figure is a weighted heuristic from inputs that are actually present.
It is not a probability, not a certainty, and missing inputs are omitted
rather than treated as safe or unsafe. No posts, fills, or handles are created.
"""

from __future__ import annotations

from typing import Any

# Base weights. Present inputs are renormalized so missing ones do not dilute.
W_LP = 0.40
W_RUG_SCORE = 0.30
W_NAMED = 0.15
W_FLOW = 0.15

# RugCheck score_normalised bands (their scale — not a percent).
# Lower is safer on RugCheck. These are fixed map points, not the raw number.
RUG_BANDS = (
    (10.0, 8.0, "≤10 clean band → 8"),
    (50.0, 35.0, "≤50 mid band → 35"),
    (200.0, 62.0, "≤200 elevated band → 62"),
)

NAMED_LEVEL_POINTS = {
    "danger": 34.0,
    "warn": 18.0,
    "warning": 18.0,
    "info": 8.0,
    "low": 8.0,
}

# Vitality bands from 1h prints only.
VITAL_ACTIVE_TXN = 400
VITAL_ACTIVE_VOL = 80_000.0
VITAL_ALIVE_TXN = 40
VITAL_ALIVE_VOL = 8_000.0

# FOMO/pump section: observed market prints, not a FOMO feed.
FOMO_H1_ABS = 25.0
FOMO_H24_ABS = 50.0
FOMO_BUY_RATIO = 1.8
FOMO_MIN_BUYS = 20

# Early window for holder concentration. Not used when age is missing.
EARLY_MAX_AGE_H = 12.0
MAJOR_HOLDER_PCT = 5.0

RUG_LABEL = "Heuristic risk index, not a probability and not a certainty."
PAPER_NOTE = "Paper research only. No target, wallet, or order."
CONTENT_NOTE = (
    "Links are copied from the payload. No posts are written here. "
    "Pump, FOMO, and GMGN shortcuts are built from the mint and are not proof."
)


def _fnum(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("%", "")
        if not text or text in {"—", "-", "null", "None"}:
            return None
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if url.startswith("https://") or url.startswith("http://"):
        return url
    return None


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _first_num(containers: list[tuple[dict[str, Any], str]]) -> float | None:
    for container, key in containers:
        if isinstance(container, dict) and container.get(key) is not None:
            number = _fnum(container.get(key))
            if number is not None:
                return number
    return None


def _rug_band(score_n: float) -> tuple[float, str]:
    for limit, risk, label in RUG_BANDS:
        if score_n <= limit:
            return risk, label
    return 88.0, ">200 danger band → 88"


def _named_risk(risks: list[Any]) -> tuple[float, str]:
    if not risks:
        return 0.0, "RugCheck named risks: 0 → 0"
    total = 0.0
    bits: list[str] = []
    for risk in risks:
        if isinstance(risk, dict):
            level = str(risk.get("level") or "").strip().lower()
            name = str(risk.get("name") or "risk").strip() or "risk"
            points = NAMED_LEVEL_POINTS.get(level, 12.0)
            total += points
            bits.append(f"{name} ({level or 'unleveled'}) +{points:.0f}")
        else:
            total += 12.0
            bits.append(f"{risk} +12")
    capped = min(100.0, total)
    extra = "" if capped == total else f" (capped from {total:.0f})"
    shown = "; ".join(bits[:6])
    if len(bits) > 6:
        shown += f"; +{len(bits) - 6} more"
    return capped, f"RugCheck risks: {shown} → {capped:.0f}{extra}"


def _flow_risk(vol: float | None, liq: float | None, buy_sell: float | None) -> tuple[float, str] | None:
    vol_liq_risk: float | None = None
    sell_risk: float | None = None
    bits: list[str] = []
    if vol is not None and liq is not None and liq > 0:
        ratio = vol / liq
        if ratio > 30:
            vol_liq_risk = 80.0
        elif ratio > 10:
            vol_liq_risk = 45.0
        elif ratio > 3:
            vol_liq_risk = 25.0
        else:
            vol_liq_risk = 8.0
        bits.append(f"vol/liq 1h {ratio:.2f}x → {vol_liq_risk:.0f}")
    if buy_sell is not None and buy_sell <= 0.7:
        sell_risk = 60.0
        bits.append(f"buy/sell 1h {buy_sell:.2f} ≤ 0.70 → 60")
    elif buy_sell is not None:
        bits.append(f"buy/sell 1h {buy_sell:.2f} (not sell-heavy, no rug add)")
    choices = [n for n in (vol_liq_risk, sell_risk) if n is not None]
    if not choices:
        return None
    value = max(choices)
    return value, "Flow uses the higher sub-score. " + "; ".join(bits) + f" → {value:.0f}"


def _lp_from_report(report: dict[str, Any]) -> float | None:
    direct = _fnum(report.get("lpLockedPct"))
    if direct is not None:
        return direct
    best: float | None = None
    best_usd = -1.0
    markets = report.get("markets")
    if not isinstance(markets, list):
        return None
    for market in markets:
        if not isinstance(market, dict):
            continue
        lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}
        pct = _fnum(lp.get("lpLockedPct"))
        if pct is None:
            continue
        usd = _fnum(lp.get("lpLockedUSD")) or 0.0
        if usd >= best_usd:
            best_usd = usd
            best = pct
    return best


def _exclude_accounts(report: dict[str, Any] | None, pair_address: str | None) -> set[str]:
    found: set[str] = set()
    if pair_address:
        found.add(pair_address)
    if not isinstance(report, dict):
        return found
    markets = report.get("markets")
    if not isinstance(markets, list):
        return found
    keys = (
        "pubkey",
        "mintLP",
        "mintLPAccount",
        "liquidityAAccount",
        "liquidityBAccount",
        "mintAAccount",
        "mintBAccount",
    )
    for market in markets:
        if not isinstance(market, dict):
            continue
        for key in keys:
            value = market.get(key)
            if isinstance(value, str) and value:
                found.add(value)
        lp = market.get("lp")
        if isinstance(lp, dict):
            for key in ("lpMint",):
                value = lp.get(key)
                if isinstance(value, str) and value:
                    found.add(value)
    return found


def _short_addr(addr: str) -> str:
    if len(addr) <= 10:
        return addr
    return addr[:4] + "…" + addr[-4:]


def _rug_block(row: dict[str, Any], report: dict[str, Any] | None) -> dict[str, Any]:
    metrics = _metrics(row)
    holders = row.get("holders") if isinstance(row.get("holders"), dict) else {}
    inputs: list[str] = []
    parts: list[tuple[str, float, float]] = []
    omitted: list[str] = []

    lp: float | None = None
    score_n: float | None = None
    risks: list[Any] | None = None
    lp_src = ""
    score_src = ""
    risk_src = ""

    if isinstance(report, dict):
        lp = _lp_from_report(report)
        if lp is not None:
            lp_src = "RugCheck report"
        score_n = _fnum(report.get("score_normalised"))
        if score_n is not None:
            score_src = "RugCheck report"
        if isinstance(report.get("risks"), list):
            risks = report.get("risks")
            risk_src = "RugCheck report"

    if lp is None:
        lp = _first_num(
            [
                (holders, "lp_locked_pct"),
                (metrics, "lp_locked_pct"),
                (row, "lp_locked_pct"),
            ]
        )
        if lp is not None:
            lp_src = "filter snapshot"
    if score_n is None:
        score_n = _fnum(holders.get("score_normalised"))
        if score_n is not None:
            score_src = "filter snapshot"
    if risks is None and isinstance(holders.get("risks"), list):
        risks = holders.get("risks")
        risk_src = "filter snapshot"

    if lp is None:
        omitted.append("LP lock %")
    elif lp < 0 or lp > 100:
        omitted.append(f"LP lock {lp:g} ignored — outside 0–100")
    else:
        risk = max(0.0, min(100.0, 100.0 - lp))
        parts.append(("lp", W_LP, risk))
        inputs.append(f"{lp_src}: LP locked {lp:.2f}% → unlocked risk {risk:.0f} before weight {W_LP:.2f}")

    if score_n is None:
        omitted.append("RugCheck score_normalised")
    else:
        band, band_why = _rug_band(score_n)
        parts.append(("rug_score", W_RUG_SCORE, band))
        inputs.append(
            f"{score_src}: score_normalised {score_n:g} ({band_why}) before weight {W_RUG_SCORE:.2f}. "
            "Raw RugCheck score is not used as a percent."
        )

    if risks is None:
        omitted.append("RugCheck named risks")
    else:
        named, named_why = _named_risk(risks)
        parts.append(("named", W_NAMED, named))
        inputs.append(f"{risk_src}: {named_why} before weight {W_NAMED:.2f}")

    vol = _first_num([(metrics, "volume_h1_usd"), (row, "volume_h1_usd")])
    liq = _first_num([(metrics, "liquidity_usd"), (row, "liquidity_usd")])
    buy_sell = _first_num(
        [
            (metrics, "buy_sell_ratio_h1"),
            (metrics, "buy_sell_ratio"),
            (row, "buy_sell_ratio_h1"),
        ]
    )
    flow = _flow_risk(vol, liq, buy_sell)
    if flow is None:
        omitted.append("1h flow (volume/liquidity or sell-heavy ratio)")
    else:
        flow_value, flow_why = flow
        parts.append(("flow", W_FLOW, flow_value))
        inputs.append(f"filter snapshot: {flow_why} before weight {W_FLOW:.2f}")

    rugged: bool | None = None
    if isinstance(report, dict) and isinstance(report.get("rugged"), bool):
        rugged = report["rugged"]
        inputs.append(
            "RugCheck rugged="
            + ("true" if rugged else "false")
            + ". Boolean only — not folded into the percent, and false is not a guarantee."
        )

    if not parts:
        pct = None
        why = (
            "Rug/risk % unavailable. None of LP lock, RugCheck score_normalised, "
            "named risks, or 1h flow were usable. Missing inputs were not filled in."
        )
    else:
        weight_sum = sum(weight for _, weight, _ in parts)
        pct = round(sum(weight * risk for _, weight, risk in parts) / weight_sum, 1)
        why = (
            f"Weighted average of {len(parts)} present input(s) = {pct:.1f}. "
            "Weights renormalize over those present "
            f"(base LP {W_LP:.2f}, RugCheck score {W_RUG_SCORE:.2f}, "
            f"named risks {W_NAMED:.2f}, flow {W_FLOW:.2f}). "
            "Missing inputs are omitted, not treated as safe or unsafe."
        )

    return {
        "pct": pct,
        "available": pct is not None or rugged is True,
        "label": RUG_LABEL,
        "why": why,
        "inputs": inputs,
        "omitted": omitted,
        "rugged": rugged,
        "input_count": len(parts),
    }


def _profit_block(row: dict[str, Any]) -> dict[str, Any]:
    metrics = _metrics(row)
    h1 = _fnum(metrics.get("price_change_h1"))
    if h1 is None:
        h1 = _fnum(row.get("price_change_h1"))
    h24 = _fnum(metrics.get("price_change_h24"))
    if h24 is None:
        h24 = _fnum(row.get("price_change_h24"))
    liq = _first_num([(metrics, "liquidity_usd"), (row, "liquidity_usd")])
    if h1 is None and h24 is None:
        return {
            "available": False,
            "headline": "Profit outlook unavailable",
            "why": "No h1 or h24 price-change print on this row.",
            "inputs": [],
            "note": PAPER_NOTE,
        }
    inputs: list[str] = []
    if h1 is not None:
        inputs.append(f"price_change_h1 {h1:+.2f}%")
    if h24 is not None:
        inputs.append(f"price_change_h24 {h24:+.2f}%")
    if liq is not None:
        inputs.append(f"liquidity_usd {liq:.2f}")
    if row.get("late_chase") is True:
        inputs.append("late_chase flag is true on the row")
    primary = h1 if h1 is not None else h24
    window = "1h" if h1 is not None else "24h"
    assert primary is not None
    if row.get("late_chase") is True and h1 is not None:
        headline = (
            f"Paper read: {window} print {primary:+.1f}% with the row's late-chase flag. "
            "Observed chase, not a target."
        )
    elif primary >= 30:
        headline = f"Paper read: {window} print {primary:+.1f}% (up). Observed print only."
    elif primary <= -20:
        headline = f"Paper read: {window} print {primary:+.1f}% (down). Observed print only."
    else:
        headline = f"Paper read: {window} print {primary:+.1f}% — no large move on this print."
    return {
        "available": True,
        "headline": headline,
        "why": "Uses stored price-change prints only. Does not forecast a future percent.",
        "inputs": inputs,
        "note": PAPER_NOTE,
    }


def _vitality_block(row: dict[str, Any]) -> dict[str, Any]:
    metrics = _metrics(row)
    has_buys = ("buys_h1" in metrics and metrics.get("buys_h1") is not None) or (
        "buys_h1" in row and row.get("buys_h1") is not None
    )
    has_sells = ("sells_h1" in metrics and metrics.get("sells_h1") is not None) or (
        "sells_h1" in row and row.get("sells_h1") is not None
    )
    vol_container: dict[str, Any] | None = None
    if isinstance(metrics, dict) and metrics.get("volume_h1_usd") is not None:
        vol_container = metrics
    elif row.get("volume_h1_usd") is not None:
        vol_container = row
    vol = _fnum(vol_container.get("volume_h1_usd")) if vol_container else None
    vol_present = vol is not None
    if not has_buys and not has_sells and not vol_present:
        return {
            "available": False,
            "label": None,
            "detail": "Vitality unavailable",
            "why": "No 1h buys, sells, or volume on this row.",
            "inputs": [],
        }
    buys = _fnum(metrics.get("buys_h1") if metrics.get("buys_h1") is not None else row.get("buys_h1")) if has_buys else None
    sells = _fnum(metrics.get("sells_h1") if metrics.get("sells_h1") is not None else row.get("sells_h1")) if has_sells else None
    txn: float | None = None
    if buys is not None or sells is not None:
        txn = (buys or 0.0) + (sells or 0.0)
    label = "thin"
    if (txn is not None and txn >= VITAL_ACTIVE_TXN) or (vol is not None and vol >= VITAL_ACTIVE_VOL):
        label = "active"
    elif (txn is not None and txn >= VITAL_ALIVE_TXN) or (vol is not None and vol >= VITAL_ALIVE_VOL):
        label = "alive"
    inputs: list[str] = []
    if buys is not None:
        inputs.append(f"buys_h1 {buys:.0f}")
    if sells is not None:
        inputs.append(f"sells_h1 {sells:.0f}")
    if vol is not None:
        inputs.append(f"volume_h1_usd {vol:.2f}")
    age = _first_num([(metrics, "pair_age_hours"), (row, "pair_age_hours")])
    if age is not None:
        inputs.append(f"pair_age_hours {age:.2f} (context only, not a vitality point)")
    detail_bits: list[str] = []
    if txn is not None:
        detail_bits.append(f"{txn:.0f} one-hour txs")
    if vol is not None:
        detail_bits.append(f"1h volume {vol:.0f} USD")
    return {
        "available": True,
        "label": label,
        "detail": " · ".join(detail_bits),
        "why": (
            f"Bands from the 1h print: active if txs ≥ {VITAL_ACTIVE_TXN} or volume ≥ "
            f"{VITAL_ACTIVE_VOL:.0f}; alive if txs ≥ {VITAL_ALIVE_TXN} or volume ≥ "
            f"{VITAL_ALIVE_VOL:.0f}; otherwise thin. Zeros count. This is activity, not safety."
        ),
        "inputs": inputs,
    }


def _content_block(row: dict[str, Any], report: dict[str, Any] | None) -> dict[str, Any]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(label: str, url: Any, source: str) -> None:
        http = _http_url(url)
        if not http or http in seen:
            return
        # Constructed Pump / FOMO / GMGN shortcuts are navigation, not proof.
        lowered = http.lower()
        if "fomo.family" in lowered or "pump.fun/" in lowered or "gmgn.ai/" in lowered:
            return
        seen.add(http)
        links.append({"label": label, "url": http, "source": source})

    socials = row.get("socials") if isinstance(row.get("socials"), dict) else {}
    add("X", socials.get("twitter_url"), "socials.twitter_url")
    add("Telegram", socials.get("telegram_url"), "socials.telegram_url")
    add("Website", socials.get("website_url"), "socials.website_url")

    dex = row.get("dex_url")
    if not _http_url(dex):
        links_obj = row.get("links") if isinstance(row.get("links"), dict) else {}
        dex = links_obj.get("dex")
    add("Pair page", dex, "row dex_url or links.dex — market page, not a post")

    description = row.get("description") if isinstance(row.get("description"), str) else ""
    description = description.strip()
    profile_source = "row.description" if description else None
    if not description and isinstance(report, dict) and isinstance(report.get("fileMeta"), dict):
        file_desc = report["fileMeta"].get("description")
        if isinstance(file_desc, str) and file_desc.strip():
            description = file_desc.strip()
            profile_source = "RugCheck fileMeta.description"
    if len(description) > 280:
        description = description[:279].rstrip() + "…"

    missing: list[str] = []
    if socials.get("twitter") is True and not _http_url(socials.get("twitter_url")):
        missing.append("X marked on the snapshot but no URL stored — not linked")
    if socials.get("telegram") is True and not _http_url(socials.get("telegram_url")):
        missing.append("Telegram marked on the snapshot but no URL stored — not linked")
    if socials.get("website") is True and not _http_url(socials.get("website_url")):
        missing.append("Website marked on the snapshot but no URL stored — not linked")

    available = bool(links or description)
    return {
        "available": available,
        "links": links,
        "profile_text": description or None,
        "profile_source": profile_source if description else None,
        "missing": missing,
        "empty": None if available else "No proof links and no profile text on this snapshot.",
        "note": CONTENT_NOTE,
    }


def _fomo_block(row: dict[str, Any]) -> dict[str, Any]:
    metrics = _metrics(row)
    items: list[dict[str, str]] = []
    boost = _fnum(row.get("boost_amount"))
    if boost is not None and boost > 0:
        items.append(
            {
                "kind": "dex_boost",
                "text": f"DEX boost amount {boost:g} stored on this row (boosts feed). Not a social post.",
            }
        )
    else:
        sources = row.get("sources") if isinstance(row.get("sources"), list) else []
        if "boosts" in sources:
            items.append(
                {
                    "kind": "dex_boost_list",
                    "text": "Row sources include the DEX boosts feed, but this row has no boost amount number.",
                }
            )
    h1 = _fnum(metrics.get("price_change_h1"))
    if h1 is None:
        h1 = _fnum(row.get("price_change_h1"))
    h24 = _fnum(metrics.get("price_change_h24"))
    if h24 is None:
        h24 = _fnum(row.get("price_change_h24"))
    if h1 is not None and abs(h1) >= FOMO_H1_ABS:
        items.append(
            {
                "kind": "hour_print",
                "text": f"Observed 1h price {h1:+.1f}%. Market print, not a FOMO post.",
            }
        )
    elif h24 is not None and abs(h24) >= FOMO_H24_ABS:
        items.append(
            {
                "kind": "day_print",
                "text": f"Observed 24h price {h24:+.1f}% (1h print missing or under {FOMO_H1_ABS:.0f}%). Market print, not a FOMO post.",
            }
        )
    buy_sell = _first_num(
        [
            (metrics, "buy_sell_ratio_h1"),
            (metrics, "buy_sell_ratio"),
            (row, "buy_sell_ratio_h1"),
        ]
    )
    buys = _fnum(metrics.get("buys_h1") if metrics.get("buys_h1") is not None else row.get("buys_h1"))
    sells = _fnum(metrics.get("sells_h1") if metrics.get("sells_h1") is not None else row.get("sells_h1"))
    if buy_sell is not None and buy_sell >= FOMO_BUY_RATIO and buys is not None and buys >= FOMO_MIN_BUYS:
        sell_txt = f"{sells:.0f}" if sells is not None else "—"
        items.append(
            {
                "kind": "buy_pressure",
                "text": (
                    f"1h buy/sell {buy_sell:.2f} ({buys:.0f} buys / {sell_txt} sells). "
                    "Flow print, not a social post."
                ),
            }
        )
    if not items:
        return {
            "available": False,
            "items": [],
            "empty": (
                "No DEX boost on this row and no large price print "
                f"(1h |Δ| ≥ {FOMO_H1_ABS:.0f}% or, if that is absent, 24h |Δ| ≥ {FOMO_H24_ABS:.0f}%). "
                "No FOMO-feed posts are connected, so none are shown."
            ),
            "note": "Pump/FOMO website links on the row are mint shortcuts, not evidence.",
        }
    return {
        "available": True,
        "items": items,
        "empty": None,
        "note": "These are market fields already on the row. No FOMO post was created.",
    }


def _holders_block(row: dict[str, Any], report: dict[str, Any] | None) -> dict[str, Any]:
    metrics = _metrics(row)
    age = _first_num([(metrics, "pair_age_hours"), (row, "pair_age_hours")])
    pair_address = None
    if isinstance(metrics.get("pair_address"), str):
        pair_address = metrics.get("pair_address")
    elif isinstance(row.get("pair_address"), str):
        pair_address = row.get("pair_address")

    if not isinstance(report, dict):
        return {
            "available": False,
            "headline": "Holder concentration unavailable",
            "why": "This snapshot has no top-holder list. RugCheck summary does not include per-wallet percentages.",
            "inputs": [],
            "empty": "No top-holder percentages on this snapshot.",
            "note": "Not labeled early. No wallets were invented.",
            "early": False,
            "top1_pct": None,
            "top10_pct": None,
        }

    has_top = isinstance(report.get("topHolders"), list)
    total_holders = report.get("totalHolders")
    total_n = _fnum(total_holders) if total_holders is not None else None
    insiders_n = report.get("graphInsidersDetected")
    insiders_num = _fnum(insiders_n) if insiders_n is not None and not isinstance(insiders_n, bool) else None
    if not has_top and total_n is None and insiders_num is None:
        return {
            "available": False,
            "headline": "Holder concentration unavailable",
            "why": "RugCheck report had no topHolders, totalHolders, or graphInsidersDetected.",
            "inputs": [],
            "empty": "No holder concentration fields on the RugCheck report.",
            "note": "Not labeled early. No wallets were invented.",
            "early": False,
            "top1_pct": None,
            "top10_pct": None,
        }

    exclude = _exclude_accounts(report, pair_address if isinstance(pair_address, str) else None)
    creator = report.get("creator") if isinstance(report.get("creator"), str) else None
    wallets: list[dict[str, Any]] = []
    pools: list[float] = []
    top_list = report.get("topHolders") if has_top else []
    for holder in top_list:
        if not isinstance(holder, dict):
            continue
        pct = _fnum(holder.get("pct"))
        if pct is None:
            continue
        address = holder.get("address") if isinstance(holder.get("address"), str) else ""
        owner = holder.get("owner") if isinstance(holder.get("owner"), str) else ""
        if (address and address in exclude) or (owner and owner in exclude):
            pools.append(pct)
            continue
        insider: bool | None
        if isinstance(holder.get("insider"), bool):
            insider = holder.get("insider")
        else:
            insider = None
        is_creator = bool(creator) and (address == creator or owner == creator)
        wallets.append(
            {
                "pct": pct,
                "insider": insider,
                "creator": is_creator,
                "short": _short_addr(owner or address),
            }
        )
    wallets.sort(key=lambda item: item["pct"], reverse=True)

    inputs: list[str] = []
    if pools:
        inputs.append(
            f"Excluded {len(pools)} pool/market account(s) totaling {sum(pools):.2f}% "
            "(pair or RugCheck market accounts, not counted as wallets)."
        )
    if total_n is not None:
        inputs.append(f"RugCheck totalHolders {total_n:.0f}")
    if insiders_num is not None:
        inputs.append(
            f"RugCheck graphInsidersDetected {insiders_num:.0f} (their count, not a guarantee)"
        )
    if age is not None:
        inputs.append(f"pair_age_hours {age:.2f}")
    elif age is None:
        inputs.append("pair age missing — not labeled early")

    if not wallets:
        bits = ["No non-pool wallet percentages."]
        if pools:
            bits.append(f"{len(pools)} top account(s) matched the pool/market exclude list.")
        if total_n is not None:
            bits.append(f"totalHolders {total_n:.0f}.")
        headline = " ".join(bits)
        return {
            "available": True,
            "headline": headline,
            "why": "Concentration % needs a non-pool holder percent. Pool accounts were not relabeled as wallets.",
            "inputs": inputs,
            "empty": None,
            "note": "Not a wallet list. Addresses are shortened from the report.",
            "early": False,
            "top1_pct": None,
            "top10_pct": None,
        }

    top1 = float(wallets[0]["pct"])
    top10 = sum(float(item["pct"]) for item in wallets[:10])
    insider_true = sum(1 for item in wallets if item["insider"] is True)
    insider_known = sum(1 for item in wallets if item["insider"] is not None)
    early = age is not None and age <= EARLY_MAX_AGE_H and top1 >= MAJOR_HOLDER_PCT
    inputs.append(f"largest non-pool holder {top1:.2f}% ({wallets[0]['short'] or 'address missing'})")
    inputs.append(f"top {min(10, len(wallets))} non-pool sum {top10:.2f}%")
    if wallets[0]["creator"]:
        inputs.append("Largest non-pool account matches RugCheck creator.")
    if insider_known:
        inputs.append(
            f"RugCheck insider=true on {insider_true} of {insider_known} listed non-pool wallets "
            "(flag as reported, not a new finding)."
        )
    if top10 > 100.05:
        inputs.append("Top-holder percents sum over 100 — shown as reported, not renormalized.")

    if early:
        headline = (
            f"Early concentration: largest non-pool holder {top1:.2f}% "
            f"and pair age {age:.2f}h (early window ≤ {EARLY_MAX_AGE_H:.0f}h and ≥ {MAJOR_HOLDER_PCT:.0f}%)."
        )
    elif age is not None and age <= EARLY_MAX_AGE_H:
        headline = (
            f"Pair age {age:.2f}h. Largest non-pool holder {top1:.2f}% — "
            f"below the {MAJOR_HOLDER_PCT:.0f}% major-holder line used here."
        )
    elif age is None:
        headline = (
            f"Largest non-pool holder {top1:.2f}%. Pair age is missing, so this is not labeled early."
        )
    else:
        headline = (
            f"Largest non-pool holder {top1:.2f}%. Pair age {age:.2f}h is outside the "
            f"{EARLY_MAX_AGE_H:.0f}h early window, so this is concentration only."
        )
    return {
        "available": True,
        "headline": headline,
        "why": "Percents are RugCheck topHolders.pct after dropping pair/market accounts. Not a full holder census.",
        "inputs": inputs,
        "empty": None,
        "note": "Not a wallet list and not a trade signal. Short addresses come from the report.",
        "early": early,
        "top1_pct": round(top1, 2),
        "top10_pct": round(top10, 2),
    }


def research_card(row: dict[str, Any] | None, report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the six-block coin card. `report` is an optional RugCheck full report."""
    src = row if isinstance(row, dict) else {}
    # Never read a previously attached card back in as an input.
    clean = {key: value for key, value in src.items() if key != "research"}
    rug = _rug_block(clean, report if isinstance(report, dict) else None)
    return {
        "rug_risk": rug,
        "profit": _profit_block(clean),
        "vitality": _vitality_block(clean),
        "content": _content_block(clean, report if isinstance(report, dict) else None),
        "fomo_pump": _fomo_block(clean),
        "holders": _holders_block(clean, report if isinstance(report, dict) else None),
        "live_report": isinstance(report, dict),
        "as_of": clean.get("data_as_of") if isinstance(clean.get("data_as_of"), str) else None,
        "disclaimer": "Paper research only. Not financial advice. No trades placed. Figures are labeled inputs, not certainty.",
    }
