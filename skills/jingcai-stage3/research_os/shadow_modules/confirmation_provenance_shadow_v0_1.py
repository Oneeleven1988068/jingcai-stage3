#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List

VERSION = "Confirmation Provenance / Same-Source Overcount Audit Shadow v0.1"
STATUS = "SHADOW_ONLY__NO_MODEL_PROMOTION"
GOVERNANCE = "RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED"

SPORTTERY_POOLS = {"HAD", "HHAD", "TTG", "CRS", "HAFU"}


def _default_family(e: Dict[str, Any]) -> str:
    market = str(e.get("market") or "").upper()
    source_system = str(e.get("source_system") or "").upper()
    if market in SPORTTERY_POOLS or "SPORTTERY" in source_system or "体彩" in source_system:
        return "SPORTTERY_OFFICIAL"
    if e.get("source_family"):
        return str(e["source_family"])
    if source_system:
        return source_system
    return "UNVERIFIED_SOURCE_FAMILY"


def audit_confirmation_provenance(evidence: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for raw in evidence or []:
        row = dict(raw)
        row["source_family_resolved"] = _default_family(row)
        row["direction"] = str(row.get("direction") or "UNSPECIFIED").upper()
        rows.append(row)
        by_family[row["source_family_resolved"]].append(row)

    direction_counts_raw: Dict[str, int] = defaultdict(int)
    for r in rows:
        direction_counts_raw[r["direction"]] += 1

    family_direction: Dict[str, str] = {}
    family_conflict: Dict[str, List[str]] = {}
    for fam, fam_rows in by_family.items():
        dirs = sorted({r["direction"] for r in fam_rows if r["direction"] != "UNSPECIFIED"})
        if len(dirs) == 1:
            family_direction[fam] = dirs[0]
        elif len(dirs) > 1:
            family_direction[fam] = "MIXED"
            family_conflict[fam] = dirs
        else:
            family_direction[fam] = "UNSPECIFIED"

    structural_rows = [r for r in rows if r["source_family_resolved"] == "SPORTTERY_OFFICIAL"]
    flags: List[str] = []
    if len(structural_rows) >= 2:
        flags.append("SAME_SOURCE_STRUCTURAL_CONSISTENCY__NOT_INDEPENDENT_CONFIRMATION")
    if len(by_family) < len(rows):
        flags.append("RAW_CONFIRMATION_COUNT_EXCEEDS_INDEPENDENT_SOURCE_FAMILY_COUNT")
    if family_conflict:
        flags.append("WITHIN_FAMILY_DIRECTION_CONFLICT_PRESENT")

    return {
        "module": VERSION,
        "status": STATUS,
        "governance": GOVERNANCE,
        "raw_evidence_count": len(rows),
        "independent_source_family_count": len(by_family),
        "source_families": {
            fam: {
                "evidence_count": len(fam_rows),
                "markets": sorted({str(r.get("market") or "") for r in fam_rows}),
                "resolved_direction": family_direction[fam],
            }
            for fam, fam_rows in sorted(by_family.items())
        },
        "raw_direction_counts": dict(direction_counts_raw),
        "flags": flags,
        "governance_rule": (
            "HAD/HHAD/TTG/CRS/HAFU from the same Sporttery source may demonstrate structural consistency, "
            "but must not be counted as five independent confirmations. Independent confirmation is counted "
            "at the source-family level with provenance preserved."
        ),
        "governance_controls": {
            "automatic_rating_change": False,
            "automatic_stake_change": False,
            "production_probability_change": False,
            "thresholds_frozen": False,
            "promotion": "NOT_GRANTED",
        },
    }


def self_test() -> Dict[str, Any]:
    evidence = [
        {"market": "HAD", "source_system": "Sporttery", "direction": "H"},
        {"market": "HHAD", "source_system": "Sporttery", "direction": "H"},
        {"market": "TTG", "source_system": "Sporttery", "direction": "H"},
        {"market": "CRS", "source_system": "Sporttery", "direction": "H"},
        {"market": "HAFU", "source_system": "Sporttery", "direction": "H"},
        {"market": "H2H", "source_system": "Pinnacle", "source_family": "PINNACLE", "direction": "H"},
    ]
    out = audit_confirmation_provenance(evidence)
    assert out["raw_evidence_count"] == 6
    assert out["independent_source_family_count"] == 2
    assert "SAME_SOURCE_STRUCTURAL_CONSISTENCY__NOT_INDEPENDENT_CONFIRMATION" in out["flags"]
    assert out["source_families"]["SPORTTERY_OFFICIAL"]["evidence_count"] == 5
    return {
        "self_test": "PASS",
        "version": VERSION,
        "same_source_five_pool_collapsed_to_one_family": True,
        "independent_confirmation_counting": "SOURCE_FAMILY_LEVEL",
        "production_promotion": False,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", help="JSON file containing an evidence array or {'evidence': [...]} object")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    if not args.input:
        p.error("--input or --self-test is required")
    with open(args.input, "r", encoding="utf-8") as f:
        obj = json.load(f)
    evidence = obj.get("evidence", []) if isinstance(obj, dict) else obj
    print(json.dumps(audit_confirmation_provenance(evidence), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
