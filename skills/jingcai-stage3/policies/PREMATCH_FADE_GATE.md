# Prematch Fade Gate v1

Status: **SHADOW / 3.5 only**. Does not promote into 3.4 staking by itself.

## Job
Sporttery prints who is strong. This gate asks whether that print is wet.

1. Board layer: HAD favorite + HHAD max bucket.
2. Off-board layer: Europe clock, spine absences, table inversion, H2H trap, fixture identity, external overheat.
3. Verdict against the printed favorite:
   - `LONG` follow favorite
   - `FLAT` drop from long parlay
   - `SHORT` fade favorite

## Short construction
Default SHORT selection is `FAVORITE_NOT_WIN`.
`UNDERDOG_WIN` only when score >= 6 and table-inversion or dog-proven is present.
Never mix SHORT legs with LONG legs on the same ticket.

## Evidence
Facts must carry source, captured_at, and OBSERVED|DERIVED.
UNVERIFIED facts cannot create SHORT.

## Regression
`python prematch_fade_gate_v1.py` must keep Friday 011 and 012 off LONG
when the pre-match facts known on 2026-09-04 are supplied.
