#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set

from full_distribution_tail_aware_shadow_v0_2 import analyze_match, flatten_matches, parse_wrapper

VERSION = "Final Compression / Bet Construction Shadow v0.1"
STATUS = "SHADOW_ONLY__NO_MODEL_PROMOTION"
GOVERNANCE = "RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED"


def had_result_from_score(score: str) -> str:
    h, a = [int(x) for x in score.split(":", 1)]
    return "H" if h > a else "D" if h == a else "A"


def hhad_selection_had_coverage(line: str, selections: List[str]) -> Set[str]:
    """Map a 3-way Sporttery HHAD selection back to HAD result-level coverage.

    This intentionally stays at result level. It does not pretend that HHAD and HAD
    are a single jointly priced probability distribution.
    """
    line = str(line).strip()
    sel = {str(x).upper() for x in selections}
    coverage: Set[str] = set()
    if line in {"-1", "-1.0", "-1.00"}:
        # HHAD_H = home wins 2+; HHAD_D = home wins exactly 1;
        # HHAD_A = ordinary draw OR away win.
        if "H" in sel or "D" in sel:
            coverage.add("H")
        if "A" in sel:
            coverage.update({"D", "A"})
    elif line in {"+1", "1", "1.0", "1.00", "+1.0", "+1.00"}:
        # HHAD_H = ordinary home win OR ordinary draw;
        # HHAD_D/A are away-win margin buckets.
        if "H" in sel:
            coverage.update({"H", "D"})
        if "D" in sel or "A" in sel:
            coverage.add("A")
    return coverage


def exact_score_rank(analysis: Dict[str, Any], score: str) -> Dict[str, Any]:
    rows = analysis.get("crs", {}).get("all_exact_states", []) or []
    for i, row in enumerate(rows, 1):
        if row.get("score") == score:
            return {"rank": i, "p": row.get("p"), "odds": row.get("odds"), "result": row.get("result")}
    return {"rank": None, "p": None, "odds": None, "result": had_result_from_score(score)}


