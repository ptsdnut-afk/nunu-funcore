"""Gold Scar promotion — stricter than the owner 7-gate stack (2026-10-27).

Paper research only. A gold row is a rare stand-out, not a buy and not a
promise. False gold is worse than an empty lane, so every gate fails closed
when the evidence is missing. A high Token Flow score does not promote.

Owner stack (never loosened here):
  1. Filter score/12 — under ~10 usually skip
  2. Clone / copycat pack (~5 same hour / same name family) = no
  3. Narrative not crowded Elon / Trump / politics junk
  4. Tape — not a late chase, not already +300% in an hour, not a 5m dump
  5. On-chain dig clean (snipers, bundles, fake LP lock)
  6. Overheard / Twitter is real people, not bot promo
  7. Big-picture veto — Fed day or Solana broken means no gold ping

Stricter cuts this module applies on top of that stack:
  1. score/12 must be at least 11.0, with liquidity, real 1h volume, buy
     pressure, LP lock, a RugCheck so mint/freeze can be seen, and a
     top-holder percent so "not one wallet" is evidence rather than a hope.
  2. Three coins in one name family already kill the whole pack (owner ~5).
  3. Crowded politics stems fail, including years glued on (TRUMP2028)
     and a stem glued inside a compound ticker (elon inside GTAELON).
     Short accidents (melon, advance) stay off the list.
  4. A 1h move of +180% fails (owner line is +300). A 24h move of +250%
     fails. Age must be under 2h. The 5m print is required.
  5. RugCheck score at most 8, zero listed risks, top holder at most 12%,
     and a top-10 sum at most 40% so a bundle cannot hide.
  6. Only overheard posts handed in by the caller count. A profile URL is
     not "real people". The live server context passes no posts, so this
     gate stays shut until a real sample exists.
  7. Macro clears only when a clearance file explicitly says Fed-quiet and
     Solana-ok. A missing file is a veto.

Rows cannot stamp themselves gold. ``annotate`` recomputes every verdict.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PAPER = "Paper research only. Not a buy and not a promise."
STRICTNESS = "stricter-than-owner-2026-10-27"
WHY_GOLD = (
    "Cleared a stricter 7-gate stack than a high score: filter basics held, "
    "it was not in a copy pack, the name was not crowded politics, "
    "tape was not a late chase or a dump, the on-chain dig was clean, "
    "the overheard sample was not bot promo, and the big-picture veto was clear. "
    + PAPER
)
FIXTURE_WHY = (
    "Synthetic stand-in so the rare lane can be seen while live gold is empty. "
    "Score, copycat, narrative, tape, on-chain, overheard, and macro are marked "
    "clear for this fixture only. Not a live coin. " + PAPER
)

# Owner: under ~10 skip. This ship requires 11.0.
MIN_SCORE_12 = 11.0
MIN_LIQUIDITY_USD = 30_000.0
MIN_VOLUME_H1_USD = 20_000.0
MIN_BUY_SELL = 1.2
MIN_LP_LOCKED_PCT = 95.0
MAX_TOP1_PCT = 12.0
MAX_TOP10_PCT = 40.0
MAX_RUG_SCORE = 8.0
# Owner pack is ~5. Three near-copies already fail.
CLONE_PACK_MIN = 3
# Owner late line is +300% in an hour. 180 fails here.
MAX_H1_PCT = 180.0
MIN_H1_PCT = -8.0
MAX_H24_PCT = 250.0
MAX_M5_PCT = 60.0
MIN_M5_PCT = -8.0
MIN_AGE_H = 0.15
MAX_AGE_H = 2.0

GATE_ORDER: tuple[tuple[str, str], ...] = (
    ("filter", "Filter score/12"),
    ("clone", "Copycat pack"),
    ("narrative", "Narrative"),
    ("tape", "Tape timing"),
    ("onchain", "On-chain dig"),
    ("overheard", "Overheard"),
    ("macro", "Big-picture veto"),
)

MACRO_PATH = Path(__file__).resolve().parent / "gold_macro.json"

_SUFFIXES = ("coin", "token", "inu", "solana", "sol", "pump")
_CROWDED = (
    "elon",
    "musk",
    "trump",
    "maga",
    "biden",
    "harris",
    "kamala",
    "potus",
    "election",
    "democrat",
    "republican",
    "obama",
    "vance",
    "desantis",
    "rfk",
    "politics",
    "political",
)
_PROMO = re.compile(
    r"(will moon|to the moon|\bmoon\b|100x|guaranteed|buy now|airdrop|"
    r"don'?t miss|\bdm me\b|t\.me/|\bpromo\b|\baped\b|\bape in\b|"
    r"\bgem\b|paid shill)",
    re.I,
)
_DIRTY = (
    "sniper",
    "bundle",
    "bundled",
    "insider",
    "mint",
    "freeze",
    "honeypot",
    "fake",
    "unlocked",
)


def _f(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _metric(row: dict[str, Any], key: str) -> float | None:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    if key in metrics and metrics.get(key) is not None:
        return _f(metrics.get(key))
    return _f(row.get(key))


def _holders(row: dict[str, Any]) -> dict[str, Any]:
    holders = row.get("holders") if isinstance(row.get("holders"), dict) else {}
    return holders


def _research_holders(row: dict[str, Any]) -> dict[str, Any]:
    research = row.get("research") if isinstance(row.get("research"), dict) else {}
    block = research.get("holders") if isinstance(research.get("holders"), dict) else {}
    return block


def _holder_num(row: dict[str, Any], key: str) -> float | None:
    holders = _holders(row)
    if holders.get(key) is not None:
        return _f(holders.get(key))
    return _f(_research_holders(row).get(key))


def _lp(row: dict[str, Any]) -> float | None:
    direct = _metric(row, "lp_locked_pct")
    if direct is not None:
        return direct
    return _f(_holders(row).get("lp_locked_pct"))


def _texts(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("ticker") or ""),
        str(row.get("name") or ""),
        str(row.get("description") or ""),
    ]
    return " ".join(parts)


def family_key(row: dict[str, Any]) -> str:
    """Collapse ticker/name so TRUMP, TRUMP2028, and TrumpCoin share a family."""
    raw = str(row.get("ticker") or "").strip() or str(row.get("name") or "").strip()
    raw = raw.lower()
    raw = re.sub(r"(19|20)\d{2}", "", raw)
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    changed = True
    while changed and raw:
        changed = False
        for suffix in _SUFFIXES:
            if raw.endswith(suffix) and len(raw) - len(suffix) >= 3:
                raw = raw[: -len(suffix)]
                changed = True
                break
    raw = re.sub(r"\d+$", "", raw)
    return raw


def _stem_inside_compound(token: str, stem: str) -> bool:
    """Stem glued inside a ticker, past a real prefix.

    Prefix matches stay on the caller (`elon…`, `trump…`). An interior hit
    needs at least three characters in front of a stem of length >= 4:
    `gtaelon` matches `elon` (index 3). `melon` (index 1) and `advance`
    containing `vance` (index 2) do not. This only adds hits.
    """
    if len(stem) < 4 or len(token) <= len(stem):
        return False
    start = 1
    while True:
        idx = token.find(stem, start)
        if idx < 0:
            return False
        if idx >= 3:
            return True
        start = idx + 1


def _crowded_hit(row: dict[str, Any]) -> str | None:
    folded = re.sub(r"[^a-z0-9]+", " ", _texts(row).lower())
    tokens = [tok for tok in folded.split() if tok]
    for token in tokens:
        if token in {"2024", "2028"}:
            return token
        for stem in _CROWDED:
            if token == stem or token.startswith(stem) or _stem_inside_compound(token, stem):
                return stem
    return None


def _risk_lines(row: dict[str, Any]) -> list[str]:
    holders = _holders(row)
    risks = holders.get("risks") if isinstance(holders.get("risks"), list) else []
    red = row.get("red_flags") if isinstance(row.get("red_flags"), list) else []
    return [str(item) for item in list(risks) + list(red)]


def _dirty_hit(row: dict[str, Any]) -> str | None:
    for line in _risk_lines(row):
        low = line.lower()
        for word in _DIRTY:
            if word in low:
                return word
    return None


def _gate(gid: str, title: str, ok: bool, reason: str) -> dict[str, Any]:
    number = next(i for i, pair in enumerate(GATE_ORDER, start=1) if pair[0] == gid)
    return {"id": gid, "n": number, "title": title, "pass": bool(ok), "reason": reason}


def _gate_filter(row: dict[str, Any]) -> dict[str, Any]:
    title = "Filter score/12"
    misses: list[str] = []
    score_12 = _f(row.get("score_12"))
    if score_12 is None:
        misses.append("score/12 missing")
    elif score_12 < MIN_SCORE_12:
        misses.append(f"score/12 {score_12:.1f} is under {MIN_SCORE_12:.0f} (stricter than ~10)")
    if row.get("flagged") is not True:
        misses.append("not a flagged row")
    hard = row.get("hard_fails")
    if not isinstance(hard, list) or hard:
        misses.append("hard fails present or unknown")
    liq = _metric(row, "liquidity_usd")
    if liq is None or liq < MIN_LIQUIDITY_USD:
        misses.append("pool money under the gold floor or missing")
    vol = _metric(row, "volume_h1_usd")
    if vol is None or vol < MIN_VOLUME_H1_USD:
        misses.append("1h volume under the gold floor or missing")
    buys = _metric(row, "buys_h1")
    sells = _metric(row, "sells_h1")
    ratio = _metric(row, "buy_sell_ratio_h1")
    if ratio is None:
        ratio = _metric(row, "buy_sell_ratio")
    if buys is None or sells is None or sells < 1 or ratio is None or ratio < MIN_BUY_SELL:
        misses.append("buys vs sells not clearly buy-heavy")
    lp = _lp(row)
    if lp is None or lp < MIN_LP_LOCKED_PCT:
        misses.append("LP lock under 95% or missing")
    holders = _holders(row)
    risks = holders.get("risks")
    if holders.get("rugcheck_available") is not True:
        misses.append("mint/freeze not checked — RugCheck missing")
    elif not isinstance(risks, list):
        misses.append("mint/freeze not checked — risk list missing")
    elif any(("mint" in str(item).lower() or "freeze" in str(item).lower()) for item in risks):
        misses.append("mint or freeze authority flagged")
    top1 = _holder_num(row, "top1_pct")
    if top1 is None:
        misses.append("not one wallet is unproven — no top-holder percent")
    elif top1 > MAX_TOP1_PCT:
        misses.append(f"top holder {top1:.1f}% looks like one wallet")
    if misses:
        return _gate("filter", title, False, "; ".join(misses))
    return _gate(
        "filter",
        title,
        True,
        f"score/12 {score_12:.1f} with pool, volume, buy pressure, LP lock, and a spread top holder",
    )


def _gate_clone(row: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    title = "Copycat pack"
    key = family_key(row)
    if not key:
        return _gate("clone", title, False, "no name family to compare — copycat check failed closed")
    count = int(counts.get(key) or 0)
    if count >= CLONE_PACK_MIN:
        return _gate(
            "clone",
            title,
            False,
            f"{count} coins share the {key} family this pass (bar is {CLONE_PACK_MIN}, stricter than ~5)",
        )
    if count < 1:
        return _gate("clone", title, False, "name family was not in the cohort — copycat check failed closed")
    return _gate("clone", title, True, f"name family {key} is alone enough ({count} < {CLONE_PACK_MIN})")


def _gate_narrative(row: dict[str, Any]) -> dict[str, Any]:
    title = "Narrative"
    if not str(row.get("ticker") or "").strip() and not str(row.get("name") or "").strip():
        return _gate("narrative", title, False, "no name — crowded-narrative check failed closed")
    hit = _crowded_hit(row)
    if hit:
        return _gate("narrative", title, False, f"crowded narrative marker '{hit}' (Elon/Trump/politics flood)")
    return _gate(
        "narrative",
        title,
        True,
        "no crowded Elon/Trump/politics marker in the name. Not a claim the joke is new.",
    )


def _gate_tape(row: dict[str, Any]) -> dict[str, Any]:
    title = "Tape timing"
    misses: list[str] = []
    red = row.get("red_flags") if isinstance(row.get("red_flags"), list) else []
    if row.get("late_chase") is True or "late_chase" in red:
        misses.append("late chase flag")
    age = _metric(row, "pair_age_hours")
    if age is None or age < MIN_AGE_H or age >= MAX_AGE_H:
        misses.append("age missing or outside the early window (under 2h)")
    h1 = _metric(row, "price_change_h1")
    if h1 is None or h1 >= MAX_H1_PCT or h1 <= MIN_H1_PCT:
        misses.append(f"1h move missing or outside {-MIN_H1_PCT:.0f}% to +{MAX_H1_PCT:.0f}% (stricter than +300)")
    h24 = _metric(row, "price_change_h24")
    if h24 is not None and h24 >= MAX_H24_PCT:
        misses.append(f"24h move {h24:.0f}% already ran")
    m5 = _metric(row, "price_change_m5")
    if m5 is None:
        misses.append("no 5m print — dump check failed closed")
    elif m5 >= MAX_M5_PCT or m5 <= MIN_M5_PCT:
        misses.append("5m print is a chase or a dump")
    if misses:
        return _gate("tape", title, False, "; ".join(misses))
    return _gate("tape", title, True, "age, 1h, and 5m prints are inside the early tape window")


def _gate_onchain(row: dict[str, Any]) -> dict[str, Any]:
    title = "On-chain dig"
    misses: list[str] = []
    holders = _holders(row)
    if holders.get("rugcheck_available") is not True:
        misses.append("no RugCheck dig on the snapshot")
    score_n = _f(holders.get("score_normalised"))
    if score_n is None or score_n > MAX_RUG_SCORE:
        misses.append(f"RugCheck score missing or above {MAX_RUG_SCORE:.0f}")
    risks = holders.get("risks") if isinstance(holders.get("risks"), list) else None
    risk_count = _f(holders.get("risk_count"))
    if risks is None:
        misses.append("risk list missing")
    elif risks:
        misses.append("listed on-chain risks")
    if risk_count != 0.0:
        misses.append("risk count is not zero")
    dirty = _dirty_hit(row)
    if dirty:
        misses.append(f"dirty dig marker '{dirty}'")
    lp = _lp(row)
    if lp is None or lp < MIN_LP_LOCKED_PCT:
        misses.append("LP lock missing or too weak to trust")
    top1 = _holder_num(row, "top1_pct")
    if top1 is None or top1 > MAX_TOP1_PCT:
        misses.append("sniper/wallet concentration unproven or too high")
    top10 = _holder_num(row, "top10_pct")
    if top10 is None or top10 > MAX_TOP10_PCT:
        misses.append("bundle check needs a top-10 sum at or under 40%")
    if misses:
        return _gate("onchain", title, False, "; ".join(misses))
    return _gate("onchain", title, True, "RugCheck clean, LP locked, top holder and top-10 inside the dig limits")


def _norm_post(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _gate_overheard(row: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    title = "Overheard"
    ticker = str(row.get("ticker") or "").strip()
    if len(ticker) < 4:
        return _gate("overheard", title, False, "ticker too short to match overheard without false hits")
    posts = context.get("overheard_posts") if isinstance(context.get("overheard_posts"), list) else []
    if not posts:
        return _gate(
            "overheard",
            title,
            False,
            "no overheard sample — a profile link is not real people talking",
        )
    pattern = re.compile(rf"\b{re.escape(ticker)}\b", re.I)
    kept: list[tuple[str, str]] = []
    seen_text: set[str] = set()
    for post in posts:
        if not isinstance(post, dict):
            continue
        user = str(post.get("username") or "").strip().lower()
        text = str(post.get("text") or post.get("title") or "").strip()
        if not user or not text:
            continue
        if not pattern.search(text):
            continue
        if _PROMO.search(text):
            return _gate("overheard", title, False, "overheard sample includes bot or paid promo language")
        folded = _norm_post(text)
        if not folded or folded in seen_text:
            return _gate("overheard", title, False, "overheard sample repeats the same text")
        seen_text.add(folded)
        kept.append((user, text))
    users = {user for user, _text in kept}
    if len(kept) < 2 or len(users) < 2:
        return _gate(
            "overheard",
            title,
            False,
            "overheard sample needs two different people naming the ticker, without promo language",
        )
    return _gate(
        "overheard",
        title,
        True,
        "two different people named the ticker and the sample was not promo copy",
    )


def _gate_macro(context: dict[str, Any]) -> dict[str, Any]:
    title = "Big-picture veto"
    macro = context.get("macro") if isinstance(context.get("macro"), dict) else None
    if not isinstance(macro, dict):
        return _gate("macro", title, False, "big-picture veto holds — macro clearance missing")
    reason = macro.get("reason") if isinstance(macro.get("reason"), str) else ""
    if macro.get("clear") is True and macro.get("solana_ok") is True and macro.get("fed_quiet") is True:
        if not reason.strip():
            return _gate("macro", title, False, "big-picture veto holds — clearance had no reason")
        return _gate("macro", title, True, "macro clearance on file: " + reason.strip()[:180])
    detail = reason.strip() or "Fed quiet and Solana ok are not both on file"
    return _gate("macro", title, False, "big-picture veto holds — " + detail[:180])


def read_macro(path: Path | None = None) -> dict[str, Any]:
    """Default is a veto. A clearance file must opt in explicitly."""
    path = path or MACRO_PATH
    veto = {
        "clear": False,
        "solana_ok": False,
        "fed_quiet": False,
        "reason": "no Fed-quiet and Solana-ok clearance on file",
    }
    if not path.exists():
        return veto
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        veto["reason"] = "macro file unreadable — veto holds"
        return veto
    if not isinstance(data, dict):
        veto["reason"] = "macro file unreadable — veto holds"
        return veto
    reason = data.get("reason") if isinstance(data.get("reason"), str) and data.get("reason").strip() else veto["reason"]
    return {
        "clear": data.get("clear") is True,
        "solana_ok": data.get("solana_ok") is True,
        "fed_quiet": data.get("fed_quiet") is True,
        "reason": reason,
    }


def live_context() -> dict[str, Any]:
    """What the panel uses. No overheard posts are invented from profile URLs."""
    return {"macro": read_macro(), "overheard_posts": []}


def evaluate(row: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """One row. ``context`` carries cohort family counts, macro, and overheard posts."""
    ctx = context if isinstance(context, dict) else live_context()
    counts = ctx.get("family_counts") if isinstance(ctx.get("family_counts"), dict) else {}
    gates = [
        _gate_filter(row),
        _gate_clone(row, counts),
        _gate_narrative(row),
        _gate_tape(row),
        _gate_onchain(row),
        _gate_overheard(row, ctx),
        _gate_macro(ctx),
    ]
    blocked = [gate["id"] for gate in gates if not gate["pass"]]
    gold = not blocked and row.get("qa_max_fixture") is not True
    if row.get("qa_max_fixture") is True and not blocked:
        blocked = ["qa_max_fixture"]
    elif row.get("qa_max_fixture") is True:
        gold = False
    return {
        "gold": gold,
        "tier": "rare" if gold else None,
        "fixture": False,
        "strictness": STRICTNESS,
        "why": WHY_GOLD if gold else "",
        "paper": PAPER,
        "gates": gates,
        "blocked_by": blocked,
    }


def fixture_verdict() -> dict[str, Any]:
    """Labeled synthetic clearance. Not produced by ``evaluate``."""
    gates = []
    for index, (gid, title) in enumerate(GATE_ORDER, start=1):
        gates.append(
            {
                "id": gid,
                "n": index,
                "title": title,
                "pass": True,
                "reason": "QA fixture — synthetic clearance for chrome and sound. Not a live coin.",
            }
        )
    return {
        "gold": True,
        "tier": "rare",
        "fixture": True,
        "strictness": STRICTNESS,
        "why": FIXTURE_WHY,
        "paper": PAPER,
        "gates": gates,
        "blocked_by": [],
    }


def _family_counts(rows: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("qa_gold_fixture") is True:
            continue
        key = family_key(row)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def annotate(rows: list[Any] | None, context: dict[str, Any] | None = None) -> list[Any]:
    """Recompute gold on a copy of each row. Disk stamps are ignored."""
    source = list(rows) if isinstance(rows, list) else []
    base = dict(context) if isinstance(context, dict) else live_context()
    if "macro" not in base:
        base["macro"] = read_macro()
    if "overheard_posts" not in base:
        base["overheard_posts"] = []
    counts = _family_counts(source)
    base["family_counts"] = counts
    out: list[Any] = []
    for row in source:
        if not isinstance(row, dict):
            out.append(row)
            continue
        cloned = {key: value for key, value in row.items() if key != "gold_scar"}
        cloned.pop("gold", None)
        # A fixture stamp in flagged.json is not a promotion. Re-check the row.
        cloned.pop("qa_gold_fixture", None)
        verdict = evaluate(cloned, base)
        cloned["gold_scar"] = verdict
        out.append(cloned)
    return out
