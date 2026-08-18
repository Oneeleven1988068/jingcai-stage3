#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jingcai 3.5 Alpha
-------------
Betting-oriented upgrade of Jingcai 2.4.

Design:
1) Case1/2/3 are diagnostic-only and do NOT alter production selection or grade.
2) Market implied probabilities + Poisson/Dixon-Coles score layer.
3) S grade -> exact-score betting: Top-N score coverage is evaluated.
4) A grade -> 1X2 betting; optional Asian/let-ball evaluation when handicap
   odds AND a compatible result field are supplied.
5) B/C -> no core bet.
6) Chronological non-overlapping holdout reporting; no claim of training unless parameters are actually fitted.
7) No future-result leakage.
8) Produces machine-readable JSON and optional CSV predictions.

Important data limitation:
- A CSV containing only 1X2 odds + final score can evaluate S exact-score
  coverage and A 1X2 performance.
- Exact-score ROI requires exact-score odds, which are not present in hf_oos.
- Let-ball ROI requires handicap odds/results, which are not present in hf_oos.
The engine never invents these missing fields.

3.4.3 decision-chain change:
- Case1/Case2/Case3 no longer exclude H/D/A and no longer affect grading.
- The legacy Case signal is still logged in odds_rule_case and
  case_excluded_signal for research/ablation only.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple, Any, Sequence
import argparse, csv, json, math, os
from pathlib import Path
from collections import defaultdict, deque
from datetime import timedelta, datetime, timezone
from statistics import median
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import time

OUTCOMES = ("H", "D", "A")

# ---------------------------------------------------------------------------
# Jingcai 3.5 ALPHA PRODUCTION POLICY
# Frozen from the strict OOS decision:
#   1) Case1/2/3 are retired from the production decision chain. They remain diagnostic-only.
#   2) A-grade is an outcome/handicap betting grade. Score-market disagreement
#      must NOT demote a valid A; exact-score validation is for S only.
#   3) Goal engine: 1X2 + O/U + league ETG -> lambda_home/lambda_away/rho;
#      handicap is fused only downstream in coherent goal-difference space.
#   4) Production score pair:
#        - Bivariate Poisson = primary Top3 ranking.
#        - Dynamic-rho Dixon-Coles = independent confirmation / Top5 coverage.
#   5) Exact-score odds are downstream de-vig/value validation only.
#   6) ZIP / Negative Binomial / CMP / Hierarchical Bayes / Rolling xG /
#      Elo / Pi / LightGBM / CatBoost / Platt remain diagnostic-only until
#      separate strict OOS activation.
# ---------------------------------------------------------------------------
PRODUCTION_POLICY = {
    "version": "3.5-alpha",
    "outcome_layer": "standard_1x2_no_case_production",
    "goal_engine": "1x2_ou_etg_then_coherent_goal_difference_market_fusion",
    "score_primary": "bivariate_poisson",
    "score_confirmation": "dynamic_rho_dixon_coles",
    "hard_sequence": "1x2_result_then_coherent_margin_distribution_then_total_goals_then_exact_score",
    "bivariate_shared": 0.12,
    "s_requires_score_value": True,
    "s_requires_dual_model_confirmation": True,
    "a_score_market_independent": True,
    "research_models_diagnostic_only": False,
    "alpha_status": "EXPERIMENTAL_REQUIRES_WALK_FORWARD_OOS",
    "global_market_prior": True,
    "multi_devig": ["multiplicative", "power", "shin"],
    "joint_market_calibration": True,
    "multi_score_distribution": ["poisson", "dixon_coles", "bivariate_poisson", "negative_binomial", "hierarchical_bayes"],
    "residual_ml": True,
    "ev_edge_gate": True,
    "strict_external_market_gate": True,
    "play_specific_grades": True,
}

# ---------------------------------------------------------------------------
# Jingcai 2.8 production score policy
# ---------------------------------------------------------------------------
# Frozen after strict OOS:
# - Dynamic odds table / Case1/2/3 are retained for diagnostics only; they never exclude an outcome.
# - S/A/B/C grading remains unchanged.
# - A-grade 1X2 logic remains unchanged.
# - S exact-score Top3/Top5 are ranked by outcome-aware MARKET score grid.
# - Rolling team/xG is diagnostic only. It must not overwrite production
#   Top3/Top5 because strict 2026 holdout did not show stable improvement.
# - Experimental score models can be supplied separately and compared OOS
#   before activation.
PRODUCTION_SCORE_POLICY = {
    "s_topn_primary": "outcome_aware_dixon_coles_market",
    "rolling_xg_role": "diagnostic_only",
    "bivariate_poisson_role": "diagnostic_only",
    "negative_binomial_role": "diagnostic_only",
    "weibull_copula_role": "research_candidate",
    "hierarchical_bayesian_role": "research_candidate",
    "activation_rule": "candidate must improve untouched holdout Top3 or Top5 before production activation",
    "a_layer_frozen": False,
    "dynamic_odds_table_role": "diagnostic_only",
}

@dataclass
class OddsInterval:
    low: float
    high: float
    def contains(self, x: float) -> bool:
        return self.low <= x <= self.high
    def edge_distance(self, x: float) -> float:
        return min(abs(x-self.low), abs(self.high-x))

@dataclass
class MatchInput:
    match_id: str
    home_odds: float
    draw_odds: float
    away_odds: float
    goals_home: Optional[int] = None
    goals_away: Optional[int] = None
    support_home: Optional[float] = None
    support_draw: Optional[float] = None
    support_away: Optional[float] = None
    over_odds: Optional[float] = None
    under_odds: Optional[float] = None
    btts_yes: Optional[float] = None
    top_scores: Optional[List[str]] = None
    handicap_home: Optional[float] = None
    handicap_draw: Optional[float] = None
    handicap_away: Optional[float] = None
    # Sports-lottery home-team handicap line, normally -1 or +1.
    handicap_line: Optional[int] = None
    handicap_result: Optional[str] = None
    date: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None

    # Optional pre-match information. These fields are never fabricated.
    odds_history: Optional[List[Tuple[float,float,float]]] = None
    expected_goals_home: Optional[float] = None
    expected_goals_away: Optional[float] = None

    # Optional identity fields used by the real rolling xG/team-strength layer.
    league_id: Optional[int] = None
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
    xg_source: Optional[str] = None

    # Jingcai 3.4 research inputs ------------------------------------------------
    # O/U market. Existing over_odds/under_odds are interpreted at total_line.
    total_line: float = 2.5

    # Independent team-strength signals. These never fabricate values.
    elo_home: Optional[float] = None
    elo_away: Optional[float] = None
    pi_home: Optional[float] = None
    pi_away: Optional[float] = None

    # Optional league dispersion (variance / mean style index). 1.0 ~= Poisson.
    league_dispersion: Optional[float] = None

    # Sufficient statistics for conjugate hierarchical/Gamma-Poisson shrinkage.
    home_goals_for_hist: Optional[float] = None
    home_goals_against_hist: Optional[float] = None
    away_goals_for_hist: Optional[float] = None
    away_goals_against_hist: Optional[float] = None
    home_matches_hist: Optional[int] = None
    away_matches_hist: Optional[int] = None

    # Optional structured pre-match intelligence (injuries, rotation, fatigue,
    # suspensions, motivation etc.). It is intended for trained meta models;
    # it never directly overrides odds by hand-coded rules.
    structured_context: Optional[Dict[str, Any]] = None

    # Jingcai 3.0 optional inputs.
    # `league` is used only for a conservative ETG prior/regularizer.
    # `league_etg` overrides the built-in fallback and is preferred when a
    # rolling pre-match league average is available.
    league: Optional[str] = None
    league_etg: Optional[float] = None

    # Exact-score market odds. Supported keys:
    # "0-0", "1-0", "0-1", ... plus optional outcome residual buckets
    # "H_OTHER", "D_OTHER", "A_OTHER" (胜其他/平其他/负其他).
    exact_score_odds: Optional[Dict[str, float]] = None

    # Exact total-goals market odds: keys 0,1,2,3,4,5,6 and "7+".
    # The market is standard de-vigged and fused upstream of exact scores.
    total_goal_odds: Optional[Dict[Any, float]] = None

    # Jingcai 3.5 Alpha market/data inputs -----------------------------------
    # External reference rows are source-level decimal odds. Each 1X2 row uses
    # {bookmaker, home, draw, away}; totals use {bookmaker,line,over,under};
    # handicap rows preferably use 3-way {bookmaker,line,home,draw,away}.
    external_1x2_books: Optional[List[Dict[str, Any]]] = None
    external_total_books: Optional[List[Dict[str, Any]]] = None
    external_handicap_books: Optional[List[Dict[str, Any]]] = None

    # Half/full-time market from Sporttery, keys hh,hd,ha,dh,dd,da,ah,ad,aa.
    hafu_odds: Optional[Dict[str, float]] = None

    # Market provenance / movement. opening_odds is raw H/D/A decimal odds.
    opening_odds: Optional[Tuple[float,float,float]] = None
    market_timestamp: Optional[str] = None
    data_quality: Optional[Dict[str, Any]] = None

@dataclass
class Prediction:
    match_id: str
    probabilities: Dict[str,float]
    market_probabilities: Dict[str,float]
    score_1x2_probabilities: Dict[str,float]
    selected: str
    excluded: Optional[str]
    grade: str
    confidence: float
    odds_rule_case: int
    score_top: List[Tuple[str,float]]
    reasons: List[str]
    total_goals_top: Optional[List[Tuple[int,float]]] = None
    margin_structure: Optional[Dict[str,Any]] = None
    # Case1/2/3 are retained only as research/diagnostic signals.
    # This field records which result the legacy rule *would* have excluded.
    case_excluded_signal: Optional[str] = None

    # 3.5 Alpha outputs. Legacy fields remain for backward compatibility.
    play_grades: Optional[Dict[str,str]] = None
    play_picks: Optional[Dict[str,Any]] = None
    play_ev: Optional[Dict[str,Any]] = None
    validation_status: str = "LEGACY"
    validation_missing: Optional[List[str]] = None
    market_prior: Optional[Dict[str,float]] = None
    market_prior_meta: Optional[Dict[str,Any]] = None
    uncertainty: Optional[Dict[str,Any]] = None
    alpha_status: str = "LEGACY_3_4_COMPAT"

