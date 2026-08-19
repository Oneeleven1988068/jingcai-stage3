#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Iterable, List, Tuple

VERSION = "Draw Branch Preservation / Final Compression Audit Shadow v0.1"
STATUS = "SHADOW_ONLY__NO_MODEL_PROMOTION"
GOVERNANCE = "RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED"


def _norm_result(x: str) -> str:
    x = (x or "").upper()
    if x in {"H", "HOME", "主胜", "胜"}:
        return "H"
    if x in {"D", "DRAW", "平", "平局"}:
        return "D"
    if x in {"A", "AWAY", "客胜", "负"}:
        return "A"
    return x


def _score_tuple(score: str) -> Tuple[int, int] | None:
    try:
        a, b = score.split(":", 1)
        return int(a), int(b)
    except Exception:
        return None


def _result_from_score(score: str) -> str | None:
    t = _score_tuple(score)
    if t is None:
        return None
    h, a = t
    if h > a:
        return "H"
    if h == a:
        return "D"
    return "A"


def _total_from_score(score: str) -> int | None:
    t = _score_tuple(score)
    if t is None:
        return None
    return t[0] + t[1]


def _coerce_probs(d: Dict[str, Any] | None) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in (d or {}).items():
        try:
            out[_norm_result(str(k))] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _rank_desc(values: Dict[str, float], key: str) -> int | None:
    if key not in values:
        return None
    ordered = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    for i, (k, _) in enumerate(ordered, 1):
        if k == key:
            return i
    return None


def _crs_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        score = str(r.get("score") or r.get("selection") or "")
        result = _norm_result(str(r.get("result") or "")) or _result_from_score(score)
        if result not in {"H", "D", "A"}:
            result = _result_from_score(score)
        try:
            p = float(r.get("probability"))
        except (TypeError, ValueError):
            p = 0.0
        out.append({**r, "score": score, "result": result, "probability": p})
    return out


