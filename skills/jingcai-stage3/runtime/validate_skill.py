#!/usr/bin/env python3
from pathlib import Path
import json, sys
ROOT = Path(__file__).resolve().parents[1]
required = [
    "SKILL.md",
    "core/jingcai_3_4_4_live_market_consensus.py",
    "core/jingcai_3_5_alpha.py",
    "core/jingcai_3_5_alpha_stage2.py",
    "core/jingcai_3_5_alpha_stage3_data.py",
    "core/jingcai_stage3_parser_builder_v1.py",
    "core/jingcai_stage3_external_market_v1.py",
    "research_os/shadow_modules/market_structure_shadow_v0_1.py",
    "research_os/shadow_modules/full_distribution_tail_aware_shadow_v0_2.py",
    "research_os/shadow_modules/final_compression_bet_construction_shadow_v0_1.py",
    "research_os/shadow_modules/crs_two_sided_presentation_shadow_v0_1.py",
    "research_os/shadow_modules/hybrid_crs_satellite_shadow_v0_1.py",
    "research_os/SELF_TEST_v0.1.5.json",
    "policies/EXTERNAL_COVERAGE_GATE.md",
    "runtime/external_coverage_gate.py",
]
missing = [p for p in required if not (ROOT / p).exists()]
selftest = json.loads((ROOT / "research_os/SELF_TEST_v0.1.5.json").read_text())
skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
checks = {
    "required_files": not missing,
    "research_os_v0_1_5_self_test": selftest.get("self_test") == "PASS",
    "production_boundary_present": "Jingcai 3.4 = REAL_MONEY / STAKING BASELINE" in skill,
    "promotion_not_granted_present": "RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED" in skill,
    "external_coverage_gate_present": "EXTERNAL_COVERAGE_GATE" in skill,
    "browser_fallback_present": "browser/web fallback" in skill,
    "sales_cutoff_present": "Monday–Friday: **22:00**" in skill,
    "full_distribution_present": "full 0–7+ distribution" in skill,
}
status = "PASS" if all(checks.values()) else "FAIL"
out = {"skill": "Jingcai Stage3 Canonical Skill", "version": "1.0.0", "status": status, "checks": checks, "missing": missing}
print(json.dumps(out, ensure_ascii=False, indent=2))
sys.exit(0 if status == "PASS" else 1)
