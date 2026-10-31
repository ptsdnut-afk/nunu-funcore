"""Coin checklist for one mint (SHIP X).

Paper research only. Missing inputs stay Unknown. Nothing here is a buy.

Verdict bands are pass-counts. Unknown is not a pass. X2 can retune
VERDICT_BANDS and CHECKS when the sheet grows to 10 rows
(BUY-WATCH 9–10, WATCH 7–8, SKIP at 6 or lower). Callers must key off
the verdict name, not the pass count.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import filter as filter_mod

# Inclusive pass-count bands, highest first. Forced SKIP ignores the count.
VERDICT_BANDS = (
    ("BUY-WATCH", 8, 8),
    ("WATCH", 6, 7),
    ("SKIP", 0, 5),
)

CHECKS = (
    ("lp", "LP burned or locked"),
    ("mint_freeze", "Mint and freeze revoked"),
    ("dev_history", "Clean dev history"),
    ("not_bundled", "Not bundled"),
    ("top10", "Top-10 holders under 30%"),
    ("socials", "Real socials"),
    ("organic", "Organic volume"),
    ("narrative", "Narrative fit"),
)

# Pass when strictly under this percent.
TOP10_PASS_BELOW = 30.0
# A linked cluster at or above this percent is bundled and forces SKIP.
CLUSTER_BUNDLE_AT = 10.0
# LP percent at or above this counts as burned or locked.
LP_LOCK_MIN = 95.0
ORGANIC_MIN_TRADERS = 100
# "Over 10%" fails. Exactly 10% does not.
ORGANIC_WALLET_MAX_PCT = 10.0

CACHE_TTL_S = 150.0
FUNDER_TTL_S = 6 * 3600.0
FUNDER_MISS_TTL_S = 90.0
CACHE_VERSION = "x83"

PUMP_COIN = "https://frontend-api-v3.pump.fun/coins-v2/{mint}"
PUMP_CREATOR = "https://frontend-api-v3.pump.fun/coins-v2/user-created-coins/{creator}?offset=0&limit=20"
DEX_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
BUBBLE_MAP = "https://api.bubblemaps.io/v0/tokens/map/solana/{mint}?return_clusters=true&limit=80"

PUBLIC_RPCS = (
    "https://public.rpc.solanavibestation.com",
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
)

BURN_OWNERS = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
}

_mem: dict[str, tuple[float, dict[str, Any]]] = {}
_bubble_blocked = False


def helius_key() -> str:
    return (os.environ.get("HELIUS_API_KEY") or os.environ.get("HELIUS_KEY") or "").strip()


def verdict_for(pass_count: int, *, forced_skip: bool) -> str:
    """Final verdict name. Forced SKIP wins over the pass count."""
    if forced_skip:
        return "SKIP"
    count = int(pass_count)
    for name, lo, hi in VERDICT_BANDS:
        if lo <= count <= hi:
            return name
    return "SKIP"


def count_verdict(pass_count: int) -> str:
    return verdict_for(pass_count, forced_skip=False)


def rules_payload() -> dict[str, Any]:
    return {
        "bands": [
            {"name": name, "min_pass": lo, "max_pass": hi}
            for name, lo, hi in VERDICT_BANDS
        ],
        "top10_pass_below": TOP10_PASS_BELOW,
        "cluster_bundle_at": CLUSTER_BUNDLE_AT,
        "lp_lock_min": LP_LOCK_MIN,
        "organic_min_traders": ORGANIC_MIN_TRADERS,
        "organic_wallet_max_pct": ORGANIC_WALLET_MAX_PCT,
        "unknown_passes": False,
    }


def _check(cid: str, label: str, status: str, detail: str, source: str) -> dict[str, Any]:
    if status not in {"pass", "fail", "unknown"}:
        status = "unknown"
    return {
        "id": cid,
        "label": label,
        "status": status,
        "pass": status == "pass",
        "detail": detail,
        "source": source,
    }


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("%", "").replace("$", "")
        if not text:
            return None
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _short(addr: str | None) -> str | None:
    if not addr or not isinstance(addr, str):
        return None
    if len(addr) <= 10:
        return addr
    return addr[:4] + "…" + addr[-4:]


def _age_text(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    seconds = max(0.0, float(seconds))
    if seconds < 3600:
        return str(max(1, round(seconds / 60))) + "m"
    if seconds < 48 * 3600:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins:02d}m"
    return f"{seconds / 86400:.1f}d"


def _when(raw: Any) -> float | None:
    number = _num(raw)
    if number is None or number <= 0:
        return None
    if number > 1e12:
        number = number / 1000.0
    return number


def _present(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"none", "null", "unknown", "n/a", "-"}:
        return None
    return text


def top10_pct(holders: list[dict[str, Any]] | None) -> float | None:
    """Sum of the largest non-excluded holder percents, at most 10."""
    if not holders:
        return None
    pcts = []
    for row in holders:
        if not isinstance(row, dict) or row.get("excluded"):
            continue
        pct = _num(row.get("pct"))
        if pct is None:
            continue
        pcts.append(pct)
    if not pcts:
        return None
    pcts.sort(reverse=True)
    return round(sum(pcts[:10]), 4)


def cluster_from_traces(traces: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Largest funding-source cluster among traced top holders.

    A cluster is two or more wallets with the same funder. The percent is
    known when every trace resolved, or when a resolved cluster is already
    at the bundle line (that finding stands even if other wallets did not).
    """
    rows = [row for row in (traces or []) if isinstance(row, dict)]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    untraced = 0
    for row in rows:
        funder = row.get("funder") if isinstance(row.get("funder"), str) and row.get("funder") else None
        if funder:
            groups[funder].append(row)
        else:
            untraced += 1
    best_pct = 0.0
    best_n = 0
    best_funder = None
    for funder, members in groups.items():
        if len(members) < 2:
            continue
        pct = sum(_num(member.get("pct")) or 0.0 for member in members)
        if pct > best_pct:
            best_pct = pct
            best_n = len(members)
            best_funder = funder
    best_pct = round(best_pct, 4)
    bundled_known = best_n >= 2 and best_pct >= CLUSTER_BUNDLE_AT
    known = bool(rows) and (untraced == 0 or bundled_known)
    return {
        "pct": best_pct if known else None,
        "traced_pct": best_pct if best_n else 0.0,
        "wallets": best_n if best_n else 0,
        "funder": best_funder,
        "untraced": untraced,
        "known": known,
        "bundled": bool(known and best_pct >= CLUSTER_BUNDLE_AT),
    }


