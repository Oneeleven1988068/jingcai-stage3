#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jingcai Reality-First Research OS v0.1.0-alpha.

Additive research/audit control plane for existing Stage3 SQLite databases.
It does NOT promote Jingcai 3.5 or modify Jingcai 3.4 production logic.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

VERSION = "0.1.0-alpha"
UTC = timezone.utc
EVIDENCE_STATES = {
    "OBSERVED", "DERIVED", "MODEL_OUTPUT", "INFERRED",
    "HYPOTHESIS", "INPUT_INCOMPLETE", "UNVERIFIED"
}


def now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_hash(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def read_json(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError(f"timezone required: {s}")
    return d.astimezone(UTC)


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def schema_text() -> str:
    return (Path(__file__).with_name("research_os_schema.sql")).read_text(encoding="utf-8")


class ResearchOS:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.executescript(schema_text())
        meta = {
            "research_os_version": VERSION,
            "constitution_priority": "ABOVE_ALL_MODEL_VERSIONS",
            "staking_baseline": "Jingcai 3.4",
            "3_5_promotion": "NOT_GRANTED_BY_RESEARCH_OS_INSTALL",
            "created_at_utc": now_utc(),
        }
        for k, v in meta.items():
            self.conn.execute("INSERT OR IGNORE INTO research_meta(key,value) VALUES (?,?)", (k, v))
        self.conn.commit()

    def record_evidence(self, p: Dict[str, Any]) -> str:
        state = p["evidence_state"]
        if state not in EVIDENCE_STATES:
            raise ValueError(f"invalid evidence_state={state}")
        created = now_utc()
        row_payload = {
            "match_key": p.get("match_key"), "match_ref": p.get("match_ref"),
            "field_name": p["field_name"], "evidence_state": state,
            "source_name": p.get("source_name"), "source_entity": p.get("source_entity"),
            "measured_at_utc": p.get("measured_at_utc"), "collected_at_utc": p.get("collected_at_utc"),
            "raw_sha256": p.get("raw_sha256"), "value": p.get("value"),
            "provenance": p.get("provenance"), "created_at_utc": created,
        }
        eid = p.get("evidence_id") or make_id("ev")
        self.conn.execute(
            """INSERT INTO research_evidence(
               evidence_id,match_key,match_ref,field_name,evidence_state,source_name,source_entity,
               measured_at_utc,collected_at_utc,raw_sha256,value_json,provenance_json,created_at_utc,row_sha256)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, p.get("match_key"), p.get("match_ref"), p["field_name"], state,
             p.get("source_name"), p.get("source_entity"), p.get("measured_at_utc"),
             p.get("collected_at_utc"), p.get("raw_sha256"), canonical_json(p.get("value")),
             canonical_json(p.get("provenance")) if p.get("provenance") is not None else None,
             created, payload_hash(row_payload))
        )
        self.conn.commit()
        return eid

    def freeze_prediction(self, p: Dict[str, Any]) -> str:
        pred_ts = parse_dt(p["prediction_timestamp_utc"])
        kickoff = parse_dt(p["kickoff_utc"])
        if pred_ts >= kickoff:
            raise ValueError("prediction must be frozen before kickoff")
        probs = p.get("had_probabilities")
        if probs is not None:
            vals = [float(probs.get(k, 0.0)) for k in ("H", "D", "A")]
            if any(x < 0 or x > 1 for x in vals) or abs(sum(vals) - 1.0) > 1e-5:
                raise ValueError("H/D/A probabilities must be within [0,1] and sum to 1")
        integrity = p.get("input_integrity_state", "UNVERIFIED")
        if integrity not in {"VERIFIED","INPUT_INCOMPLETE","UNVERIFIED"}:
            raise ValueError("input_integrity_state must be VERIFIED/INPUT_INCOMPLETE/UNVERIFIED")
        pid = p.get("prediction_id") or make_id("pred")
        payload = dict(p)
        payload["prediction_id"] = pid
        h = payload_hash(payload)
        self.conn.execute(
            """INSERT INTO research_prediction_freeze(
            prediction_id,match_key,match_ref,kickoff_utc,prediction_timestamp_utc,model_version,model_role,
            source_snapshot_hash,input_integrity_state,had_probabilities_json,had_pick,hhad_json,ttg_json,crs_json,
            hafu_json,rating,gates_json,staking_json,payload_json,payload_sha256,created_at_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid,p.get("match_key"),p["match_ref"],kickoff.isoformat(),pred_ts.isoformat(),p["model_version"],p["model_role"],
             p.get("source_snapshot_hash"),integrity,canonical_json(probs) if probs else None,p.get("had_pick"),
             canonical_json(p.get("HHAD")) if p.get("HHAD") is not None else None,
             canonical_json(p.get("TTG")) if p.get("TTG") is not None else None,
             canonical_json(p.get("CRS")) if p.get("CRS") is not None else None,
             canonical_json(p.get("HAFU")) if p.get("HAFU") is not None else None,
             p.get("rating"),canonical_json(p.get("gates",[])),canonical_json(p.get("staking")) if p.get("staking") is not None else None,
             canonical_json(payload),h,now_utc())
        )
        self.conn.commit()
        return pid

    def append_outcome(self, p: Dict[str, Any]) -> str:
        gh, ga = p.get("goals_home"), p.get("goals_away")
        result = p.get("result_1x2")
        if result is None and gh is not None and ga is not None:
            result = "H" if gh > ga else "A" if gh < ga else "D"
        if result not in {"H","D","A"}:
            raise ValueError("outcome requires result_1x2 H/D/A or goals_home/goals_away")
        oid = p.get("outcome_event_id") or make_id("out")
        payload = dict(p); payload["result_1x2"] = result; payload["outcome_event_id"] = oid
        self.conn.execute(
            """INSERT INTO research_outcome_event(outcome_event_id,match_key,match_ref,known_at_utc,recorded_at_utc,
            source_name,goals_home,goals_away,result_1x2,payload_json,payload_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (oid,p.get("match_key"),p["match_ref"],p.get("known_at_utc"),now_utc(),p.get("source_name"),gh,ga,result,
             canonical_json(payload),payload_hash(payload))
        )
        self.conn.commit(); return oid

    def register_experiment(self, p: Dict[str, Any]) -> str:
        required = ["experiment_name","module_name","module_version","hypothesis","baseline_id",
                    "primary_metrics","success_criteria","failure_criteria","falsification_contract"]
        for k in required:
            if k not in p: raise ValueError(f"missing experiment field: {k}")
        eid = p.get("experiment_id") or make_id("exp")
        prereg = p.get("preregistered_at_utc") or now_utc()
        payload = dict(p); payload["experiment_id"] = eid; payload["preregistered_at_utc"] = prereg
        self.conn.execute(
            """INSERT INTO research_experiment(experiment_id,experiment_name,module_name,module_version,hypothesis,baseline_id,
            data_cutoff_utc,train_window_json,oos_window_json,primary_metrics_json,success_criteria_json,failure_criteria_json,
            falsification_contract_json,search_space_json,attempted_config_index,preregistered_at_utc,payload_json,payload_sha256)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid,p["experiment_name"],p["module_name"],p["module_version"],p["hypothesis"],p["baseline_id"],
             p.get("data_cutoff_utc"),canonical_json(p.get("train_window")) if p.get("train_window") is not None else None,
             canonical_json(p.get("oos_window")) if p.get("oos_window") is not None else None,
             canonical_json(p["primary_metrics"]),canonical_json(p["success_criteria"]),canonical_json(p["failure_criteria"]),
             canonical_json(p["falsification_contract"]),canonical_json(p.get("search_space")) if p.get("search_space") is not None else None,
             int(p.get("attempted_config_index",1)),prereg,canonical_json(payload),payload_hash(payload))
        )
        ev = {"experiment_id":eid,"event_type":"REGISTERED","event_at_utc":prereg,"note":"pre-registered experiment"}
        self._experiment_event(ev)
        self.conn.commit(); return eid

    def _experiment_event(self, p: Dict[str, Any]) -> str:
        event_id = p.get("event_id") or make_id("expev")
        base = {"experiment_id":p["experiment_id"],"event_type":p["event_type"],
                "event_at_utc":p.get("event_at_utc") or now_utc(),"evidence":p.get("evidence"),"note":p.get("note")}
        self.conn.execute(
            "INSERT INTO research_experiment_event(event_id,experiment_id,event_type,event_at_utc,evidence_json,note,payload_sha256) VALUES (?,?,?,?,?,?,?)",
            (event_id,base["experiment_id"],base["event_type"],base["event_at_utc"],
             canonical_json(base["evidence"]) if base["evidence"] is not None else None,base["note"],payload_hash(base))
        )
        return event_id

    def experiment_event(self, p: Dict[str, Any]) -> str:
        eid = self._experiment_event(p); self.conn.commit(); return eid

    def record_failure(self, p: Dict[str, Any]) -> str:
        fid = p.get("failure_id") or make_id("fail")
        payload = dict(p); payload["failure_id"] = fid
        self.conn.execute(
            """INSERT INTO research_failure(failure_id,prediction_id,experiment_id,match_ref,severity,failure_type,detected_at_utc,
            input_truth_check,time_leakage_check,promotion_layer,missed_gate,root_cause_class,historical_recurrence,
            candidate_prevention_rule,oos_generalization_required,narrative_json,payload_sha256)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fid,p.get("prediction_id"),p.get("experiment_id"),p.get("match_ref"),p.get("severity","MAJOR"),p["failure_type"],
             p.get("detected_at_utc") or now_utc(),p.get("input_truth_check"),p.get("time_leakage_check"),p.get("promotion_layer"),
             p.get("missed_gate"),p.get("root_cause_class"),p.get("historical_recurrence"),p.get("candidate_prevention_rule"),
             int(bool(p.get("oos_generalization_required",True))),canonical_json(payload),payload_hash(payload))
        )
        self.conn.commit(); return fid

    def promotion_event(self, p: Dict[str, Any]) -> str:
        if p.get("to_state") == "PRODUCTION" and not p.get("explicit_human_approval", False):
            raise ValueError("PRODUCTION promotion requires explicit_human_approval=true")
        peid = p.get("promotion_event_id") or make_id("prom")
        payload = dict(p); payload["promotion_event_id"] = peid
        self.conn.execute(
            """INSERT INTO research_promotion_event(promotion_event_id,component_name,component_version,from_state,to_state,
            experiment_id,decision_at_utc,decision_basis_json,approver,payload_sha256) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (peid,p["component_name"],p["component_version"],p["from_state"],p["to_state"],p.get("experiment_id"),
             p.get("decision_at_utc") or now_utc(),canonical_json(p.get("decision_basis",{})),p.get("approver","USER_REQUIRED"),payload_hash(payload))
        )
        self.conn.commit(); return peid

    def provenance_edge(self, p: Dict[str, Any]) -> str:
        eid = p.get("edge_id") or make_id("edge")
        base = {"from_kind":p["from_kind"],"from_ref":p["from_ref"],"relation":p["relation"],
                "to_kind":p["to_kind"],"to_ref":p["to_ref"],"metadata":p.get("metadata"),"created_at_utc":now_utc()}
        self.conn.execute(
            "INSERT INTO research_provenance_edge(edge_id,from_kind,from_ref,relation,to_kind,to_ref,created_at_utc,metadata_json,edge_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
            (eid,base["from_kind"],base["from_ref"],base["relation"],base["to_kind"],base["to_ref"],base["created_at_utc"],
             canonical_json(base["metadata"]) if base["metadata"] is not None else None,payload_hash(base))
        )
        self.conn.commit(); return eid

    def audit(self) -> Dict[str, Any]:
        tables = [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'research_%' ORDER BY name")]
        count = lambda t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        predictions = count("research_prediction_freeze") if "research_prediction_freeze" in tables else 0
        unverified = self.conn.execute("SELECT COUNT(*) FROM research_prediction_freeze WHERE input_integrity_state!='VERIFIED'").fetchone()[0] if predictions else 0
        post_kick = self.conn.execute("SELECT COUNT(*) FROM research_prediction_freeze WHERE prediction_timestamp_utc>=kickoff_utc").fetchone()[0] if predictions else 0
        states = {r[0]:r[1] for r in self.conn.execute("SELECT evidence_state,COUNT(*) FROM research_evidence GROUP BY evidence_state")} if "research_evidence" in tables else {}
        return {
            "research_os_version": VERSION,
            "db": self.db_path,
            "tables": {t: count(t) for t in tables},
            "prediction_integrity": {"total":predictions,"unverified_or_incomplete":unverified,"post_kickoff":post_kick},
            "evidence_states": states,
            "production_boundary": "Jingcai 3.4 remains staking baseline; Research OS install grants no 3.5 promotion",
            "audit_status": "PASS" if post_kick == 0 else "FAIL"
        }

    def score(self, model_version: Optional[str] = None) -> Dict[str, Any]:
        where = "WHERE p.had_probabilities_json IS NOT NULL"
        params: list[Any] = []
        if model_version:
            where += " AND p.model_version=?"; params.append(model_version)
        sql = f"""
        SELECT p.prediction_id,p.model_version,p.rating,p.had_pick,p.had_probabilities_json,
               o.result_1x2
        FROM research_prediction_freeze p
        JOIN research_outcome_event o ON o.outcome_event_id=(
          SELECT o2.outcome_event_id FROM research_outcome_event o2
          WHERE (p.match_key IS NOT NULL AND o2.match_key=p.match_key) OR (o2.match_ref=p.match_ref)
          ORDER BY o2.recorded_at_utc DESC LIMIT 1
        )
        {where}
        """
        rows = self.conn.execute(sql, params).fetchall()
        briers=[]; logloss=[]; hits=[]; s_hits=[]; a1_hits=[]
        for r in rows:
            probs=json.loads(r["had_probabilities_json"]); y=r["result_1x2"]
            pvec=[float(probs[k]) for k in ("H","D","A")]
            yvec=[1.0 if k==y else 0.0 for k in ("H","D","A")]
            briers.append(sum((a-b)**2 for a,b in zip(pvec,yvec)))
            py=max(float(probs[y]),1e-15); logloss.append(-math.log(py))
            pick=r["had_pick"] or max(("H","D","A"), key=lambda k: float(probs[k]))
            hit=1 if pick==y else 0; hits.append(hit)
            if r["rating"]=="S": s_hits.append(hit)
            if r["rating"]=="A1": a1_hits.append(hit)
        def mean(xs): return sum(xs)/len(xs) if xs else None
        out={"n":len(rows),"brier_3way":mean(briers),"logloss":mean(logloss),"had_hit_rate":mean(hits),
             "S_n":len(s_hits),"S_hit_rate":mean(s_hits),"S_error_rate":None if not s_hits else 1-mean(s_hits),
             "A1_n":len(a1_hits),"A1_hit_rate":mean(a1_hits),"A1_error_rate":None if not a1_hits else 1-mean(a1_hits)}
        return out


def write_json_stdout(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def self_test() -> Dict[str, Any]:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db=Path(td)/"test.db"; ro=ResearchOS(db); ro.init()
        exp=ro.register_experiment({
            "experiment_name":"synthetic falsification test","module_name":"SYNTHETIC_TEST_ONLY","module_version":"0",
            "hypothesis":"test registry works","baseline_id":"none","primary_metrics":["Brier"],
            "success_criteria":{"schema":"works"},"failure_criteria":{"schema":"fails"},
            "falsification_contract":{"reject_if":"insert or immutability fails"},"attempted_config_index":1
        })
        pred=ro.freeze_prediction({
            "match_ref":"SYNTHETIC_MATCH","kickoff_utc":"2026-08-18T12:00:00+00:00",
            "prediction_timestamp_utc":"2026-08-18T11:00:00+00:00","model_version":"SYNTHETIC_TEST_ONLY",
            "model_role":"RESEARCH","input_integrity_state":"VERIFIED","source_snapshot_hash":"synthetic",
            "had_probabilities":{"H":0.5,"D":0.3,"A":0.2},"had_pick":"H","rating":"A1","gates":[]
        })
        ro.append_outcome({"match_ref":"SYNTHETIC_MATCH","goals_home":1,"goals_away":0,"source_name":"SYNTHETIC_TEST_ONLY"})
        ro.record_evidence({"match_ref":"SYNTHETIC_MATCH","field_name":"synthetic_field","evidence_state":"OBSERVED","source_name":"SYNTHETIC_TEST_ONLY","value":1})
        score=ro.score("SYNTHETIC_TEST_ONLY")
        immutable=False
        try:
            ro.conn.execute("UPDATE research_prediction_freeze SET rating='S' WHERE prediction_id=?",(pred,)); ro.conn.commit()
        except sqlite3.DatabaseError:
            immutable=True; ro.conn.rollback()
        audit=ro.audit(); ro.close()
        ok=immutable and audit["audit_status"]=="PASS" and score["n"]==1 and score["had_hit_rate"]==1.0
        return {"self_test":"PASS" if ok else "FAIL","append_only_enforced":immutable,"score":score,"audit":audit,"experiment_id":exp}


def main(argv: Optional[Iterable[str]]=None) -> int:
    ap=argparse.ArgumentParser(description="Jingcai Reality-First Research OS")
    sub=ap.add_subparsers(dest="cmd",required=True)
    sub.add_parser("self-test")
    p=sub.add_parser("init"); p.add_argument("--db",required=True)
    for name in ["record-evidence","freeze-prediction","append-outcome","register-experiment","experiment-event","record-failure","promotion-event","provenance-edge"]:
        p=sub.add_parser(name); p.add_argument("--db",required=True); p.add_argument("--input",required=True)
    p=sub.add_parser("audit"); p.add_argument("--db",required=True)
    p=sub.add_parser("score"); p.add_argument("--db",required=True); p.add_argument("--model-version")
    args=ap.parse_args(list(argv) if argv is not None else None)
    if args.cmd=="self-test": write_json_stdout(self_test()); return 0
    ro=ResearchOS(args.db); ro.init()
    try:
        if args.cmd=="init": out={"status":"OK","db":args.db,"research_os_version":VERSION}
        elif args.cmd=="record-evidence": out={"evidence_id":ro.record_evidence(read_json(args.input))}
        elif args.cmd=="freeze-prediction": out={"prediction_id":ro.freeze_prediction(read_json(args.input))}
        elif args.cmd=="append-outcome": out={"outcome_event_id":ro.append_outcome(read_json(args.input))}
        elif args.cmd=="register-experiment": out={"experiment_id":ro.register_experiment(read_json(args.input))}
        elif args.cmd=="experiment-event": out={"event_id":ro.experiment_event(read_json(args.input))}
        elif args.cmd=="record-failure": out={"failure_id":ro.record_failure(read_json(args.input))}
        elif args.cmd=="promotion-event": out={"promotion_event_id":ro.promotion_event(read_json(args.input))}
        elif args.cmd=="provenance-edge": out={"edge_id":ro.provenance_edge(read_json(args.input))}
        elif args.cmd=="audit": out=ro.audit()
        elif args.cmd=="score": out=ro.score(args.model_version)
        else: raise RuntimeError("unknown command")
        write_json_stdout(out); return 0
    finally:
        ro.close()

if __name__=="__main__":
    raise SystemExit(main())
