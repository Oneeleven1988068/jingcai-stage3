# Jingcai Reality-First Research OS v0.1.1-alpha — Match 001 Patch

Production boundary is unchanged: Jingcai 3.4 remains the staking baseline; Jingcai 3.5 Alpha remains shadow/research.

## Production-safe governance clarifications activated now
- `SPORTTERY_SALES_CUTOFF_GATE`: Monday-Friday 22:00, weekends 23:00.
- Close provenance separates `last_official_update_time` from `effective_through_sales_close`.
- A user-confirmed unchanged quote through cutoff may be recorded as `USER_OBSERVED_UNCHANGED_TO_CUTOFF`, but must not be mislabeled as an independently archived official 22:00 snapshot.
- Cross-source confirmation must carry each source timestamp. Do not call movements "simultaneous" without timestamp evidence.
- Three-Way Directional Reversal remains a confidence/risk gate; it never automatically instructs a bet on the moved side.
- In-play shocks must not be used to retrofit pre-match weights.

## Shadow-only experiments
1. HHAD Margin Decomposition.
2. TTG Tail-Mass Diagnostic.
3. Reversal Persistence.
4. Asynchronous Market Time Alignment.

No thresholds or production weights are frozen from match 001.
