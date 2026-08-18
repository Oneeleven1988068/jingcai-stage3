#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jingcai 3.5 Alpha — Stage 2 governance wrapper.

Stage-2 OOS decision:
- 3.4 remains the staking baseline.
- Multiplicative + Power de-vig plus rolling temperature calibration passed the
  1X2 probability-core benchmark, but the full 3.5 betting tier stack did not
  beat the 3.4 ROI/max-drawdown benchmark.
- Therefore new 3.5 modules stay shadow/diagnostic unless explicitly allowed
  for research. A live external-market gate remains mandatory.

Place this file next to jingcai_3_5_alpha.py.
"""
from __future__ import annotations
import math
from typing import Dict
from jingcai_3_5_alpha import Jingcai35AlphaEngine, MultiDeVig, OUTCOMES

STAGE2_STATUS = "PROBABILITY_CORE_PASS__FULL_BETTING_PROMOTION_FAIL"
STAGE2_OOS_N = 16260
STAGE2_FOLDS = 6

# Median reference only. Production research should re-fit these weights on
# strictly earlier chronological data. They are not declared universal.
DEVIG_REFERENCE_WEIGHTS = {
    "multiplicative": 0.357350833,
    "power": 0.642649167,
    "shin": 0.0,
}
TEMPERATURE_REFERENCE = 0.9807436202

STAGE2_ACTIVATION_REGISTRY = {
    "multiplicative_power_devig": "ALPHA_CANDIDATE",
    "temperature_calibration": "ALPHA_CANDIDATE",
    "shin": "SHADOW_ONLY",
    "external_positive_probability_fusion": "NOT_PROMOTED",
    "external_market_audit_gate": "MANDATORY_LIVE",
    "dynamic_team_strength_xg": "SHADOW_ONLY",
    "multi_score_distribution_as_had_weight": "SHADOW_ONLY",
    "residual_ml": "PRODUCTION_WEIGHT_DISABLED",
    "hhad_crs_ttg_hafu_joint_weights": "UNVALIDATED_NEEDS_SPORTTERY_HISTORY",
    "full_s_a1_a2_thresholds": "NOT_FROZEN",
    "strong_had_confidence_filter": "ALPHA_SHADOW_CANDIDATE",
}


def _normalize(p: Dict[str, float]) -> Dict[str, float]:
    z = sum(max(0.0, float(p[k])) for k in OUTCOMES)
    return {k: max(0.0, float(p[k])) / z for k in OUTCOMES} if z else {k: 1/3 for k in OUTCOMES}


def reference_mdv_temperature(odds) -> Dict[str, float]:
    """Stage-2 reference 1X2 probability candidate.

    This is for shadow comparison/audit only. The learned coefficients came
    from a Pinnacle-dominant historical benchmark and must not be silently
    treated as Sporttery-specific production coefficients.
    """
    pm = MultiDeVig.multiplicative(odds)
    pp = MultiDeVig.power(odds)
    if pm is None or pp is None:
        raise ValueError("valid three-way decimal odds required")
    w = DEVIG_REFERENCE_WEIGHTS
    raw = [w["multiplicative"] * pm[i] + w["power"] * pp[i] for i in range(3)]
    T = TEMPERATURE_REFERENCE
    logits = [math.log(max(1e-12, x)) / T for x in raw]
    mx = max(logits)
    ex = [math.exp(x - mx) for x in logits]
    z = sum(ex)
    vals = [x/z for x in ex]
    return dict(zip(OUTCOMES, vals))


class Jingcai35AlphaStage2Engine(Jingcai35AlphaEngine):
    """Governed Stage-2 engine.

    By default, new 3.5 betting grades cannot be treated as a FINAL betting
    freeze because Stage 2 failed the full ROI/max-drawdown promotion test.
    Set allow_unvalidated_alpha_betting=True only for explicit research; this
    does not change the model-governance status.
    """
    VERSION = "3.5 Alpha Stage2"
    ALPHA_STATUS = STAGE2_STATUS

    def __init__(self, *args, allow_unvalidated_alpha_betting=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_unvalidated_alpha_betting = bool(allow_unvalidated_alpha_betting)

    def predict(self, m):
        p = super().predict(m)
        p.reasons.append("Stage2 OOS: probability-core candidate passed; full betting promotion failed ROI/max-DD gate.")
        p.reasons.append("Stage2: 3.4 remains staking baseline; 3.5 new modules are shadow unless separately validated.")
        if p.uncertainty is None:
            p.uncertainty = {}
        p.uncertainty["stage2_status"] = STAGE2_STATUS
        p.uncertainty["stage2_activation_registry"] = dict(STAGE2_ACTIVATION_REGISTRY)
        p.uncertainty["stage2_reference_probability"] = reference_mdv_temperature(
            (m.home_odds, m.draw_odds, m.away_odds)
        )
        if not self.allow_unvalidated_alpha_betting:
            p.validation_status = "STAGE2_SHADOW_ONLY"
            p.validation_missing = sorted(set((p.validation_missing or []) + [
                "SPORTTERY_SPECIFIC_OOS_EV_TIER_VALIDATION"
            ]))
            p.grade = "UNVERIFIED"
            if p.play_grades:
                p.play_grades = {
                    k: ("UNVERIFIED" if v in ("S", "A1", "A2") else v)
                    for k, v in p.play_grades.items()
                }
        return p


def stage2_status():
    return {
        "version": Jingcai35AlphaStage2Engine.VERSION,
        "status": STAGE2_STATUS,
        "oos_rows": STAGE2_OOS_N,
        "folds": STAGE2_FOLDS,
        "devig_reference_weights": DEVIG_REFERENCE_WEIGHTS,
        "temperature_reference": TEMPERATURE_REFERENCE,
        "activation_registry": STAGE2_ACTIVATION_REGISTRY,
        "staking_baseline": "Jingcai 3.4",
    }


if __name__ == "__main__":
    import json
    print(json.dumps(stage2_status(), ensure_ascii=False, indent=2))
