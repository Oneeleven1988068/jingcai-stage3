#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

VERSION = "Hybrid CRS Satellite Bet Construction Shadow v0.1"
STATUS = "SHADOW_ONLY__NO_MODEL_PROMOTION"
GOVERNANCE = "RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED"
PORTFOLIO_CLASS = "HYBRID_CRS_SATELLITE"

ALLOWED_ANCHOR_RATINGS = {"A1", "A2"}
ALLOWED_CRS_OPPORTUNITY = {"CENTER", "TAIL"}
PAIR_PLUS_TRIPLE = ["SATELLITE_X_ANCHOR_1", "SATELLITE_X_ANCHOR_2", "ANCHOR_1_X_ANCHOR_2", "FULL_TRIPLE"]


def _sel_count(leg: Dict[str, Any]) -> int:
    selections = leg.get("selections") or []
    return len(selections)


def _product(a: int, b: int) -> int:
    return a * b


def _expanded_counts(sat_n: int, a1_n: int, a2_n: int, cover: List[str]) -> Tuple[Dict[str, int], int]:
    counts: Dict[str, int] = {}
    if "SATELLITE_X_ANCHOR_1" in cover:
        counts["SATELLITE_X_ANCHOR_1"] = _product(sat_n, a1_n)
    if "SATELLITE_X_ANCHOR_2" in cover:
        counts["SATELLITE_X_ANCHOR_2"] = _product(sat_n, a2_n)
    if "ANCHOR_1_X_ANCHOR_2" in cover:
        counts["ANCHOR_1_X_ANCHOR_2"] = _product(a1_n, a2_n)
    if "FULL_TRIPLE" in cover:
        counts["FULL_TRIPLE"] = sat_n * a1_n * a2_n
    return counts, sum(counts.values())


