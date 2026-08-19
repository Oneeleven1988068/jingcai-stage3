# v0.1.6-alpha — Draw Branch Preservation + Confirmation Provenance

Status: `RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED`

## Implemented

1. Added a shadow **Draw Branch Preservation Gate** that audits whether final compression extinguishes a draw branch still present in HAD/CRS/TTG state space.
2. Added explicit `DRAW_BRANCH_EXTINCTION` reporting.
3. Added shadow recommendation `PRESERVE_AT_LEAST_ONE_DRAW_STATE_FOR_SHADOW_REVIEW` when a draw exact state is already present in the diagnostic candidate layer but the final shortlist removes all draw states.
4. Added a **Confirmation Provenance Gate**: Sporttery HAD/HHAD/TTG/CRS/HAFU are one source family for independent-confirmation counting.
5. Added label `SAME_SOURCE_STRUCTURAL_CONSISTENCY__NOT_INDEPENDENT_CONFIRMATION`.
6. Registered `EXP-010-DRAW-BRANCH-PRESERVATION` with matched-cost OOS falsification.
7. Registered `EXP-011-CONFIRMATION-PROVENANCE` with calibration/high-confidence-error falsification.
8. Added 2026-08-18 Tue001–004 postmortem and Tue003 Failure Graveyard record.

## Explicitly not implemented

- No automatic draw selection is added to a ticket.
- No S/A1/A2 threshold changes.
- No production probability change.
- No staking change.
- No threshold is frozen from one four-match result set.

## Promotion

`MODEL_PROMOTION_NOT_GRANTED` remains unchanged.
