"""Market-cap progress for Filter and paper-trade surfaces.

Ceiling order is binding: bonding, else graduation, else FDV, else ATH.
Each ceiling carries its label. A candidate is a research ceiling only when
it is meaningfully above NOW (more than 2% headroom). FDV ≈ NOW on a fully
circulating coin is not “100% gone.” Missing inputs stay empty. This module
does not fetch, guess, or fill a number that was not supplied.
"""

from __future__ import annotations

from typing import Any

_LABEL = {
    "bonding": "Bonding",
    "graduation": "Bonding",
    "fdv": "FDV",
    "ath": "ATH",
}

_FORMULA = {
    "bonding": "Bonded / stated max × mark. Not a promise of max price.",
    "graduation": (
        "Graduation target — bonding-curve completion value from the source. "
        "Not a promise of max price."
    ),
    "fdv": "FDV @ max supply — mark × stated max supply. Not a promise of max price.",
    "ath": "ATH mcap — highest market cap recorded by the source. Not a promise of max price.",
}

_UNKNOWN = "Unknown — no bonding target, FDV, or ATH mcap. Nothing invented."

# Skip a candidate at or below this multiple of NOW. 1.02 = ~2% headroom.
_UPSIDE = 1.02

_AT_FDV = (
    "FDV ≈ now (fully circulating). No upside ceiling — not 100% gone. "
    "Not a promise of max price."
)
_NO_UPSIDE = (
    "No upside ceiling — stored targets are not meaningfully above now. "
    "Nothing invented."
)

_BONDING_KEYS = ("bonding_mcap_usd", "bondingMarketCap", "bondingMarketCapUsd")
_GRAD_KEYS = ("graduation_mcap_usd", "graduationMarketCap", "graduationMarketCapUsd")
_ATH_KEYS = ("ath_mcap_usd", "athMarketCap", "athMarketCapUsd")


