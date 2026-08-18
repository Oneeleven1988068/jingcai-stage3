# CRS Two-Sided Distribution Presentation Policy

The final CRS view must not collapse to `Top3 exact scores` alone.

Mandatory display:

- **CENTER**: representative exact scores with total goals 2–3.
- **LEFT TAIL**: representative exact scores with total goals 0–1.
- **RIGHT TAIL**: representative exact scores with total goals 4+.
- **EXTREME RIGHT**: representative exact scores with total goals 5+.
- **TTG MASS**: P(0–1), P(2–3), P(4+), P(5+), P(6+), P(7+).
- **COVERAGE AUDIT**: unique displayed exact-score mass / exact CRS market mass.
- **OTHER BUCKETS**: retain OTHER_H / OTHER_D / OTHER_A separately; do not invent their total or margin.

The purpose is representation fidelity, not to force high-score picks. A match may genuinely be low-scoring, but the output must make that conclusion visible through distribution mass rather than through an incomplete Top-N shortlist.

No automatic rating/staking change is allowed from this presentation layer.
