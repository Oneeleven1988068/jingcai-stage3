#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

VERSION = "CRS Two-Sided Distribution Presentation Shadow v0.1"
STATUS = "SHADOW_ONLY__NO_MODEL_PROMOTION"
GOVERNANCE = "RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED"


def _top(rows: List[Dict[str, Any]], pred, n: int) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        if pred(r):
            out.append({
                "score": r.get("score"),
                "p": r.get("p"),
                "odds": r.get("odds"),
                "result": r.get("result"),
                "margin": r.get("margin"),
                "total": r.get("total"),
                "total_band": r.get("total_band"),
            })
            if len(out) >= n:
                break
    return out


def _unique_selected(groups: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for group in groups:
        for r in group:
            s = r.get("score")
            if s and s not in seen:
                seen.add(s)
                out.append(r)
    return out


def _band_direction_mass(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    ans = {
        "LEFT_0_1": {"H": 0.0, "D": 0.0, "A": 0.0},
        "CENTER_2_3": {"H": 0.0, "D": 0.0, "A": 0.0},
        "RIGHT_4_PLUS": {"H": 0.0, "D": 0.0, "A": 0.0},
        "EXTREME_RIGHT_5_PLUS": {"H": 0.0, "D": 0.0, "A": 0.0},
    }
    for r in rows:
        t = int(r.get("total", 0))
        result = r.get("result")
        p = float(r.get("p") or 0.0)
        if result not in {"H", "D", "A"}:
            continue
        if t <= 1:
            ans["LEFT_0_1"][result] += p
        if 2 <= t <= 3:
            ans["CENTER_2_3"][result] += p
        if t >= 4:
            ans["RIGHT_4_PLUS"][result] += p
        if t >= 5:
            ans["EXTREME_RIGHT_5_PLUS"][result] += p
    return ans


def present_match(match: Dict[str, Any], n_center: int = 3, n_left: int = 3, n_right: int = 5, n_extreme: int = 3) -> Dict[str, Any]:
    ttg = match.get("ttg") or {}
    crs = match.get("crs") or {}
    rows = crs.get("all_exact_states") or []
    rows = sorted(rows, key=lambda x: float(x.get("p") or 0.0), reverse=True)

    center = _top(rows, lambda r: 2 <= int(r.get("total", -1)) <= 3, n_center)
    left = _top(rows, lambda r: int(r.get("total", 99)) <= 1, n_left)
    right = _top(rows, lambda r: int(r.get("total", -1)) >= 4, n_right)
    extreme = _top(rows, lambda r: int(r.get("total", -1)) >= 5, n_extreme)
    selected = _unique_selected([center, left, right, extreme])

    selected_exact_mass = sum(float(r.get("p") or 0.0) for r in selected)
    exact_mass = float(crs.get("exact_mass") or 0.0)
    other_mass = float(crs.get("other_mass") or 0.0)
    bands = ttg.get("bands") or {}
    tails = ttg.get("tail_mass") or {}

    return {
        "module": VERSION,
        "status": STATUS,
        "governance": GOVERNANCE,
        "match_num": match.get("match_num"),
        "fixture": match.get("fixture"),
        "source_full_distribution_module": match.get("module"),
        "principle": "CRS_OUTPUT_MUST_SHOW_CENTER_PLUS_LEFT_TAIL_PLUS_RIGHT_TAIL__TOP3_ONLY_FORBIDDEN_AS_COMPLETE_REPRESENTATION",
        "ttg_distribution_view": {
            "mode": ttg.get("mode"),
            "mode_p": ttg.get("mode_p"),
            "left_0_1": float(bands.get("0-1") or 0.0),
            "center_2_3": float(bands.get("2-3") or 0.0),
            "right_4_plus": float(tails.get("4+") or 0.0),
            "extreme_right_5_plus": float(tails.get("5+") or 0.0),
            "six_plus": float(tails.get("6+") or 0.0),
            "seven_plus": float(tails.get("7+") or 0.0),
            "retained_probability_mass": ttg.get("retained_probability_mass"),
            "truncation": ttg.get("truncation"),
        },
        "crs_presentation": {
            "display_policy": "CENTER_PLUS_TWO_SIDED_TAILS",
            "center_scores_total_2_3": center,
            "left_tail_scores_total_0_1": left,
            "right_tail_scores_total_4_plus": right,
            "extreme_right_scores_total_5_plus": extreme,
            "exact_band_direction_mass_lower_bound": _band_direction_mass(rows),
            "other_score_buckets": crs.get("other_buckets") or [],
        },
        "coverage_audit": {
            "unique_display_score_count": len(selected),
            "selected_exact_score_mass": selected_exact_mass,
            "crs_exact_market_mass": exact_mass,
            "selected_share_of_exact_market_mass": (selected_exact_mass / exact_mass) if exact_mass > 0 else None,
            "crs_other_bucket_mass": other_mass,
            "important_note": "Display-score mass is not a joint market probability and must not be multiplied by HAD/HHAD/TTG. OTHER_H/D/A reveal result direction only; their exact total/margin is unknown.",
        },
        "governance_controls": {
            "automatic_rating_change": False,
            "automatic_bet_change": False,
            "production_probability_change": False,
            "staking_change": False,
            "thresholds_frozen": False,
            "promotion": "NOT_GRANTED",
        },
    }


def present_file(path: str, **kwargs: Any) -> Dict[str, Any]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    matches = d.get("matches") or []
    return {
        "module": VERSION,
        "status": STATUS,
        "governance": GOVERNANCE,
        "source": path,
        "matches": [present_match(m, **kwargs) for m in matches],
    }


def self_test() -> Dict[str, Any]:
    fixture = {
        "module": "Full Distribution / Tail-Aware CRS Shadow v0.2",
        "match_num": "TEST",
        "fixture": "Home vs Away",
        "ttg": {
            "mode": 3,
            "mode_p": 0.22,
            "bands": {"0-1": 0.20, "2-3": 0.44, "4-5": 0.27, "6+": 0.09},
            "tail_mass": {"4+": 0.36, "5+": 0.19, "6+": 0.09, "7+": 0.04},
            "retained_probability_mass": 1.0,
            "truncation": "NONE",
        },
        "crs": {
            "exact_mass": 0.96,
            "other_mass": 0.04,
            "other_buckets": [{"bucket": "OTHER_H", "p": 0.02, "result": "H"}],
            "all_exact_states": [
                {"score": "2:1", "p": 0.10, "odds": 8.0, "result": "H", "margin": 1, "total": 3, "total_band": "2-3"},
                {"score": "1:1", "p": 0.09, "odds": 9.0, "result": "D", "margin": 0, "total": 2, "total_band": "2-3"},
                {"score": "1:0", "p": 0.08, "odds": 10.0, "result": "H", "margin": 1, "total": 1, "total_band": "0-1"},
                {"score": "3:1", "p": 0.06, "odds": 13.0, "result": "H", "margin": 2, "total": 4, "total_band": "4-5"},
                {"score": "2:2", "p": 0.05, "odds": 15.0, "result": "D", "margin": 0, "total": 4, "total_band": "4-5"},
                {"score": "3:2", "p": 0.04, "odds": 20.0, "result": "H", "margin": 1, "total": 5, "total_band": "4-5"},
                {"score": "0:1", "p": 0.07, "odds": 11.0, "result": "A", "margin": -1, "total": 1, "total_band": "0-1"},
            ],
        },
    }
    out = present_match(fixture)
    assert out["crs_presentation"]["left_tail_scores_total_0_1"]
    assert out["crs_presentation"]["right_tail_scores_total_4_plus"]
    assert any(x["score"] == "3:2" for x in out["crs_presentation"]["extreme_right_scores_total_5_plus"])
    assert out["ttg_distribution_view"]["retained_probability_mass"] == 1.0
    assert out["governance_controls"]["production_probability_change"] is False
    return {
        "self_test": "PASS",
        "version": VERSION,
        "center_plus_two_sided_tails": True,
        "coverage_audit": True,
        "no_naive_cross_market_multiplication": True,
        "production_promotion": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")
    p = sub.add_parser("present")
    p.add_argument("--input", required=True)
    p.add_argument("--output")
    args = ap.parse_args()
    if args.cmd == "self-test":
        out = self_test()
    else:
        out = present_file(args.input)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if getattr(args, "output", None):
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
