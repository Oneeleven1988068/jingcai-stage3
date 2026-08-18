# Hybrid CRS Satellite Bet Construction Policy

Status: `SHADOW_ONLY__MODEL_PROMOTION_NOT_GRANTED`

## Purpose

Separate **direction confidence** from **exact-score payout opportunity**.

A match does not need to be S/A1 in the direction layer to be a CRS opportunity. Conversely, a high-odds exact score is not automatically a value bet.

The new construction class is:

`HYBRID_CRS_SATELLITE`

Default role structure:

1. **CRS SATELLITE** — one match that independently passes `CRS_SINGLE_OPPORTUNITY` as `CENTER` or `TAIL`.
2. **DIRECTION ANCHOR 1** — a different match already rated A1 or A2.
3. **DIRECTION ANCHOR 2** — a third match already rated A1 or A2.
4. **COVER PLAN** — pair coverage plus the full triple: Satellite×Anchor1, Satellite×Anchor2, Anchor1×Anchor2, Satellite×Anchor1×Anchor2.

## Hard governance rules

- Never upgrade a B/C match to A1/A2 merely to fill the three-leg template.
- The three legs must be three distinct matches.
- CRS opportunity and direction rating are separate axes.
- `HIGH_ODDS != POSITIVE_EV`. Price value must be stated separately; `UNVERIFIED` stays `UNVERIFIED`.
- Mixed-pool / cross-play compatibility must be **observed in the actual Sporttery calculator/ticket interface** before it may be treated as executable. Generic formula lists are insufficient proof.
- The Sporttery sales state and prediction-freeze timestamp must be verified before any staking permission.
- No automatic stake sizing, rating change, model promotion, or ticket submission.

## Critical cost-expansion rule

A nominal “3x4 / pair+triple” structure equals four primitive bets **only when every leg has exactly one selection**.

If the CRS satellite contains `k` score selections and both anchors are single selections, the true primitive-bet count is:

`k + k + 1 + k = 3k + 1`

Examples:

- 1 CRS score + 1 + 1 anchor selections → 4 primitive bets.
- 3 CRS scores + 1 + 1 → 10 primitive bets.

If an A2 anchor itself uses two outcomes, expansion is larger again. Ticket cost must always be audited from primitive selections, never from the nominal pass label alone.

## Evaluation requirement

This is a portfolio-construction hypothesis, not a production edge. Evaluate pre-match frozen, cost-matched OOS variants:

- anchor-only direction pair;
- standalone CRS + anchor-only pair;
- pure three-leg combination;
- `HYBRID_CRS_SATELLITE` pair+triple cover.

Primary metrics:

- total stake / primitive bet count;
- payout and ROI;
- max drawdown;
- positive-return frequency;
- tail-hit contribution;
- payout concentration / dependence on a few rare hits;
- S/A1/A2 high-confidence error rate;
- league/time-window stability.

Promotion remains forbidden without explicit human approval after OOS falsification.
