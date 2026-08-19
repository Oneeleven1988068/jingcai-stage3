# Draw Branch Preservation Gate

Status: **SHADOW / RESEARCH ONLY**  
Production baseline: **Jingcai 3.4 unchanged**  
Promotion: **NOT GRANTED**

## Purpose

Prevent a live draw branch that survives HAD/CRS/TTG analysis from being silently deleted during final compression.

This gate was added after the 2026-08-18 Tue003 postmortem, where the realized state `2:2` was present in the pre-compression CRS distribution and the 4+ TTG tail, but the final score satellite retained only home-win tail states.

## Audit invariant

Before a final score or direction shortlist is presented:

1. Preserve the full pre-compression CRS distribution.
2. Aggregate CRS by H/D/A result branch.
3. Record HAD H/D/A probabilities.
4. Record the final retained selections and their result branches.
5. If a draw state exists in the diagnostic candidate layer but the final shortlist contains no draw state, emit `DRAW_BRANCH_EXTINCTION`.
6. If a draw exact state is already inside the diagnostic Top-K candidate layer, request `PRESERVE_AT_LEAST_ONE_DRAW_STATE_FOR_SHADOW_REVIEW`.
7. The review request is not an automatic bet addition, rating change, or production rule.

## Important boundary

No probability threshold is frozen in v0.1. The diagnostic Top-K is a research control and must be OOS-tested before any promotion.

## Required outputs

- HAD draw probability and rank
- CRS aggregate draw probability and rank
- diagnostic Top-K draw states
- TTG support for the totals corresponding to those draw states
- draw movement evidence, if observed
- final draw retained / extinguished
- explicit retained-vs-dropped branch audit

## Falsification

Keep shadow / reject if preserving a draw branch does not improve matched-cost OOS decision quality, materially worsens ROI/drawdown, or merely increases ticket cost without reducing high-confidence misses.