def audit_match(analysis: Dict[str, Any], spec: Dict[str, Any], actual_score: str | None = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "module": VERSION,
        "status": STATUS,
        "governance": GOVERNANCE,
        "match_num": analysis.get("match_num"),
        "fixture": analysis.get("fixture"),
        "spec_evidence_state": spec.get("evidence_state", "UNVERIFIED"),
        "rating": spec.get("rating"),
        "principle": "FULL_DISTRIBUTION_FIRST__FINAL_COMPRESSION_MUST_NOT_SILENTLY_DELETE_MATERIAL_BRANCHES",
        "production_change": False,
        "thresholds_frozen": False,
    }

    action = spec.get("production_action") or {}
    market = str(action.get("market") or "").upper()
    selections = [str(x).upper() for x in action.get("selections", [])]
    action_type = action.get("action_type", "BET" if selections else "NO_BET")
    out["production_action"] = {
        "market": market,
        "selections": selections,
        "label": action.get("label"),
        "action_type": action_type,
    }

    had_p = analysis.get("had", {}).get("p") or {}
    crs_dir = analysis.get("crs", {}).get("direction_mass_including_other") or {}
    crs_top5 = analysis.get("crs", {}).get("core_exact_top5") or []

    if market == "HAD" and selections:
        selected = set(selections)
        dropped = [r for r in "HDA" if r not in selected]
        out["compression_diagnostics"] = {
            "selected_had_results": selections,
            "dropped_had_results": dropped,
            "had_mass_retained": sum(float(had_p.get(r, 0.0)) for r in selected),
            "had_mass_dropped": sum(float(had_p.get(r, 0.0)) for r in dropped),
            "crs_direction_mass_retained": sum(float(crs_dir.get(r, 0.0)) for r in selected),
            "crs_direction_mass_dropped": sum(float(crs_dir.get(r, 0.0)) for r in dropped),
            "crs_top5_alternate_states": [
                {"rank": i + 1, "score": r.get("score"), "result": r.get("result"), "p": r.get("p")}
                for i, r in enumerate(crs_top5)
                if r.get("result") not in selected
            ],
        }
        flags: List[str] = []
        if out["compression_diagnostics"]["crs_top5_alternate_states"]:
            flags.append("CRS_TOP5_CONTAINS_DROPPED_RESULT_BRANCH")
        if len(selected) == 1 and flags:
            flags.append("SINGLE_RESULT_FINAL_COMPRESSION_CONFLICT")
        out["compression_diagnostics"]["flags"] = flags
    else:
        out["compression_diagnostics"] = {
            "state": "NO_HAD_FINAL_COMPRESSION_TO_AUDIT" if action_type == "NO_BET" else "UNSUPPORTED_ACTION_MARKET",
            "flags": [],
        }

    protection = spec.get("protection_action") or {}
    if protection:
        pmarket = str(protection.get("market") or "").upper()
        psel = [str(x).upper() for x in protection.get("selections", [])]
        line = str(protection.get("line") or analysis.get("hhad", {}).get("line") or "")
        claimed = str(protection.get("claimed_intent") or "")
        coverage = hhad_selection_had_coverage(line, psel) if pmarket == "HHAD" else set()
        semantic_flags: List[str] = []
        intent_target = {
            "PROTECT_HAD_DRAW": "D",
            "PROTECT_HAD_AWAY": "A",
            "PROTECT_HAD_HOME": "H",
        }.get(claimed)
        if intent_target and intent_target not in coverage:
            semantic_flags.append("BET_CONSTRUCTION_SEMANTIC_ERROR")
            semantic_flags.append(f"CLAIMED_{intent_target}_PROTECTION_NOT_COVERED")
        out["protection_semantics"] = {
            "market": pmarket,
            "line": line,
            "selections": psel,
            "claimed_intent": claimed or None,
            "had_result_level_coverage": sorted(coverage),
            "flags": semantic_flags,
            "important_note": "For home -1, HHAD H+D partitions ordinary HAD home wins; it does not protect an ordinary draw.",
        }
    else:
        out["protection_semantics"] = {"state": "NONE_DECLARED", "flags": []}

    # Cross-layer directional warning: diagnostic only, no threshold/promotion.
    hhad = analysis.get("hhad", {})
    if market == "HAD" and selections == ["H"] and hhad.get("line") in {"-1", "-1.0", "-1.00"} and hhad.get("p"):
        hp = hhad["p"]
        top = max(hp, key=hp.get)
        if top == "A":
            out.setdefault("cross_layer_flags", []).append("HAD_HOME_ONLY__HHAD_MINUS1_NONWIN_AGGREGATE_TOP")
            out["cross_layer_note"] = "HHAD -1 A contains ordinary draw plus away-win states; this is a risk diagnostic, not an automatic reversal."

    if actual_score is not None:
        actual_had = had_result_from_score(actual_score)
        rank = exact_score_rank(analysis, actual_score)
        selected = set(selections)
        out["postmatch"] = {
            "actual_score": actual_score,
            "actual_had": actual_had,
            "production_action_hit": (actual_had in selected) if (market == "HAD" and selections) else None,
            "actual_result_was_dropped_by_final_compression": (actual_had not in selected) if (market == "HAD" and selections) else None,
            "actual_score_crs_exact_rank": rank["rank"],
            "actual_score_crs_dejuiced_p": rank["p"],
            "actual_score_was_preserved_in_full_crs_state": rank["rank"] is not None,
        }
        if rank["rank"] is not None and market == "HAD" and selections and actual_had not in selected:
            out["postmatch"]["attribution"] = "FULL_DISTRIBUTION_PRESERVED_REALIZED_SCORE__FINAL_COMPRESSION_DROPPED_REALIZED_RESULT_BRANCH"

    compression_flags = out.get("compression_diagnostics", {}).get("flags") or []
    severe_compression = "SINGLE_RESULT_FINAL_COMPRESSION_CONFLICT" in compression_flags
    out["decision"] = {
        "shadow_gate_state": "REVIEW_REQUIRED" if (
            severe_compression
            or out.get("protection_semantics", {}).get("flags")
            or out.get("cross_layer_flags")
        ) else "NO_SHADOW_ALERT",
        "automatic_bet_change": False,
        "automatic_rating_change": False,
        "promotion": "NOT_GRANTED",
    }
    return out


