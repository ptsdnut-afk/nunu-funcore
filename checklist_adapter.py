"""Read the SHIP X coin checklist without requiring that branch.

The checklist lives on cursor/ship-x-coin-checklist-93ad and may be absent.
This adapter returns verdict ``unknown`` when the module is missing, the
read fails, or the payload has no BUY-WATCH / WATCH / SKIP verdict.
Unknown is not a pass. LEDGER treats unknown as not eligible.
"""

from __future__ import annotations

from typing import Any

_VERDICTS = {"BUY-WATCH", "WATCH", "SKIP"}


def _unknown(mint: str, source: str) -> dict[str, Any]:
    return {
        "ok": False,
        "mint": mint or "",
        "verdict": "unknown",
        "pass_count": None,
        "check_count": 8,
        "source": source,
    }


def checklist_for_mint(mint: str) -> dict[str, Any]:
    """Checklist card for one mint. ``verdict`` is ``unknown`` when X is absent."""
    mint = (mint or "").strip()
    try:
        import coin_checklist
    except ImportError:
        return _unknown(mint, "missing")
    fn = getattr(coin_checklist, "for_mint", None)
    if not callable(fn):
        return _unknown(mint, "missing")
    try:
        payload = fn(mint)
    except Exception:
        return _unknown(mint, "error")
    if not isinstance(payload, dict):
        return _unknown(mint, "error")
    verdict = payload.get("verdict")
    if verdict not in _VERDICTS:
        verdict = "unknown"
    passes = payload.get("pass_count")
    if not isinstance(passes, int):
        passes = None
    try:
        checks = int(payload.get("check_count")) if payload.get("check_count") is not None else 8
    except (TypeError, ValueError):
        checks = 8
    if checks <= 0:
        checks = 8
    return {
        "ok": verdict != "unknown" and passes is not None,
        "mint": mint,
        "verdict": verdict,
        "pass_count": passes,
        "check_count": checks,
        "source": "coin_checklist",
    }