def _social_flags(pump: dict[str, Any] | None, dex: dict[str, Any] | None) -> dict[str, Any]:
    website = None
    twitter = None
    telegram = None
    fetched = False
    if isinstance(pump, dict) and not pump.get("missing") and not pump.get("error"):
        fetched = True
        website = _present(pump.get("website"))
        twitter = _present(pump.get("twitter"))
        telegram = _present(pump.get("telegram"))
    info = {}
    if isinstance(dex, dict) and not dex.get("error"):
        fetched = True
        raw = dex.get("info") if isinstance(dex.get("info"), dict) else {}
        info = raw
        for site in raw.get("websites") or []:
            if isinstance(site, dict):
                website = website or _present(site.get("url") or site.get("label"))
            else:
                website = website or _present(site)
        for social in raw.get("socials") or []:
            if not isinstance(social, dict):
                continue
            kind = str(social.get("type") or "").lower()
            url = _present(social.get("url"))
            if not url:
                continue
            if kind in {"twitter", "x"}:
                twitter = twitter or url
            elif kind in {"telegram", "tg"}:
                telegram = telegram or url
            elif kind in {"website", "web"}:
                website = website or url
    return {
        "fetched": fetched,
        "website": website,
        "twitter": twitter,
        "telegram": telegram,
        "info_present": bool(info) or isinstance(pump, dict),
    }


def _lp_pct(rug: dict[str, Any] | None) -> float | None:
    if not isinstance(rug, dict):
        return None
    direct = _num(rug.get("lpLockedPct"))
    if direct is not None:
        return direct
    best = None
    best_usd = -1.0
    markets = rug.get("markets")
    if not isinstance(markets, list):
        return None
    for market in markets:
        if not isinstance(market, dict):
            continue
        lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}
        pct = _num(lp.get("lpLockedPct"))
        if pct is None:
            continue
        usd = _num(lp.get("lpLockedUSD")) or 0.0
        if usd >= best_usd:
            best_usd = usd
            best = pct
    return best


def _rug_top10(rug: dict[str, Any] | None, supply: float | None) -> float | None:
    if not isinstance(rug, dict):
        return None
    holders = rug.get("topHolders")
    if not isinstance(holders, list) or not holders:
        return None
    pcts = []
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        pct = _num(holder.get("pct"))
        if pct is None and supply and holder.get("uiAmount") is not None:
            amount = _num(holder.get("uiAmount"))
            if amount is not None and supply > 0:
                pct = amount / supply * 100.0
        if pct is not None:
            pcts.append(pct)
    if not pcts:
        return None
    # Some payloads store a 0–1 share. A real holder percent list is not entirely ≤ 1
    # unless each wallet is under 1%, which we still scale only when the max is ≤ 1
    # AND the values were given as `pct` (uiAmount math is already 0–100).
    # A 0–1 share list sums to at most 1. Percents that all happen to be under 1 do not.
    if max(pcts) <= 1.0 and sum(pcts) <= 1.01 and any(
        isinstance(holder, dict) and holder.get("pct") is not None for holder in holders
    ):
        pcts = [pct * 100.0 for pct in pcts]
    pcts.sort(reverse=True)
    return round(sum(pcts[:10]), 4)


