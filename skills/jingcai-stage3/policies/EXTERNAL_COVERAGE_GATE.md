# External Coverage Gate

For any live target set, external capture is not PASS merely because an ExternalOdds file exists.

PASS requires every target match to be matched to the correct external event or explicitly labeled missing.

Required output:
- target_match_id / label
- external_event_id
- event time difference
- team mapping confidence
- external source
- bookmaker count
- latest quote timestamp
- status: MATCHED / AMBIGUOUS / MISSING

If one or more target matches are MISSING:
- `EXTERNAL_COVERAGE_GATE = FAIL`
- trigger browser/web fallback if available
- keep price confirmation as `INPUT_INCOMPLETE` until a valid target price source is found
- context reports may be used only as `EXTERNAL_CONTEXT_CONFIRM`, not as Sharp price consensus