def _positive(value: Any) -> float | None:
    """Finite number strictly above zero. Everything else is missing."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text or text in {"—", "-", "null", "None", "unknown", "Unknown"}:
            return None
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        return None
    return number


def _usd(value: Any) -> float | None:
    number = _positive(value)
    if number is None:
        return None
    return round(number, 2)


def _first(container: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in container and container.get(key) is not None:
            number = _usd(container.get(key))
            if number is not None:
                return number
    return None


def _meaningfully_above(ceiling: float | None, now: float | None) -> bool:
    """True when ceiling is a real upside vs NOW.

    Missing NOW does not reject a positive ceiling — percent stays empty.
    """
    if ceiling is None or ceiling <= 0:
        return False
    if now is None:
        return True
    return ceiling > now * _UPSIDE


def _approx_now(value: float | None, now: float | None) -> bool:
    """True when value sits in the no-upside band around NOW."""
    if value is None or now is None or now <= 0 or value <= 0:
        return False
    low = now / _UPSIDE
    high = now * _UPSIDE
    return low <= value <= high


def build_mcap_progress(
    *,
    market_cap_usd: Any = None,
    fdv_usd: Any = None,
    bonding_mcap_usd: Any = None,
    graduation_mcap_usd: Any = None,
    ath_mcap_usd: Any = None,
    market_cap_from_fdv: bool = False,
    now_source: str | None = None,
) -> dict[str, Any]:
    """One progress record. Percent gone is now / ceiling, only when both exist."""
    now = _usd(market_cap_usd)
    fdv = _usd(fdv_usd)
    bonding = _usd(bonding_mcap_usd)
    graduation = _usd(graduation_mcap_usd)
    ath = _usd(ath_mcap_usd)
    from_fdv = bool(market_cap_from_fdv)

    # Source order: bonding, then graduation, then FDV (when it is not the
    # same number copied into NOW), then ATH. Skip any candidate with no
    # meaningful upside and try the next source. Bonding still wins when it
    # itself clears the gate.
    candidates: list[tuple[str, float]] = []
    if bonding is not None:
        candidates.append(("bonding", bonding))
    if graduation is not None:
        candidates.append(("graduation", graduation))
    if fdv is not None and not from_fdv:
        candidates.append(("fdv", fdv))
    if ath is not None:
        candidates.append(("ath", ath))

    kind: str | None = None
    ceiling: float | None = None
    for candidate_kind, candidate in candidates:
        if _meaningfully_above(candidate, now):
            kind, ceiling = candidate_kind, candidate
            break

    if kind:
        ceiling_empty = None
        formula = _FORMULA[kind]
    elif _approx_now(fdv, now):
        # Fully circulating (or NOW taken from FDV). Keep NOW; do not paint
        # the bar as max-hit.
        ceiling_empty = "at_fdv"
        formula = _AT_FDV
    elif candidates:
        ceiling_empty = "no_upside"
        formula = _NO_UPSIDE
    else:
        ceiling_empty = "unknown"
        formula = _UNKNOWN

    pct: float | None = None
    bar: float | None = None
    if now is not None and ceiling is not None and ceiling > 0:
        pct = round((now / ceiling) * 100.0, 1)
        bar = round(min(100.0, max(0.0, pct)), 1)

    source = now_source if now_source in {"dex", "snapshot"} else None
    return {
        "market_cap_usd": now,
        "fdv_usd": fdv,
        "bonding_mcap_usd": bonding,
        "graduation_mcap_usd": graduation,
        "ath_mcap_usd": ath,
        "market_cap_from_fdv": from_fdv,
        "ceiling_usd": ceiling,
        "ceiling_kind": kind,
        "ceiling_label": _LABEL.get(kind) if kind else None,
        "ceiling_empty": ceiling_empty,
        "pct_gone": pct,
        "bar_pct": bar,
        "formula": formula,
        "now_source": source,
        "now_available": now is not None,
        "ceiling_available": ceiling is not None,
        "progress_available": pct is not None,
    }


def progress_from_pair(pair: dict[str, Any] | None, *, now_source: str | None = "dex") -> dict[str, Any]:
    """Read a DEX-style pair. marketCap is current; fdv is a separate ceiling input."""
    if not isinstance(pair, dict):
        return build_mcap_progress(now_source=now_source)
    market = _usd(pair.get("marketCap"))
    fdv = _usd(pair.get("fdv"))
    now = market if market is not None else fdv
    return build_mcap_progress(
        market_cap_usd=now,
        fdv_usd=fdv,
        bonding_mcap_usd=_first(pair, _BONDING_KEYS),
        graduation_mcap_usd=_first(pair, _GRAD_KEYS),
        ath_mcap_usd=_first(pair, _ATH_KEYS),
        market_cap_from_fdv=market is None and fdv is not None,
        now_source=now_source,
    )


def progress_from_row(row: dict[str, Any] | None, *, now_source: str | None = "snapshot") -> dict[str, Any]:
    """Read fields already stored on a filter row or quote. Does not invent the rest."""
    if not isinstance(row, dict):
        return build_mcap_progress(now_source=now_source)
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in metrics and metrics.get(key) is not None:
                return metrics.get(key)
            if key in row and row.get(key) is not None:
                return row.get(key)
        return None

    from_fdv = bool(metrics.get("market_cap_from_fdv") or row.get("market_cap_from_fdv"))
    return build_mcap_progress(
        market_cap_usd=pick("market_cap_usd"),
        fdv_usd=pick("fdv_usd"),
        bonding_mcap_usd=pick("bonding_mcap_usd"),
        graduation_mcap_usd=pick("graduation_mcap_usd"),
        ath_mcap_usd=pick("ath_mcap_usd"),
        market_cap_from_fdv=from_fdv,
        now_source=now_source,
    )


def apply_mcap(row: dict[str, Any] | None, pair: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy a row and attach mcap_progress.

    A live pair is used only when it actually carries a market cap or a ceiling.
    Otherwise the stored row is used as-is. flagged.json is not written here.
    """
    base = row if isinstance(row, dict) else {}
    out = dict(base)
    metrics = dict(out.get("metrics") or {}) if isinstance(out.get("metrics"), dict) else {}
    prog = progress_from_row(out, now_source="snapshot")
    if isinstance(pair, dict):
        live = progress_from_pair(pair, now_source="dex")
        if live["now_available"] or live["ceiling_available"]:
            prog = live
            metrics["market_cap_usd"] = prog["market_cap_usd"]
            metrics["fdv_usd"] = prog["fdv_usd"]
            metrics["bonding_mcap_usd"] = prog["bonding_mcap_usd"]
            metrics["graduation_mcap_usd"] = prog["graduation_mcap_usd"]
            metrics["ath_mcap_usd"] = prog["ath_mcap_usd"]
            metrics["market_cap_from_fdv"] = prog["market_cap_from_fdv"]
            out["metrics"] = metrics
    out["mcap_progress"] = prog
    return out