class JingcaiEngine:
    # These remain the 2.4 deployment defaults. Replace with the user's
    # production baseline when available; do not silently invent one.
    # Production normal-odds table supplied by the user.
    # Row selection is determined ONLY by the home-win odds.
    #
    # Boundary convention:
    #   >2.50
    #   [2.00, 2.50]
    #   [1.75, 2.00)
    #   [1.50, 1.75)
    #   [1.30, 1.50)
    #   [1.15, 1.30)
    #   <1.15
    #
    # This removes the old placeholder fixed H/D/A intervals.
    PRODUCTION_ODDS_TABLE = (
        {"home_min": 2.50, "home_max": None, "home_min_open": True,
         "D": OddsInterval(3.00, 3.20), "A": OddsInterval(2.50, 2.80),
         "label": ">2.50"},
        {"home_min": 2.00, "home_max": 2.50, "home_min_open": False,
         "D": OddsInterval(3.00, 3.20), "A": OddsInterval(2.60, 3.50),
         "label": "2.00-2.50"},
        {"home_min": 1.75, "home_max": 2.00, "home_min_open": False,
         "D": OddsInterval(3.00, 3.50), "A": OddsInterval(3.50, 4.50),
         "label": "1.75-2.00"},
        {"home_min": 1.50, "home_max": 1.75, "home_min_open": False,
         "D": OddsInterval(3.20, 4.00), "A": OddsInterval(3.80, 6.50),
         "label": "1.50-1.75"},
        {"home_min": 1.30, "home_max": 1.50, "home_min_open": False,
         "D": OddsInterval(3.50, 5.50), "A": OddsInterval(5.00, 7.50),
         "label": "1.30-1.50"},
        {"home_min": 1.15, "home_max": 1.30, "home_min_open": False,
         "D": OddsInterval(4.00, 6.00), "A": OddsInterval(6.50, 12.00),
         "label": "1.15-1.30"},
        {"home_min": None, "home_max": 1.15, "home_min_open": False,
         "D": OddsInterval(4.50, 6.00), "A": OddsInterval(10.00, float("inf")),
         "label": "<1.15"},
    )

    MARKET_WEIGHT = 0.65
    SCORE_WEIGHT = 0.35
    DC_RHO = -0.08
    BIVARIATE_SHARED = 0.12

    # Conservative fallback ETG priors. These are not treated as live facts:
    # callers should pass `league_etg` when they have a rolling pre-match
    # league average. The priors only regularize sparse/noisy score markets.
    LEAGUE_ETG_PRIORS = {
        "日职": 2.45, "J1": 2.45,
        "日乙": 2.35, "J2": 2.35,
        "韩职": 2.35, "K1": 2.35, "K LEAGUE 1": 2.35,
        "德乙": 2.85, "2. BUNDESLIGA": 2.85,
        "英冠": 2.65, "CHAMPIONSHIP": 2.65,
        "瑞超": 2.80, "ALLSVENSKAN": 2.80,
    }

    def __init__(self, normal_intervals=None, market_weight=None,
                 score_weight=None, grade_s_conf=0.58, grade_s_top=0.54,
                 grade_a_conf=0.47, grade_a_top=0.44,
                 a_min_odds=1.60, strength_weight=0.0,
                 enable_research_score_ensemble=False,
                 meta_predictor=None, probability_calibrator=None):
        self.intervals = normal_intervals
        mw = self.MARKET_WEIGHT if market_weight is None else market_weight
        sw = self.SCORE_WEIGHT if score_weight is None else score_weight
        z = max(mw+sw, 1e-12)
        self.market_weight, self.score_weight = mw/z, sw/z
        self.grade_s_conf, self.grade_s_top = grade_s_conf, grade_s_top
        self.grade_a_conf, self.grade_a_top = grade_a_conf, grade_a_top
        self.a_min_odds = a_min_odds
        self.strength_weight = max(0.0, min(0.30, float(strength_weight)))
        self.enable_research_score_ensemble = bool(enable_research_score_ensemble)
        self.meta_predictor = meta_predictor
        self.probability_calibrator = probability_calibrator
        self._score_cache = {}

    @staticmethod
    def normalize(v):
        s=sum(max(x,0.0) for x in v.values())
        return {k:(max(x,0.0)/s if s else 1/len(v)) for k,x in v.items()}

    def market_probabilities(self,m):
        inv={"H":1/m.home_odds,"D":1/m.draw_odds,"A":1/m.away_odds}
        return self.normalize(inv)

    def _production_row(self, home_odds):
        h=float(home_odds)
        # Exact boundaries are assigned to the row whose lower bound equals
        # the boundary: 2.00 -> 2.00-2.50, 1.75 -> 1.75-2.00, etc.
        if h > 2.50:
            return self.PRODUCTION_ODDS_TABLE[0]
        if h >= 2.00:
            return self.PRODUCTION_ODDS_TABLE[1]
        if h >= 1.75:
            return self.PRODUCTION_ODDS_TABLE[2]
        if h >= 1.50:
            return self.PRODUCTION_ODDS_TABLE[3]
        if h >= 1.30:
            return self.PRODUCTION_ODDS_TABLE[4]
        if h >= 1.15:
            return self.PRODUCTION_ODDS_TABLE[5]
        return self.PRODUCTION_ODDS_TABLE[6]

    def odds_rule(self,m):
        """
        Production implementation of the user's normal-odds table + three
        mutually-exclusive screening rules.

        Because the row is selected by home odds, H is the row anchor and is
        considered in-range by construction.

        Case 1: H/D/A all normal -> compare each item's normalized position
                inside its corresponding row interval; exclude the one closest
                to either boundary.
        Case 2: exactly one of D/A is outside -> exclude that outside item.
        Case 3: D and A are both outside -> only H remains normal, so exclude H.
        """
        odds={"H":float(m.home_odds),"D":float(m.draw_odds),"A":float(m.away_odds)}

        # Optional custom fixed intervals remain supported only if explicitly passed.
        if self.intervals is not None:
            inside={k:self.intervals[k].contains(v) for k,v in odds.items()}
            n=sum(inside.values())
            if n==3:
                dist={k:self.intervals[k].edge_distance(odds[k]) for k in OUTCOMES}
                ex=min(dist,key=dist.get)
                return 1,ex,[f"Case1(custom): nearest interval boundary {ex} excluded."]
            if n==2:
                ex=next(k for k,v in inside.items() if not v)
                return 2,ex,[f"Case2(custom): sole out-of-range outcome {ex} excluded."]
            if n==1:
                ex=next(k for k,v in inside.items() if v)
                return 3,ex,[f"Case3(custom): sole in-range outcome {ex} excluded."]
            return 0,None,["Custom intervals: all three outcomes outside; forced C/no-bet."]

        row=self._production_row(odds["H"])
        d_in=row["D"].contains(odds["D"])
        a_in=row["A"].contains(odds["A"])

        # Case 1 follows the user's original example literally:
        # compare RAW odds-distance to the nearest valid interval boundary.
        # For an open-ended band, use its only finite boundary.
        label=row["label"]
        if label==">2.50":
            h_edge=abs(odds["H"]-2.50)
        elif label=="<1.15":
            h_edge=abs(1.15-odds["H"])
        else:
            lo=float(row["home_min"]); hi=float(row["home_max"])
            h_edge=min(abs(odds["H"]-lo),abs(hi-odds["H"]))

        def raw_edge(interval, x):
            lo,hi=interval.low,interval.high
            if math.isinf(hi):
                return abs(x-lo)
            return min(abs(x-lo),abs(hi-x))

        if d_in and a_in:
            dist={
                "H":h_edge,
                "D":raw_edge(row["D"],odds["D"]),
                "A":raw_edge(row["A"],odds["A"]),
            }
            ex=min(dist,key=dist.get)
            return 1,ex,[f"Case1[{label}]: H/D/A all normal; raw nearest-boundary {ex} excluded."]

        if d_in != a_in:
            ex="A" if d_in else "D"
            return 2,ex,[f"Case2[{label}]: sole out-of-range outcome {ex} excluded."]

        # Both D and A are outside; H is the sole normal item.
        return 3,"H",[f"Case3[{label}]: only H is normal; H excluded."]

    @staticmethod
    def poisson(k,lam):
        return math.exp(-lam)*lam**k/math.factorial(k) if lam>0 else (1.0 if k==0 else 0.0)

    def league_etg_target(self, m, market=None):
        """Return pre-match ETG target used as a regularizer, never as a hard truth."""
        if m is not None and m.league_etg is not None:
            return max(1.60, min(3.80, float(m.league_etg)))
        name = (getattr(m, "league", None) or "").strip()
        if name:
            upper = name.upper()
            for key, val in self.LEAGUE_ETG_PRIORS.items():
                if key.upper() in upper:
                    return float(val)
        if market is None and m is not None:
            market = self.market_probabilities(m)
        if market:
            # Legacy 2.9 market ETG as neutral fallback.
            pd = market["D"]
            return min(3.35, max(1.85, 2.55 + 0.45*(1-pd)))
        return 2.65

    def _dc_matrix_from_lambdas(self, lh, la, max_goals=10):
        mat = {}
        for h in range(max_goals+1):
            for a in range(max_goals+1):
                p = self.poisson(h,lh)*self.poisson(a,la)
                if h==0 and a==0: tau=1-lh*la*self.DC_RHO
                elif h==0 and a==1: tau=1+lh*self.DC_RHO
                elif h==1 and a==0: tau=1+la*self.DC_RHO
                elif h==1 and a==1: tau=1-self.DC_RHO
                else: tau=1.0
                mat[(h,a)] = max(0.0, p*tau)
        z = sum(mat.values())
        return {k:v/z for k,v in mat.items()} if z else mat

    @staticmethod
    def _score_key_to_tuple(key):
        try:
            h,a = key.split("-")
            return int(h), int(a)
        except Exception:
            return None

    def score_market_devig(self, m):
        """De-vig the complete supplied score market by inverse-odds normalization."""
        odds = getattr(m, "exact_score_odds", None) or {}
        inv = {}
        for key, odd in odds.items():
            try:
                odd = float(odd)
            except Exception:
                continue
            if odd > 1.0 and (self._score_key_to_tuple(key) is not None or key in {"H_OTHER","D_OTHER","A_OTHER"}):
                inv[key] = 1.0 / odd
        z = sum(inv.values())
        if z <= 0:
            return {}
        return {k:v/z for k,v in inv.items()}

    def _bucket_model_probs(self, mat, market_keys):
        exact = {k for k in market_keys if self._score_key_to_tuple(k) is not None}
        out = {}
        used = {"H":0.0, "D":0.0, "A":0.0}
        totals = {"H":0.0, "D":0.0, "A":0.0}
        for (h,a), p in mat.items():
            side = "H" if h>a else "D" if h==a else "A"
            totals[side] += p
            k = f"{h}-{a}"
            if k in exact:
                out[k] = p
                used[side] += p
        for side in ("H","D","A"):
            key = side + "_OTHER"
            if key in market_keys:
                out[key] = max(1e-12, totals[side] - used[side])
        z = sum(out.values())
        return ({k:v/z for k,v in out.items()} if z else out)

    def fit_lambdas_from_score_market(self, m, market=None):
        """Infer λ_home/λ_away from de-vigged exact-score odds with league/1X2 regularization."""
        target = self.score_market_devig(m)
        if len(target) < 6:
            return None
        market = market or self.market_probabilities(m)
        etg = self.league_etg_target(m, market)

        def loss(lh, la):
            mat = self._dc_matrix_from_lambdas(lh, la, max_goals=10)
            b = self._bucket_model_probs(mat, target.keys())
            # Cross entropy on score-market probabilities.
            ce = -sum(q * math.log(max(b.get(k,1e-12),1e-12)) for k,q in target.items())
            # Preserve 1X2 as a soft anchor, not the λ source.
            sides = {"H":0.0,"D":0.0,"A":0.0}
            for (h,a),p in mat.items():
                sides["H" if h>a else "D" if h==a else "A"] += p
            one_x_two = sum((sides[k]-market[k])**2 for k in OUTCOMES)
            # Dynamic league ETG regularization; lower-scoring leagues naturally
            # resist a noisy score board that implies an implausibly high total.
            etg_pen = ((lh+la-etg)/0.55)**2
            return ce + 0.55*one_x_two + 0.10*etg_pen

        # Robust two-stage deterministic search; no scipy dependency.
        best = (float("inf"), 1.35, 1.15)
        for lh_i in range(35, 306, 10):
            lh = lh_i/100.0
            for la_i in range(25, 286, 10):
                la = la_i/100.0
                v = loss(lh,la)
                if v < best[0]:
                    best = (v,lh,la)
        _, bh, ba = best
        best2 = best
        for i in range(-10,11):
            lh = max(0.20, bh + i*0.02)
            for j in range(-10,11):
                la = max(0.20, ba + j*0.02)
                v = loss(lh,la)
                if v < best2[0]:
                    best2 = (v,lh,la)
        return best2[1], best2[2], {
            "loss": best2[0],
            "league_etg_target": etg,
            "score_market_points": len(target),
        }

    def _handicap_outcome_from_score(self, h, a, line):
        """Return H/D/A in the lottery handicap market for an integer home line."""
        adj = (h - a) + int(line)
        return "H" if adj > 0 else "D" if adj == 0 else "A"

    def totals_probabilities(self, m):
        """De-vig O/U market at m.total_line; None when unavailable."""
        if m.over_odds is None or m.under_odds is None:
            return None
        try:
            o=float(m.over_odds); u=float(m.under_odds)
        except Exception:
            return None
        if o<=1.0 or u<=1.0:
            return None
        z=1.0/o+1.0/u
        return {"O":(1.0/o)/z,"U":(1.0/u)/z,"line":float(getattr(m,"total_line",2.5))}

    @staticmethod
    def _dc_matrix_params(lh, la, rho=-0.08, max_goals=10):
        mat={}
        for h in range(max_goals+1):
            for a in range(max_goals+1):
                ph=math.exp(-lh)*lh**h/math.factorial(h)
                pa=math.exp(-la)*la**a/math.factorial(a)
                p=ph*pa
                if h==0 and a==0: tau=1-lh*la*rho
                elif h==0 and a==1: tau=1+lh*rho
                elif h==1 and a==0: tau=1+la*rho
                elif h==1 and a==1: tau=1-rho
                else: tau=1.0
                mat[(h,a)]=max(0.0,p*tau)
        z=sum(mat.values())
        return {k:v/z for k,v in mat.items()} if z else mat

    def strength_probabilities(self, m, market=None):
        """
        Independent Elo/Pi 1X2 signals.
        Draw mass is anchored to market draw probability; rating systems decide
        how the non-draw mass splits between H/A.
        """
        market=market or self.market_probabilities(m)
        pieces=[]
        if m.elo_home is not None and m.elo_away is not None:
            d=float(m.elo_home)-float(m.elo_away)
            qh=1.0/(1.0+10.0**(-d/400.0))
            pd=market["D"]
            pieces.append({"H":(1-pd)*qh,"D":pd,"A":(1-pd)*(1-qh)})
        if m.pi_home is not None and m.pi_away is not None:
            # Pi ratings are treated only as a relative signal; scale is deliberately
            # conservative until calibrated OOS.
            d=max(-4.0,min(4.0,float(m.pi_home)-float(m.pi_away)))
            qh=1.0/(1.0+math.exp(-0.85*d))
            pd=market["D"]
            pieces.append({"H":(1-pd)*qh,"D":pd,"A":(1-pd)*(1-qh)})
        if not pieces:
            return None
        out={k:sum(x[k] for x in pieces)/len(pieces) for k in OUTCOMES}
        return self.normalize(out)

    def fit_goal_parameters(self, m, market=None):
        """
        3.4 BASE goal engine:
          ordinary 1X2 + O/U + league ETG -> λH, λA, rho.

        IMPORTANT:
        Handicap odds are deliberately NOT used here. They are fused later in
        goal-difference space. This prevents double-counting the same handicap
        information once in λ and again in the margin layer.
        """
        market=market or self.market_probabilities(m)
        ou=self.totals_probabilities(m)
        etg=self.league_etg_target(m,market)
        def objective(lh,la,rho):
            mat=self._dc_matrix_params(lh,la,rho,max_goals=10)
            p1={"H":0.0,"D":0.0,"A":0.0}
            over=0.0
            for (h,a),p in mat.items():
                p1["H" if h>a else "D" if h==a else "A"]+=p
                if ou is not None and (h+a)>ou["line"]:
                    over+=p
            l1=sum((p1[k]-market[k])**2 for k in OUTCOMES)
            lou=(over-ou["O"])**2 if ou is not None else 0.0
            letg=((lh+la-etg)/0.60)**2
            return 1.00*l1+0.95*lou+0.08*letg

        best=(float("inf"),1.35,1.15,self.DC_RHO)
        for lh_i in range(35,316,20):
            lh=lh_i/100.0
            for la_i in range(25,296,20):
                la=la_i/100.0
                for rho in (-0.16,-0.12,-0.08,-0.04,0.0,0.04,0.08):
                    v=objective(lh,la,rho)
                    if v<best[0]:
                        best=(v,lh,la,rho)
        _,bh,ba,br=best
        best2=best
        for i in range(-8,9):
            lh=max(0.20,bh+i*0.025)
            for j in range(-8,9):
                la=max(0.20,ba+j*0.025)
                for rr in (br-0.04,br-0.02,br,br+0.02,br+0.04):
                    rho=max(-0.20,min(0.12,rr))
                    v=objective(lh,la,rho)
                    if v<best2[0]:
                        best2=(v,lh,la,rho)
        return best2[1],best2[2],best2[3],{
            "loss":best2[0],
            "league_etg_target":etg,
            "handicap_used":False,
            "handicap_fused_downstream":bool(self.handicap_probs(m) is not None and getattr(m,"handicap_line",None) in (-1,1)),
            "ou_used":bool(ou is not None),
            "total_line": None if ou is None else ou["line"],
        }


    def fit_lambdas_from_result_handicap(self, m, market=None):
        """Backward-compatible wrapper around the 3.3 λ/ρ market engine."""
        lh,la,rho,meta=self.fit_goal_parameters(m,market)
        meta=dict(meta); meta["rho"]=rho
        return lh,la,meta


    def lambdas(self,market,m=None):
        if m is not None and m.expected_goals_home is not None and m.expected_goals_away is not None:
            lh=max(0.10,min(4.50,float(m.expected_goals_home)))
            la=max(0.10,min(4.00,float(m.expected_goals_away)))
            return lh,la
        if m is not None:
            lh,la,_,_=self.fit_goal_parameters(m,market)
            return lh,la
        ph,pd,pa=market["H"],market["D"],market["A"]
        total=min(3.35,max(1.85,2.55+0.45*(1-pd)))
        strength=math.log((ph+1e-6)/(pa+1e-6))
        diff=max(-1.55,min(1.55,0.52*strength))
        lh=max(0.20,(total+diff)/2)
        return lh,max(0.20,total-lh)


    def score_matrix(self,m,max_goals=7):
        key=(
            round(m.home_odds,4),round(m.draw_odds,4),round(m.away_odds,4),
            None if m.handicap_home is None else round(m.handicap_home,4),
            None if m.handicap_draw is None else round(m.handicap_draw,4),
            None if m.handicap_away is None else round(m.handicap_away,4),
            getattr(m,"handicap_line",None),
            None if m.over_odds is None else round(float(m.over_odds),4),
            None if m.under_odds is None else round(float(m.under_odds),4),
            getattr(m,"total_line",2.5),
            getattr(m,"league",None),
            None if getattr(m,"league_etg",None) is None else round(m.league_etg,4),
            None if m.expected_goals_home is None else round(m.expected_goals_home,4),
            None if m.expected_goals_away is None else round(m.expected_goals_away,4),
            max_goals
        )
        if key in self._score_cache:
            return self._score_cache[key]
        market=self.market_probabilities(m)
        if m.expected_goals_home is not None and m.expected_goals_away is not None:
            lh,la=self.lambdas(market,m)
            rho=self.DC_RHO
        else:
            lh,la,rho,_=self.fit_goal_parameters(m,market)
        out=self._dc_matrix_params(lh,la,rho,max_goals=max_goals)
        self._score_cache[key]=out
        return out

    def score_probabilities(self,m):
        mat=self.score_matrix(m)
        p={"H":0,"D":0,"A":0}
        for (h,a),v in mat.items():
            p["H" if h>a else "D" if h==a else "A"] += v
        return self.normalize(p)

    def trajectory(self,m):
        h=m.odds_history or []
        if len(h)<2:
            return {"available":False,"reversal":False,"strength":0.0}
        s,e=h[0],h[-1]
        dh,dd,da=e[0]-s[0],e[1]-s[1],e[2]-s[2]
        reversal=(dh>=0.08 and da<=-0.08) or (dh<=-0.08 and da>=0.08)
        strength=min(1.0,(abs(dh)+abs(da))/0.40) if reversal else 0.0
        return {"available":True,"reversal":reversal,"strength":strength,"delta":{"H":dh,"D":dd,"A":da}}

    def handicap_probs(self,m):
        vals=(m.handicap_home,m.handicap_draw,m.handicap_away)
        if any(v is None or v<=1.0 for v in vals):
            return None
        return self.normalize({"H":1/vals[0],"D":1/vals[1],"A":1/vals[2]})

    @staticmethod
    def _nb_pmf(k, mu, alpha):
        """NB2 parameterisation: Var = mu + alpha * mu^2."""
        if alpha <= 1e-10:
            return math.exp(-mu) * mu**k / math.factorial(k)
        r = 1.0 / alpha
        p = r / (r + mu)
        return math.exp(
            math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
            + r * math.log(p) + k * math.log1p(-p)
        )

    @staticmethod
    def _zip_pmf(k, mu, omega):
        """Zero-inflated Poisson preserving target marginal mean approximately."""
        omega=max(0.0,min(0.35,float(omega)))
        lam=max(1e-8,float(mu)/(1.0-omega)) if omega<0.999 else float(mu)
        pois=math.exp(-lam)*lam**k/math.factorial(k)
        return omega+(1.0-omega)*pois if k==0 else (1.0-omega)*pois

    @staticmethod
    def _cmp_distribution(mu, nu, max_goals=12):
        """
        Conway-Maxwell-Poisson discrete pmf with lambda numerically chosen
        to match target mean. nu>1 under-dispersed, nu<1 over-dispersed.
        """
        mu=max(1e-6,float(mu)); nu=max(0.35,min(2.5,float(nu)))
        def dist(lam):
            vals=[]
            for k in range(max_goals+1):
                logv=k*math.log(max(lam,1e-12))-nu*math.lgamma(k+1)
                vals.append(math.exp(min(700.0,logv)))
            z=sum(vals)
            probs=[v/z for v in vals]
            mean=sum(k*p for k,p in enumerate(probs))
            return probs,mean
        lo,hi=1e-5,max(2.0,mu*4+4)
        for _ in range(45):
            mid=(lo+hi)/2
            _,mean=dist(mid)
            if mean<mu: lo=mid
            else: hi=mid
        probs,_=dist((lo+hi)/2)
        return probs

    @staticmethod
    def _bivariate_poisson_pmf(x, y, mean_h, mean_a, shared):
        """
        Karlis/Ntzoufras construction:
            X = U + W, Y = V + W
        while preserving requested marginal means.
        """
        c = min(
            max(float(shared), 0.0),
            max(float(mean_h) - 1e-8, 0.0),
            max(float(mean_a) - 1e-8, 0.0),
        )
        l1 = max(float(mean_h) - c, 1e-8)
        l2 = max(float(mean_a) - c, 1e-8)
        total = 0.0
        for j in range(min(int(x), int(y)) + 1):
            total += (
                l1 ** (x-j) / math.factorial(x-j)
                * l2 ** (y-j) / math.factorial(y-j)
                * c ** j / math.factorial(j)
            )
        return math.exp(-(l1+l2+c)) * total

    def hierarchical_bayes_lambdas(self, m, base_lh=None, base_la=None):
        """
        Conjugate Gamma-Poisson shrinkage using league ETG as hyper-prior.
        This is a lightweight hierarchical Bayes layer for sparse teams.
        When sufficient stats are absent, it returns the market goal parameters.
        """
        market=self.market_probabilities(m)
        if base_lh is None or base_la is None:
            base_lh,base_la,_,_=self.fit_goal_parameters(m,market)
        hm=getattr(m,"home_matches_hist",None)
        am=getattr(m,"away_matches_hist",None)
        fields=(m.home_goals_for_hist,m.home_goals_against_hist,
                m.away_goals_for_hist,m.away_goals_against_hist,hm,am)
        if any(x is None for x in fields) or hm<=0 or am<=0:
            return base_lh,base_la,False

        etg=self.league_etg_target(m,market)
        league_h=max(0.4,etg*0.54)
        league_a=max(0.35,etg*0.46)
        prior_matches=6.0

        def posterior_rate(goals,matches,prior_mean):
            alpha=prior_mean*prior_matches+max(0.0,float(goals))
            beta=prior_matches+max(0.0,float(matches))
            return alpha/beta

        h_attack=posterior_rate(m.home_goals_for_hist,hm,league_h)
        h_def=posterior_rate(m.home_goals_against_hist,hm,league_a)
        a_attack=posterior_rate(m.away_goals_for_hist,am,league_a)
        a_def=posterior_rate(m.away_goals_against_hist,am,league_h)

        # Geometric attack/defence combination, then conservative market blend.
        team_lh=math.sqrt(max(1e-8,h_attack*a_def))
        team_la=math.sqrt(max(1e-8,a_attack*h_def))
        lh=math.sqrt(max(1e-8,base_lh*team_lh))
        la=math.sqrt(max(1e-8,base_la*team_la))
        return max(0.15,min(4.5,lh)),max(0.15,min(4.0,la)),True

    def candidate_score_matrix(self, m, model="dixon_coles",
                               nb_alpha=None, biv_shared=None,
                               zip_omega=None, cmp_nu=None,
                               max_goals=8):
        """
        3.3 score-model zoo. All models use the SAME upstream market goal
        parameters so comparison is about distribution shape, not direction leakage.
        """
        market=self.market_probabilities(m)
        lh,la,rho,_=self.fit_goal_parameters(m,market)

        dispersion=float(m.league_dispersion) if m.league_dispersion is not None else 1.0
        if nb_alpha is None:
            nb_alpha=max(0.015,min(0.35,0.08+0.18*max(0.0,dispersion-1.0)))
        if biv_shared is None:
            biv_shared=self.BIVARIATE_SHARED
        if zip_omega is None:
            etg=lh+la
            zip_omega=max(0.0,min(0.18,0.10*(2.55-etg))) if etg<2.55 else 0.0
        if cmp_nu is None:
            # dispersion>1 -> nu<1; dispersion<1 -> nu>1
            cmp_nu=max(0.55,min(1.65,1.0/max(0.65,min(1.55,dispersion))))

        if model=="dixon_coles":
            return self._dc_matrix_params(lh,la,rho,max_goals=max_goals)

        if model=="hierarchical_bayes":
            bh,ba,_=self.hierarchical_bayes_lambdas(m,lh,la)
            return self._dc_matrix_params(bh,ba,rho,max_goals=max_goals)

        cmp_h=cmp_a=None
        if model=="cmp":
            cmp_h=self._cmp_distribution(lh,cmp_nu,max_goals=max_goals)
            cmp_a=self._cmp_distribution(la,cmp_nu,max_goals=max_goals)

        matrix={}
        for h in range(max_goals+1):
            for a in range(max_goals+1):
                if model=="negative_binomial":
                    prob=self._nb_pmf(h,lh,nb_alpha)*self._nb_pmf(a,la,nb_alpha)
                elif model=="bivariate_poisson":
                    prob=self._bivariate_poisson_pmf(h,a,lh,la,biv_shared)
                elif model=="zero_inflated_poisson":
                    prob=self._zip_pmf(h,lh,zip_omega)*self._zip_pmf(a,la,zip_omega)
                elif model=="cmp":
                    prob=cmp_h[h]*cmp_a[a]
                else:
                    raise ValueError(f"Unknown candidate score model: {model}")
                matrix[(h,a)]=max(0.0,prob)
        z=sum(matrix.values())
        return {k:v/z for k,v in matrix.items()} if z else self.score_matrix(m)

    @staticmethod
    def _normalize_matrix(mat):
        z=sum(max(0.0,v) for v in mat.values())
        return {k:(max(0.0,v)/z if z>0 else 0.0) for k,v in mat.items()}

    def total_goal_market_devig(self, m):
        """Standard de-vig of the 0/1/2/3/4/5/6/7+ total-goals market."""
        odds=getattr(m,"total_goal_odds",None) or {}
        if not odds:
            return None
        raw={}
        for key,val in odds.items():
            try:
                o=float(val)
            except Exception:
                continue
            if o<=1e-12:
                continue
            k=str(key).strip()
            if k in ("7", "7+", "7plus", "7_plus"):
                k="7+"
            elif k.isdigit() and 0 <= int(k) <= 6:
                k=int(k)
            else:
                continue
            raw[k]=1.0/o
        required={0,1,2,3,4,5,6,"7+"}
        if set(raw) != required:
            return None
        z=sum(raw.values())
        return {k:v/z for k,v in raw.items()} if z>0 else None

    def total_target_with_market(self, m, lh, la, max_goals=8):
        """
        Exact total-goals market is authoritative when supplied.
        The 7+ bucket is spread across totals 7..2*max_goals according to the
        base Poisson shape, preserving the market's aggregate 7+ probability.
        """
        base=self._total_target_from_base(lh,la,max_goals=max_goals)
        q=self.total_goal_market_devig(m)
        if not q:
            return base
        out={t:0.0 for t in range(2*max_goals+1)}
        for t in range(7):
            out[t]=q[t]
        tail=sum(base.get(t,0.0) for t in range(7,2*max_goals+1))
        if tail<=1e-15:
            # Conservative fallback if base tail is numerically empty.
            n=max(1,2*max_goals-6)
            for t in range(7,2*max_goals+1): out[t]=q["7+"]/n
        else:
            for t in range(7,2*max_goals+1):
                out[t]=q["7+"]*base.get(t,0.0)/tail
        z=sum(out.values())
        return {k:v/z for k,v in out.items()} if z>0 else base

    def _total_target_from_base(self, lh, la, max_goals=8):
        mu=max(0.05,lh+la)
        vals={t:math.exp(-mu)*(mu**t)/math.factorial(t) for t in range(2*max_goals+1)}
        z=sum(vals.values())
        return {t:v/z for t,v in vals.items()}

    def _rake_score_matrix(self, mat, m, margin_targets, total_targets, iterations=18):
        """
        Iterative proportional fitting:
          - match reconciled goal-difference bucket probabilities;
          - preserve the upstream total-goal distribution.
        Within each cell group, the base model's relative score structure remains.
        """
        out=self._normalize_matrix(dict(mat))
        line=getattr(m,"handicap_line",None)
        if not margin_targets or line not in (-1,1):
            return out

        for _ in range(iterations):
            # Margin projection.
            sums={k:0.0 for k in margin_targets}
            for (h,a),p in out.items():
                b=self._margin_bucket_from_score(h,a,line)
                if b in sums: sums[b]+=p
            for b,target in margin_targets.items():
                cur=sums.get(b,0.0)
                if cur<=1e-15: continue
                factor=target/cur
                for key in list(out):
                    if self._margin_bucket_from_score(key[0],key[1],line)==b:
                        out[key]*=factor
            out=self._normalize_matrix(out)

            # Total-goal projection.
            tsum={t:0.0 for t in total_targets}
            for (h,a),p in out.items():
                t=h+a
                if t in tsum: tsum[t]+=p
            for t,target in total_targets.items():
                cur=tsum.get(t,0.0)
                if cur<=1e-15: continue
                factor=target/cur
                for key in list(out):
                    if key[0]+key[1]==t:
                        out[key]*=factor
            out=self._normalize_matrix(out)
        return out

    def production_score_pair(self, m):
        """
        3.4 production score pair.

        Upstream:
          ordinary 1X2 + O/U + ETG -> λH, λA, rho
        Goal-difference market:
          ordinary 1X2 + 3-way handicap -> reconciled margin buckets
        Fusion:
          IPF calibrates Bivariate/DC score grids to the margin buckets while
          preserving the upstream total-goal distribution.
        """
        market=self.market_probabilities(m)
        lh,la,rho,_=self.fit_goal_parameters(m,market)
        dc=self._dc_matrix_params(lh,la,rho,max_goals=8)

        biv={}
        for h in range(9):
            for a in range(9):
                biv[(h,a)]=max(0.0,self._bivariate_poisson_pmf(h,a,lh,la,self.BIVARIATE_SHARED))
        biv=self._normalize_matrix(biv)

        fused=self.goal_difference_market_fusion(m)
        if fused.get("available"):
            tt=self.total_target_with_market(m,lh,la,max_goals=8)
            biv=self._rake_score_matrix(biv,m,fused["buckets"],tt)
            dc=self._rake_score_matrix(dc,m,fused["buckets"],tt)

        return biv,dc,{
            "lambda_home":lh,"lambda_away":la,"rho":rho,
            "margin_fusion":fused
        }


    def _structured_score_ranking(self, m, selected, mat):
        """
        Production ordering is now:
          1X2 result -> handicap goal difference -> total goals -> exact score.

        The score model only ranks states that survive those three upstream
        constraints. It does not decide the result or total goals itself.
        """
        raw=sorted(
            ((f"{h}-{a}",p) for (h,a),p in mat.items()),
            key=lambda x:x[1],reverse=True
        )

        if selected not in OUTCOMES:
            return raw

        space=self.total_goal_score_space(m,selected,min_scores=7,max_total=9)
        core_set=space["score_set"]
        hs=space["handicap"]
        rule=space["active_rule"]
        allowed_diffs=space.get("allowed_goal_diffs")

        core=[x for x in raw if x[0] in core_set]

        # These fallback groups are placed only after the total-goal-derived
        # core. With >=7 core states the official Top3/Top5 stay upstream-valid.
        margin_rest=[
            x for x in raw
            if x[0] not in core_set
            and self._score_matches_margin_rule(x[0],selected,rule,allowed_diffs)
        ]
        result_rest=[
            x for x in raw
            if x[0] not in core_set
            and x not in margin_rest
            and self._score_side(x[0])==selected
        ]
        incompatible=[
            x for x in raw
            if x[0] not in core_set
            and x not in margin_rest
            and x not in result_rest
        ]
        return core+margin_rest+result_rest+incompatible


    def production_score_consensus(self, m, selected):
        """
        Bivariate Top3 is primary. Dynamic-DC supplies confirmation.

        A primary Top3 score is "dual confirmed" when it also appears in the
        structured DC Top5. No unvalidated averaging weight is introduced.
        """
        biv,dc,meta=self.production_score_pair(m)
        rb=self._structured_score_ranking(m,selected,biv)
        rd=self._structured_score_ranking(m,selected,dc)
        dc_top5={s for s,_ in rd[:5]}
        primary_top3=[s for s,_ in rb[:3]]
        confirmed=[s for s in primary_top3 if s in dc_top5]
        return {
            "primary": rb,
            "dc": rd,
            "primary_top3": primary_top3,
            "dc_top5": [s for s,_ in rd[:5]],
            "confirmed_top3": confirmed,
            "confirmed_count": len(confirmed),
            "meta": meta,
        }

    def research_score_ensemble(self, m):
        """
        Dynamic multi-distribution ensemble:
        DC + Bivariate Poisson + ZIP + Negative Binomial + CMP + Hierarchical Bayes.
        These weights are research defaults, not claimed OOS-optimal.
        """
        market=self.market_probabilities(m)
        lh,la,_,_=self.fit_goal_parameters(m,market)
        etg=lh+la
        disp=float(m.league_dispersion) if m.league_dispersion is not None else 1.0
        _,_,has_bayes=self.hierarchical_bayes_lambdas(m,lh,la)

        weights={
            "dixon_coles":0.30,
            "bivariate_poisson":0.18,
            "zero_inflated_poisson":0.10 if etg<2.55 else 0.04,
            "negative_binomial":0.08+0.10*max(0.0,min(1.0,disp-1.0)),
            "cmp":0.16,
            "hierarchical_bayes":0.14 if has_bayes else 0.04,
        }
        z=sum(weights.values()); weights={k:v/z for k,v in weights.items()}
        mats={name:self.candidate_score_matrix(m,model=name,max_goals=8) for name in weights}
        keys=set().union(*(mat.keys() for mat in mats.values()))
        out={k:sum(weights[name]*mats[name].get(k,0.0) for name in weights) for k in keys}
        z=sum(out.values())
        return ({k:v/z for k,v in out.items()} if z else self.score_matrix(m)),weights


    def candidate_top_scores(self, m, selected, model, topn=5):
        mat = self.candidate_score_matrix(m, model=model)
        ranked = sorted(
            ((f"{h}-{a}", p) for (h,a),p in mat.items()),
            key=lambda x:x[1], reverse=True
        )
        if selected in OUTCOMES:
            ranked = (
                [x for x in ranked if self._score_side(x[0]) == selected]
                + [x for x in ranked if self._score_side(x[0]) != selected]
            )
        return ranked[:topn]

    @staticmethod
    def _score_side(score_text):
        h,a=map(int,score_text.split("-"))
        return "H" if h>a else "D" if h==a else "A"

    @staticmethod
    def ordinary_result_from_goal_diff(goal_diff):
        """Actual ordinary 1X2 from d = home_goals - away_goals."""
        d=int(goal_diff)
        return "H" if d>0 else "D" if d==0 else "A"

    @staticmethod
    def handicap_result_from_goal_diff(goal_diff, line):
        """
        Actual handicap 1X2 from d + line, where line is the home-team
        lottery handicap. Example line=-1:
          d=+1 -> D, d=+2 -> H, d=0 -> A.
        """
        x=int(goal_diff)+int(line)
        return "H" if x>0 else "D" if x==0 else "A"

    @staticmethod
    def _margin_bucket_from_score(h, a, line):
        d=int(h)-int(a)
        if int(line)==-1:
            if d>=2: return "H2+"
            if d==1: return "H1"
            if d==0: return "D"
            return "A"
        if int(line)==1:
            if d>=1: return "H"
            if d==0: return "D"
            if d==-1: return "A1"
            return "A2+"
        return None

    @staticmethod
    def _coherent_margin_aggregates(q, line):
        """Map the four latent goal-difference buckets back to both 3-way markets."""
        if int(line)==-1:
            # q = H2+, H1, D, A
            ordinary={"H":q[0]+q[1],"D":q[2],"A":q[3]}
            handicap={"H":q[0],"D":q[1],"A":q[2]+q[3]}
        else:
            # q = H, D, A1, A2+
            ordinary={"H":q[0],"D":q[1],"A":q[2]+q[3]}
            handicap={"H":q[0]+q[1],"D":q[2],"A":q[3]}
        return ordinary,handicap

    def _coherent_margin_projection(self, market, handicap, line, iterations=120):
        """
        Symmetric KL projection of ordinary 1X2 and three-way handicap markets
        onto one coherent four-state actual-goal-difference distribution.

        Both markets are standard de-vigged first.  No 100-P pseudo-probability,
        no branch deletion, and no fitted weight from tonight's outcomes.
        Exponentiated-gradient descent keeps q positive and on the simplex.
        """
        eps=1e-12
        if int(line)==-1:
            # Directly identified cells supply a stable initial partition.
            q=[handicap["H"],handicap["D"],market["D"],market["A"]]
            names=["H2+","H1","D","A"]
        else:
            q=[market["H"],market["D"],handicap["D"],handicap["A"]]
            names=["H","D","A1","A2+"]
        z=sum(q); q=[max(eps,x/z) for x in q]

        def grad_from_agg(agg,target,mapping):
            # d KL(agg||target) / d q_i
            g=[0.0]*4
            for outcome,idxs in mapping.items():
                a=max(eps,agg[outcome]); t=max(eps,target[outcome])
                term=math.log(a/t)+1.0
                for i in idxs: g[i]+=term
            return g

        if int(line)==-1:
            omap={"H":(0,1),"D":(2,),"A":(3,)}
            hmap={"H":(0,),"D":(1,),"A":(2,3)}
        else:
            omap={"H":(0,),"D":(1,),"A":(2,3)}
            hmap={"H":(0,1),"D":(2,),"A":(3,)}

        # Diminishing step makes this deterministic and dependency-free.
        for it in range(iterations):
            op,hp=self._coherent_margin_aggregates(q,line)
            go=grad_from_agg(op,market,omap)
            gh=grad_from_agg(hp,handicap,hmap)
            g=[go[i]+gh[i] for i in range(4)]
            eta=0.22/math.sqrt(1.0+it/12.0)
            q=[max(eps,q[i]*math.exp(-eta*g[i])) for i in range(4)]
            z=sum(q); q=[x/z for x in q]

        op,hp=self._coherent_margin_aggregates(q,line)
        def kl(a,b):
            return sum(max(eps,a[k])*math.log(max(eps,a[k])/max(eps,b[k])) for k in OUTCOMES)
        obj=kl(op,market)+kl(hp,handicap)
        return dict(zip(names,q)),op,hp,obj

    def goal_difference_market_fusion(self, m):
        """
        3.4.3 coherent latent goal-difference market model.

        Ordinary 1X2 and ±1 three-way handicap are de-vigged by the same
        standard rule and then reconciled in the actual goal-difference state
        space with a symmetric KL projection.

        Home -1 latent partition: H2+, H1, D, A.
        Home +1 latent partition: H, D, A1, A2+.
        """
        mp=self.market_probabilities(m)
        hp=self.handicap_probs(m)
        line=getattr(m,"handicap_line",None)
        if hp is None or line not in (-1,1):
            return {
                "available":False,"line":line,"market":mp,"handicap":hp,
                "buckets":None,"consistency_gap":None,"projection_loss":None,
                "reconciled_market":None,"reconciled_handicap":None,
            }

        q,mp_fit,hp_fit,loss=self._coherent_margin_projection(mp,hp,line)
        if line==-1:
            gap=abs(mp["H"]-(hp["H"]+hp["D"]))
        else:
            gap=abs((mp["H"]+mp["D"])-hp["H"])
        return {
            "available":True,"line":line,"market":mp,"handicap":hp,
            "buckets":q,"consistency_gap":gap,"projection_loss":loss,
            "reconciled_market":mp_fit,"reconciled_handicap":hp_fit,
        }

    def handicap_structure(self, m, selected):
        """
        Report a *distribution* over winning-margin regimes after the ordinary
        result has been selected.  The distribution is never collapsed into a
        hard constraint for score generation.

        margin_signal:
          DETERMINISTIC — ordinary result fixes the ±1 handicap result exactly;
          STRONG        — split posterior >=68% with >=18pp lead;
          LEAN          — split posterior >=60% with >=10pp lead;
          OPEN          — do not make a margin bet; preserve both paths.
        """
        fused=self.goal_difference_market_fusion(m)
        if not fused["available"] or selected not in OUTCOMES:
            return {
                "available":False,"handicap_pick":None,"line":fused.get("line"),
                "margin_rule":"probabilistic_result_only",
                "margin_text":"handicap unavailable; ordinary result only",
                "conflict":False,"allowed_goal_diffs":None,
                "probabilities":fused.get("handicap"),
                "conditional_probabilities":None,
                "compatible_handicap_results":[],
                "margin_buckets":fused.get("buckets"),
                "consistency_gap":fused.get("consistency_gap"),
                "margin_signal":"UNAVAILABLE","margin_bet_pick":None,
            }

        line=fused["line"]; q=fused["buckets"]
        cond={}; mapping={}; exact_diff=False
        if line==-1:
            if selected=="H":
                cond={"win_2plus":q["H2+"],"win_1":q["H1"]}
                mapping={"win_2plus":"H","win_1":"D"}
            elif selected=="D":
                cond={"draw":q["D"]}; mapping={"draw":"A"}; exact_diff=True
            else:
                cond={"away_win":q["A"]}; mapping={"away_win":"A"}
        else:
            if selected=="A":
                cond={"lose_1":q["A1"],"lose_2plus":q["A2+"]}
                mapping={"lose_1":"D","lose_2plus":"A"}
            elif selected=="D":
                cond={"draw":q["D"]}; mapping={"draw":"H"}; exact_diff=True
            else:
                cond={"home_win":q["H"]}; mapping={"home_win":"H"}

        z=sum(cond.values())
        condn={k:(v/z if z>0 else 1/len(cond)) for k,v in cond.items()}
        ordered=sorted(condn.items(),key=lambda kv:kv[1],reverse=True)
        top,top_p=ordered[0]
        second_p=ordered[1][1] if len(ordered)>1 else 0.0
        lead=top_p-second_p
        hpick=mapping[top]

        if len(condn)==1:
            signal="DETERMINISTIC"
        elif top_p>=0.68 and lead>=0.18:
            signal="STRONG"
        elif top_p>=0.60 and lead>=0.10:
            signal="LEAN"
        else:
            signal="OPEN"
        bet_pick=hpick if signal in ("DETERMINISTIC","STRONG","LEAN") else None

        if len(condn)==1:
            margin_text=next(iter(condn.keys()))
        else:
            margin_text=", ".join(f"{k}={v:.1%}" for k,v in ordered)

        return {
            "available":True,"handicap_pick":hpick,"line":line,
            "margin_rule":"coherent_probabilistic_goal_difference",
            "margin_text":margin_text,"conflict":False,
            "allowed_goal_diffs":None,
            "probabilities":fused["handicap"],
            "conditional_probabilities":condn,
            "compatible_handicap_results":sorted(set(mapping.values())),
            "margin_buckets":q,"consistency_gap":fused["consistency_gap"],
            "projection_loss":fused.get("projection_loss"),
            "margin_signal":signal,"margin_bet_pick":bet_pick,
            "margin_top_probability":top_p,"margin_top_lead":lead,
            "exact_goal_difference":exact_diff,
        }



    @staticmethod
    def _score_matches_margin_rule(score, selected, rule, allowed_goal_diffs=None):
        h,a=map(int,score.split("-"))
        gd=h-a

        # Ordinary result is always mandatory.
        if selected=="H" and gd<=0: return False
        if selected=="D" and gd!=0: return False
        if selected=="A" and gd>=0: return False

        # When handicap information is available, the exact market-intersection
        # set is the authoritative goal-difference constraint.
        if allowed_goal_diffs is not None:
            return gd in set(allowed_goal_diffs)

        # No handicap data: ordinary result alone remains valid.
        return True


    def total_goal_profile(self, m, max_total=9):
        """
        Stage 3: infer total goals BEFORE exact score.

        λH/λA are fitted from ordinary 1X2 + O/U + league ETG only.
        Handicap is fused independently in goal-difference space, preventing
        double-counting. Their sum is the upstream expected-total-goals signal.
        Exact-score odds are deliberately excluded.
        """
        market=self.market_probabilities(m)
        lh,la,rho,meta=self.fit_goal_parameters(m,market)
        mu=max(0.05,lh+la)

        q_market=self.total_goal_market_devig(m)
        if q_market:
            # Expand 7+ using the base goal shape; exact 0..6 probabilities stay
            # equal to the de-vigged total-goals market.
            full=self.total_target_with_market(m,lh,la,max_goals=max(8,(max_total+1)//2))
            probs={t:p for t,p in full.items() if t<=max_total}
            z=sum(probs.values())
            if z>0: probs={k:v/z for k,v in probs.items()}
        else:
            probs={}
            for total in range(max_total+1):
                probs[total]=math.exp(-mu)*(mu**total)/math.factorial(total)
            z=sum(probs.values())
            if z>0:
                probs={k:v/z for k,v in probs.items()}

        ranked=sorted(probs.items(),key=lambda kv:kv[1],reverse=True)
        return {
            "etg":mu,
            "lambda_home":lh,
            "lambda_away":la,
            "rho":rho,
            "probabilities":probs,
            "ranked":ranked,
            "ou_used":meta.get("ou_used",False),
            "total_goal_market_used": bool(q_market),
            "total_line":meta.get("total_line"),
        }

    def total_goal_score_space(self, m, selected, min_scores=7, max_total=9):
        """
        Stage 4 bridge: combine
            ordinary result sign
          + handicap-derived goal-difference rule
          + total-goal distribution
        to create the only score states allowed into production Top-N.

        If the handicap mode structurally conflicts with the selected ordinary
        result, the score space falls back to the ordinary-result constraint
        and the grade layer will downgrade the match.
        """
        profile=self.total_goal_profile(m,max_total=max_total)
        hs=self.handicap_structure(m,selected)
        # A structural conflict means there is no legal score satisfying both
        # market predictions. For diagnostics we fall back to ordinary result,
        # while the grading layer downgrades S/A to B.
        allowed_diffs=None if hs.get("conflict") else hs.get("allowed_goal_diffs")

        chosen_totals=[]
        score_space=[]
        cumulative=0.0

        for total,p_total in profile["ranked"]:
            legal=[]
            for h in range(total+1):
                a=total-h
                s=f"{h}-{a}"
                if self._score_matches_margin_rule(s,selected,hs.get("margin_rule"),allowed_diffs):
                    legal.append(s)

            if not legal:
                continue

            chosen_totals.append((total,p_total))
            cumulative+=p_total
            score_space.extend(legal)

            # Enough exact-score states for Top5 plus a meaningful amount of
            # total-goal probability mass.
            if len(score_space)>=min_scores and cumulative>=0.55:
                break

        # Safety expansion for rare parity/range structures.
        if len(score_space)<min_scores:
            seen={t for t,_ in chosen_totals}
            for total,p_total in profile["ranked"]:
                if total in seen:
                    continue
                legal=[]
                for h in range(total+1):
                    a=total-h
                    s=f"{h}-{a}"
                    if self._score_matches_margin_rule(s,selected,hs.get("margin_rule"),allowed_diffs):
                        legal.append(s)
                if legal:
                    chosen_totals.append((total,p_total))
                    score_space.extend(legal)
                if len(score_space)>=min_scores:
                    break

        return {
            "profile":profile,
            "handicap":hs,
            "active_rule":hs.get("margin_rule"),
            "allowed_goal_diffs":allowed_diffs,
            "chosen_totals":chosen_totals,
            "score_space":score_space,
            "score_set":set(score_space),
        }


    def rank_scores(self,m,selected=None,outcome_aware=False,
                    production=True, diagnostic_matrix=None):
        """
        Official 3.3 exact-score ranking.

        Production:
          - Bivariate Poisson supplies the primary ranking.
          - Result + handicap structure constrains candidate scores.
          - Exact-score market can only re-rank upstream-compatible scores by
            de-vig relative value; it cannot change the selected 1X2 result.
          - Dynamic-rho Dixon-Coles remains the independent confirmation model.

        Diagnostic calls can still request the raw DC matrix.
        """
        if production:
            pair=self.production_score_consensus(m,selected)
            ranked=list(pair["primary"])
        else:
            mat=diagnostic_matrix if diagnostic_matrix is not None else self.score_matrix(m)
            ranked=self._structured_score_ranking(m,selected,mat)

        devig=self.score_market_devig(m) if getattr(m,"exact_score_odds",None) else {}
        exact_devig={k:v for k,v in devig.items() if self._score_key_to_tuple(k) is not None}

        if exact_devig and selected in OUTCOMES:
            hs=self.handicap_structure(m,selected)
            rule=hs.get("margin_rule","result_only")
            allowed_diffs=None if hs.get("conflict") else hs.get("allowed_goal_diffs")
            pool=[x for x in ranked if self._score_matches_margin_rule(x[0],selected,rule,allowed_diffs)]
            if not pool:
                pool=[x for x in ranked if self._score_side(x[0])==selected]

            if pool:
                top_p=pool[0][1]
                passed=[]
                failed=[]
                for score,pv in pool:
                    qm=exact_devig.get(score)
                    if qm is None or qm<=0:
                        failed.append((score,pv))
                        continue
                    ratio=pv/qm
                    # Probability first, value second. This prevents a high-odds
                    # tiny-probability score from jumping into the official Top3.
                    if pv>=0.45*top_p and ratio>=0.85:
                        passed.append((score,pv,ratio))
                    else:
                        failed.append((score,pv))

                # Probability remains the primary sort key; value is a capped
                # tie-breaker only.
                passed.sort(
                    key=lambda x:(x[1],min(x[2],1.35)),
                    reverse=True
                )
                picked=[(s,pv) for s,pv,_ in passed]
                seen={s for s,_ in picked}
                picked += [(s,pv) for s,pv in pool if s not in seen]
                rest=[x for x in ranked if x[0] not in {s for s,_ in picked}]
                ranked=picked+rest

        return ranked


    def result_handicap_score_gate(self, m, selected, base_grade, market=None):
        """
        Official S-score confirmation gate.

        A is an outcome/handicap grade and is NOT demoted merely because exact
        score odds disagree. S is an exact-score grade and therefore requires:
          1) upstream result + handicap structure;
          2) Bivariate primary Top3;
          3) Dynamic-DC confirmation of at least 2/3 primary scores;
          4) score-market de-vig value confirmation on at least 2/3 Top3 scores.
        """
        market=market or self.market_probabilities(m)
        hs=self.handicap_structure(m,selected)
        pair=self.production_score_consensus(m,selected)
        devig=self.score_market_devig(m) if getattr(m,"exact_score_odds",None) else {}

        margin_score_ok = hs.get("margin_signal") in ("DETERMINISTIC","STRONG","LEAN")
        out={
            "available":bool(hs.get("available") and (not hs.get("conflict")) and margin_score_ok and devig),
            "handicap_pick":hs.get("handicap_pick"),
            "margin_rule":hs.get("margin_rule"),
            "margin_text":hs.get("margin_text"),
            "margin_signal":hs.get("margin_signal"),
            "margin_bet_pick":hs.get("margin_bet_pick"),
            "handicap_conflict":bool(hs.get("conflict")),
            "dual_confirm_count":pair["confirmed_count"],
            "dual_confirmed_scores":pair["confirmed_top3"],
            "primary_top3":pair["primary_top3"],
            "dc_top5":pair["dc_top5"],
            "value_pass_count":0,
            "top3_value_ratios":{},
        }

        if not devig:
            return out

        exact={k:v for k,v in devig.items() if self._score_key_to_tuple(k) is not None and v>0}
        ratios={}
        passes=0

        # Use the final production ranking so value validation is applied to
        # the same three scores the bettor will see.
        official=self.rank_scores(m,selected=selected,production=True)[:3]
        for score,pv in official:
            q=exact.get(score)
            if q is None or q<=0:
                ratios[score]=None
                continue
            ratio=pv/q
            ratios[score]=ratio
            if ratio>=0.85:
                passes+=1

        out["value_pass_count"]=passes
        out["top3_value_ratios"]=ratios
        out["official_top3"]=[s for s,_ in official]
        return out

    def apply_result_handicap_score_gate(self, base_grade, gate, selected_odds, conf, top):
        """
        Grade policy:
          - Base A stays A: exact-score market is irrelevant to A betting.
          - Base S remains S only with full score confirmation; otherwise it
            falls back to A because the outcome signal can still be valid.
          - Near-S A may become S only when both dual-model and value gates pass.
          - B/C are never promoted by the score market.
        """
        if base_grade=="S":
            if (
                gate.get("available")
                and gate.get("dual_confirm_count",0)>=2
                and gate.get("value_pass_count",0)>=2
            ):
                return "S","S retained: result/handicap + dual score models + score-market value confirmed."
            return "A","Base S lacks complete exact-score confirmation: downgrade to A, not B."

        if base_grade=="A":
            if (
                gate.get("available")
                and gate.get("dual_confirm_count",0)>=2
                and gate.get("value_pass_count",0)>=2
                and conf>=self.grade_s_conf-0.03
                and top>=self.grade_s_top-0.03
            ):
                return "S","Near-S A promoted: dual-model Top3 and score-market value both confirm."
            return "A","A retained: score-market disagreement does not demote outcome/handicap grade."

        return base_grade,"B/C unchanged: downstream score information cannot create an upstream betting grade."


    def diagnostic_xg_scores(self,m,selected=None):
        """Return xG-based ranking for diagnostics only; never official Top-N."""
        if m.expected_goals_home is None or m.expected_goals_away is None:
            return []
        mat=self.score_matrix(m)
        ranked=sorted(
            ((f"{h}-{a}",p) for (h,a),p in mat.items()),
            key=lambda x:x[1],
            reverse=True,
        )
        if selected in OUTCOMES:
            ranked=(
                [x for x in ranked if self._score_side(x[0])==selected]
                + [x for x in ranked if self._score_side(x[0])!=selected]
            )
        return ranked

    def meta_feature_dict(self, m, market=None, score=None, base=None):
        """Stable numeric feature map for LightGBM/CatBoost meta-outcome models."""
        market=market or self.market_probabilities(m)
        score=score or self.score_probabilities(m)
        base=base or {k:0.5*market[k]+0.5*score[k] for k in OUTCOMES}
        hp=self.handicap_probs(m)
        ou=self.totals_probabilities(m)
        strength=self.strength_probabilities(m,market)
        lh,la,rho,gmeta=self.fit_goal_parameters(m,market)
        f={
            "home_odds":float(m.home_odds),"draw_odds":float(m.draw_odds),"away_odds":float(m.away_odds),
            "market_H":market["H"],"market_D":market["D"],"market_A":market["A"],
            "score_H":score["H"],"score_D":score["D"],"score_A":score["A"],
            "base_H":base["H"],"base_D":base["D"],"base_A":base["A"],
            "lambda_home":lh,"lambda_away":la,"rho":rho,"etg":lh+la,
            "league_etg":self.league_etg_target(m,market),
            "handicap_line":float(m.handicap_line or 0),
            "hp_H":0.0 if hp is None else hp["H"],
            "hp_D":0.0 if hp is None else hp["D"],
            "hp_A":0.0 if hp is None else hp["A"],
            "ou_over":0.0 if ou is None else ou["O"],
            "ou_under":0.0 if ou is None else ou["U"],
            "support_H":0.0 if m.support_home is None else float(m.support_home)/100.0,
            "support_D":0.0 if m.support_draw is None else float(m.support_draw)/100.0,
            "support_A":0.0 if m.support_away is None else float(m.support_away)/100.0,
            "strength_H":0.0 if strength is None else strength["H"],
            "strength_D":0.0 if strength is None else strength["D"],
            "strength_A":0.0 if strength is None else strength["A"],
            "league_dispersion":1.0 if m.league_dispersion is None else float(m.league_dispersion),
        }
        ctx=getattr(m,"structured_context",None) or {}
        for k,v in sorted(ctx.items()):
            if isinstance(v,bool):
                f["ctx_"+str(k)]=1.0 if v else 0.0
            elif isinstance(v,(int,float)) and math.isfinite(float(v)):
                f["ctx_"+str(k)]=float(v)
        return f

    def _apply_strength_and_meta(self, m, final, market, score):
        out=dict(final)
        strength=self.strength_probabilities(m,market)
        if strength is not None and self.strength_weight>0:
            w=self.strength_weight
            out=self.normalize({k:(1-w)*out[k]+w*strength[k] for k in OUTCOMES})

        if self.meta_predictor is not None and getattr(self.meta_predictor,"fitted",False):
            feat=self.meta_feature_dict(m,market,score,out)
            mp=self.meta_predictor.predict_proba_dict(feat)
            if mp:
                w=0.25
                out=self.normalize({k:(1-w)*out[k]+w*mp[k] for k in OUTCOMES})

        if self.probability_calibrator is not None and getattr(self.probability_calibrator,"fitted",False):
            out=self.probability_calibrator.calibrate_dict(out)
        return out

    def predict(self,m):
        market=self.market_probabilities(m)
        score=self.score_probabilities(m)
        final={k:self.market_weight*market[k]+self.score_weight*score[k] for k in OUTCOMES}
        # Optional support is only used when actually present.
        if all(x is not None for x in (m.support_home,m.support_draw,m.support_away)):
            sup=self.normalize({"H":m.support_home,"D":m.support_draw,"A":m.support_away})
            final=self.normalize({k:0.90*final[k]+0.10*sup[k] for k in OUTCOMES})
        # 3.4.3 Production: Case1/2/3 are RETIRED from the decision chain.
        # We still compute the legacy signal for diagnostics, but it cannot:
        #   - remove an outcome,
        #   - change selected 1X2,
        #   - change confidence,
        #   - promote/demote a grade.
        case,case_excluded_signal,case_reasons=self.odds_rule(m)
        reasons=list(case_reasons)
        if case:
            reasons.append(
                f"Legacy Case{case} diagnostic only: would_exclude={case_excluded_signal}; "
                "ignored by 3.4.3 production selection and grading."
            )
        else:
            reasons.append("Legacy Case diagnostic: no Case signal; production remains standard no-Case 1X2.")

        excluded=None
        cand=list(OUTCOMES)
        selected=max(cand,key=lambda k:final[k])
        vals=sorted((final[k] for k in cand),reverse=True)
        top,second=vals[0],vals[1]
        conf=0.65*top+0.35*max(0,top-second)

        if conf>=self.grade_s_conf and top>=self.grade_s_top:
            grade="S"
        elif conf>=self.grade_a_conf and top>=self.grade_a_top:
            selected_odds={"H":m.home_odds,"D":m.draw_odds,"A":m.away_odds}[selected]
            grade="A" if selected_odds >= self.a_min_odds else "B"
        elif conf>=0.34:
            grade="B"
        else:
            grade="C"

        base_grade = grade
        selected_odds={"H":m.home_odds,"D":m.draw_odds,"A":m.away_odds}[selected]
        hs=self.handicap_structure(m,selected)
        score_gate=self.result_handicap_score_gate(
            m, selected=selected, base_grade=base_grade, market=market
        )
        grade, gate_reason=self.apply_result_handicap_score_gate(
            base_grade, score_gate, selected_odds, conf, top
        )
        total_space=self.total_goal_score_space(m,selected,min_scores=7,max_total=9)
        total_top=total_space["profile"]["ranked"][:4]
        reasons.append(
            "Result→GoalDiffFusion→TotalGoals→Score Gate: "
            f"base={base_grade}, result={selected}, "
            f"handicap_pick={hs.get('handicap_pick')}, line={hs.get('line')}, "
            f"margin={hs.get('margin_text')}, conflict={hs.get('conflict')}, "
            f"ETG={total_space['profile']['etg']:.3f}, "
            f"active_totals={[t for t,_ in total_space['chosen_totals']]}, "
            f"Top3_value_pass={score_gate.get('value_pass_count')}."
        )
        reasons.append(gate_reason)

        trend=self.trajectory(m)
        if trend["reversal"] and grade in ("S","A"):
            grade={"S":"A","A":"B"}[grade]
            reasons.append(f"Reversal odds trend detected; grade downgraded, strength={trend['strength']:.2f}.")

        hp=self.handicap_probs(m)
        if hp is not None:
            hsel=max(hp,key=hp.get)
            hs_now=self.handicap_structure(m,selected)
            reasons.append(
                f"Handicap stage: pick={hsel}, line={hs_now.get('line')}, "
                f"margin={hs_now.get('margin_text')}, conflict={hs_now.get('conflict')}. "
                "Ordinary 1X2 decides result first; handicap then constrains actual goal difference."
            )

        # Exact-score ranking is outcome-aware only for S bets.
        # For A/B/C we preserve the unconstrained raw grid.
        score_top=self.rank_scores(
            m,
            selected=selected,
            outcome_aware=True,
            production=True,
        )
        gh,ga,gr,gmeta=self.fit_goal_parameters(m,market)
        reasons.append(
            f"3.4 base 1X2+O/U goal engine: home={gh:.3f}, away={ga:.3f}, "
            f"rho={gr:.3f}, ETG={gh+ga:.3f}, league_target={gmeta['league_etg_target']:.3f}, "
            f"handicap_used={gmeta['handicap_used']}, OU_used={gmeta['ou_used']}."
        )
        pair=self.production_score_consensus(m,selected)
        reasons.append(
            "3.4.3 Goal-Difference + Total-Market Fusion score pair: Bivariate Poisson primary Top3="
            + ",".join(pair["primary_top3"])
            + "; Dynamic-rho DC Top5="
            + ",".join(pair["dc_top5"])
            + f"; dual_confirm={pair['confirmed_count']}/3."
        )
        reasons.append(
            "Elo/Pi, LightGBM/CatBoost, Platt, ZIP, Negative Binomial, CMP, "
            "Hierarchical Bayes and Rolling xG are diagnostic-only in 3.3 Production."
        )
        if getattr(m,"exact_score_odds",None):
            reasons.append("Exact-score odds are downstream only: de-vig/value validation, never upstream direction or λ source.")
        else:
            reasons.append("No exact-score odds supplied: score-value confirmation unavailable.")
        reasons.append("Rolling xG remains diagnostic-only unless a separate strict OOS test activates it.")
        if m.expected_goals_home is not None and m.expected_goals_away is not None:
            dx=self.diagnostic_xg_scores(m,selected=selected)[:3]
            reasons.append("Diagnostic xG Top3="+", ".join(s for s,_ in dx))
        if grade=="S":
            reasons.append("S exact-score ranking is outcome-aware (Top-N constrained to selected 1X2 first).")
            # Candidate distributions remain diagnostics only after strict OOS
            # failed to show holdout Top3/Top5 improvement over production DC.
            for model_name in ("zero_inflated_poisson","negative_binomial",
                               "cmp","hierarchical_bayes"):
                diag=self.candidate_top_scores(m,selected,model_name,3)
                reasons.append(f"Diagnostic-only {model_name} Top3="+", ".join(s for s,_ in diag))
        tg=self.total_goal_profile(m,max_total=9)
        reasons.append(
            "Total-goals stage Top4="
            + ", ".join(f"{t}({p:.3f})" for t,p in tg["ranked"][:4])
            + ". Exact scores are derived only after result + margin + total-goal constraints."
        )
        reasons.append(f"Final={selected}, p={final[selected]:.4f}, confidence={conf:.4f}.")
        return Prediction(
            m.match_id,final,market,score,selected,excluded,grade,conf,case,score_top,reasons,
            total_goals_top=tg["ranked"][:5],
            margin_structure=hs,
            case_excluded_signal=case_excluded_signal,
        )


class MetaOutcomeEnsemble:
    """
    Trainable 1X2 meta learner using LightGBM + CatBoost.

    Fit only on chronological pre-match features. The class does not perform
    random train/test splitting and therefore does not create future leakage
    by itself; walk-forward/OOS control belongs to the caller/backtester.
    """
    LABEL_TO_INT={"H":0,"D":1,"A":2}
    INT_TO_LABEL={0:"H",1:"D",2:"A"}

    def __init__(self, random_state=42):
        self.random_state=int(random_state)
        self.models=[]
        self.feature_names=[]
        self.fitted=False

    @staticmethod
    def _matrix(rows, feature_names):
        return [[float(r.get(k,0.0)) for k in feature_names] for r in rows]

    def fit(self, feature_rows: Sequence[Dict[str,float]], labels: Sequence[str]):
        if len(feature_rows)!=len(labels) or len(labels)<30:
            raise ValueError("Meta model needs equal-length feature/label rows and at least 30 samples.")
        self.feature_names=sorted(set().union(*(r.keys() for r in feature_rows)))
        X=self._matrix(feature_rows,self.feature_names)
        y=[self.LABEL_TO_INT[str(v)] for v in labels]
        models=[]

        try:
            from lightgbm import LGBMClassifier
            lgb=LGBMClassifier(
                objective="multiclass",num_class=3,n_estimators=220,
                learning_rate=0.035,num_leaves=15,max_depth=5,
                subsample=0.85,colsample_bytree=0.85,reg_lambda=1.0,
                random_state=self.random_state,verbosity=-1
            )
            lgb.fit(X,y)
            models.append(("lightgbm",lgb))
        except Exception:
            pass

        try:
            from catboost import CatBoostClassifier
            cat=CatBoostClassifier(
                loss_function="MultiClass",iterations=260,depth=5,
                learning_rate=0.035,l2_leaf_reg=4.0,
                random_seed=self.random_state,verbose=False,
                allow_writing_files=False
            )
            cat.fit(X,y)
            models.append(("catboost",cat))
        except Exception:
            pass

        if not models:
            from sklearn.ensemble import HistGradientBoostingClassifier
            sk=HistGradientBoostingClassifier(
                max_iter=220,learning_rate=0.04,max_leaf_nodes=15,
                l2_regularization=1.0,random_state=self.random_state
            )
            sk.fit(X,y)
            models.append(("sklearn_fallback",sk))

        self.models=models
        self.fitted=True
        return self

    def predict_proba_dict(self, feature_row: Dict[str,float]):
        if not self.fitted:
            return None
        X=self._matrix([feature_row],self.feature_names)
        probs=[]
        for _,model in self.models:
            p=model.predict_proba(X)[0]
            # Most classifiers keep classes_ ordering.
            cls=list(getattr(model,"classes_",[0,1,2]))
            d={self.INT_TO_LABEL[int(c)]:float(v) for c,v in zip(cls,p)}
            probs.append(d)
        out={k:sum(d.get(k,0.0) for d in probs)/len(probs) for k in OUTCOMES}
        z=sum(out.values())
        return {k:v/z for k,v in out.items()} if z else None


class MulticlassPlattCalibrator:
    """One-vs-rest Platt scaling for H/D/A probabilities."""
    def __init__(self):
        self.models={}
        self.fitted=False

    @staticmethod
    def _logit(p):
        p=max(1e-6,min(1-1e-6,float(p)))
        return math.log(p/(1-p))

    def fit(self, probability_rows: Sequence[Dict[str,float]], labels: Sequence[str]):
        if len(probability_rows)!=len(labels) or len(labels)<30:
            raise ValueError("Calibration needs equal-length rows and at least 30 samples.")
        from sklearn.linear_model import LogisticRegression
        for side in OUTCOMES:
            X=[[self._logit(r[side])] for r in probability_rows]
            y=[1 if str(v)==side else 0 for v in labels]
            model=LogisticRegression(C=1.0,solver="lbfgs")
            model.fit(X,y)
            self.models[side]=model
        self.fitted=True
        return self

    def calibrate_dict(self, probs: Dict[str,float]):
        if not self.fitted:
            return dict(probs)
        out={}
        for side in OUTCOMES:
            x=[[self._logit(probs[side])]]
            out[side]=float(self.models[side].predict_proba(x)[0,1])
        z=sum(out.values())
        return {k:v/z for k,v in out.items()} if z else dict(probs)


OPENAI_MATCH_CONTEXT_SCHEMA={
    "type":"object",
    "properties":{
        "home_injury_impact":{"type":"number"},
        "away_injury_impact":{"type":"number"},
        "home_rotation_index":{"type":"number"},
        "away_rotation_index":{"type":"number"},
        "home_fatigue_index":{"type":"number"},
        "away_fatigue_index":{"type":"number"},
        "home_suspension_impact":{"type":"number"},
        "away_suspension_impact":{"type":"number"},
        "home_motivation_index":{"type":"number"},
        "away_motivation_index":{"type":"number"},
        "source_confidence":{"type":"number"}
    },
    "required":[
        "home_injury_impact","away_injury_impact",
        "home_rotation_index","away_rotation_index",
        "home_fatigue_index","away_fatigue_index",
        "home_suspension_impact","away_suspension_impact",
        "home_motivation_index","away_motivation_index",
        "source_confidence"
    ],
    "additionalProperties":False
}

def extract_match_context_openai(source_text: str, model: str, client=None):
    """
    Optional OpenAI Structured Outputs adapter.

    Requires an explicit model name and the official `openai` Python package.
    It extracts structured pre-match intelligence only; it does NOT ask the LLM
    for betting probabilities or a predicted winner.
    """
    if not model:
        raise ValueError("Pass an explicit OpenAI API model name.")
    if client is None:
        from openai import OpenAI
        client=OpenAI()
    prompt=(
        "Extract only evidence explicitly supported by the supplied pre-match text. "
        "Return numeric impact/index values in [-1,1] where 0 means no supported impact. "
        "Do not predict the match result and do not invent missing information.\\n\\n"
        + str(source_text)
    )
    response=client.responses.create(
        model=model,
        input=prompt,
        text={
            "format":{
                "type":"json_schema",
                "name":"match_context",
                "schema":OPENAI_MATCH_CONTEXT_SCHEMA,
                "strict":True
            }
        }
    )
    return json.loads(response.output_text)


@dataclass
class TeamHistoryRow:
    date_ts: object
    is_home: bool
    gf: float
    ga: float
    xgf: Optional[float]
    xga: Optional[float]
    league_id: Optional[int]

class RollingXGStrength:
    """
    Strict pre-match rolling team attack/defence estimator.

    It only consumes PRIOR matches. Current-match post-match xG is added to
    history only after the prediction step (or when its known_at becomes
    available in the parquet backtester).
    """
    def __init__(self, half_life_days=240.0, window=36, shrinkage=8.0,
                 xg_weight=0.60, max_team_weight=0.72):
        self.half_life_days=float(half_life_days)
        self.window=int(window)
        self.shrinkage=float(shrinkage)
        self.xg_weight=float(xg_weight)
        self.max_team_weight=float(max_team_weight)
        self.team_rows=defaultdict(lambda: deque(maxlen=self.window))
        self.league_rows=defaultdict(lambda: deque(maxlen=2500))

    def _time_weight(self, current_ts, past_ts):
        try:
            days=max(0.0,(current_ts-past_ts).total_seconds()/86400.0)
        except Exception:
            days=0.0
        return 0.5 ** (days/max(self.half_life_days,1.0))

    @staticmethod
    def _safe_mean(vals, weights, default):
        if not vals:
            return float(default)
        sw=sum(weights)
        if sw<=0:
            return float(default)
        return sum(v*w for v,w in zip(vals,weights))/sw

    def _league_priors(self, league_id, current_ts):
        rows=list(self.league_rows.get(league_id,()))
        if not rows:
            return {"home_xg":1.45,"away_xg":1.15,"home_g":1.45,"away_g":1.15}

        hw=[]; aw=[]; hg=[]; ag=[]; wh=[]; wa=[]; wgh=[]; wga=[]
        for r in rows:
            w=self._time_weight(current_ts,r["date_ts"])
            if r["home_xg"] is not None:
                hw.append(float(r["home_xg"])); wh.append(w)
            if r["away_xg"] is not None:
                aw.append(float(r["away_xg"])); wa.append(w)
            hg.append(float(r["home_g"])); wgh.append(w)
            ag.append(float(r["away_g"])); wga.append(w)
        return {
            "home_xg":self._safe_mean(hw,wh,1.45),
            "away_xg":self._safe_mean(aw,wa,1.15),
            "home_g":self._safe_mean(hg,wgh,1.45),
            "away_g":self._safe_mean(ag,wga,1.15),
        }

    def _team_metric(self, team_id, current_ts, is_home, field, prior):
        rows=list(self.team_rows.get(team_id,()))
        venue=[r for r in rows if r.is_home==is_home]
        # Back off to all venues when venue-specific sample is sparse.
        use=venue if len(venue)>=4 else rows
        vals=[]; ws=[]
        for r in use:
            v=getattr(r,field)
            if v is None:
                continue
            vals.append(float(v)); ws.append(self._time_weight(current_ts,r.date_ts))
        n=len(vals)
        mean=self._safe_mean(vals,ws,prior)
        shrunk=(n*mean+self.shrinkage*prior)/(n+self.shrinkage)
        return shrunk,n

    def expected_goals(self, league_id, home_team_id, away_team_id,
                       current_ts, market_lh=None, market_la=None):
        pr=self._league_priors(league_id,current_ts)

        # Home team at home.
        h_xgf,hnx=self._team_metric(home_team_id,current_ts,True,"xgf",pr["home_xg"])
        h_xga,hnd=self._team_metric(home_team_id,current_ts,True,"xga",pr["away_xg"])
        h_gf,hng=self._team_metric(home_team_id,current_ts,True,"gf",pr["home_g"])
        h_ga,_  =self._team_metric(home_team_id,current_ts,True,"ga",pr["away_g"])

        # Away team away.
        a_xgf,anx=self._team_metric(away_team_id,current_ts,False,"xgf",pr["away_xg"])
        a_xga,andf=self._team_metric(away_team_id,current_ts,False,"xga",pr["home_xg"])
        a_gf,ang=self._team_metric(away_team_id,current_ts,False,"gf",pr["away_g"])
        a_ga,_  =self._team_metric(away_team_id,current_ts,False,"ga",pr["home_g"])

        # Blend real historical xG with actual goals. xG is provider-level and
        # coarse, so it is deliberately not given 100% weight.
        h_att=self.xg_weight*h_xgf+(1-self.xg_weight)*h_gf
        h_def=self.xg_weight*h_xga+(1-self.xg_weight)*h_ga
        a_att=self.xg_weight*a_xgf+(1-self.xg_weight)*a_gf
        a_def=self.xg_weight*a_xga+(1-self.xg_weight)*a_ga

        home_base=self.xg_weight*pr["home_xg"]+(1-self.xg_weight)*pr["home_g"]
        away_base=self.xg_weight*pr["away_xg"]+(1-self.xg_weight)*pr["away_g"]

        # Multiplicative attack × opponent defensive weakness.
        lh=home_base*(h_att/max(home_base,.20))*(a_def/max(home_base,.20))
        la=away_base*(a_att/max(away_base,.20))*(h_def/max(away_base,.20))

        # Market is retained as a prior, not as the sole source.
        history_n=min(hnx+hng,anx+ang)
        team_weight=min(self.max_team_weight, max(0.25, history_n/28.0))
        if market_lh is not None and market_la is not None:
            # Geometric blend is stable for positive rate parameters.
            lh=math.exp(team_weight*math.log(max(lh,.05))+
                        (1-team_weight)*math.log(max(market_lh,.05)))
            la=math.exp(team_weight*math.log(max(la,.05))+
                        (1-team_weight)*math.log(max(market_la,.05)))

        return (
            max(.12,min(4.75,lh)),
            max(.10,min(4.25,la)),
            {
                "history_strength":history_n,
                "team_weight":team_weight,
                "league_home_base":home_base,
                "league_away_base":away_base,
            },
        )

    def update(self, date_ts, league_id, home_team_id, away_team_id,
               goals_home, goals_away, home_xg=None, away_xg=None):
        hx=None if home_xg is None or (isinstance(home_xg,float) and math.isnan(home_xg)) else float(home_xg)
        ax=None if away_xg is None or (isinstance(away_xg,float) and math.isnan(away_xg)) else float(away_xg)
        self.team_rows[home_team_id].append(
            TeamHistoryRow(date_ts,True,float(goals_home),float(goals_away),hx,ax,league_id)
        )
        self.team_rows[away_team_id].append(
            TeamHistoryRow(date_ts,False,float(goals_away),float(goals_home),ax,hx,league_id)
        )
        self.league_rows[league_id].append({
            "date_ts":date_ts,
            "home_g":float(goals_home),"away_g":float(goals_away),
            "home_xg":hx,"away_xg":ax,
        })


def outcome(h,a):
    return "H" if h>a else "D" if h==a else "A"

def roi_from_1x2(rows, preds):
    profit=0.0; bets=0
    for m,p in zip(rows,preds):
        if p.grade!="A": continue
        bets += 1
        odds={"H":m.home_odds,"D":m.draw_odds,"A":m.away_odds}[p.selected]
        actual=outcome(m.goals_home,m.goals_away)
        profit += odds-1 if p.selected==actual else -1
    return {"bets":bets,"profit":profit,"roi":profit/bets if bets else None}

def score_metrics(rows,preds):
    out={}
    for n in (1,3,5,10):
        hits=0; s_n=0
        for m,p in zip(rows,preds):
            if p.grade!="S": continue
            s_n += 1
            actual=f"{m.goals_home}-{m.goals_away}"
            if any(sc==actual for sc,_ in p.score_top[:n]): hits+=1
        out[f"top{n}"]={"s_bets":s_n,"hits":hits,
                        "coverage":hits/s_n if s_n else None}
    return out

def metrics(rows,engine):
    preds=[engine.predict(m) for m in rows if m.goals_home is not None and m.goals_away is not None]
    used=[m for m in rows if m.goals_home is not None and m.goals_away is not None]
    n=len(used)
    correct=sum(p.selected==outcome(m.goals_home,m.goals_away) for m,p in zip(used,preds))
    by={}
    for g in "SABC":
        xs=[(m,p) for m,p in zip(used,preds) if p.grade==g]
        by[g]={
            "n":len(xs),
            "accuracy_1x2":(sum(p.selected==outcome(m.goals_home,m.goals_away) for m,p in xs)/len(xs) if xs else None)
        }
    def ll(prob,actual): return -math.log(max(prob[actual],1e-12))
    def bs(prob,actual): return sum((prob[k]-(1.0 if k==actual else 0.0))**2 for k in OUTCOMES)
    market_ll=sum(ll(p.market_probabilities,outcome(m.goals_home,m.goals_away)) for m,p in zip(used,preds))/n if n else None
    model_ll=sum(ll(p.probabilities,outcome(m.goals_home,m.goals_away)) for m,p in zip(used,preds))/n if n else None
    market_bs=sum(bs(p.market_probabilities,outcome(m.goals_home,m.goals_away)) for m,p in zip(used,preds))/n if n else None
    model_bs=sum(bs(p.probabilities,outcome(m.goals_home,m.goals_away)) for m,p in zip(used,preds))/n if n else None
    return {
        "n":n,
        "overall_1x2_accuracy":correct/n if n else None,
        "market_logloss":market_ll,
        "model_logloss":model_ll,
        "market_brier":market_bs,
        "model_brier":model_bs,
        "by_grade":by,
        "A_1x2_betting":roi_from_1x2(used,preds),
        "S_exact_score_coverage":score_metrics(used,preds),
        "data_scope":{
            "exact_score_roi_available":False,
            "let_ball_roi_available":all(m.handicap_result is not None and
                                         m.handicap_home is not None for m in used)
        }
    }

def load_csv(path):
    rows=[]
    with open(path,encoding="utf-8-sig",newline="") as f:
        r=csv.DictReader(f)
        for i,x in enumerate(r):
            try:
                h=float(x["home_win"]); d=float(x["draw"]); a=float(x["away_win"])
            except: continue
            def sf(k):
                try: return float(x[k]) if x.get(k) not in (None,"") else None
                except: return None
            gh=sf("goals_home"); ga=sf("goals_away")
            rows.append(MatchInput(
                str(x.get("id") or i),h,d,a,
                int(gh) if gh is not None else None,
                int(ga) if ga is not None else None,
                sf("support_home"),sf("support_draw"),sf("support_away"),
                sf("over_odds"),sf("under_odds"),sf("btts_yes"),
                None,sf("handicap_home"),sf("handicap_draw"),sf("handicap_away"),
                x.get("handicap_result"),x.get("date"),x.get("home_team"),x.get("away_team")
            ))
    rows.sort(key=lambda m:(m.date or "",m.match_id))
    return rows


def backtest_hf_parquet(data_dir, min_history=6, bookmaker="Pinnacle"):
    """
    Strict chronological HF parquet backtest for the score layer.

    Expected files:
      fixtures.parquet
      match_stats.parquet
      odds.parquet

    Schema is based on eatpizzanot/soccer-dataset. Post-match statistics are
    used only after they become available; current-match xG never enters the
    pre-match prediction.
    """
    import pandas as pd
    from heapq import heappush, heappop

    d=Path(data_dir)
    fx=pd.read_parquet(d/"fixtures.parquet")
    ms=pd.read_parquet(d/"match_stats.parquet")
    od=pd.read_parquet(d/"odds.parquet")

    fx=fx[(fx["is_played"]==True)&fx["goals_home"].notna()&fx["goals_away"].notna()].copy()
    fx["date_utc"]=pd.to_datetime(fx["date_utc"],utc=True,errors="coerce")

    ms=ms[["fixture_id","home_xg","away_xg","xg_covered","known_at"]].copy()
    ms["known_at"]=pd.to_datetime(ms["known_at"],utc=True,errors="coerce")

    od["known_at"]=pd.to_datetime(od["known_at"],utc=True,errors="coerce")
    if bookmaker:
        chosen=od[od["bookmaker"].astype(str).str.lower()==bookmaker.lower()].copy()
        if chosen.empty:
            chosen=od.copy()
    else:
        chosen=od.copy()

    # Only odds known no later than kickoff; choose latest snapshot.
    base=fx[["id","date_utc","league_id","home_team_id","away_team_id",
             "goals_home","goals_away"]].rename(columns={"id":"fixture_id"})
    chosen=chosen.merge(base[["fixture_id","date_utc"]],on="fixture_id",how="inner")
    chosen=chosen[chosen["known_at"].isna() | (chosen["known_at"]<=chosen["date_utc"])]
    chosen=chosen.sort_values(["fixture_id","known_at"]).drop_duplicates("fixture_id",keep="last")

    df=base.merge(ms,on="fixture_id",how="left")
    df=df.merge(chosen[["fixture_id","home_win","draw","away_win"]],on="fixture_id",how="inner")
    df=df[(df["home_win"]>1)&(df["draw"]>1)&(df["away_win"]>1)]
    df=df.sort_values(["date_utc","fixture_id"]).reset_index(drop=True)

    engine=JingcaiEngine(a_min_odds=1.60)
    state=RollingXGStrength()
    pending=[]
    sequence=0
    evaluated=[]

    for _,r in df.iterrows():
        now=r["date_utc"]

        # Apply only histories whose post-match facts were already known.
        while pending and pending[0][0] <= now:
            _,_,payload=heappop(pending)
            state.update(**payload)

        m=MatchInput(
            match_id=str(int(r["fixture_id"])),
            home_odds=float(r["home_win"]),
            draw_odds=float(r["draw"]),
            away_odds=float(r["away_win"]),
            goals_home=int(r["goals_home"]),
            goals_away=int(r["goals_away"]),
            date=str(now),
            league_id=int(r["league_id"]),
            home_team_id=int(r["home_team_id"]),
            away_team_id=int(r["away_team_id"]),
        )

        market=engine.market_probabilities(m)
        mlh,mla=engine.lambdas(market,m=None)
        lh,la,meta=state.expected_goals(
            m.league_id,m.home_team_id,m.away_team_id,now,mlh,mla
        )
        m.expected_goals_home=lh
        m.expected_goals_away=la
        m.xg_source="rolling prior-match team xG/goals"

        pred=engine.predict(m)
        actual_score=f"{m.goals_home}-{m.goals_away}"
        evaluated.append({
            "fixture_id":m.match_id,
            "date":str(now),
            "grade":pred.grade,
            "selected":pred.selected,
            "actual_score":actual_score,
            "top1":actual_score in [s for s,_ in pred.score_top[:1]],
            "top3":actual_score in [s for s,_ in pred.score_top[:3]],
            "top5":actual_score in [s for s,_ in pred.score_top[:5]],
            "lh":lh,"la":la,
            **meta,
        })

        hx=(float(r["home_xg"]) if pd.notna(r["home_xg"]) and bool(r.get("xg_covered",True)) else None)
        ax=(float(r["away_xg"]) if pd.notna(r["away_xg"]) and bool(r.get("xg_covered",True)) else None)

        # Conservative availability time:
        # known_at when provided, otherwise kickoff + 3 hours.
        ka=r["known_at"]
        available=(ka if pd.notna(ka) else now+pd.Timedelta(hours=3))
        available=max(available,now+pd.Timedelta(hours=2))
        payload=dict(
            date_ts=now,league_id=m.league_id,
            home_team_id=m.home_team_id,away_team_id=m.away_team_id,
            goals_home=m.goals_home,goals_away=m.goals_away,
            home_xg=hx,away_xg=ax,
        )
        sequence+=1
        heappush(pending,(available,sequence,payload))

    s=[x for x in evaluated if x["grade"]=="S"]
    def cv(k):
        return sum(bool(x[k]) for x in s)/len(s) if s else None

    return {
        "n":len(evaluated),
        "s_n":len(s),
        "s_top1":cv("top1"),
        "s_top3":cv("top3"),
        "s_top5":cv("top5"),
        "rows":evaluated,
    }




# ---------------------------------------------------------------------------
# Jingcai 3.4.5 FLIP / UPSET RISK LAYER
# ---------------------------------------------------------------------------
# Important:
# - "market_flip_probability" = 1 - de-vigged probability of the selected result.
# - Crowd support is NOT itself a probability, so heat is recorded as a separate
#   excess-support feature and does not automatically get added to the probability.
# - A heat-adjusted calibrated flip probability should only be produced after
#   historical support/global-odds snapshots have been collected and fitted OOS.

class FlipRiskEstimator:
    @staticmethod
    def _selected_support(m, selected):
        mp={"H":m.support_home,"D":m.support_draw,"A":m.support_away}
        v=mp.get(selected)
        return None if v is None else float(v)

    @staticmethod
    def _band(heat_gap_pp, selected_market_prob):
        if heat_gap_pp is None:
            return "NO_SUPPORT"
        if selected_market_prob < 0.50 and heat_gap_pp >= 15:
            return "CROWD_CONFLICT"
        if heat_gap_pp >= 15:
            return "VERY_HOT"
        if heat_gap_pp >= 8:
            return "HOT"
        if heat_gap_pp >= 4:
            return "WARM"
        if heat_gap_pp <= -8:
            return "COLD"
        return "NEUTRAL"

    @staticmethod
    def evaluate(m, selected, model_probabilities=None, external_consensus=None,
                 previous_sporttery_odds=None):
        market=_devig_three((m.home_odds,m.draw_odds,m.away_odds))
        if market is None:
            return None
        idx={"H":0,"D":1,"A":2}[selected]
        p_market=float(market[idx])
        p_model=(float(model_probabilities.get(selected))
                 if isinstance(model_probabilities,dict) and selected in model_probabilities
                 else p_market)

        support=FlipRiskEstimator._selected_support(m,selected)
        heat_gap=(support - 100*p_market) if support is not None else None

        global_gap=None
        global_selected=None
        global_dispersion=None
        if external_consensus:
            cp=external_consensus.get("median_prob") or {}
            gp=cp.get(selected)
            if gp is not None:
                global_selected=float(gp)
                global_gap=100*(p_market-global_selected)
            mad=(external_consensus.get("mad") or {}).get(selected)
            if mad is not None:
                global_dispersion=100*float(mad)

        adverse_move=None
        if previous_sporttery_odds:
            prev=_devig_three(previous_sporttery_odds)
            if prev:
                adverse_move=100*(float(prev[idx])-p_market)

        # Two honest base probabilities:
        # market: derived directly from current no-vig odds
        # model: current engine probability if supplied
        market_flip=1.0-p_market
        model_flip=1.0-p_model

        # Heat-adjusted calibrated value deliberately remains unavailable until
        # a fitted calibration model exists. This prevents made-up percentages.
        calibrated_flip=None

        band=FlipRiskEstimator._band(heat_gap,p_market)

        diagnostic_flags=[]
        if band in ("HOT","VERY_HOT","CROWD_CONFLICT"):
            diagnostic_flags.append("crowd_heat")
        if global_gap is not None and global_gap >= 4:
            diagnostic_flags.append("sporttery_hot_vs_global")
        if adverse_move is not None and adverse_move >= 2:
            diagnostic_flags.append("adverse_market_move")

        if len(diagnostic_flags)>=2 or band=="CROWD_CONFLICT":
            risk_level="HIGH"
        elif diagnostic_flags or market_flip>=0.40:
            risk_level="MEDIUM"
        else:
            risk_level="LOW"

        return {
            "selected":selected,
            "selected_market_probability":p_market,
            "selected_model_probability":p_model,
            "market_flip_probability":market_flip,
            "model_flip_probability":model_flip,
            "support_selected_pct":support,
            "heat_gap_pp":heat_gap,
            "heat_band":band,
            "external_selected_probability":global_selected,
            "sporttery_minus_global_pp":global_gap,
            "global_dispersion_pp":global_dispersion,
            "adverse_move_pp":adverse_move,
            "diagnostic_flags":diagnostic_flags,
            "risk_level":risk_level,
            "calibrated_heat_adjusted_flip_probability":calibrated_flip,
            "calibration_status":"UNFITTED_NEEDS_HISTORICAL_SUPPORT_AND_GLOBAL_SNAPSHOTS",
        }


# ---------------------------------------------------------------------------
# Jingcai 3.4.5 LIVE MARKET ADAPTERS
# ---------------------------------------------------------------------------
# Sporttery = execution market / timestamped primary source.
# External bookmakers/exchanges = diagnostic reference markets until a strict
# historical OOS test proves a positive fusion weight.

SPORTTERY_ENDPOINTS = (
    # Primary: confirmed working from the user's mobile network on 2026-08-16.
    "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry",
    # Fallback: legacy path, often returns HTTP 567/WAF on some exits.
    "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry",
)

def _safe_float(v):
    try:
        x=float(v)
        return x if math.isfinite(x) and x > 0 else None
    except Exception:
        return None

def _devig_three(odds):
    vals=[_safe_float(x) for x in odds]
    if any(x is None for x in vals):
        return None
    inv=[1/x for x in vals]
    z=sum(inv)
    return tuple(x/z for x in inv) if z>0 else None

def _sporttery_score_key(code):
    m=re.match(r"^s(\d{2})s(\d{2})$", str(code))
    if m:
        return f"{int(m.group(1))}-{int(m.group(2))}"
    return {"s1sh":"H_OTHER","s1sd":"D_OTHER","s1sa":"A_OTHER"}.get(str(code))

def _sporttery_total_key(code):
    m=re.match(r"^s(\d)$", str(code))
    if not m:
        return None
    n=int(m.group(1))
    return "7+" if n>=7 else n

class SportteryLiveAdapter:
    """Low-frequency reader for the JSON endpoint used by the football calculator."""
    def __init__(self, timeout=8.0, pools="hhad,had,crs,ttg,hafu"):
        self.timeout=float(timeout)
        self.pools=pools

    def fetch_raw(self):
        params=urlencode({"poolCode":self.pools,"channel":"c"})
        last_error=None
        headers={
            "User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                         "Mobile/15E148 Safari/604.1",
            "Referer":"https://m.sporttery.cn/",
            "Accept":"application/json, text/plain, */*",
            "Accept-Language":"zh-CN,zh;q=0.9",
        }
        for endpoint in SPORTTERY_ENDPOINTS:
            req=Request(endpoint+"?"+params,headers=headers,method="GET")
            try:
                with urlopen(req,timeout=self.timeout) as r:
                    data=json.loads(r.read().decode("utf-8"))
                if not isinstance(data,dict) or not isinstance(data.get("value"),dict):
                    raise RuntimeError("unexpected Sporttery payload")
                data["_jingcai_source_endpoint"] = endpoint
                return data
            except HTTPError as e:
                last_error=RuntimeError(f"Sporttery HTTP {e.code}; trying alternate endpoint once")
                # Never hammer the endpoint: just continue to the single fallback path.
                continue
            except (URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as e:
                last_error=e
                continue
        raise RuntimeError(f"Sporttery live fetch failed: {last_error}")

    @staticmethod
    def _pool_odds(raw, pool):
        obj=(raw or {}).get(pool)
        return obj if isinstance(obj,dict) else None

    @staticmethod
    def parse_raw(payload):
        out=[]
        groups=((payload or {}).get("value") or {}).get("matchInfoList") or []
        updated=((payload or {}).get("value") or {}).get("lastUpdateTime")
        for group in groups:
            for sm in group.get("subMatchList") or []:
                had=SportteryLiveAdapter._pool_odds(sm,"had")
                if not had:
                    continue
                ho,do,ao=_safe_float(had.get("h")),_safe_float(had.get("d")),_safe_float(had.get("a"))
                if not all(x is not None for x in (ho,do,ao)):
                    continue
                rec={
                    "match_id":str(sm.get("matchNumStr") or sm.get("matchId") or ""),
                    "upstream_match_id":sm.get("matchId"),
                    "match_num":sm.get("matchNum"),
                    "match_num_date":str(sm.get("matchNumDate") or group.get("matchNumDate") or ""),
                    "business_date":str(sm.get("businessDate") or group.get("businessDate") or ""),
                    "match_date":str(sm.get("matchDate") or ""),
                    "match_time":str(sm.get("matchTime") or ""),
                    "league":str(sm.get("leagueAbbName") or sm.get("leagueAllName") or ""),
                    "league_id":sm.get("leagueId"),
                    "home_team":str(sm.get("homeTeamAbbName") or sm.get("homeTeamAllName") or ""),
                    "away_team":str(sm.get("awayTeamAbbName") or sm.get("awayTeamAllName") or ""),
                    "home_team_id":sm.get("homeTeamId"),
                    "away_team_id":sm.get("awayTeamId"),
                    "home_odds":ho,"draw_odds":do,"away_odds":ao,
                    "sporttery_updated_at":updated,
                }

                hhad=SportteryLiveAdapter._pool_odds(sm,"hhad")
                if hhad:
                    line_raw=hhad.get("goalLineValue") or hhad.get("goalLine")
                    try:
                        line=int(float(line_raw)) if str(line_raw).strip() else None
                    except Exception:
                        line=None
                    rec.update({
                        "handicap_home":_safe_float(hhad.get("h")),
                        "handicap_draw":_safe_float(hhad.get("d")),
                        "handicap_away":_safe_float(hhad.get("a")),
                        "handicap_line":line,
                    })

                ttg=SportteryLiveAdapter._pool_odds(sm,"ttg")
                if ttg:
                    tg={}
                    for k,v in ttg.items():
                        kk=_sporttery_total_key(k)
                        vv=_safe_float(v)
                        if kk is not None and vv is not None:
                            tg[kk]=vv
                    if tg:
                        rec["total_goal_odds"]=tg

                crs=SportteryLiveAdapter._pool_odds(sm,"crs")
                if crs:
                    sc={}
                    for k,v in crs.items():
                        kk=_sporttery_score_key(k)
                        vv=_safe_float(v)
                        if kk and vv is not None:
                            sc[kk]=vv
                    if sc:
                        rec["exact_score_odds"]=sc

                hafu=SportteryLiveAdapter._pool_odds(sm,"hafu")
                if hafu:
                    hf={}
                    for k in ("hh","hd","ha","dh","dd","da","ah","ad","aa"):
                        vv=_safe_float(hafu.get(k))
                        if vv is not None:
                            hf[k]=vv
                    if hf:
                        rec["hafu_odds"]=hf
                rec["market_timestamp"]=updated
                out.append(rec)
        return {"updated_at":updated,"matches":out}

    def fetch_matches(self):
        return self.parse_raw(self.fetch_raw())

    @staticmethod
    def to_match_input(rec):
        return MatchInput(
            match_id=str(rec["match_id"]),
            home_odds=float(rec["home_odds"]),
            draw_odds=float(rec["draw_odds"]),
            away_odds=float(rec["away_odds"]),
            handicap_home=rec.get("handicap_home"),
            handicap_draw=rec.get("handicap_draw"),
            handicap_away=rec.get("handicap_away"),
            handicap_line=rec.get("handicap_line"),
            date=(str(rec.get("match_date") or "")+" "+str(rec.get("match_time") or "")).strip(),
            home_team=rec.get("home_team"),
            away_team=rec.get("away_team"),
            league=rec.get("league"),
            league_id=rec.get("league_id"),
            home_team_id=rec.get("home_team_id"),
            away_team_id=rec.get("away_team_id"),
            exact_score_odds=rec.get("exact_score_odds"),
            total_goal_odds=rec.get("total_goal_odds"),
            hafu_odds=rec.get("hafu_odds"),
            market_timestamp=rec.get("market_timestamp") or rec.get("sporttery_updated_at"),
            opening_odds=tuple(rec.get("opening_odds")) if rec.get("opening_odds") else None,
            external_1x2_books=rec.get("external_1x2_books"),
            external_total_books=rec.get("external_total_books"),
            external_handicap_books=rec.get("external_handicap_books"),
            data_quality=rec.get("data_quality"),
        )

    @staticmethod
    def save_snapshot(parsed, directory, prefix="sporttery"):
        directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        p=directory/f"{prefix}_{stamp}.json"
        p.write_text(json.dumps(parsed,ensure_ascii=False,indent=2),encoding="utf-8")
        return str(p)

    @staticmethod
    def compare_snapshots(old, new, probability_pp_threshold=2.0, odds_rel_threshold=0.05):
        old_map={str(x["match_id"]):x for x in old.get("matches",[])}
        changes=[]
        for cur in new.get("matches",[]):
            mid=str(cur["match_id"])
            prev=old_map.get(mid)
            if not prev:
                changes.append({"match_id":mid,"type":"new_match"})
                continue
            op=_devig_three((prev["home_odds"],prev["draw_odds"],prev["away_odds"]))
            np=_devig_three((cur["home_odds"],cur["draw_odds"],cur["away_odds"]))
            if not op or not np:
                continue
            pp=[100*(np[i]-op[i]) for i in range(3)]
            rel=[abs(cur[k]-prev[k])/prev[k] for k in ("home_odds","draw_odds","away_odds")
                 if prev.get(k) and cur.get(k)]
            if max(abs(x) for x in pp)>=probability_pp_threshold or (rel and max(rel)>=odds_rel_threshold):
                changes.append({
                    "match_id":mid,"type":"1x2_move",
                    "probability_move_pp":{"H":round(pp[0],3),"D":round(pp[1],3),"A":round(pp[2],3)},
                    "old_odds":[prev["home_odds"],prev["draw_odds"],prev["away_odds"]],
                    "new_odds":[cur["home_odds"],cur["draw_odds"],cur["away_odds"]],
                })
        return changes

class ExternalMarketConsensus:
    """Robust, source-by-source de-vigged multi-bookmaker 1X2 reference."""
    @staticmethod
    def aggregate_1x2(bookmaker_rows):
        usable=[]
        for row in bookmaker_rows:
            p=_devig_three((row.get("home"),row.get("draw"),row.get("away")))
            if p:
                usable.append((str(row.get("bookmaker") or "unknown"),p))
        if not usable:
            return None
        cols=list(zip(*(p for _,p in usable)))
        med=tuple(median(c) for c in cols)
        z=sum(med); med=tuple(x/z for x in med)
        def mad(vals):
            m=median(vals)
            return median([abs(x-m) for x in vals])
        dispersion=tuple(mad(list(c)) for c in cols)
        return {
            "n_bookmakers":len(usable),
            "median_prob":{"H":med[0],"D":med[1],"A":med[2]},
            "mad":{"H":dispersion[0],"D":dispersion[1],"A":dispersion[2]},
            "sources":[name for name,_ in usable],
        }

    @staticmethod
    def compare_sporttery(sporttery_odds, consensus, divergence_pp=4.0):
        if consensus is None:
            return None
        sp=_devig_three(sporttery_odds)
        cp=consensus["median_prob"]
        delta={"H":100*(sp[0]-cp["H"]),"D":100*(sp[1]-cp["D"]),"A":100*(sp[2]-cp["A"])}
        return {
            "sporttery_prob":{"H":sp[0],"D":sp[1],"A":sp[2]},
            "external_median_prob":cp,
            "delta_pp":{k:round(v,3) for k,v in delta.items()},
            "divergent":max(abs(v) for v in delta.values())>=divergence_pp,
        }

class TheOddsApiAdapter:
    """Optional multi-bookmaker reference source; requires ODDS_API_KEY."""
    BASE="https://api.the-odds-api.com/v4"

    def __init__(self, api_key=None, timeout=8.0):
        self.api_key=api_key or os.getenv("ODDS_API_KEY")
        self.timeout=float(timeout)

    def fetch(self, sport_key, regions="eu,uk", markets="h2h"):
        if not self.api_key:
            raise RuntimeError("ODDS_API_KEY is not set")
        q=urlencode({"apiKey":self.api_key,"regions":regions,"markets":markets,
                     "oddsFormat":"decimal","dateFormat":"iso"})
        req=Request(f"{self.BASE}/sports/{sport_key}/odds?{q}",
                    headers={"Accept":"application/json","User-Agent":"Jingcai-3.4.4/1.0"})
        with urlopen(req,timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    @staticmethod
    def extract_1x2(event):
        rows=[]
        home=event.get("home_team"); away=event.get("away_team")
        for book in event.get("bookmakers") or []:
            for market in book.get("markets") or []:
                if market.get("key")!="h2h":
                    continue
                price={}
                for o in market.get("outcomes") or []:
                    name=str(o.get("name") or "")
                    if name==home: price["home"]=_safe_float(o.get("price"))
                    elif name==away: price["away"]=_safe_float(o.get("price"))
                    elif name.lower()=="draw": price["draw"]=_safe_float(o.get("price"))
                if all(price.get(k) for k in ("home","draw","away")):
                    rows.append({"bookmaker":book.get("title") or book.get("key"),**price})
        return rows


# ---------------------------------------------------------------------------
# Jingcai 3.5 Alpha: probability/market architecture
# ---------------------------------------------------------------------------

class MultiDeVig:
    """Three-way de-vig ensemble used by 3.5 Alpha.

    The methods are intentionally exposed separately so walk-forward testing can
    learn league/bookmaker-specific weights instead of silently hard-coding one
    conversion forever.
    """
    DEFAULT_WEIGHTS={"multiplicative":0.34,"power":0.33,"shin":0.33}

    @staticmethod
    def _valid(odds):
        vals=[_safe_float(x) for x in odds]
        return vals if all(x is not None and x>1.0 for x in vals) else None

    @staticmethod
    def multiplicative(odds):
        vals=MultiDeVig._valid(odds)
        if not vals: return None
        inv=[1.0/x for x in vals]; z=sum(inv)
        return tuple(x/z for x in inv) if z>0 else None

    @staticmethod
    def power(odds):
        vals=MultiDeVig._valid(odds)
        if not vals: return None
        q=[1.0/x for x in vals]
        # solve sum(q_i**k)=1; football books normally have overround > 1.
        lo,hi=0.05,8.0
        for _ in range(90):
            mid=(lo+hi)/2.0
            s=sum(x**mid for x in q)
            if s>1.0: lo=mid
            else: hi=mid
        k=(lo+hi)/2.0
        p=[x**k for x in q]; z=sum(p)
        return tuple(x/z for x in p) if z>0 else None

    @staticmethod
    def shin(odds):
        vals=MultiDeVig._valid(odds)
        if not vals: return None
        q=[1.0/x for x in vals]; Q=sum(q)
        if Q<=1.0+1e-10:
            return MultiDeVig.multiplicative(vals)
        def probs(z):
            if z>=1.0: z=1.0-1e-10
            den=2.0*(1.0-z)
            return [(math.sqrt(z*z+4.0*(1.0-z)*(x*x)/Q)-z)/den for x in q]
        lo,hi=0.0,0.999999
        # If numerical shape is unusual, fall back rather than fabricate.
        if sum(probs(lo))<1.0:
            return MultiDeVig.multiplicative(vals)
        for _ in range(90):
            mid=(lo+hi)/2.0
            if sum(probs(mid))>1.0: lo=mid
            else: hi=mid
        p=probs((lo+hi)/2.0); z=sum(p)
        return tuple(max(0.0,x)/z for x in p) if z>0 else None

    @classmethod
    def ensemble(cls, odds, weights=None):
        pieces={
            "multiplicative":cls.multiplicative(odds),
            "power":cls.power(odds),
            "shin":cls.shin(odds),
        }
        pieces={k:v for k,v in pieces.items() if v is not None}
        if not pieces: return None
        w=dict(cls.DEFAULT_WEIGHTS if weights is None else weights)
        zw=sum(max(0.0,w.get(k,0.0)) for k in pieces)
        if zw<=0:
            w={k:1.0/len(pieces) for k in pieces}
        else:
            w={k:max(0.0,w.get(k,0.0))/zw for k in pieces}
        p=tuple(sum(w[k]*pieces[k][i] for k in pieces) for i in range(3))
        z=sum(p)
        p=tuple(x/z for x in p)
        return {"prob":{"H":p[0],"D":p[1],"A":p[2]},"methods":pieces,"weights":w}

    @staticmethod
    def two_way(a,b):
        aa,bb=_safe_float(a),_safe_float(b)
        if aa is None or bb is None: return None
        x,y=1.0/aa,1.0/bb; z=x+y
        return (x/z,y/z) if z>0 else None


class ResidualMarketCorrector:
    """Trainable residual layer: target = realised one-hot - market prior.

    It never receives permission to random-split time. The caller must fit it on
    strictly earlier observations and evaluate with walk-forward/OOS windows.
    """
    def __init__(self, random_state=42):
        self.random_state=int(random_state)
        self.models={}; self.feature_names=[]; self.fitted=False

    @staticmethod
    def _matrix(rows,names):
        return [[float(r.get(n,0.0) or 0.0) for n in names] for r in rows]

    def fit(self, feature_rows, market_rows, labels):
        if len(labels)<60 or not (len(feature_rows)==len(market_rows)==len(labels)):
            raise ValueError("Residual correction requires >=60 chronologically valid rows.")
        self.feature_names=sorted(set().union(*(r.keys() for r in feature_rows)))
        X=self._matrix(feature_rows,self.feature_names)
        try:
            from catboost import CatBoostRegressor
            factory=lambda:CatBoostRegressor(iterations=260,depth=5,learning_rate=0.035,
                loss_function="RMSE",random_seed=self.random_state,verbose=False,
                allow_writing_files=False)
        except Exception:
            from sklearn.ensemble import HistGradientBoostingRegressor
            factory=lambda:HistGradientBoostingRegressor(max_iter=220,learning_rate=0.04,
                max_leaf_nodes=15,l2_regularization=1.0,random_state=self.random_state)
        for side in OUTCOMES:
            y=[]
            for prior,label in zip(market_rows,labels):
                y.append((1.0 if str(label)==side else 0.0)-float(prior[side]))
            model=factory(); model.fit(X,y); self.models[side]=model
        self.fitted=True
        return self

    def correction(self, feature_row):
        if not self.fitted: return None
        X=self._matrix([feature_row],self.feature_names)
        raw={k:float(self.models[k].predict(X)[0]) for k in OUTCOMES}
        # Corrections are deliberately bounded until OOS calibration learns a scale.
        return {k:max(-0.18,min(0.18,v)) for k,v in raw.items()}


class Jingcai35AlphaEngine(JingcaiEngine):
    VERSION="3.5 Alpha"
    ALPHA_STATUS="EXPERIMENTAL_REQUIRES_WALK_FORWARD_OOS"

    def __init__(self,*args,strict_final_gate=True,residual_corrector=None,
                 team_strength_provider=None, external_min_books=2,
                 ev_a1=0.015,ev_a2=0.0,ev_s=0.025,**kwargs):
        # The 3.4 weight parameters are retained only for compatibility; 3.5
        # uses market prior + score distribution + residual correction below.
        super().__init__(*args,**kwargs)
        self.strict_final_gate=bool(strict_final_gate)
        self.residual_corrector=residual_corrector
        self.team_strength_provider=team_strength_provider
        self.external_min_books=int(external_min_books)
        self.ev_a1=float(ev_a1); self.ev_a2=float(ev_a2); self.ev_s=float(ev_s)
        self._alpha_prior_cache={}
        self._alpha_goal_cache={}
        self._alpha_score_cache={}

    def _alpha_cache_guard(self):
        # Bounded caches keep live repeated calculations fast without allowing
        # long historical runs to grow memory without limit.
        if len(self._alpha_goal_cache)>5000:
            self._alpha_prior_cache.clear(); self._alpha_goal_cache.clear(); self._alpha_score_cache.clear()

    # ---- Global Market Prior ------------------------------------------------
    def sporttery_devig(self,m):
        return MultiDeVig.ensemble((m.home_odds,m.draw_odds,m.away_odds))

    def external_consensus(self,m):
        rows=getattr(m,"external_1x2_books",None) or []
        usable=[]
        for row in rows:
            e=MultiDeVig.ensemble((row.get("home"),row.get("draw"),row.get("away")))
            if e:
                usable.append((str(row.get("bookmaker") or "unknown"),e["prob"],e))
        if not usable: return None
        med={k:median([x[1][k] for x in usable]) for k in OUTCOMES}
        med=self.normalize(med)
        mad={k:median([abs(x[1][k]-med[k]) for x in usable]) for k in OUTCOMES}
        return {"n_bookmakers":len(usable),"median_prob":med,"mad":mad,
                "sources":[x[0] for x in usable],"devig":"multi_method_ensemble"}

    def alpha_market_prior_meta(self,m):
        key=id(m)
        cached=self._alpha_prior_cache.get(key)
        if cached is not None: return cached
        sp=self.sporttery_devig(m)
        ext=self.external_consensus(m)
        if sp is None:
            raise ValueError("Sporttery 1X2 odds are required")
        sport=sp["prob"]
        if not ext:
            prior=dict(sport); ew=0.0
        else:
            disp=max(ext["mad"].values())
            n=ext["n_bookmakers"]
            # Alpha prior; walk-forward calibration may replace these weights.
            ew=min(0.82,0.52+0.055*min(n,5))
            if disp>0.035: ew=max(0.50,ew-0.12)
            elif disp>0.020: ew=max(0.52,ew-0.06)
            prior=self.normalize({k:(1-ew)*sport[k]+ew*ext["median_prob"][k] for k in OUTCOMES})
        result={"prior":prior,"sporttery":sport,"external":ext,
                "external_weight":ew,"sporttery_devig_methods":sp["methods"],
                "sporttery_devig_weights":sp["weights"]}
        self._alpha_prior_cache[key]=result
        self._alpha_cache_guard()
        return result

    def market_probabilities(self,m):
        return self.alpha_market_prior_meta(m)["prior"]

    # ---- Joint market calibration ------------------------------------------
    @staticmethod
    def _matrix_1x2(mat):
        p={"H":0.0,"D":0.0,"A":0.0}
        for (h,a),v in mat.items(): p["H" if h>a else "D" if h==a else "A"]+=v
        z=sum(p.values()); return {k:v/z for k,v in p.items()} if z else p

    @staticmethod
    def _matrix_hhad(mat,line):
        p={"H":0.0,"D":0.0,"A":0.0}
        for (h,a),v in mat.items():
            gd=h-a
            if line==-1:
                key="H" if gd>=2 else "D" if gd==1 else "A"
            elif line==1:
                key="H" if gd>=0 else "D" if gd==-1 else "A"
            else:
                return None
            p[key]+=v
        z=sum(p.values()); return {k:v/z for k,v in p.items()} if z else p

    @staticmethod
    def _matrix_total_bins(mat):
        d=defaultdict(float)
        for (h,a),v in mat.items(): d[h+a]+=v
        return d

    @staticmethod
    def _external_total_target(row):
        q=MultiDeVig.two_way(row.get("over"),row.get("under"))
        if not q: return None
        try: line=float(row.get("line"))
        except Exception: return None
        return line,q[0],q[1]

    def fit_goal_parameters(self,m,market=None):
        key=id(m)
        cached=self._alpha_goal_cache.get(key)
        if cached is not None: return cached
        market=market or self.market_probabilities(m)
        # Base gives a stable starting point; Alpha then fuses all available
        # projections of the same latent score distribution.
        bh,ba,br,bmeta=JingcaiEngine.fit_goal_parameters(self,m,market)
        hp=self.handicap_probs(m)
        hline=getattr(m,"handicap_line",None)
        tg_target=self.total_goal_market_devig(m)
        score_target=self.score_market_devig(m) if getattr(m,"exact_score_odds",None) else {}
        ext_tot=[x for x in (getattr(m,"external_total_books",None) or []) if self._external_total_target(x)]
        ext_hcap=[]
        for row in (getattr(m,"external_handicap_books",None) or []):
            try: line=int(float(row.get("line")))
            except Exception: continue
            e=MultiDeVig.ensemble((row.get("home"),row.get("draw"),row.get("away")))
            if e and line in (-1,1): ext_hcap.append((line,e["prob"]))
        etg=self.league_etg_target(m,market)
        dyn_target=None
        provider=self.team_strength_provider
        if provider is not None and all(getattr(m,k,None) is not None for k in ("league_id","home_team_id","away_team_id")):
            try:
                raw_ts=str(getattr(m,"date",None) or getattr(m,"market_timestamp",None) or "").replace("Z","+00:00")
                current_ts=datetime.fromisoformat(raw_ts) if raw_ts else datetime.now(timezone.utc)
                dl,da,dmeta=provider.expected_goals(m.league_id,m.home_team_id,m.away_team_id,current_ts,
                                                     market_lh=bh,market_la=ba)
                dyn_target=(dl,da,dmeta)
            except Exception:
                dyn_target=None


        def objective(lh,la,rho):
            mat=self._dc_matrix_params(lh,la,rho,max_goals=10)
            p1=self._matrix_1x2(mat)
            loss=1.15*sum((p1[k]-market[k])**2 for k in OUTCOMES)
            if hp is not None and hline in (-1,1):
                mh=self._matrix_hhad(mat,hline)
                loss+=0.85*sum((mh[k]-hp[k])**2 for k in OUTCOMES)
            for line,target in ext_hcap:
                mh=self._matrix_hhad(mat,line)
                loss+=0.34*sum((mh[k]-target[k])**2 for k in OUTCOMES)
            totals=self._matrix_total_bins(mat)
            if tg_target:
                for key,q in tg_target.items():
                    if key=="7+": mp=sum(v for t,v in totals.items() if t>=7)
                    else:
                        try: mp=totals.get(int(key),0.0)
                        except Exception: continue
                    loss+=0.42*(mp-q)**2
            for row in ext_tot:
                line,po,pu=self._external_total_target(row)
                push=sum(v for t,v in totals.items() if abs(t-line)<1e-9)
                over=sum(v for t,v in totals.items() if t>line)
                under=sum(v for t,v in totals.items() if t<line)
                den=max(1e-9,1.0-push)
                loss+=0.36*((over/den-po)**2+(under/den-pu)**2)
            if score_target:
                # Exact-score market is a soft joint-calibration signal, not an
                # absolute oracle. Limit to the most market-relevant exact states.
                exact=[(s,q) for s,q in score_target.items() if self._score_key_to_tuple(s) is not None]
                exact=sorted(exact,key=lambda x:x[1],reverse=True)[:12]
                for s,q in exact:
                    h,a=self._score_key_to_tuple(s); loss+=0.10*(mat.get((h,a),0.0)-q)**2
            if m.expected_goals_home is not None and m.expected_goals_away is not None:
                loss+=0.12*((lh-float(m.expected_goals_home))/0.55)**2
                loss+=0.12*((la-float(m.expected_goals_away))/0.55)**2
            if dyn_target is not None:
                dl,da,_=dyn_target
                loss+=0.15*((lh-dl)/0.60)**2+0.15*((la-da)/0.60)**2
            loss+=0.035*((lh+la-etg)/0.65)**2
            return loss

        best=(objective(bh,ba,br),bh,ba,br)
        for step,span in ((0.10,6),(0.04,5),(0.015,4)):
            _,ch,ca,cr=best
            candidates=[]
            for i in range(-span,span+1):
                lh=max(0.15,min(4.5,ch+i*step))
                for j in range(-span,span+1):
                    la=max(0.15,min(4.2,ca+j*step))
                    for rho in (max(-0.22,min(0.14,cr+x)) for x in (-step/2,0.0,step/2)):
                        candidates.append((objective(lh,la,rho),lh,la,rho))
            best=min([best]+candidates,key=lambda x:x[0])
        result=(best[1],best[2],best[3],{
            "loss":best[0],"league_etg_target":etg,"handicap_used":hp is not None,
            "handicap_fused_downstream":False,"ou_used":bool(self.totals_probabilities(m) or ext_tot),
            "total_line":getattr(m,"total_line",None),"joint_calibration":True,
            "exact_score_used":bool(score_target),"external_total_rows":len(ext_tot),
            "external_handicap_rows":len(ext_hcap),"base_start":(bh,ba,br),
            "dynamic_team_strength_used":dyn_target is not None,
            "dynamic_team_strength_meta":None if dyn_target is None else dyn_target[2],
        })
        self._alpha_goal_cache[key]=result
        self._alpha_cache_guard()
        return result

    # ---- Multi-distribution score model ------------------------------------
    def score_matrix(self,m,max_goals=8):
        key=(id(m),int(max_goals))
        cached=self._alpha_score_cache.get(key)
        if cached is not None: return cached
        market=self.market_probabilities(m)
        lh,la,rho,_=self.fit_goal_parameters(m,market)
        disp=float(m.league_dispersion) if m.league_dispersion is not None else 1.0
        mats={
            "poisson":self._dc_matrix_params(lh,la,0.0,max_goals=max_goals),
            "dixon_coles":self._dc_matrix_params(lh,la,rho,max_goals=max_goals),
            "bivariate_poisson":JingcaiEngine.candidate_score_matrix(self,m,"bivariate_poisson",max_goals=max_goals),
            "negative_binomial":JingcaiEngine.candidate_score_matrix(self,m,"negative_binomial",max_goals=max_goals),
        }
        _,_,has_bayes=self.hierarchical_bayes_lambdas(m,lh,la)
        if has_bayes:
            mats["hierarchical_bayes"]=JingcaiEngine.candidate_score_matrix(self,m,"hierarchical_bayes",max_goals=max_goals)
        etg=lh+la
        weights={"poisson":0.15,"dixon_coles":0.30,"bivariate_poisson":0.25,"negative_binomial":0.15}
        if has_bayes: weights["hierarchical_bayes"]=0.15
        else: weights["dixon_coles"]+=0.08; weights["bivariate_poisson"]+=0.07
        if disp>1.10:
            weights["negative_binomial"]+=min(0.12,0.18*(disp-1.0)); weights["poisson"]-=0.05
        if etg<2.35:
            weights["dixon_coles"]+=0.07; weights["poisson"]-=0.03
        weights={k:max(0.02,v) for k,v in weights.items() if k in mats}
        z=sum(weights.values()); weights={k:v/z for k,v in weights.items()}
        keys=set().union(*(x.keys() for x in mats.values()))
        out={k:sum(weights[n]*mats[n].get(k,0.0) for n in weights) for k in keys}
        result=self._normalize_matrix(out)
        self._alpha_score_cache[key]=result
        self._alpha_cache_guard()
        return result

    def score_ensemble_meta(self,m):
        market=self.market_probabilities(m); lh,la,rho,meta=self.fit_goal_parameters(m,market)
        return {"lambda_home":lh,"lambda_away":la,"rho":rho,"etg":lh+la,
                "joint_calibration":meta,"league_dispersion":m.league_dispersion}

    # ---- Residual ML / probability calibration ------------------------------
    def _alpha_features(self,m,prior,score):
        base={k:0.72*prior[k]+0.28*score[k] for k in OUTCOMES}
        f=self.meta_feature_dict(m,market=prior,score=score,base=base)
        meta=self.alpha_market_prior_meta(m)
        ext=meta.get("external") or {}
        f["external_books"]=float(ext.get("n_bookmakers",0))
        for k in OUTCOMES:
            f["sporttery_minus_external_"+k]=0.0 if not ext else meta["sporttery"][k]-ext["median_prob"][k]
            f["external_mad_"+k]=0.0 if not ext else ext["mad"][k]
        return f

    def alpha_probabilities(self,m):
        prior=self.market_probabilities(m)
        score=self.score_probabilities(m)
        # Market remains the dominant prior; independent score structure can move
        # it, but not erase the information contained in the closing market.
        out=self.normalize({k:0.68*prior[k]+0.32*score[k] for k in OUTCOMES})
        strength=self.strength_probabilities(m,prior)
        if strength is not None:
            out=self.normalize({k:0.88*out[k]+0.12*strength[k] for k in OUTCOMES})
        if self.residual_corrector is not None and getattr(self.residual_corrector,"fitted",False):
            corr=self.residual_corrector.correction(self._alpha_features(m,prior,score))
            if corr:
                out=self.normalize({k:max(1e-6,out[k]+0.22*corr[k]) for k in OUTCOMES})
        if self.probability_calibrator is not None and getattr(self.probability_calibrator,"fitted",False):
            out=self.probability_calibrator.calibrate_dict(out)
        return out,prior,score

    # ---- Play-specific model probabilities ---------------------------------
    def model_hhad_probabilities(self,m):
        if getattr(m,"handicap_line",None) not in (-1,1): return None
        return self._matrix_hhad(self.score_matrix(m,max_goals=9),m.handicap_line)

    def model_ttg_probabilities(self,m):
        totals=self._matrix_total_bins(self.score_matrix(m,max_goals=10))
        p={k:totals.get(k,0.0) for k in range(7)}
        p["7+"]=sum(v for k,v in totals.items() if k>=7)
        z=sum(p.values()); return {k:v/z for k,v in p.items()} if z else p

    def model_hafu_probabilities(self,m,half_fraction=0.46,max_goals=7):
        market=self.market_probabilities(m); lh,la,_,_=self.fit_goal_parameters(m,market)
        fh=max(0.05,lh*half_fraction); fa=max(0.05,la*half_fraction)
        sh=max(0.05,lh*(1-half_fraction)); sa=max(0.05,la*(1-half_fraction))
        out={k:0.0 for k in ("hh","hd","ha","dh","dd","da","ah","ad","aa")}
        def side(h,a): return "h" if h>a else "d" if h==a else "a"
        for hh in range(max_goals+1):
            ph=self.poisson(hh,fh)
            for ha in range(max_goals+1):
                pht=ph*self.poisson(ha,fa); hs=side(hh,ha)
                for rh in range(max_goals+1):
                    prh=self.poisson(rh,sh)
                    for ra in range(max_goals+1):
                        p=pht*prh*self.poisson(ra,sa)
                        fs=side(hh+rh,ha+ra); out[hs+fs]+=p
        z=sum(out.values()); return {k:v/z for k,v in out.items()} if z else out

    # ---- Strict final gate --------------------------------------------------
    def validation_gate(self,m):
        missing=[]
        if not all(_safe_float(x) for x in (m.home_odds,m.draw_odds,m.away_odds)): missing.append("Sporttery_HAD")
        if self.handicap_probs(m) is None or getattr(m,"handicap_line",None) not in (-1,1): missing.append("Sporttery_HHAD")
        if not getattr(m,"total_goal_odds",None): missing.append("Sporttery_TTG")
        if not getattr(m,"exact_score_odds",None): missing.append("Sporttery_CRS")
        if not getattr(m,"hafu_odds",None): missing.append("Sporttery_HAFU")
        ext=self.external_consensus(m)
        if not ext or ext.get("n_bookmakers",0)<self.external_min_books: missing.append("External_1X2")
        if not ((getattr(m,"external_total_books",None) or []) or (getattr(m,"external_handicap_books",None) or [])):
            missing.append("External_Derivative_Market")
        if not (getattr(m,"home_team",None) and getattr(m,"away_team",None) and getattr(m,"league",None)):
            missing.append("Basic_Team_League_Info")
        if not (getattr(m,"market_timestamp",None) or getattr(m,"date",None)):
            missing.append("Market_Timestamp")
        return {"complete":not missing,"missing":missing,
                "status":"COMPLETE" if not missing else "3.5 Alpha 未完整执行"}

    def movement_and_dispersion(self,m,selected,prior):
        meta=self.alpha_market_prior_meta(m); ext=meta.get("external")
        selected_ext=None; sport_minus_ext=None; disp=None
        if ext:
            selected_ext=ext["median_prob"][selected]
            sport_minus_ext=100*(meta["sporttery"][selected]-selected_ext)
            disp=100*ext["mad"][selected]
        move=None
        if getattr(m,"opening_odds",None):
            o=MultiDeVig.ensemble(m.opening_odds)
            if o: move=100*(prior[selected]-o["prob"][selected])
        elif getattr(m,"odds_history",None) and len(m.odds_history)>=2:
            o=MultiDeVig.ensemble(m.odds_history[0])
            if o: move=100*(prior[selected]-o["prob"][selected])
        return {"external_selected_probability":selected_ext,"sporttery_minus_external_pp":sport_minus_ext,
                "external_dispersion_pp":disp,"open_to_current_selected_pp":move}

    @staticmethod
    def _grade_rank(g):
        return {"S":5,"A1":4,"A2":3,"B":2,"C":1,"UNVERIFIED":0}.get(g,0)

    def _edge_grade(self,p,odds,lead,risk_level="MEDIUM",s_allowed=True):
        if odds is None or odds<=1.0: return "B",None
        ev=p*odds-1.0
        if ev<self.ev_a2: return ("C" if ev<-0.06 else "B"),ev
        grade="B"
        if p>=0.44 and lead>=0.045 and ev>=self.ev_a2: grade="A2"
        if p>=0.515 and lead>=0.085 and ev>=self.ev_a1: grade="A1"
        if s_allowed and p>=0.595 and lead>=0.135 and ev>=self.ev_s: grade="S"
        if risk_level=="HIGH" and grade in ("S","A1","A2"):
            grade={"S":"A1","A1":"A2","A2":"B"}[grade]
        return grade,ev

    def independent_play_ratings(self,m,final,selected,flip,score_gate):
        gate=self.validation_gate(m)
        if self.strict_final_gate and not gate["complete"]:
            return ({k:"UNVERIFIED" for k in ("HAD","HHAD","CRS","TTG","HAFU")},
                    {},{},gate)
        risk=(flip or {}).get("risk_level","MEDIUM")
        vals=sorted(final.values(),reverse=True); lead=vals[0]-vals[1]
        oddmap={"H":m.home_odds,"D":m.draw_odds,"A":m.away_odds}
        had_g,had_ev=self._edge_grade(final[selected],oddmap[selected],lead,risk,True)
        grades={"HAD":had_g}; picks={"HAD":selected}; evs={"HAD":had_ev}

        hp=self.model_hhad_probabilities(m)
        if hp and self.handicap_probs(m):
            sel=max(hp,key=hp.get); sv=sorted(hp.values(),reverse=True)
            om={"H":m.handicap_home,"D":m.handicap_draw,"A":m.handicap_away}
            g,e=self._edge_grade(hp[sel],om.get(sel),sv[0]-sv[1],risk,False)
            grades["HHAD"]=g; picks["HHAD"]={"line":m.handicap_line,"pick":sel,"p":hp[sel]}; evs["HHAD"]=e
        else: grades["HHAD"]="B"

        # CRS: value of the best exact score + structural confirmation.
        mat=self.score_matrix(m,max_goals=9)
        sr=sorted(((f"{h}-{a}",p) for (h,a),p in mat.items()),key=lambda x:x[1],reverse=True)
        candidates=[]
        for s,p in sr[:12]:
            odd=(m.exact_score_odds or {}).get(s)
            if odd and odd>1: candidates.append((p*odd-1.0,p,s,odd))
        if candidates:
            best=max(candidates); e,p,s,odd=best
            sfull=(score_gate.get("available") and score_gate.get("dual_confirm_count",0)>=2 and score_gate.get("value_pass_count",0)>=2)
            if sfull and e>=self.ev_s and p>=0.08 and risk!="HIGH": g="S"
            elif e>=self.ev_a1 and p>=0.065: g="A1"
            elif e>=0 and p>=0.045: g="A2"
            else: g="B"
            grades["CRS"]=g; picks["CRS"]={"score":s,"p":p,"odds":odd}; evs["CRS"]=e
        else: grades["CRS"]="B"

        tp=self.model_ttg_probabilities(m); tc=[]
        for k,p in tp.items():
            odd=(m.total_goal_odds or {}).get(k)
            if odd is None: odd=(m.total_goal_odds or {}).get(str(k))
            if odd and odd>1: tc.append((p*float(odd)-1.0,p,k,float(odd)))
        if tc:
            e,p,k,odd=max(tc); second=sorted(tp.values(),reverse=True)[1]
            g,_=self._edge_grade(p,odd,p-second,risk,False)
            grades["TTG"]=g; picks["TTG"]={"goals":k,"p":p,"odds":odd}; evs["TTG"]=e
        else: grades["TTG"]="B"

        hf=self.model_hafu_probabilities(m); hc=[]
        for k,p in hf.items():
            odd=(m.hafu_odds or {}).get(k)
            if odd and odd>1: hc.append((p*float(odd)-1.0,p,k,float(odd)))
        if hc:
            e,p,k,odd=max(hc); vals=sorted(hf.values(),reverse=True)
            g,_=self._edge_grade(p,odd,vals[0]-vals[1],risk,False)
            grades["HAFU"]=g; picks["HAFU"]={"pick":k,"p":p,"odds":odd}; evs["HAFU"]=e
        else: grades["HAFU"]="B"
        return grades,picks,evs,gate

    def _overall_grade(self,grades,risk):
        if any(g=="UNVERIFIED" for g in grades.values()): return "UNVERIFIED"
        had=grades.get("HAD","B")
        derivatives=[grades.get(k,"B") for k in ("HHAD","CRS","TTG")]
        support=max((self._grade_rank(g) for g in derivatives),default=0)
        if had=="S" and support>=self._grade_rank("A1") and risk!="HIGH": return "S"
        if had in ("S","A1") and support>=self._grade_rank("A2"): return "A1"
        if had in ("S","A1","A2"): return "A2"
        return "B" if had=="B" else "C"

    def predict(self,m):
        final,prior,score=self.alpha_probabilities(m)
        selected=max(OUTCOMES,key=lambda k:final[k])
        vals=sorted(final.values(),reverse=True); conf=0.65*vals[0]+0.35*(vals[0]-vals[1])

        # Legacy Case is diagnostic-only and cannot alter Alpha selection.
        case,case_excluded_signal,case_reasons=self.odds_rule(m)
        reasons=list(case_reasons)
        reasons.append("3.5 Alpha: Case1/2/3 permanently excluded from production decision chain.")

        ext=self.external_consensus(m)
        previous=None
        if getattr(m,"opening_odds",None): previous=m.opening_odds
        elif getattr(m,"odds_history",None): previous=m.odds_history[0]
        flip=FlipRiskEstimator.evaluate(m,selected,final,external_consensus=ext,
                                        previous_sporttery_odds=previous)
        score_gate=self.result_handicap_score_gate(m,selected,"A",market=prior)
        grades,picks,evs,gate=self.independent_play_ratings(m,final,selected,flip,score_gate)
        overall=self._overall_grade(grades,(flip or {}).get("risk_level","MEDIUM"))
        if self.strict_final_gate and not gate["complete"]:
            reasons.append("3.5 Alpha 未完整执行: "+", ".join(gate["missing"]))
        else:
            reasons.append("3.5 Alpha Final Gate complete; play-specific EV/Edge grades are eligible.")

        movement=self.movement_and_dispersion(m,selected,prior)
        smeta=self.score_ensemble_meta(m)
        reasons.append(f"Global Market Prior={prior}; selected={selected}, p={final[selected]:.4f}.")
        reasons.append(f"Joint λ: home={smeta['lambda_home']:.3f}, away={smeta['lambda_away']:.3f}, ETG={smeta['etg']:.3f}.")
        reasons.append(f"Play grades={grades}; EV={evs}; risk={(flip or {}).get('risk_level')}.")

        mat=self.score_matrix(m,max_goals=9)
        score_top=sorted(((f"{h}-{a}",p) for (h,a),p in mat.items()),key=lambda x:x[1],reverse=True)
        tp=self.model_ttg_probabilities(m)
        total_top=sorted(tp.items(),key=lambda kv:kv[1],reverse=True)[:5]
        hs=self.handicap_structure(m,selected)
        return Prediction(
            match_id=m.match_id,probabilities=final,market_probabilities=prior,
            score_1x2_probabilities=score,selected=selected,excluded=None,grade=overall,
            confidence=conf,odds_rule_case=case,score_top=score_top,reasons=reasons,
            total_goals_top=total_top,margin_structure=hs,case_excluded_signal=case_excluded_signal,
            play_grades=grades,play_picks=picks,play_ev=evs,
            validation_status=gate["status"],validation_missing=gate["missing"],
            market_prior=prior,market_prior_meta=self.alpha_market_prior_meta(m),
            uncertainty={"flip_risk":flip,"movement_dispersion":movement,
                         "score_model":smeta,"alpha_unfitted_thresholds":True},
            alpha_status=self.ALPHA_STATUS,
        )


def merge_external_market_snapshot(snapshot, external_payload):
    """Merge user/API supplied external market rows into a Sporttery snapshot.

    Supported external JSON shape:
      {"matches":[{"match_id":"周日016","1x2":[...],"totals":[...],"handicap":[...]}]}
    A team-name fallback is used only when match_id is unavailable.
    """
    if not external_payload: return snapshot
    rows=external_payload.get("matches",external_payload if isinstance(external_payload,list) else [])
    by_id={str(x.get("match_id")):x for x in rows if x.get("match_id")}
    by_name={(str(x.get("home_team") or "").strip().lower(),str(x.get("away_team") or "").strip().lower()):x
             for x in rows if x.get("home_team") and x.get("away_team")}
    for rec in snapshot.get("matches",[]):
        ext=by_id.get(str(rec.get("match_id"))) or by_name.get((str(rec.get("home_team") or "").strip().lower(),
                                                               str(rec.get("away_team") or "").strip().lower()))
        if not ext: continue
        rec["external_1x2_books"]=ext.get("1x2") or ext.get("h2h") or []
        rec["external_total_books"]=ext.get("totals") or ext.get("ou") or []
        rec["external_handicap_books"]=ext.get("handicap") or ext.get("spreads") or []
        if ext.get("opening_odds"): rec["opening_odds"]=ext.get("opening_odds")
        if ext.get("data_quality"): rec["data_quality"]=ext.get("data_quality")
    return snapshot


def build_live_predictions(snapshot,engine=None):
    engine=engine or Jingcai35AlphaEngine(a_min_odds=1.60,strict_final_gate=True)
    out=[]
    for rec in snapshot.get("matches",[]):
        m=SportteryLiveAdapter.to_match_input(rec)
        p=engine.predict(m)
        out.append({
            "match_id":m.match_id,"home_team":m.home_team,"away_team":m.away_team,
            "selected":p.selected,"grade":p.grade,"confidence":p.confidence,
            "probabilities":p.probabilities,"market_prior":p.market_prior,
            "play_grades":p.play_grades,"play_picks":p.play_picks,"play_ev":p.play_ev,
            "score_top":p.score_top[:5],"margin_structure":p.margin_structure,
            "total_goals_top":p.total_goals_top[:5],"uncertainty":p.uncertainty,
            "validation_status":p.validation_status,"validation_missing":p.validation_missing,
            "alpha_status":p.alpha_status,"source_updated_at":snapshot.get("updated_at"),
        })
    return out


def alpha_metrics(rows,engine):
    """Chronological-safe metric shell. It reports what can be measured from rows.

    Formal Alpha promotion still requires walk-forward probability calibration,
    RPS/LogLoss, ROI, drawdown and CLV data with timestamped external snapshots.
    """
    preds=[engine.predict(m) for m in rows]
    valid=[]
    brier=[]; logloss=[]; rps=[]; correct=0
    for m,p in zip(rows,preds):
        if m.goals_home is None or m.goals_away is None: continue
        y="H" if m.goals_home>m.goals_away else "D" if m.goals_home==m.goals_away else "A"
        valid.append((m,p,y)); correct+=int(p.selected==y)
        brier.append(sum((p.probabilities[k]-(1.0 if y==k else 0.0))**2 for k in OUTCOMES))
        logloss.append(-math.log(max(1e-12,p.probabilities[y])))
        # RPS with ordered H-D-A as a conventional 3-category diagnostic.
        cp=[p.probabilities["H"],p.probabilities["H"]+p.probabilities["D"]]
        yy=[1.0 if y=="H" else 0.0, 0.0 if y=="A" else 1.0]
        rps.append(sum((a-b)**2 for a,b in zip(cp,yy))/2.0)
    n=len(valid)
    return {"n":n,"accuracy":correct/n if n else None,
            "brier":sum(brier)/n if n else None,"logloss":sum(logloss)/n if n else None,
            "rps":sum(rps)/n if n else None,
            "formal_alpha_promotion":False,
            "promotion_requires":["walk_forward_oos","calibration","ROI","max_drawdown","CLV"]}


def main():
    ap=argparse.ArgumentParser(description="Jingcai 3.5 Alpha")
    ap.add_argument("--csv",default=None)
    ap.add_argument("--json-output")
    ap.add_argument("--predict-output")
    ap.add_argument("--a-min-odds",type=float,default=1.60)
    ap.add_argument("--live-sporttery",action="store_true")
    ap.add_argument("--snapshot-dir",default=None)
    ap.add_argument("--live-date",default=None)
    ap.add_argument("--external-market-json",default=None,
                    help="External market snapshot JSON; required for strict FINAL S/A eligibility")
    ap.add_argument("--research-mode",action="store_true",
                    help="Allow probability research without strict Final Gate; never treat grades as betting freeze")
    args=ap.parse_args()

    strict=not args.research_mode
    if args.live_sporttery:
        adapter=SportteryLiveAdapter(); snap=adapter.fetch_matches()
        if args.live_date:
            snap={"updated_at":snap.get("updated_at"),
                  "matches":[m for m in snap.get("matches",[]) if str(m.get("match_num_date"))==str(args.live_date)]}
        if args.external_market_json:
            ext=json.loads(Path(args.external_market_json).read_text(encoding="utf-8"))
            snap=merge_external_market_snapshot(snap,ext)
        saved=adapter.save_snapshot(snap,args.snapshot_dir) if args.snapshot_dir else None
        eng=Jingcai35AlphaEngine(a_min_odds=args.a_min_odds,strict_final_gate=strict,
                                 enable_research_score_ensemble=True)
        preds=build_live_predictions(snap,eng)
        complete=sum(1 for p in preds if p["validation_status"]=="COMPLETE")
        stamp=datetime.now(timezone.utc).isoformat()
        payload={"engine":"Jingcai 3.5 Alpha","alpha_status":eng.ALPHA_STATUS,
                 "freeze_label":f"Jingcai 3.5 Alpha | FINAL | {stamp}" if strict else "RESEARCH_MODE_NOT_A_BETTING_FREEZE",
                 "source":"Sporttery + external market prior" if args.external_market_json else "Sporttery only (strict gate will remain incomplete)",
                 "updated_at":snap.get("updated_at"),"snapshot":saved,
                 "match_count":len(preds),"complete_gate_matches":complete,"predictions":preds}
        print(json.dumps(payload,ensure_ascii=False,indent=2,default=str))
        if args.json_output:
            Path(args.json_output).write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        return

    if not args.csv:
        raise SystemExit("Provide --live-sporttery or --csv.")
    rows=load_csv(args.csv)
    eng=Jingcai35AlphaEngine(a_min_odds=args.a_min_odds,strict_final_gate=False,
                             enable_research_score_ensemble=True)
    report={"engine":"Jingcai 3.5 Alpha","mode":"historical_research_not_final_freeze",
            "alpha_status":eng.ALPHA_STATUS,"metrics":alpha_metrics(rows,eng),
            "case123":"diagnostic_only_not_in_production"}
    print(json.dumps(report,ensure_ascii=False,indent=2,default=str))
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    if args.predict_output:
        fields=["match_id","date","selected","grade","validation_status","confidence",
                "HAD_grade","HHAD_grade","CRS_grade","TTG_grade","HAFU_grade","p_H","p_D","p_A"]
        with open(args.predict_output,"w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
            for m in rows:
                p=eng.predict(m); g=p.play_grades or {}
                w.writerow({"match_id":m.match_id,"date":m.date or "","selected":p.selected,
                    "grade":p.grade,"validation_status":p.validation_status,"confidence":p.confidence,
                    "HAD_grade":g.get("HAD"),"HHAD_grade":g.get("HHAD"),"CRS_grade":g.get("CRS"),
                    "TTG_grade":g.get("TTG"),"HAFU_grade":g.get("HAFU"),
                    "p_H":p.probabilities["H"],"p_D":p.probabilities["D"],"p_A":p.probabilities["A"]})

if __name__=="__main__":
    main()