def audit_ticket(spec: Dict[str, Any]) -> Dict[str, Any]:
    satellite = spec.get("satellite") or {}
    anchor_1 = spec.get("anchor_1") or {}
    anchor_2 = spec.get("anchor_2") or {}
    cover = spec.get("cover_plan") or PAIR_PLUS_TRIPLE

    flags: List[str] = []
    blockers: List[str] = []
    warnings: List[str] = []

    # Structural completeness.
    if not satellite:
        blockers.append("MISSING_CRS_SATELLITE")
    if not anchor_1:
        blockers.append("MISSING_DIRECTION_ANCHOR_1")
    if not anchor_2:
        blockers.append("MISSING_DIRECTION_ANCHOR_2")

    sat_n = _sel_count(satellite)
    a1_n = _sel_count(anchor_1)
    a2_n = _sel_count(anchor_2)

    if satellite and sat_n < 1:
        blockers.append("CRS_SATELLITE_HAS_NO_SELECTION")
    if anchor_1 and a1_n < 1:
        blockers.append("ANCHOR_1_HAS_NO_SELECTION")
    if anchor_2 and a2_n < 1:
        blockers.append("ANCHOR_2_HAS_NO_SELECTION")

    # Satellite opportunity is independent of direction rating.
    opp = satellite.get("crs_opportunity_state")
    if satellite and opp not in ALLOWED_CRS_OPPORTUNITY:
        blockers.append("CRS_SINGLE_OPPORTUNITY_GATE_NOT_PASSED")
    if satellite and satellite.get("price_value_state") in {None, "UNVERIFIED"}:
        warnings.append("CRS_PRICE_VALUE_UNVERIFIED__HIGH_ODDS_NOT_EQUAL_POSITIVE_EV")

    # Anchors must already be A1/A2. Never promote B/C to fill the template.
    for idx, anchor in ((1, anchor_1), (2, anchor_2)):
        if not anchor:
            continue
        rating = anchor.get("rating")
        if rating not in ALLOWED_ANCHOR_RATINGS:
            blockers.append(f"ANCHOR_{idx}_RATING_NOT_A1_A2")

    if anchor_1 and anchor_2:
        if anchor_1.get("rating") == "A2" and anchor_2.get("rating") == "A2":
            warnings.append("A2_PLUS_A2_ANCHORS__HIGHER_STRUCTURAL_RISK")
        if "A1" not in {anchor_1.get("rating"), anchor_2.get("rating")}:
            warnings.append("NO_A1_ANCHOR")

    # Matches must be distinct: one satellite match + two direction matches.
    match_ids = [x.get("match_num") for x in (satellite, anchor_1, anchor_2) if x]
    if len(match_ids) != len(set(match_ids)):
        blockers.append("LEGS_MUST_USE_THREE_DISTINCT_MATCHES")

    # Cross-pool/mixed-pass compatibility cannot be assumed from generic formula lists.
    compatibility = spec.get("mixed_pool_compatibility") or "UNVERIFIED"
    if compatibility == "FAIL":
        blockers.append("MIXED_POOL_COMPATIBILITY_FAILED")
    elif compatibility != "OBSERVED_PASS":
        blockers.append("MIXED_POOL_COMPATIBILITY_UNVERIFIED")

    # Sales/timestamp gate must be upstream-verified for any actual staking decision.
    sales_state = spec.get("sales_state") or "UNVERIFIED"
    if sales_state != "OPEN_VERIFIED":
        blockers.append("SPORTTERY_SALES_STATE_NOT_OPEN_VERIFIED")
    if not spec.get("freeze_timestamp"):
        blockers.append("PREDICTION_FREEZE_TIMESTAMP_MISSING")

    allowed_cover = set(PAIR_PLUS_TRIPLE)
    unknown_cover = [x for x in cover if x not in allowed_cover]
    if unknown_cover:
        blockers.append("UNKNOWN_COVER_COMPONENT")

    counts, total_primitive_bets = _expanded_counts(sat_n, a1_n, a2_n, cover)
    unit_stake = spec.get("unit_stake")
    total_cost = None
    if isinstance(unit_stake, (int, float)) and unit_stake >= 0:
        total_cost = total_primitive_bets * float(unit_stake)

    classic_3x4 = (
        sat_n == 1 and a1_n == 1 and a2_n == 1 and set(cover) == set(PAIR_PLUS_TRIPLE)
    )
    if classic_3x4:
        flags.append("CLASSIC_3X4_EQUIVALENT__4_PRIMITIVE_BETS")
    else:
        flags.append("EXPANDED_MIXED_COVER__DO_NOT_LABEL_AS_FOUR_BETS")

    # Cost growth is a first-class audit item when multiple selections are used.
    if total_primitive_bets > 4:
        warnings.append("SELECTION_EXPANSION_INCREASES_TRUE_TICKET_COST")

    if blockers:
        construction_state = "CONSTRUCTION_INCOMPLETE_OR_BLOCKED"
    else:
        construction_state = "ELIGIBLE_FOR_SHADOW_USER_REVIEW"

    return {
        "module": VERSION,
        "status": STATUS,
        "governance": GOVERNANCE,
        "portfolio_class": PORTFOLIO_CLASS,
        "construction_state": construction_state,
        "principle": "ONE_CRS_OPPORTUNITY_SATELLITE_PLUS_TWO_PREEXISTING_A1_A2_DIRECTION_ANCHORS__PAIR_PLUS_TRIPLE_COVER__NO_FORCED_PROMOTION",
        "roles": {
            "satellite": "CRS opportunity / payout convexity leg; not a direction banker",
            "anchor_1": "pre-existing A1/A2 direction leg",
            "anchor_2": "pre-existing A1/A2 direction leg",
        },
        "input_summary": {
            "satellite": satellite,
            "anchor_1": anchor_1,
            "anchor_2": anchor_2,
            "cover_plan": cover,
            "mixed_pool_compatibility": compatibility,
            "sales_state": sales_state,
            "freeze_timestamp": spec.get("freeze_timestamp"),
        },
        "ticket_expansion_audit": {
            "satellite_selection_count": sat_n,
            "anchor_1_selection_count": a1_n,
            "anchor_2_selection_count": a2_n,
            "primitive_bets_by_component": counts,
            "total_primitive_bet_count": total_primitive_bets,
            "unit_stake": unit_stake,
            "total_cost_if_unit_stake_supplied": total_cost,
            "classic_3x4_equivalent": classic_3x4,
            "important_note": "If any leg has multiple selections, the true primitive-bet count expands. A nominal pair+triple cover must not be described as only four bets unless each leg has exactly one selection.",
        },
        "flags": flags,
        "warnings": warnings,
        "blockers": blockers,
        "governance_controls": {
            "automatic_rating_promotion": False,
            "automatic_stake_change": False,
            "automatic_ticket_submission": False,
            "production_probability_change": False,
            "thresholds_frozen": False,
            "promotion": "NOT_GRANTED",
        },
    }