def self_test() -> Dict[str, Any]:
    analysis = {
        "match_num": "T006",
        "fixture": "Home vs Away",
        "had": {"p": {"H": 0.62, "D": 0.23, "A": 0.15}},
        "hhad": {"line": "-1", "p": {"H": 0.35, "D": 0.28, "A": 0.37}},
        "crs": {
            "direction_mass_including_other": {"H": 0.61, "D": 0.21, "A": 0.18},
            "core_exact_top5": [
                {"score": "2:0", "result": "H", "p": 0.11},
                {"score": "1:0", "result": "H", "p": 0.10},
                {"score": "2:1", "result": "H", "p": 0.10},
                {"score": "1:1", "result": "D", "p": 0.10},
            ],
            "all_exact_states": [
                {"score": "2:0", "result": "H", "p": 0.11, "odds": 7.0},
                {"score": "1:0", "result": "H", "p": 0.10, "odds": 7.5},
                {"score": "2:1", "result": "H", "p": 0.10, "odds": 8.0},
                {"score": "1:1", "result": "D", "p": 0.10, "odds": 8.5},
            ],
        },
    }
    spec = {
        "rating": "A2",
        "evidence_state": "TEST_FIXTURE",
        "production_action": {"market": "HAD", "selections": ["H"], "label": "home"},
        "protection_action": {"market": "HHAD", "line": "-1", "selections": ["H", "D"], "claimed_intent": "PROTECT_HAD_DRAW"},
    }
    out = audit_match(analysis, spec, "1:1")
    assert "CRS_TOP5_CONTAINS_DROPPED_RESULT_BRANCH" in out["compression_diagnostics"]["flags"]
    assert "BET_CONSTRUCTION_SEMANTIC_ERROR" in out["protection_semantics"]["flags"]
    assert out["protection_semantics"]["had_result_level_coverage"] == ["H"]
    assert out["postmatch"]["actual_result_was_dropped_by_final_compression"] is True
    assert out["decision"]["automatic_bet_change"] is False
    return {
        "self_test": "PASS",
        "version": VERSION,
        "semantic_type_check": True,
        "final_compression_branch_audit": True,
        "automatic_production_change": False,
    }


def replay(input_path: str, specs_path: str, outcomes_path: str) -> Dict[str, Any]:
    _, inner = parse_wrapper(input_path)
    matches = {m.get("matchNumStr"): m for m in flatten_matches(inner)}
    specs = json.loads(Path(specs_path).read_text(encoding="utf-8"))
    outcomes = json.loads(Path(outcomes_path).read_text(encoding="utf-8"))
    rows = []
    for spec in specs:
        mn = spec["match_num"]
        if mn not in matches:
            rows.append({"match_num": mn, "state": "INPUT_INCOMPLETE__MATCH_NOT_FOUND"})
            continue
        a = analyze_match(matches[mn])
        score = outcomes.get(mn)
        rows.append(audit_match(a, spec, score))
    actionable = [r for r in rows if r.get("production_action", {}).get("action_type") == "BET"]
    hits = [r.get("postmatch", {}).get("production_action_hit") for r in actionable]
    hits = [x for x in hits if x is not None]
    return {
        "module": VERSION,
        "status": STATUS,
        "governance": GOVERNANCE,
        "source_input": str(input_path),
        "specs_source": str(specs_path),
        "outcomes_source": str(outcomes_path),
        "n_rows": len(rows),
        "n_actionable": len(actionable),
        "action_hit_count": sum(bool(x) for x in hits),
        "action_miss_count": sum(not bool(x) for x in hits),
        "important_caveat": "This is a retrospective replay of frozen/reconstructed ticket semantics, not prospective proof of predictive improvement.",
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")
    r = sub.add_parser("replay")
    r.add_argument("--input", required=True)
    r.add_argument("--specs", required=True)
    r.add_argument("--outcomes", required=True)
    r.add_argument("--output")
    args = ap.parse_args()
    if args.cmd == "self-test":
        out = self_test()
    else:
        out = replay(args.input, args.specs, args.outcomes)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if getattr(args, "output", None):
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
