# Final Compression / Bet Construction Gate Policy

State: **SHADOW ONLY**. Production baseline remains Jingcai 3.4. Model promotion is not granted.

## Why this gate exists

The full-distribution patch repaired information loss upstream. That does not guarantee the final betting layer keeps the information. A second failure mode can occur when the final ticket compresses a broad probability state into a narrow selection and silently deletes a branch that survived HAD/HHAD/TTG/CRS analysis.

## Mandatory audit order

1. Read full HAD result distribution.
2. Interpret HHAD only as margin/result decomposition for its actual handicap line.
3. Preserve full TTG and CRS state maps.
4. Inspect the proposed final betting selection.
5. Report which HAD branches and CRS states are retained vs dropped.
6. Type-check any word such as “保护 / hedge / cover” against the actual event space.
7. Only then permit a final presentation/ticket compression.

## Deterministic semantic rule

For a home -1 three-way Sporttery handicap:

- HHAD H = home wins by 2+
- HHAD D = home wins by exactly 1
- HHAD A = ordinary draw or away win

Therefore **HHAD H + D covers only ordinary HAD home-win states. It does not protect an ordinary draw.** Calling H+D “防平” is a `BET_CONSTRUCTION_SEMANTIC_ERROR`.

## Shadow-only conflict rule

A proposed single-result HAD action is flagged for review when an alternate HAD-result score is already present in CRS Top-5. This is a candidate rule for OOS falsification, not a frozen production threshold. HHAD directional conflict can add a second review flag.

## No automatic action

This gate does not automatically add selections, change S/A ratings, increase stake, or reverse direction. Any predictive promotion must pass frozen OOS/walk-forward tests at matched ticket cost.