def self_test() -> Dict[str, Any]:
    valid = {
        "satellite": {
            "match_num": "M1", "pool": "CRS", "direction_rating": "A2",
            "crs_opportunity_state": "TAIL", "price_value_state": "UNVERIFIED",
            "selections": ["3:1"],
        },
        "anchor_1": {"match_num": "M2", "pool": "HAD", "rating": "A1", "selections": ["H"]},
        "anchor_2": {"match_num": "M3", "pool": "HAD", "rating": "A2", "selections": ["A"]},
        "cover_plan": PAIR_PLUS_TRIPLE,
        "mixed_pool_compatibility": "OBSERVED_PASS",
        "sales_state": "OPEN_VERIFIED",
        "freeze_timestamp": "2026-08-18T21:30:00+08:00",
        "unit_stake": 2.0,
    }
    a = audit_ticket(valid)
    assert a["construction_state"] == "ELIGIBLE_FOR_SHADOW_USER_REVIEW"
    assert a["ticket_expansion_audit"]["total_primitive_bet_count"] == 4
    assert a["ticket_expansion_audit"]["classic_3x4_equivalent"] is True

    multi = json.loads(json.dumps(valid))
    multi["satellite"]["selections"] = ["3:1", "3:2", "4:1"]
    b = audit_ticket(multi)
    assert b["ticket_expansion_audit"]["total_primitive_bet_count"] == 10
    assert b["ticket_expansion_audit"]["classic_3x4_equivalent"] is False
    assert "SELECTION_EXPANSION_INCREASES_TRUE_TICKET_COST" in b["warnings"]

    bad_anchor = json.loads(json.dumps(valid))
    bad_anchor["anchor_2"]["rating"] = "B"
    c = audit_ticket(bad_anchor)
    assert "ANCHOR_2_RATING_NOT_A1_A2" in c["blockers"]

    no_compat = json.loads(json.dumps(valid))
    no_compat["mixed_pool_compatibility"] = "UNVERIFIED"
    d = audit_ticket(no_compat)
    assert "MIXED_POOL_COMPATIBILITY_UNVERIFIED" in d["blockers"]

    same_match = json.loads(json.dumps(valid))
    same_match["anchor_1"]["match_num"] = "M1"
    e = audit_ticket(same_match)
    assert "LEGS_MUST_USE_THREE_DISTINCT_MATCHES" in e["blockers"]

    return {
        "self_test": "PASS",
        "version": VERSION,
        "hybrid_crs_satellite_role_separation": True,
        "a1_a2_anchor_gate": True,
        "no_forced_b_or_c_promotion": True,
        "mixed_pool_compatibility_must_be_observed": True,
        "multi_selection_cost_expansion_audited": True,
        "classic_single_selection_pair_plus_triple_count": 4,
        "three_score_satellite_single_anchor_pair_plus_triple_count": 10,
        "production_promotion": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")
    p = sub.add_parser("audit")
    p.add_argument("--input", required=True)
    p.add_argument("--output")
    args = ap.parse_args()

    if args.cmd == "self-test":
        out = self_test()
    else:
        spec = json.loads(Path(args.input).read_text(encoding="utf-8"))
        out = audit_ticket(spec)

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if getattr(args, "output", None):
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