def _launch_counts(mint: str, creator_coins: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Return (launched, prior_total). prior_total is None when the page is incomplete."""
    if not isinstance(creator_coins, dict) or creator_coins.get("error"):
        return None, None
    coins = [
        coin for coin in (creator_coins.get("coins") or [])
        if isinstance(coin, dict) and coin.get("mint")
    ]
    others = [coin for coin in coins if coin.get("mint") != mint]
    raw_count = creator_coins.get("count")
    count_n = int(raw_count) if isinstance(raw_count, (int, float)) and not isinstance(raw_count, bool) else None
    page_complete = count_n is None or count_n <= len(coins)
    if not page_complete:
        return count_n, None
    launched = count_n if count_n is not None else len(coins)
    if any(coin.get("mint") == mint for coin in coins):
        return max(launched, len(coins)), len(others)
    return launched, len(others)


def assemble(mint: str, sources: dict[str, Any] | None, now: float | None = None) -> dict[str, Any]:
    """Build the checklist from already-fetched sources. Does not touch the network."""
    sources = sources or {}
    now_s = float(now if now is not None else time.time())
    pump = sources.get("pump") if isinstance(sources.get("pump"), dict) else None
    dex = sources.get("dex") if isinstance(sources.get("dex"), dict) else None
    rug = sources.get("rug") if isinstance(sources.get("rug"), dict) else None
    holders = sources.get("holders") if isinstance(sources.get("holders"), list) else []
    traces = sources.get("traces") if isinstance(sources.get("traces"), list) else []
    creator_coins = sources.get("creator_coins") if isinstance(sources.get("creator_coins"), dict) else None
    authorities = sources.get("authorities") if isinstance(sources.get("authorities"), dict) else None
    organic = sources.get("organic") if isinstance(sources.get("organic"), dict) else None

    symbol = None
    name = None
    image_url = None
    if isinstance(pump, dict) and not pump.get("missing") and not pump.get("error"):
        symbol = _present(pump.get("symbol"))
        name = _present(pump.get("name"))
        image_url = _present(pump.get("image_uri") or pump.get("image_url"))
    if isinstance(dex, dict) and not dex.get("error"):
        base = dex.get("baseToken") if isinstance(dex.get("baseToken"), dict) else {}
        symbol = symbol or _present(base.get("symbol"))
        name = name or _present(base.get("name"))
        info = dex.get("info") if isinstance(dex.get("info"), dict) else {}
        image_url = image_url or _present(info.get("imageUrl"))

    price = None
    price_source = None
    mcap = None
    mcap_source = None
    h1 = None
    h1_source = None
    if isinstance(dex, dict) and not dex.get("error"):
        price = _num(dex.get("priceUsd") if dex.get("priceUsd") is not None else dex.get("price_usd"))
        if price is not None:
            price_source = "Dexscreener"
        mcap = _num(dex.get("marketCap") if dex.get("marketCap") is not None else dex.get("market_cap_usd"))
        if mcap is None:
            mcap = _num(dex.get("fdv"))
        if mcap is not None:
            mcap_source = "Dexscreener"
        change = dex.get("priceChange") if isinstance(dex.get("priceChange"), dict) else {}
        h1 = _num(change.get("h1") if change.get("h1") is not None else dex.get("price_change_h1"))
        if h1 is not None:
            h1_source = "Dexscreener"
    if price is None and isinstance(pump, dict) and not pump.get("missing"):
        price = _num(pump.get("price_usd"))
        if price is not None:
            price_source = "Pump.fun"
    if mcap is None and isinstance(pump, dict) and not pump.get("missing"):
        mcap = _num(pump.get("usd_market_cap") if pump.get("usd_market_cap") is not None else pump.get("market_cap_usd"))
        if mcap is not None:
            mcap_source = "Pump.fun"

    created = None
    created_source = None
    bonded = None
    if isinstance(pump, dict) and not pump.get("missing") and not pump.get("error"):
        created = _when(pump.get("created_timestamp"))
        if created is not None:
            created_source = "Pump.fun"
    if created is None and isinstance(dex, dict):
        created = _when(dex.get("pairCreatedAt"))
        if created is not None:
            created_source = "Dexscreener"
    age_s = (now_s - created) if created is not None else None

    socials = _social_flags(pump if isinstance(pump, dict) and not pump.get("error") else None, dex)
    description = None
    description_source = None
    if isinstance(pump, dict) and not pump.get("missing") and not pump.get("error"):
        description = _present(pump.get("description"))
        if description:
            description_source = "Pump.fun"

    launchpad = None
    launchpad_source = None
    if isinstance(pump, dict) and pump.get("missing"):
        launchpad = None
    elif isinstance(pump, dict) and not pump.get("error"):
        launchpad_source = "Pump.fun"
        if pump.get("complete"):
            if _present(str(pump.get("raydium_pool") or "")):
                launchpad = "Pump.fun → bonded to Raydium"
            elif _present(str(pump.get("pump_swap_pool") or "")):
                launchpad = "Pump.fun → bonded to PumpSwap"
            else:
                launchpad = "Pump.fun → bonded"
        else:
            launchpad = "Pump.fun · bonding curve"
    elif isinstance(dex, dict) and not dex.get("error") and dex.get("dexId"):
        launchpad = "Listed on " + str(dex.get("dexId"))
        launchpad_source = "Dexscreener"

    creator = None
    if isinstance(pump, dict) and not pump.get("missing") and not pump.get("error"):
        creator = _present(pump.get("creator"))

    launched, prior_total = _launch_counts(mint, creator_coins)
    prior_coins = []
    if isinstance(creator_coins, dict) and not creator_coins.get("error"):
        prior_coins = [
            coin for coin in (creator_coins.get("coins") or [])
            if isinstance(coin, dict) and coin.get("mint") and coin.get("mint") != mint
        ]

    rugs = sources.get("prior_rugs")
    rugs_n = int(rugs) if isinstance(rugs, int) and rugs >= 0 else None
    rugs_checked = sources.get("prior_rugs_checked")
    checked_n = int(rugs_checked) if isinstance(rugs_checked, int) and rugs_checked >= 0 else 0

    best_prior = None
    best_prior_source = None
    for coin in prior_coins:
        ath = _num(coin.get("ath_market_cap") if coin.get("ath_market_cap") is not None else coin.get("usd_market_cap"))
        if ath is None:
            continue
        if best_prior is None or ath > best_prior:
            best_prior = ath
            best_prior_source = "Pump.fun"

    holder_pct = top10_pct(holders)
    holder_source = sources.get("holder_source") if holder_pct is not None else None
    if holder_pct is not None and not holder_source:
        holder_source = "Solana RPC"
    rug_top = _rug_top10(rug, _num(sources.get("supply")))
    rug_delta = None
    if holder_pct is not None and rug_top is not None:
        rug_delta = round(abs(holder_pct - rug_top), 4)

    dev_holds = None
    if creator:
        for row in holders:
            if isinstance(row, dict) and row.get("owner") == creator and not row.get("excluded"):
                dev_holds = _num(row.get("pct"))
                break

    cluster = cluster_from_traces(traces)
    trace_rows = []
    for index, row in enumerate(traces[:5]):
        if not isinstance(row, dict):
            continue
        trace_rows.append({
            "rank": index + 1,
            "owner": row.get("owner"),
            "owner_short": _short(row.get("owner")),
            "pct": _num(row.get("pct")),
            "funder": row.get("funder"),
            "funder_short": _short(row.get("funder")) if row.get("funder") else None,
            "reason": row.get("reason"),
            "source": row.get("source") or ("Helius" if helius_key() else "public RPC"),
        })

    bubbles = []
    shown = [row for row in holders if isinstance(row, dict) and not row.get("excluded") and _num(row.get("pct")) is not None]
    shown.sort(key=lambda row: _num(row.get("pct")) or 0.0, reverse=True)
    cluster_owners = set()
    if cluster.get("funder"):
        for row in traces:
            if isinstance(row, dict) and row.get("funder") == cluster.get("funder"):
                if row.get("owner"):
                    cluster_owners.add(row.get("owner"))
    for row in shown[:8]:
        bubbles.append({
            "pct": round(_num(row.get("pct")) or 0.0, 4),
            "owner_short": _short(row.get("owner")),
            "cluster": row.get("owner") in cluster_owners,
        })

    # —— eight rows ——
    lp = _lp_pct(rug)
    if lp is None:
        lp_row = _check("lp", CHECKS[0][1], "unknown", "Unknown — RugCheck did not return a lock percent", "RugCheck")
    elif lp >= LP_LOCK_MIN:
        lp_row = _check("lp", CHECKS[0][1], "pass", f"{lp:.1f}% locked", "RugCheck")
    else:
        lp_row = _check("lp", CHECKS[0][1], "fail", f"{lp:.1f}% locked", "RugCheck")

    if not isinstance(authorities, dict) or not authorities.get("known"):
        auth_row = _check(
            "mint_freeze", CHECKS[1][1], "unknown",
            "Unknown — mint account authorities were not returned",
            authorities.get("source") if isinstance(authorities, dict) and authorities.get("source") else "Solana RPC",
        )
    else:
        mint_auth = authorities.get("mint_authority")
        freeze_auth = authorities.get("freeze_authority")
        source = authorities.get("source") or "Solana RPC"
        if not mint_auth and not freeze_auth:
            auth_row = _check("mint_freeze", CHECKS[1][1], "pass", "both off", source)
        else:
            bits = []
            if mint_auth:
                bits.append("mint authority on")
            if freeze_auth:
                bits.append("freeze authority on")
            auth_row = _check("mint_freeze", CHECKS[1][1], "fail", ", ".join(bits), source)

    forced_reasons: list[str] = []
    if creator is None and not (isinstance(pump, dict) and not pump.get("error") and not pump.get("missing")):
        dev_row = _check(
            "dev_history", CHECKS[2][1], "unknown",
            "Unknown — creator wallet was not returned",
            "Pump.fun",
        )
    elif creator is None:
        dev_row = _check(
            "dev_history", CHECKS[2][1], "unknown",
            "Unknown — Pump.fun returned no creator",
            "Pump.fun",
        )
    elif rugs_n is not None and rugs_n >= 1:
        forced_reasons.append("rug-history dev")
        dev_row = _check(
            "dev_history", CHECKS[2][1], "fail",
            f"{rugs_n} rug" + ("s" if rugs_n != 1 else "") + f" in {checked_n} checked",
            "RugCheck",
        )
    elif launched is not None and prior_total == 0:
        dev_row = _check("dev_history", CHECKS[2][1], "pass", "0 prior coins", "Pump.fun")
    elif rugs_n == 0 and prior_total is not None and checked_n >= prior_total:
        dev_row = _check(
            "dev_history", CHECKS[2][1], "pass",
            f"0 rugs in {launched} launches",
            "Pump.fun + RugCheck",
        )
    else:
        detail = "Unknown — RugCheck did not return rugged flags"
        if prior_total is not None:
            detail += f" for {prior_total} prior coin" + ("s" if prior_total != 1 else "")
        elif launched is not None:
            detail += f" ({launched} launches, prior list incomplete)"
        dev_row = _check("dev_history", CHECKS[2][1], "unknown", detail, "RugCheck")

    if cluster.get("bundled"):
        forced_reasons.append("linked cluster ≥10%")
        bundle_row = _check(
            "not_bundled", CHECKS[3][1], "fail",
            f"linked cluster {cluster['pct']:.1f}% ≥ {CLUSTER_BUNDLE_AT:.0f}% · {cluster['wallets']} wallets",
            trace_rows[0]["source"] if trace_rows else "public RPC",
        )
    elif cluster.get("known"):
        bundle_row = _check(
            "not_bundled", CHECKS[3][1], "pass",
            f"linked cluster {cluster['pct']:.1f}% < {CLUSTER_BUNDLE_AT:.0f}%"
            + (f" · {cluster['wallets']} wallets" if cluster.get("wallets") else " · no shared funder"),
            trace_rows[0]["source"] if trace_rows else "public RPC",
        )
    else:
        extra = ""
        if traces:
            extra = f" — traced cluster {cluster.get('traced_pct') or 0:.1f}%, {cluster.get('untraced') or 0} holder(s) untraced"
        bundle_row = _check(
            "not_bundled", CHECKS[3][1], "unknown",
            "Unknown — funding trace incomplete" + extra,
            "public RPC" if not helius_key() else "Helius",
        )

    if holder_pct is None:
        top_row = _check("top10", CHECKS[4][1], "unknown", "Unknown — top holder list was not returned", holder_source or "Solana RPC")
    elif holder_pct < TOP10_PASS_BELOW:
        detail = f"{holder_pct:.1f}% < {TOP10_PASS_BELOW:.0f}%"
        if rug_top is not None:
            detail += f" · RugCheck {rug_top:.1f}%"
        else:
            detail += " · RugCheck unavailable"
        top_row = _check("top10", CHECKS[4][1], "pass", detail, holder_source or "Solana RPC")
    else:
        detail = f"{holder_pct:.1f}% ≥ {TOP10_PASS_BELOW:.0f}%"
        if rug_top is not None:
            detail += f" · RugCheck {rug_top:.1f}%"
        else:
            detail += " · RugCheck unavailable"
        top_row = _check("top10", CHECKS[4][1], "fail", detail, holder_source or "Solana RPC")

    social_source = "Pump.fun + Dexscreener" if socials["fetched"] else "Dexscreener"
    if not socials["fetched"]:
        social_row = _check(
            "socials", CHECKS[5][1], "unknown",
            "Unknown — Pump.fun and Dexscreener did not return socials",
            social_source,
        )
    elif socials["website"] and (socials["twitter"] or socials["telegram"]):
        bits = ["website"]
        if socials["twitter"]:
            bits.append("X")
        if socials["telegram"]:
            bits.append("Telegram")
        social_row = _check("socials", CHECKS[5][1], "pass", " + ".join(bits) + " listed", social_source)
    else:
        missing = []
        if not socials["website"]:
            missing.append("website not listed")
        if not socials["twitter"] and not socials["telegram"]:
            missing.append("no X or Telegram")
        social_row = _check("socials", CHECKS[5][1], "fail", " · ".join(missing), social_source)

    trades = None
    if isinstance(dex, dict):
        txns = dex.get("txns") if isinstance(dex.get("txns"), dict) else {}
        hour = txns.get("h1") if isinstance(txns.get("h1"), dict) else {}
        buys = _num(hour.get("buys"))
        sells = _num(hour.get("sells"))
        if buys is not None or sells is not None:
            trades = (buys or 0.0) + (sells or 0.0)
    unique = _num(organic.get("unique_traders")) if isinstance(organic, dict) else None
    wallet_pct = _num(organic.get("max_wallet_pct")) if isinstance(organic, dict) else None
    wallet_known = bool(isinstance(organic, dict) and organic.get("known"))
    organic_source = "Dexscreener"
    if wallet_known:
        organic_source = (organic or {}).get("source") or "Helius"
    if trades is not None and trades < ORGANIC_MIN_TRADERS and not wallet_known:
        organic_row = _check(
            "organic", CHECKS[6][1], "fail",
            f"{trades:.0f} trades in 1h, so unique traders are under {ORGANIC_MIN_TRADERS}",
            "Dexscreener",
        )
    elif not wallet_known:
        if trades is None:
            detail = f"Unknown — unique traders and per-wallet share need a parsed trade feed (no Helius key)"
        else:
            detail = (
                f"Unknown — {trades:.0f} trades in 1h. Unique traders and whether one wallet "
                f"is over {ORGANIC_WALLET_MAX_PCT:.0f}% of that volume were not returned"
            )
        organic_row = _check("organic", CHECKS[6][1], "unknown", detail, "Dexscreener")
    elif unique is None or unique < ORGANIC_MIN_TRADERS:
        shown_unique = "unknown count" if unique is None else f"{unique:.0f} unique traders"
        organic_row = _check(
            "organic", CHECKS[6][1], "fail",
            f"{shown_unique} in 1h, under {ORGANIC_MIN_TRADERS}",
            organic_source,
        )
    elif wallet_pct is None:
        organic_row = _check(
            "organic", CHECKS[6][1], "unknown",
            f"{unique:.0f} unique traders. Per-wallet share of 1h volume was not returned",
            organic_source,
        )
    elif wallet_pct > ORGANIC_WALLET_MAX_PCT:
        organic_row = _check(
            "organic", CHECKS[6][1], "fail",
            f"one wallet is {wallet_pct:.1f}% of 1h volume",
            organic_source,
        )
    else:
        organic_row = _check(
            "organic", CHECKS[6][1], "pass",
            f"{unique:.0f} unique traders · largest wallet {wallet_pct:.1f}% of 1h volume",
            organic_source,
        )

    narrative_row = _check(
        "narrative", CHECKS[7][1], "unknown",
        "Unknown — no narrative source on this panel",
        "not wired",
    )

    checks = [lp_row, auth_row, dev_row, bundle_row, top_row, social_row, organic_row, narrative_row]
    pass_count = sum(1 for row in checks if row["status"] == "pass")
    fail_count = sum(1 for row in checks if row["status"] == "fail")
    unknown_count = sum(1 for row in checks if row["status"] == "unknown")
    forced = bool(forced_reasons)
    final = verdict_for(pass_count, forced_skip=forced)
    before = count_verdict(pass_count)

    if cluster.get("known") and not cluster.get("bundled"):
        bundle_note = (
            f"Linked cluster {cluster['pct']:.1f}% < {CLUSTER_BUNDLE_AT:.0f}% — not bundled."
        )
    elif cluster.get("bundled"):
        bundle_note = (
            f"Linked cluster {cluster['pct']:.1f}% ≥ {CLUSTER_BUNDLE_AT:.0f}% — bundled. Forced SKIP."
        )
    else:
        bundle_note = "Cluster % is unknown. Untraced holders are not treated as bundled or clean."

    helius_state = "used" if helius_key() else "no_key"
    bubble_state = sources.get("bubblemaps") or "paid_key_required"

    origin_note_bits = []
    if socials["fetched"] and not socials["website"]:
        origin_note_bits.append("website not listed on Pump.fun or Dexscreener")
    elif not socials["fetched"]:
        origin_note_bits.append("socials Unknown")
    if description is None:
        origin_note_bits.append("description Unknown")

    dev_note = "A rug in history forces SKIP."
    if dev_holds is None and creator:
        dev_note = "Dev balance Unknown (creator was not in the returned holder list). Sell history was not traced. " + dev_note

    return {
        "ok": True,
        "mint": mint,
        "verdict": final,
        "count_verdict": before,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "unknown_count": unknown_count,
        "check_count": len(CHECKS),
        "forced_skip": forced,
        "forced_reasons": forced_reasons,
        "rules": rules_payload(),
        "header": {
            "symbol": symbol,
            "name": name,
            "image_url": image_url if isinstance(image_url, str) and image_url.startswith("http") else None,
            "price_usd": price,
            "price_source": price_source,
            "mcap_usd": mcap,
            "mcap_source": mcap_source,
            "age_seconds": age_s,
            "age_text": _age_text(age_s),
            "h1_pct": h1,
            "h1_source": h1_source,
        },
        "origin": {
            "launchpad": launchpad,
            "launchpad_source": launchpad_source,
            "created_at": created,
            "created_source": created_source,
            "bonded_at": bonded,
            "description": description,
            "description_source": description_source,
            "twitter": socials["twitter"],
            "telegram": socials["telegram"],
            "website": socials["website"],
            "social_source": "Pump.fun + Dexscreener" if socials["fetched"] else None,
            "note": " · ".join(origin_note_bits) if origin_note_bits else None,
        },
        "dev": {
            "wallet": creator,
            "wallet_short": _short(creator),
            "wallet_source": "Pump.fun" if creator else None,
            "launched": launched,
            "launched_source": "Pump.fun" if launched is not None else None,
            "rugs": rugs_n,
            "rugs_checked": checked_n if rugs_n is not None else None,
            "best_prior_mcap_usd": best_prior,
            "best_prior_source": best_prior_source if best_prior is not None else "Pump.fun",
            "holds_pct": dev_holds,
            "holds_source": holder_source if dev_holds is not None else None,
            "sold": None,
            "sold_source": "Helius" if helius_key() else None,
            "sold_note": None if helius_key() else "Unknown — sell history needs Helius (no API key in this repo)",
            "note": dev_note,
        },
        "bundle": {
            "top10_pct": holder_pct,
            "top10_source": holder_source,
            "rugcheck_top10_pct": rug_top,
            "rugcheck_within_2pts": (abs(rug_delta) <= 2.0) if rug_delta is not None else None,
            "rugcheck_delta": rug_delta,
            "cluster_pct": cluster.get("pct"),
            "cluster_wallets": cluster.get("wallets"),
            "cluster_known": bool(cluster.get("known")),
            "bundled": bool(cluster.get("bundled")),
            "cluster_source": (trace_rows[0]["source"] if trace_rows else ("Helius" if helius_key() else "public RPC")),
            "untraced": cluster.get("untraced"),
            "holders": _num(rug.get("totalHolders")) if isinstance(rug, dict) else None,
            "holders_source": "RugCheck" if isinstance(rug, dict) and rug.get("totalHolders") is not None else None,
            "snipers": None,
            "snipers_note": "Unknown — first-10s buyers need Helius (no API key in this repo)",
            "bundled_at_launch": None,
            "traces": trace_rows,
            "bubbles": bubbles,
            "note": bundle_note,
            "bubblemaps": bubble_state,
        },
        "checks": checks,
        "helius": helius_state,
        "bubblemaps": bubble_state,
        "cached": False,
        "disclaimer": "Paper research only. Not financial advice. No trades placed.",
    }


def _http_json(url: str, timeout: float = 12.0) -> tuple[int, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": filter_mod.USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return 0, None


def _rpc_urls() -> list[tuple[str, str]]:
    urls = []
    key = helius_key()
    if key:
        urls.append((f"https://mainnet.helius-rpc.com/?api-key={key}", "Helius"))
    for url in PUBLIC_RPCS:
        urls.append((url, "public RPC"))
    return urls


def _rpc(method: str, params: list[Any], timeout: float = 12.0) -> tuple[Any, str | None]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    for url, label in _rpc_urls():
        req = urllib.request.Request(
            url,
            data=body,
            headers={"User-Agent": filter_mod.USER_AGENT, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict) or payload.get("error"):
            continue
        return payload.get("result"), label
    return None, None


def _best_pair(payload: Any) -> dict[str, Any] | None:
    pairs = payload.get("pairs") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and isinstance(payload.get("pairs"), list):
        pairs = payload["pairs"]
    elif isinstance(payload, list):
        pairs = payload
    else:
        return None
    best = None
    best_liq = -1.0
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        if str(pair.get("chainId") or "solana").lower() not in {"solana", ""}:
            continue
        liq = _num((pair.get("liquidity") or {}).get("usd") if isinstance(pair.get("liquidity"), dict) else pair.get("liquidity")) or 0.0
        if best is None or liq > best_liq:
            best = pair
            best_liq = liq
    return best


def fetch_pump(mint: str) -> dict[str, Any]:
    cached = filter_mod.cache_get("pump_coin", mint, CACHE_TTL_S)
    if isinstance(cached, dict):
        return cached
    status, data = _http_json(PUMP_COIN.format(mint=mint))
    if status == 404:
        out = {"missing": True}
    elif isinstance(data, dict) and data.get("mint"):
        out = data
    else:
        out = {"error": True, "status": status}
    if not out.get("error"):
        filter_mod.cache_put("pump_coin", mint, out)
    return out


def fetch_creator_coins(creator: str) -> dict[str, Any]:
    cached = filter_mod.cache_get("pump_creator", creator, CACHE_TTL_S)
    if isinstance(cached, dict):
        return cached
    status, data = _http_json(PUMP_CREATOR.format(creator=creator), timeout=14)
    if isinstance(data, dict) and isinstance(data.get("coins"), list):
        out = data
        filter_mod.cache_put("pump_creator", creator, out)
        return out
    if isinstance(data, list):
        out = {"count": len(data), "coins": data}
        filter_mod.cache_put("pump_creator", creator, out)
        return out
    return {"error": True, "status": status}


def fetch_dex(mint: str) -> dict[str, Any]:
    cached = filter_mod.cache_get("dex_checklist", mint, 60)
    if isinstance(cached, dict):
        return cached
    status, data = _http_json(DEX_TOKEN.format(mint=mint))
    pair = _best_pair(data)
    if not pair:
        return {"error": True, "status": status}
    filter_mod.cache_put("dex_checklist", mint, pair)
    return pair


def fetch_bubblemaps(mint: str) -> str:
    """Bubblemaps map data requires a paid key. One probe, then stop calling."""
    global _bubble_blocked
    if _bubble_blocked:
        return "paid_key_required"
    status, data = _http_json(BUBBLE_MAP.format(mint=mint), timeout=8)
    if status == 200 and isinstance(data, dict) and (data.get("clusters") or data.get("nodes")):
        return "used"
    _bubble_blocked = True
    return "paid_key_required"


def _exclude_owners(pump: dict[str, Any] | None, dex: dict[str, Any] | None) -> set[str]:
    found = set(BURN_OWNERS)
    if isinstance(pump, dict):
        for key in ("bonding_curve", "associated_bonding_curve", "pump_swap_pool", "raydium_pool", "pool_address"):
            value = pump.get(key)
            if isinstance(value, str) and value:
                found.add(value)
    if isinstance(dex, dict):
        for key in ("pairAddress",):
            value = dex.get(key)
            if isinstance(value, str) and value:
                found.add(value)
    return found


def fetch_holders(mint: str, pump: dict[str, Any] | None, dex: dict[str, Any] | None) -> dict[str, Any]:
    cached = filter_mod.cache_get("holders_rpc", mint, CACHE_TTL_S)
    if isinstance(cached, dict):
        return cached
    supply_res, supply_label = _rpc("getTokenSupply", [mint])
    supply = None
    if isinstance(supply_res, dict):
        value = supply_res.get("value") if isinstance(supply_res.get("value"), dict) else {}
        supply = _num(value.get("uiAmountString") if value.get("uiAmountString") is not None else value.get("uiAmount"))
    largest, label = _rpc("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
    accounts = []
    if isinstance(largest, dict):
        accounts = largest.get("value") if isinstance(largest.get("value"), list) else []
    if not accounts or not supply:
        return {"holders": [], "supply": supply, "source": label or supply_label, "error": True}
    addrs = [row.get("address") for row in accounts if isinstance(row, dict) and row.get("address")]
    infos, info_label = _rpc("getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}])
    values = []
    if isinstance(infos, dict):
        values = infos.get("value") if isinstance(infos.get("value"), list) else []
    exclude = _exclude_owners(pump, dex)
    token_accounts = set()
    if isinstance(pump, dict) and isinstance(pump.get("associated_bonding_curve"), str):
        token_accounts.add(pump["associated_bonding_curve"])
    holders = []
    for raw, info in zip(accounts, values):
        if not isinstance(raw, dict):
            continue
        parsed = (((info or {}).get("data") or {}).get("parsed") or {}).get("info") if isinstance(info, dict) else {}
        if not isinstance(parsed, dict):
            parsed = {}
        owner = parsed.get("owner") if isinstance(parsed.get("owner"), str) else None
        amount = _num(raw.get("uiAmountString") if raw.get("uiAmountString") is not None else raw.get("uiAmount"))
        pct = (amount / supply * 100.0) if amount is not None and supply else None
        excluded = (owner in exclude) or (raw.get("address") in token_accounts) or (owner in BURN_OWNERS)
        holders.append({
            "token_account": raw.get("address"),
            "owner": owner,
            "pct": pct,
            "excluded": excluded,
        })
    mint_info, auth_label = _rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    authorities: dict[str, Any] = {"known": False, "source": auth_label or "Solana RPC"}
    if isinstance(mint_info, dict):
        value = mint_info.get("value") if isinstance(mint_info.get("value"), dict) else None
        parsed = (((value or {}).get("data") or {}).get("parsed") or {}).get("info") if value else None
        if isinstance(parsed, dict) and "mintAuthority" in parsed and "freezeAuthority" in parsed:
            authorities = {
                "known": True,
                "mint_authority": parsed.get("mintAuthority") or None,
                "freeze_authority": parsed.get("freezeAuthority") or None,
                "source": auth_label or "Solana RPC",
            }
    out = {
        "holders": holders,
        "supply": supply,
        "source": label or info_label or "Solana RPC",
        "authorities": authorities,
        "error": False,
    }
    filter_mod.cache_put("holders_rpc", mint, out)
    return out


def _funder_from_tx(tx: dict[str, Any] | None, wallet: str) -> str | None:
    if not isinstance(tx, dict):
        return None
    message = ((tx.get("transaction") or {}).get("message") or {})
    meta = tx.get("meta") or {}
    instructions = list(message.get("instructions") or [])
    for inner in meta.get("innerInstructions") or []:
        if isinstance(inner, dict):
            instructions.extend(inner.get("instructions") or [])
    for instruction in instructions:
        if not isinstance(instruction, dict):
            continue
        parsed = instruction.get("parsed")
        if not isinstance(parsed, dict):
            continue
        if parsed.get("type") not in {"transfer", "createAccount", "createAccountWithSeed"}:
            continue
        info = parsed.get("info") if isinstance(parsed.get("info"), dict) else {}
        dest = info.get("destination") or info.get("newAccount")
        source = info.get("source") or info.get("fundingPubkey")
        if dest == wallet and isinstance(source, str) and source and source != wallet:
            return source
    return None


def trace_funder(wallet: str) -> dict[str, Any]:
    cached = filter_mod.cache_get("funder", wallet, FUNDER_TTL_S)
    if isinstance(cached, dict) and cached.get("reason") in {"traced", "long_history", "no_tx"}:
        return cached
    source_label = "Helius" if helius_key() else "public RPC"
    sigs, label = _rpc("getSignaturesForAddress", [wallet, {"limit": 1000}])
    if not isinstance(sigs, list):
        out = {"funder": None, "reason": "rpc_error", "source": source_label}
        filter_mod.cache_put("funder_miss", wallet, out)
        return out
    if label:
        source_label = label
    if not sigs:
        out = {"funder": None, "reason": "no_tx", "source": source_label}
        filter_mod.cache_put("funder", wallet, out)
        return out
    if len(sigs) >= 1000:
        out = {"funder": None, "reason": "long_history", "source": source_label}
        filter_mod.cache_put("funder", wallet, out)
        return out
    oldest = sigs[-1] if isinstance(sigs[-1], dict) else {}
    signature = oldest.get("signature")
    if not signature:
        out = {"funder": None, "reason": "rpc_error", "source": source_label}
        return out
    tx, tx_label = _rpc(
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}],
    )
    if not isinstance(tx, dict):
        return {"funder": None, "reason": "rpc_error", "source": source_label}
    funder = _funder_from_tx(tx, wallet)
    out = {
        "funder": funder,
        "reason": "traced" if funder else "no_funder_in_oldest",
        "source": tx_label or source_label,
    }
    filter_mod.cache_put("funder", wallet, out)
    return out


def gather(mint: str) -> dict[str, Any]:
    """Fetch RugCheck, Pump.fun, Dexscreener, and a public funding trace."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        fut_pump = pool.submit(fetch_pump, mint)
        fut_dex = pool.submit(fetch_dex, mint)
        fut_rug = pool.submit(filter_mod.fetch_rug_report, mint, False)
        fut_bubble = pool.submit(fetch_bubblemaps, mint)
        pump = fut_pump.result()
        dex = fut_dex.result()
        rug = fut_rug.result()
        bubble = fut_bubble.result()

    creator = pump.get("creator") if isinstance(pump, dict) and not pump.get("missing") and not pump.get("error") else None
    creator_coins = fetch_creator_coins(creator) if creator else None
    held = fetch_holders(mint, pump if isinstance(pump, dict) else None, dex if isinstance(dex, dict) else None)
    holders = held.get("holders") if isinstance(held, dict) else []
    ranked = [row for row in holders if isinstance(row, dict) and not row.get("excluded") and row.get("owner")]
    ranked.sort(key=lambda row: _num(row.get("pct")) or 0.0, reverse=True)
    top = ranked[:10]

    def _one(row: dict[str, Any]) -> dict[str, Any]:
        traced = trace_funder(row["owner"])
        return {
            "owner": row.get("owner"),
            "pct": row.get("pct"),
            "funder": traced.get("funder"),
            "reason": traced.get("reason"),
            "source": traced.get("source") or "public RPC",
        }

    traces: list[dict[str, Any]] = []
    if top:
        with ThreadPoolExecutor(max_workers=3) as pool:
            traces = list(pool.map(_one, top))

    prior_rugs = None
    prior_checked = 0
    # Only walk prior coins when RugCheck actually answered for this mint.
    # A dead RugCheck must not turn into a guessed zero.
    if isinstance(rug, dict) and isinstance(creator_coins, dict):
        coins = creator_coins.get("coins") if isinstance(creator_coins.get("coins"), list) else []
        priors = [coin for coin in coins if isinstance(coin, dict) and coin.get("mint") and coin.get("mint") != mint]
        rugs = 0
        for coin in priors[:6]:
            report = filter_mod.fetch_rug_report(str(coin.get("mint")), False)
            if not isinstance(report, dict) or not isinstance(report.get("rugged"), bool):
                prior_rugs = None
                prior_checked = 0
                break
            prior_checked += 1
            if report.get("rugged") is True:
                rugs += 1
        else:
            if len(priors) <= 6:
                prior_rugs = rugs
            elif rugs >= 1:
                prior_rugs = rugs
            else:
                prior_rugs = None

    organic = {"known": False}
    if helius_key():
        organic = {"known": False, "source": "Helius", "note": "key set but 1h wallet volume is not parsed here"}

    return {
        "pump": pump,
        "dex": dex,
        "rug": rug if isinstance(rug, dict) else None,
        "creator_coins": creator_coins,
        "holders": holders,
        "holder_source": (held.get("source") if isinstance(held, dict) else None) or "Solana RPC",
        "supply": held.get("supply") if isinstance(held, dict) else None,
        "authorities": held.get("authorities") if isinstance(held, dict) else {"known": False},
        "traces": traces,
        "prior_rugs": prior_rugs,
        "prior_rugs_checked": prior_checked if prior_rugs is not None else 0,
        "organic": organic,
        "bubblemaps": bubble,
    }


def for_mint(mint: str, bypass: bool = False) -> dict[str, Any]:
    mint = (mint or "").strip()
    if len(mint) < 32 or len(mint) > 48:
        return {"ok": False, "error": "mint required", "mint": mint, "verdict": None}
    now = time.time()
    if not bypass:
        hit = _mem.get(mint)
        if hit and now - hit[0] < CACHE_TTL_S:
            cached = dict(hit[1])
            cached["cached"] = True
            return cached
        disk = filter_mod.cache_get("coin_checklist_" + CACHE_VERSION, mint, CACHE_TTL_S)
        if isinstance(disk, dict) and disk.get("ok") and disk.get("verdict"):
            disk = dict(disk)
            disk["cached"] = True
            _mem[mint] = (now, disk)
            return disk
    payload = assemble(mint, gather(mint), now=now)
    payload["cached"] = False
    _mem[mint] = (time.time(), payload)
    filter_mod.cache_put("coin_checklist_" + CACHE_VERSION, mint, payload)
    return payload


def clear_cache() -> None:
    _mem.clear()
    folder = filter_mod.CACHE_DIR
    if not folder.exists():
        return
    for path in folder.glob("coin_checklist_" + CACHE_VERSION + "_*.json"):
        try:
            path.unlink()
        except OSError:
            pass
