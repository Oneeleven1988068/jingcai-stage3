# v0.1.5-alpha — Hybrid CRS Satellite Bet Construction Patch

Status: `RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED`

This patch adds a **shadow portfolio-construction layer**. It does not change Jingcai 3.4 production probabilities, ratings, staking thresholds, or model promotion state.

## Implemented

1. New independent CRS opportunity axis: `CRS_SINGLE_OPPORTUNITY = NO / CENTER / TAIL`.
2. New construction class: `HYBRID_CRS_SATELLITE`.
3. Default three-match role separation:
   - one CRS opportunity satellite;
   - two pre-existing A1/A2 direction anchors;
   - pair-plus-triple coverage.
4. B/C matches may not be upgraded merely to fill the template.
5. Three legs must be three distinct matches.
6. Mixed-pool compatibility must be observed in the actual Sporttery calculator/ticket flow; generic pool formula lists are not sufficient.
7. Exact primitive-bet expansion is audited. “3x4” is four bets only when all three legs are single selections.
8. High odds and positive EV are explicitly separated; `CRS_PRICE_VALUE_UNVERIFIED` remains a warning.
9. New experiment `EXP-009-HYBRID-CRS-SATELLITE` with cost-matched OOS falsification.
10. T0 周二003 stored as a CRS-TAIL satellite candidate, but the construction remains incomplete because two qualifying anchors are not yet present and mixed-pool executability is unverified.

## No promotion

`MODEL_PROMOTION_NOT_GRANTED` remains unchanged.
