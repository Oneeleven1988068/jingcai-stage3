PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS research_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_evidence (
    evidence_id TEXT PRIMARY KEY,
    match_key INTEGER,
    match_ref TEXT,
    field_name TEXT NOT NULL,
    evidence_state TEXT NOT NULL CHECK(evidence_state IN (
        'OBSERVED','DERIVED','MODEL_OUTPUT','INFERRED','HYPOTHESIS','INPUT_INCOMPLETE','UNVERIFIED'
    )),
    source_name TEXT,
    source_entity TEXT,
    measured_at_utc TEXT,
    collected_at_utc TEXT,
    raw_sha256 TEXT,
    value_json TEXT NOT NULL,
    provenance_json TEXT,
    created_at_utc TEXT NOT NULL,
    row_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_research_evidence_match ON research_evidence(match_key, match_ref);
CREATE INDEX IF NOT EXISTS ix_research_evidence_state ON research_evidence(evidence_state);

CREATE TABLE IF NOT EXISTS research_prediction_freeze (
    prediction_id TEXT PRIMARY KEY,
    match_key INTEGER,
    match_ref TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    prediction_timestamp_utc TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_role TEXT NOT NULL CHECK(model_role IN ('PRODUCTION','SHADOW','RESEARCH')),
    source_snapshot_hash TEXT,
    input_integrity_state TEXT NOT NULL,
    had_probabilities_json TEXT,
    had_pick TEXT,
    hhad_json TEXT,
    ttg_json TEXT,
    crs_json TEXT,
    hafu_json TEXT,
    rating TEXT,
    gates_json TEXT,
    staking_json TEXT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    CHECK(prediction_timestamp_utc < kickoff_utc)
);
CREATE INDEX IF NOT EXISTS ix_prediction_match ON research_prediction_freeze(match_key, match_ref);
CREATE INDEX IF NOT EXISTS ix_prediction_time ON research_prediction_freeze(prediction_timestamp_utc);
CREATE INDEX IF NOT EXISTS ix_prediction_model ON research_prediction_freeze(model_version, model_role);

CREATE TABLE IF NOT EXISTS research_outcome_event (
    outcome_event_id TEXT PRIMARY KEY,
    match_key INTEGER,
    match_ref TEXT NOT NULL,
    known_at_utc TEXT,
    recorded_at_utc TEXT NOT NULL,
    source_name TEXT,
    goals_home INTEGER,
    goals_away INTEGER,
    result_1x2 TEXT CHECK(result_1x2 IN ('H','D','A')),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outcome_match ON research_outcome_event(match_key, match_ref, recorded_at_utc);

CREATE TABLE IF NOT EXISTS research_experiment (
    experiment_id TEXT PRIMARY KEY,
    experiment_name TEXT NOT NULL,
    module_name TEXT NOT NULL,
    module_version TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    baseline_id TEXT NOT NULL,
    data_cutoff_utc TEXT,
    train_window_json TEXT,
    oos_window_json TEXT,
    primary_metrics_json TEXT NOT NULL,
    success_criteria_json TEXT NOT NULL,
    failure_criteria_json TEXT NOT NULL,
    falsification_contract_json TEXT NOT NULL,
    search_space_json TEXT,
    attempted_config_index INTEGER NOT NULL DEFAULT 1,
    preregistered_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_experiment_module ON research_experiment(module_name, module_version);

CREATE TABLE IF NOT EXISTS research_experiment_event (
    event_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES research_experiment(experiment_id),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'REGISTERED','STARTED','METRIC_SNAPSHOT','PROMOTION_REVIEW','PROMOTED','DEMOTED','REJECTED','RETIRED','NOTE'
    )),
    event_at_utc TEXT NOT NULL,
    evidence_json TEXT,
    note TEXT,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_experiment_event ON research_experiment_event(experiment_id, event_at_utc);

CREATE TABLE IF NOT EXISTS research_failure (
    failure_id TEXT PRIMARY KEY,
    prediction_id TEXT,
    experiment_id TEXT,
    match_ref TEXT,
    severity TEXT NOT NULL CHECK(severity IN ('INFO','MINOR','MAJOR','CRITICAL')),
    failure_type TEXT NOT NULL,
    detected_at_utc TEXT NOT NULL,
    input_truth_check TEXT,
    time_leakage_check TEXT,
    promotion_layer TEXT,
    missed_gate TEXT,
    root_cause_class TEXT,
    historical_recurrence TEXT,
    candidate_prevention_rule TEXT,
    oos_generalization_required INTEGER NOT NULL DEFAULT 1,
    narrative_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_failure_prediction ON research_failure(prediction_id);
CREATE INDEX IF NOT EXISTS ix_failure_type ON research_failure(failure_type, severity);

CREATE TABLE IF NOT EXISTS research_promotion_event (
    promotion_event_id TEXT PRIMARY KEY,
    component_name TEXT NOT NULL,
    component_version TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL CHECK(to_state IN ('SHADOW','PROMOTION_REVIEW','PRODUCTION','REJECTED','RETIRED')),
    experiment_id TEXT,
    decision_at_utc TEXT NOT NULL,
    decision_basis_json TEXT NOT NULL,
    approver TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_promotion_component ON research_promotion_event(component_name, component_version, decision_at_utc);

CREATE TABLE IF NOT EXISTS research_provenance_edge (
    edge_id TEXT PRIMARY KEY,
    from_kind TEXT NOT NULL,
    from_ref TEXT NOT NULL,
    relation TEXT NOT NULL,
    to_kind TEXT NOT NULL,
    to_ref TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    metadata_json TEXT,
    edge_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_provenance_from ON research_provenance_edge(from_kind, from_ref);
CREATE INDEX IF NOT EXISTS ix_provenance_to ON research_provenance_edge(to_kind, to_ref);

CREATE TABLE IF NOT EXISTS research_metric_snapshot (
    metric_snapshot_id TEXT PRIMARY KEY,
    experiment_id TEXT,
    model_version TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    sample_start_utc TEXT,
    sample_end_utc TEXT,
    n_predictions INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    comparison_json TEXT,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_metric_model ON research_metric_snapshot(model_version, evaluated_at_utc);

-- Append-only protection. Research tables are immutable by design.
CREATE TRIGGER IF NOT EXISTS trg_no_update_research_evidence BEFORE UPDATE ON research_evidence BEGIN SELECT RAISE(ABORT,'append-only: research_evidence'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_research_evidence BEFORE DELETE ON research_evidence BEGIN SELECT RAISE(ABORT,'append-only: research_evidence'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_update_prediction BEFORE UPDATE ON research_prediction_freeze BEGIN SELECT RAISE(ABORT,'append-only: research_prediction_freeze'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_prediction BEFORE DELETE ON research_prediction_freeze BEGIN SELECT RAISE(ABORT,'append-only: research_prediction_freeze'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_update_outcome BEFORE UPDATE ON research_outcome_event BEGIN SELECT RAISE(ABORT,'append-only: research_outcome_event'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_outcome BEFORE DELETE ON research_outcome_event BEGIN SELECT RAISE(ABORT,'append-only: research_outcome_event'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_update_experiment BEFORE UPDATE ON research_experiment BEGIN SELECT RAISE(ABORT,'append-only: research_experiment'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_experiment BEFORE DELETE ON research_experiment BEGIN SELECT RAISE(ABORT,'append-only: research_experiment'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_update_failure BEFORE UPDATE ON research_failure BEGIN SELECT RAISE(ABORT,'append-only: research_failure'); END;
CREATE TRIGGER IF NOT EXISTS trg_no_delete_failure BEFORE DELETE ON research_failure BEGIN SELECT RAISE(ABORT,'append-only: research_failure'); END;
