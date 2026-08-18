# v0.1.4-alpha — CRS Two-Sided Distribution Presentation Patch

Status: `RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED`

This patch changes **presentation/compression semantics only**. It does not change Jingcai 3.4 production probabilities, staking, or rating thresholds.

## Implemented

1. Top3/Top5 exact-score lists may no longer be presented as the complete CRS view.
2. Mandatory CRS presentation becomes:
   - center exact scores (total 2–3),
   - left-tail exact scores (total 0–1),
   - right-tail exact scores (total 4+),
   - extreme-right representatives (total 5+),
   - TTG band/tail mass,
   - selected exact-score display coverage,
   - CRS OTHER_H/D/A mass shown separately.
3. TTG remains full 0–7+ with `retained_probability_mass=1.0` when the upstream v0.1.2 module provides it.
4. Exact-score display mass is explicitly **not** a joint probability and must not be multiplied by HAD/HHAD/TTG.
5. OTHER_H/D/A reveal result direction only. No exact total/margin is fabricated.
6. New shadow experiment `EXP-008-CRS-TWO-SIDED-PRESENTATION`.

## Interpretation

A low-score center may still be mathematically correct at the single-cell level. The repair is to prevent the user-facing or ticket-facing layer from hiding meaningful left/right aggregate mass behind a small Top-N list.

`MODEL_PROMOTION_NOT_GRANTED` remains unchanged.
