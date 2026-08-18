# Jingcai Reality-First Research OS v0.1.4-alpha
## CRS Two-Sided Distribution Presentation Patch

Governance: `RESEARCH_OS_ACTIVE__MODEL_PROMOTION_NOT_GRANTED`

- Production/staking baseline: **Jingcai 3.4**
- Research/shadow candidate: **Jingcai 3.5 Alpha**
- Production probability change: **FALSE**
- Staking change: **FALSE**

v0.1.4 is a presentation/compression repair on top of v0.1.3 and v0.1.2. It prevents a full CRS distribution from being re-collapsed into a misleading Top3-only user-facing view.

Mandatory CRS output now includes center scores, left-tail scores, right-tail scores, TTG band/tail mass, and display coverage audit. It does not force larger scores and does not claim predictive improvement.

Executable shadow module:
`shadow_modules/crs_two_sided_presentation_shadow_v0_1.py`

Current T0 example review:
`reviews/20260818_T0_001_004_CRS_two_sided_presentation.json`