def _final_rows(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        if isinstance(r, str):
            score = r
            result = _result_from_score(score) or _norm_result(score)
            out.append({"selection": r, "score": score, "result": result})
        elif isinstance(r, dict):
            score = str(r.get("score") or r.get("selection") or "")
            result = _norm_result(str(r.get("result") or "")) or _result_from_score(score)
            if result not in {"H", "D", "A"}:
                result = _result_from_score(score)
            out.append({**r, "score": score, "result": result})
    return out


def audit_draw_branch(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Audit whether final compression silently extinguishes a live draw branch.

    This module is intentionally diagnostic. It does not freeze predictive thresholds,
    add a bet, change a rating, or alter Jingcai 3.4 production probabilities.
    """
    had = _coerce_probs(spec.get("had_probabilities"))
    crs = _crs_rows(spec.get("crs_precompression") or [])
    final = _final_rows(spec.get("final_selections") or [])
    ttg = {str(k): float(v) for k, v in (spec.get("ttg_probabilities") or {}).items() if isinstance(v, (int, float))}

    crs_agg = {"H": 0.0, "D": 0.0, "A": 0.0}
    for r in crs:
        if r["result"] in crs_agg:
            crs_agg[r["result"]] += r["probability"]

    sorted_crs = sorted(crs, key=lambda r: (-r["probability"], r["score"]))
    diagnostic_top_k = int(spec.get("diagnostic_top_k") or 5)
    top_rows = sorted_crs[:diagnostic_top_k]
    top_draws = [r for r in top_rows if r["result"] == "D"]

    final_results = {r.get("result") for r in final}
    draw_retained = "D" in final_results
    draw_branch_extinguished = bool(crs_agg["D"] > 0 and not draw_retained)

    # TTG support for the exact draw totals that are already present in the pre-compression CRS set.
    draw_total_support: List[Dict[str, Any]] = []
    for r in crs:
        if r["result"] != "D" or not r["score"]:
            continue
        total = _total_from_score(r["score"])
        if total is None:
            continue
        ttg_key = "7+" if total >= 7 else str(total)
        draw_total_support.append({
            "score": r["score"],
            "crs_probability": r["probability"],
            "total": total,
            "ttg_key": ttg_key,
            "ttg_probability": ttg.get(ttg_key),
        })

    movement = spec.get("draw_movement") or {}
    movement_flags: List[str] = []
    if movement.get("crs_draw_strengthening") is True:
        movement_flags.append("CRS_DRAW_STRENGTHENING_OBSERVED")
    if movement.get("other_d_strengthening") is True:
        movement_flags.append("OTHER_D_STRENGTHENING_OBSERVED")
    if movement.get("had_draw_strengthening") is True:
        movement_flags.append("HAD_DRAW_STRENGTHENING_OBSERVED")

    flags: List[str] = []
    if draw_branch_extinguished:
        flags.append("DRAW_BRANCH_EXTINCTION")
    if top_draws:
        flags.append("DRAW_EXACT_STATE_PRESENT_IN_DIAGNOSTIC_TOP_K")
    if had.get("D", 0.0) > 0 and crs_agg["D"] > 0:
        flags.append("DRAW_BRANCH_PRESENT_IN_HAD_AND_CRS")
    if any(x.get("ttg_probability") not in {None, 0.0} for x in draw_total_support):
        flags.append("DRAW_TOTALS_SUPPORTED_BY_TTG")
    flags.extend(movement_flags)

    # This is a shadow audit invariant, not a frozen betting threshold:
    # if draw survives upstream, appears in the candidate layer, and is silently deleted,
    # force explicit human/model review before final presentation.
    preserve_for_review = bool(draw_branch_extinguished and top_draws)
    recommendation = (
        "PRESERVE_AT_LEAST_ONE_DRAW_STATE_FOR_SHADOW_REVIEW"
        if preserve_for_review
        else "NO_DRAW_PRESERVATION_INTERVENTION_TRIGGERED"
    )

    return {
        "module": VERSION,
        "status": STATUS,
        "governance": GOVERNANCE,
        "draw_branch_audit": {
            "had_draw_probability": had.get("D"),
            "had_draw_rank": _rank_desc(had, "D"),
            "crs_draw_probability": crs_agg["D"],
            "crs_draw_rank": _rank_desc(crs_agg, "D"),
            "diagnostic_top_k": diagnostic_top_k,
            "top_k_draw_states": top_draws,
            "draw_total_support": draw_total_support,
            "final_draw_retained": draw_retained,
            "draw_branch_extinguished": draw_branch_extinguished,
        },
        "flags": flags,
        "recommendation": recommendation,
        "governance_controls": {
            "automatic_selection_addition": False,
            "automatic_rating_change": False,
            "automatic_stake_change": False,
            "production_probability_change": False,
            "thresholds_frozen": False,
            "promotion": "NOT_GRANTED",
        },
        "interpretation": (
            "A draw state that survives upstream distribution analysis must not be silently erased "
            "by final compression without an explicit audit trail. This module requests shadow review only."
        ),
    }


def self_test() -> Dict[str, Any]:
    # Recreates the structural failure pattern seen in 2026-08-18 Tue003:
    # a material 2:2 draw state exists pre-compression but final score satellite is home-only.
    spec = {
        "had_probabilities": {"H": 0.5711, "D": 0.2299, "A": 0.1989},
        "ttg_probabilities": {"0": 0.03, "1": 0.1476, "2": 0.20, "3": 0.2467, "4": 0.1866, "5": 0.10, "6": 0.05, "7+": 0.0391},
        "crs_precompression": [
            {"score": "2:1", "probability": 0.1106},
            {"score": "1:1", "probability": 0.0964},
            {"score": "2:0", "probability": 0.0830},
            {"score": "1:0", "probability": 0.0787},
            {"score": "3:1", "probability": 0.0679},
            {"score": "2:2", "probability": 0.0622},
            {"score": "3:0", "probability": 0.0515},
            {"score": "3:2", "probability": 0.0393},
            {"score": "4:1", "probability": 0.0287},
        ],
        "final_selections": ["3:1", "3:2", "4:1"],
        "diagnostic_top_k": 6,
        "draw_movement": {"crs_draw_strengthening": True},
    }
    out = audit_draw_branch(spec)
    assert out["draw_branch_audit"]["draw_branch_extinguished"] is True
    assert "DRAW_BRANCH_EXTINCTION" in out["flags"]
    assert "DRAW_EXACT_STATE_PRESENT_IN_DIAGNOSTIC_TOP_K" in out["flags"]
    assert out["recommendation"] == "PRESERVE_AT_LEAST_ONE_DRAW_STATE_FOR_SHADOW_REVIEW"
    assert out["governance_controls"]["production_probability_change"] is False

    retained = dict(spec)
    retained["final_selections"] = ["3:1", "2:2"]
    out2 = audit_draw_branch(retained)
    assert out2["draw_branch_audit"]["draw_branch_extinguished"] is False
    assert out2["recommendation"] == "NO_DRAW_PRESERVATION_INTERVENTION_TRIGGERED"

    return {
        "self_test": "PASS",
        "version": VERSION,
        "draw_branch_extinction_detected": True,
        "draw_state_preservation_review_triggered": True,
        "no_automatic_bet_change": True,
        "thresholds_frozen": False,
        "production_promotion": False,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", help="JSON input file")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    if not args.input:
        p.error("--input or --self-test is required")
    with open(args.input, "r", encoding="utf-8") as f:
        spec = json.load(f)
    print(json.dumps(audit_draw_branch(spec), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
