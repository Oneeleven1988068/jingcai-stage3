#!/usr/bin/env python3
"""Prematch fade gate v1 — Sporttery price first, then falsify it."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

VERDICT_LONG, VERDICT_FLAT, VERDICT_SHORT = "LONG", "FLAT", "SHORT"
SHORT_SELECTION_FAV_NOT_WIN = "FAVORITE_NOT_WIN"
SHORT_SELECTION_DOG_WIN = "UNDERDOG_WIN"

@dataclass
class Fact:
    key: str
    value: Any
    source: str
    captured_at: str
    evidence_state: str = "OBSERVED"

@dataclass
class PrematchBundle:
    match_num: str
    home: str
    away: str
    had_h: float | None
    had_d: float | None
    had_a: float | None
    hhad_line: float | None
    hhad_h: float | None
    hhad_d: float | None
    hhad_a: float | None
    facts: list[Fact] = field(default_factory=list)

def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None

def implied_had(h, d, a):
    h, d, a = _f(h), _f(d), _f(a)
    if not h or not d or not a or h <= 1 or d <= 1 or a <= 1:
        return {"ok": False, "reason": "HAD_MISSING"}
    raw = {"H": 1.0 / h, "D": 1.0 / d, "A": 1.0 / a}
    s = sum(raw.values())
    p = {k: v / s for k, v in raw.items()}
    fav = max(p, key=p.get)
    return {"ok": True, "overround": s, "p": p, "favorite": fav, "favorite_p": p[fav],
            "favorite_odds": {"H": h, "D": d, "A": a}[fav],
            "dog": "A" if fav == "H" else ("H" if fav == "A" else None)}

def hhad_max_bucket(line, h, d, a):
    h, d, a = _f(h), _f(d), _f(a)
    if not h or not d or not a:
        return {"ok": False}
    raw = {"H": 1.0 / h, "D": 1.0 / d, "A": 1.0 / a}
    s = sum(raw.values())
    p = {k: v / s for k, v in raw.items()}
    return {"ok": True, "line": _f(line), "p": p, "max": max(p, key=p.get), "max_p": max(p.values())}

def _observed(fm, key):
    f = fm.get(key)
    if f is None or f.evidence_state not in ("OBSERVED", "DERIVED"):
        return None
    return f

def score_signals(bundle, board):
    fm = {f.key: f for f in bundle.facts}
    signals = []
    europe = _observed(fm, "europe_within_hours")
    if europe and isinstance(europe.value, (int, float)) and 0 < float(europe.value) <= 120:
        signals.append({"id": "EUROPE_CLOCK", "weight": 2 if float(europe.value) <= 84 else 1,
                        "detail": f"favorite europe in {europe.value}h", "source": europe.source})
    spine = _observed(fm, "favorite_spine_out")
    if spine and isinstance(spine.value, (int, float)) and int(spine.value) >= 2:
        signals.append({"id": "SPINE_HOLED", "weight": 2 if int(spine.value) >= 3 else 1,
                        "detail": f"spine out {int(spine.value)}", "source": spine.source})
    inv = _observed(fm, "table_inversion")
    if inv and inv.value is True:
        signals.append({"id": "TABLE_INVERSION", "weight": 2, "detail": "dog form better", "source": inv.source})
    h2h = _observed(fm, "favorite_h2h_unwon")
    if h2h and isinstance(h2h.value, (int, float)) and int(h2h.value) >= 4:
        signals.append({"id": "H2H_TRAP", "weight": 1, "detail": f"unwon last {int(h2h.value)}", "source": h2h.source})
    ident = _observed(fm, "fixture_identity")
    if ident and ident.value in ("home_trap", "derby", "post_humiliation", "promoted_home"):
        signals.append({"id": "FIXTURE_IDENTITY", "weight": 1, "detail": str(ident.value), "source": ident.source})
    short = board.get("favorite_odds")
    if short is not None and short <= 1.40 and signals:
        signals.append({"id": "SHORT_PRICE_NO_DISCOUNT", "weight": 1,
                        "detail": f"odds {short:.2f} ignored facts", "source": "sporttery_had"})
    water = _observed(fm, "external_overheat")
    if water and water.value is True:
        signals.append({"id": "EXTERNAL_OVERHEAT", "weight": 1, "detail": "sporttery hotter than external", "source": water.source})
    dirty = _observed(fm, "dirty_board")
    if dirty and dirty.value is True:
        signals.append({"id": "DIRTY_BOARD", "weight": 1, "detail": "HHAD max vs HAD favorite", "source": dirty.source})
    dog = _observed(fm, "underdog_proven")
    if dog and dog.value is True:
        signals.append({"id": "DOG_PROVEN", "weight": 1, "detail": "dog already beat a peer", "source": dog.source})
    return {"signals": signals, "score": sum(s["weight"] for s in signals), "ids": [s["id"] for s in signals]}

def decide(bundle):
    board = implied_had(bundle.had_h, bundle.had_d, bundle.had_a)
    hhad = hhad_max_bucket(bundle.hhad_line, bundle.hhad_h, bundle.hhad_d, bundle.hhad_a)
    aligned = None
    if board.get("ok") and hhad.get("ok") and bundle.hhad_line is not None:
        fav, mx, line = board["favorite"], hhad["max"], hhad["line"]
        if line is not None and line < 0:
            aligned = (fav == "H" and mx == "H") or (fav == "A" and mx == "A")
        elif line is not None and line > 0:
            aligned = (fav == "A" and mx == "A") or (fav == "H" and mx == "H")
        else:
            aligned = fav == mx
    if board.get("ok") and aligned is False:
        bundle.facts.append(Fact("dirty_board", True, "sporttery_hhad_vs_had", "derived", "DERIVED"))
    scored = score_signals(bundle, board if board.get("ok") else {})
    score, ids = scored["score"], set(scored["ids"])
    verdict, short_sel, note = VERDICT_LONG, None, "board not falsified"
    if not board.get("ok"):
        verdict, note = VERDICT_FLAT, "HAD missing"
    elif score >= 4:
        verdict, short_sel, note = VERDICT_SHORT, SHORT_SELECTION_FAV_NOT_WIN, "fade favorite"
        if score >= 6 and ("TABLE_INVERSION" in ids or "DOG_PROVEN" in ids):
            short_sel = SHORT_SELECTION_DOG_WIN
    elif score >= 1:
        verdict, note = VERDICT_FLAT, "signal present, drop from long parlay"
    fav = board.get("favorite")
    if verdict == VERDICT_LONG and fav:
        construction = {"side": "FOLLOW_FAVORITE", "had": fav, "parlay_ok": True}
    elif verdict == VERDICT_FLAT:
        construction = {"side": "NONE", "had": None, "parlay_ok": False}
    else:
        if fav == "H":
            fade_had = ["D", "A"] if short_sel == SHORT_SELECTION_FAV_NOT_WIN else ["A"]
        elif fav == "A":
            fade_had = ["H", "D"] if short_sel == SHORT_SELECTION_FAV_NOT_WIN else ["H"]
        else:
            fade_had = []
        construction = {"side": "FADE_FAVORITE", "had": fade_had, "selection": short_sel,
                        "parlay_ok": short_sel == SHORT_SELECTION_FAV_NOT_WIN,
                        "do_not_mix_with_long_parlay": True}
    return {"match_num": bundle.match_num, "home": bundle.home, "away": bundle.away,
            "board": board, "hhad": hhad, "had_hhad_aligned": aligned,
            "signals": scored["signals"], "score": score, "verdict": verdict,
            "note": note, "construction": construction, "gate": "PREMATCH_FADE_GATE_V1"}

def friday_011_012_regression():
    betis = PrematchBundle("周五011", "贝蒂斯", "皇马", 6.10, 5.15, 1.30, 1.0, None, None, None, [
        Fact("europe_within_hours", 72, "uefa.com", "2026-09-04"),
        Fact("favorite_spine_out", 7, "madrid squad", "2026-09-04"),
        Fact("fixture_identity", "home_trap", "Cartuja", "2026-09-04"),
    ])
    psg = PrematchBundle("周五012", "巴黎", "摩纳哥", 1.21, 5.45, 8.40, -1.0, None, None, None, [
        Fact("europe_within_hours", 96, "uefa.com", "2026-09-04"),
        Fact("favorite_spine_out", 2, "Mendes + Barcola", "2026-09-04"),
        Fact("table_inversion", True, "PSG two draws Monaco max pts", "2026-09-04"),
        Fact("underdog_proven", True, "Monaco beat Marseille", "2026-09-04"),
    ])
    r1, r2 = decide(betis), decide(psg)
    ok = r1["verdict"] != VERDICT_LONG and r2["verdict"] != VERDICT_LONG
    return {"self_test": "PASS" if ok else "FAIL", "friday011": r1["verdict"], "friday012": r2["verdict"]}

if __name__ == "__main__":
    import json, sys
    out = friday_011_012_regression()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out["self_test"] == "PASS" else 1)
