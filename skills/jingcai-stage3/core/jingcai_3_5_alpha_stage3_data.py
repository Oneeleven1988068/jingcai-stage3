#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jingcai 3.5 Alpha — Stage 3 Data Layer
======================================

Governance purpose
------------------
This module builds the timestamped research dataset required after Stage 2:
  * Sporttery HAD + HHAD + CRS + TTG + HAFU in the SAME source snapshot.
  * Global multi-bookmaker odds captured in the same collection batch.
  * Append-only/auditable SQLite storage plus raw JSON archives.
  * Leakage-safe pre-kickoff anchor / closing snapshot exports.

It does NOT promote Jingcai 3.5 to staking production, does NOT activate
external positive fusion, residual ML, dynamic xG/team strength, or score
ensemble weights. Jingcai 3.4 remains the staking baseline until downstream
OOS EV/CLV/ROI/max-drawdown validation passes.

Python: 3.10+
External dependencies: none.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

VERSION = "3.5 Alpha Stage3 Data Layer 0.1"
GOVERNANCE_STATUS = "DATA_LAYER_ACTIVE__MODEL_PROMOTION_NOT_GRANTED"
SPORTTERY_POOLS = ("had", "hhad", "crs", "ttg", "hafu")
SPORTTERY_ENDPOINTS = (
    "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry",
    "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry",
)
THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SH_TZ = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def safe_float(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) and x > 0 else None
    except Exception:
        return None


def safe_number(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def iso_to_dt(s: Optional[str], default_tz: timezone | ZoneInfo = UTC) -> Optional[datetime]:
    if not s:
        return None
    text = str(s).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    candidates = [text]
    # Common source formats without timezone.
    if "T" not in text and " " in text:
        candidates.append(text.replace(" ", "T", 1))
    fmts = (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y%m%d%H%M%S",
    )
    for c in candidates:
        try:
            d = datetime.fromisoformat(c)
            if d.tzinfo is None:
                d = d.replace(tzinfo=default_tz)
            return d.astimezone(UTC)
        except Exception:
            pass
    for fmt in fmts:
        try:
            d = datetime.strptime(text, fmt).replace(tzinfo=default_tz)
            return d.astimezone(UTC)
        except Exception:
            pass
    return None


def normalize_team_name(s: str) -> str:
    s = (s or "").strip().casefold()
    s = re.sub(r"[\s\-_.·'’`]+", "", s)
    s = re.sub(r"\b(fc|cf|sc|afc|fk|bk|if|ac|sv|sk|cd|ca|club|football|soccer)\b", "", s)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]", "", s)


def devig_three(odds: Sequence[float]) -> Optional[Tuple[float, float, float]]:
    vals = [safe_float(x) for x in odds]
    if any(x is None for x in vals):
        return None
    inv = [1.0 / float(x) for x in vals]
    z = sum(inv)
    return tuple(x / z for x in inv) if z > 0 else None


def robust_external_consensus(rows: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    probs = []
    for r in rows:
        if str(r.get("market_key")) != "h2h":
            continue
        if not all(r.get(k) is not None for k in ("home", "draw", "away")):
            continue
        p = devig_three((r["home"], r["draw"], r["away"]))
        if p:
            probs.append((str(r.get("bookmaker") or r.get("bookmaker_key") or "unknown"), p))
    if not probs:
        return None
    cols = list(zip(*(p for _, p in probs)))
    med = [statistics.median(c) for c in cols]
    z = sum(med)
    med = [x / z for x in med]
    mad = []
    for i, c in enumerate(cols):
        mad.append(statistics.median([abs(x - med[i]) for x in c]))
    return {
        "n_books": len(probs),
        "prob": {"H": med[0], "D": med[1], "A": med[2]},
        "mad": {"H": mad[0], "D": mad[1], "A": mad[2]},
        "books": [x[0] for x in probs],
    }


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Sporttery parsing / live fetch
# ---------------------------------------------------------------------------

def sporttery_score_key(code: str) -> Optional[str]:
    m = re.match(r"^s(\d{2})s(\d{2})$", str(code))
    if m:
        return f"{int(m.group(1))}-{int(m.group(2))}"
    return {"s1sh": "H_OTHER", "s1sd": "D_OTHER", "s1sa": "A_OTHER"}.get(str(code))


def sporttery_total_key(code: str) -> Optional[str]:
    m = re.match(r"^s(\d)$", str(code))
    if not m:
        return None
    n = int(m.group(1))
    return "7+" if n >= 7 else str(n)


def parse_sporttery_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    groups = ((payload or {}).get("value") or {}).get("matchInfoList") or []
    updated = ((payload or {}).get("value") or {}).get("lastUpdateTime")
    out: List[Dict[str, Any]] = []
    for group in groups:
        for sm in group.get("subMatchList") or []:
            pool_raw = {p: sm.get(p) if isinstance(sm.get(p), dict) else None for p in SPORTTERY_POOLS}
            had = pool_raw["had"]
            if not had:
                continue
            ho, do, ao = safe_float(had.get("h")), safe_float(had.get("d")), safe_float(had.get("a"))
            if not all(x is not None for x in (ho, do, ao)):
                continue

            match_date = str(sm.get("matchDate") or "")
            match_time = str(sm.get("matchTime") or "")
            kickoff_utc = parse_kickoff_utc(match_date, match_time)
            rec: Dict[str, Any] = {
                "match_id": str(sm.get("matchNumStr") or sm.get("matchId") or ""),
                "upstream_match_id": sm.get("matchId"),
                "match_num": sm.get("matchNum"),
                "match_num_date": str(sm.get("matchNumDate") or group.get("matchNumDate") or ""),
                "business_date": str(sm.get("businessDate") or group.get("businessDate") or ""),
                "match_date": match_date,
                "match_time": match_time,
                "kickoff_utc": kickoff_utc,
                "league": str(sm.get("leagueAbbName") or sm.get("leagueAllName") or ""),
                "league_id": sm.get("leagueId"),
                "home_team": str(sm.get("homeTeamAbbName") or sm.get("homeTeamAllName") or ""),
                "away_team": str(sm.get("awayTeamAbbName") or sm.get("awayTeamAllName") or ""),
                "home_team_id": sm.get("homeTeamId"),
                "away_team_id": sm.get("awayTeamId"),
                "had": {"H": ho, "D": do, "A": ao},
                "source_updated_at": updated,
                "pool_raw": pool_raw,
            }

            hhad = pool_raw["hhad"]
            if hhad:
                line_raw = hhad.get("goalLineValue") or hhad.get("goalLine")
                line = None
                try:
                    line = float(line_raw) if str(line_raw).strip() else None
                    if line is not None and line.is_integer():
                        line = int(line)
                except Exception:
                    pass
                h = safe_float(hhad.get("h")); d = safe_float(hhad.get("d")); a = safe_float(hhad.get("a"))
                rec["hhad"] = {"line": line, "H": h, "D": d, "A": a}

            ttg = pool_raw["ttg"]
            if ttg:
                vals: Dict[str, float] = {}
                for k, v in ttg.items():
                    kk = sporttery_total_key(k)
                    vv = safe_float(v)
                    if kk is not None and vv is not None:
                        vals[kk] = vv
                if vals:
                    rec["ttg"] = vals

            crs = pool_raw["crs"]
            if crs:
                vals = {}
                for k, v in crs.items():
                    kk = sporttery_score_key(k)
                    vv = safe_float(v)
                    if kk and vv is not None:
                        vals[kk] = vv
                if vals:
                    rec["crs"] = vals

            hafu = pool_raw["hafu"]
            if hafu:
                vals = {}
                for k in ("hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa"):
                    vv = safe_float(hafu.get(k))
                    if vv is not None:
                        vals[k] = vv
                if vals:
                    rec["hafu"] = vals

            available = [p for p in SPORTTERY_POOLS if rec.get(p)]
            rec["pool_presence"] = {p: bool(rec.get(p)) for p in SPORTTERY_POOLS}
            rec["pool_count"] = len(available)
            rec["five_pool_complete"] = len(available) == len(SPORTTERY_POOLS)
            out.append(rec)

    return {
        "source": "sporttery",
        "source_endpoint": payload.get("_jingcai_source_endpoint"),
        "source_updated_at": updated,
        "matches": out,
    }


def parse_kickoff_utc(match_date: str, match_time: str) -> Optional[str]:
    d = (match_date or "").strip()
    t = (match_time or "").strip()
    if not d:
        return None
    # Typical Sporttery dates are local China time; preserve only if parseable.
    text = f"{d} {t}".strip()
    dt = iso_to_dt(text, SH_TZ)
    return dt.replace(microsecond=0).isoformat() if dt else None


class SportteryClient:
    def __init__(self, timeout: float = 10.0, pools: str = "hhad,had,crs,ttg,hafu"):
        self.timeout = float(timeout)
        self.pools = pools

    def fetch_raw(self) -> Dict[str, Any]:
        params = urlencode({"poolCode": self.pools, "channel": "c"})
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1",
            "Referer": "https://m.sporttery.cn/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        last_error: Optional[BaseException] = None
        for endpoint in SPORTTERY_ENDPOINTS:
            try:
                req = Request(endpoint + "?" + params, headers=headers, method="GET")
                with urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read().decode("utf-8"))
                if not isinstance(payload, dict) or not isinstance(payload.get("value"), dict):
                    raise RuntimeError("unexpected Sporttery payload")
                payload["_jingcai_source_endpoint"] = endpoint
                return payload
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
        raise RuntimeError(f"Sporttery fetch failed: {last_error}")


# ---------------------------------------------------------------------------
# External bookmaker parsing / live fetch
# ---------------------------------------------------------------------------

class TheOddsApiClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 12.0):
        self.api_key = api_key or os.getenv("ODDS_API_KEY")
        self.timeout = float(timeout)

    def fetch(self, sport_key: str, regions: str = "eu,uk", markets: str = "h2h,spreads,totals") -> List[Dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("ODDS_API_KEY is not set")
        q = urlencode({
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        })
        req = Request(
            f"{THE_ODDS_API_BASE}/sports/{sport_key}/odds?{q}",
            headers={"Accept": "application/json", "User-Agent": "Jingcai-3.5-Stage3/0.1"},
        )
        with urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        if not isinstance(data, list):
            raise RuntimeError("unexpected The Odds API payload")
        return data


def parse_the_odds_api(payload: Sequence[Mapping[str, Any]], sport_key: str) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    for e in payload:
        home = str(e.get("home_team") or "")
        away = str(e.get("away_team") or "")
        event = {
            "source": "the_odds_api",
            "source_event_id": str(e.get("id") or ""),
            "sport_key": sport_key,
            "commence_time_utc": normalize_utc_iso(e.get("commence_time")),
            "home_team": home,
            "away_team": away,
            "sporttery_match_id": e.get("sporttery_match_id"),
            "bookmakers": [],
        }
        for b in e.get("bookmakers") or []:
            book = {
                "bookmaker_key": str(b.get("key") or ""),
                "bookmaker": str(b.get("title") or b.get("key") or "unknown"),
                "bookmaker_updated_at": normalize_utc_iso(b.get("last_update")),
                "markets": [],
            }
            for m in b.get("markets") or []:
                key = str(m.get("key") or "")
                if key not in ("h2h", "spreads", "totals"):
                    continue
                row = {"market_key": key, "market_updated_at": normalize_utc_iso(m.get("last_update")), "outcomes": []}
                for o in m.get("outcomes") or []:
                    price = safe_float(o.get("price"))
                    if price is None:
                        continue
                    row["outcomes"].append({
                        "name": str(o.get("name") or ""),
                        "price": price,
                        "point": safe_number(o.get("point")),
                    })
                if row["outcomes"]:
                    book["markets"].append(row)
            if book["markets"]:
                event["bookmakers"].append(book)
        events.append(event)
    return {"source": "the_odds_api", "sport_key": sport_key, "events": events}


def parse_normalized_external(payload: Mapping[str, Any] | Sequence[Any], source: str = "external_json") -> Dict[str, Any]:
    """Accept a normalized external payload for sources other than The Odds API.

    Shape:
      {"events":[{
        "source_event_id":"...", "sporttery_match_id":"周一001",
        "commence_time_utc":"...", "home_team":"...", "away_team":"...",
        "bookmakers":[{"bookmaker":"Pinnacle","markets":[
          {"market_key":"h2h","outcomes":[{"name":"Home","price":2.1}, ...]}
        ]}]
      }]}
    """
    events = payload.get("events") if isinstance(payload, Mapping) else payload
    if events is None and isinstance(payload, Mapping):
        events = payload.get("matches")
    if not isinstance(events, list):
        raise ValueError("normalized external payload must contain events/matches list")
    out = []
    for e in events:
        rec = dict(e)
        rec["source"] = str(rec.get("source") or source)
        rec["source_event_id"] = str(rec.get("source_event_id") or rec.get("id") or rec.get("match_id") or "")
        rec["commence_time_utc"] = normalize_utc_iso(rec.get("commence_time_utc") or rec.get("commence_time"))
        rec.setdefault("bookmakers", [])
        out.append(rec)
    return {"source": source, "events": out}


def normalize_utc_iso(v: Any) -> Optional[str]:
    d = iso_to_dt(str(v), UTC) if v else None
    return d.replace(microsecond=0).isoformat() if d else None


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

SCHEMA_SQL = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capture_run (
    run_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source TEXT NOT NULL,
    collected_at_utc TEXT NOT NULL,
    source_updated_at TEXT,
    raw_sha256 TEXT NOT NULL,
    raw_path TEXT,
    source_context_json TEXT,
    status TEXT NOT NULL DEFAULT 'OK',
    note TEXT
);
CREATE INDEX IF NOT EXISTS ix_capture_run_batch ON capture_run(batch_id, source);
CREATE INDEX IF NOT EXISTS ix_capture_run_time ON capture_run(collected_at_utc);

CREATE TABLE IF NOT EXISTS match_dim (
    match_key INTEGER PRIMARY KEY AUTOINCREMENT,
    sporttery_match_id TEXT NOT NULL,
    upstream_match_id TEXT,
    match_num_date TEXT,
    match_num TEXT,
    business_date TEXT,
    kickoff_utc TEXT,
    match_date_raw TEXT,
    match_time_raw TEXT,
    league TEXT,
    league_id TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_team_id TEXT,
    away_team_id TEXT,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    UNIQUE(sporttery_match_id, match_num_date)
);
CREATE INDEX IF NOT EXISTS ix_match_kickoff ON match_dim(kickoff_utc);

CREATE TABLE IF NOT EXISTS sporttery_snapshot (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES capture_run(run_id),
    batch_id TEXT NOT NULL,
    match_key INTEGER NOT NULL REFERENCES match_dim(match_key),
    collected_at_utc TEXT NOT NULL,
    source_updated_at TEXT,
    payload_sha256 TEXT NOT NULL,
    had_json TEXT,
    hhad_json TEXT,
    crs_json TEXT,
    ttg_json TEXT,
    hafu_json TEXT,
    pool_raw_json TEXT,
    pool_count INTEGER NOT NULL,
    five_pool_complete INTEGER NOT NULL,
    UNIQUE(run_id, match_key)
);
CREATE INDEX IF NOT EXISTS ix_st_match_time ON sporttery_snapshot(match_key, collected_at_utc);
CREATE INDEX IF NOT EXISTS ix_st_batch ON sporttery_snapshot(batch_id);

CREATE TABLE IF NOT EXISTS external_event (
    event_key INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    sport_key TEXT,
    commence_time_utc TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    UNIQUE(source, source_event_id)
);
CREATE INDEX IF NOT EXISTS ix_ext_event_time ON external_event(commence_time_utc);

CREATE TABLE IF NOT EXISTS external_event_snapshot (
    event_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES capture_run(run_id),
    batch_id TEXT NOT NULL,
    event_key INTEGER NOT NULL REFERENCES external_event(event_key),
    collected_at_utc TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    bookmaker_count INTEGER NOT NULL,
    UNIQUE(run_id, event_key)
);
CREATE INDEX IF NOT EXISTS ix_ext_snap_event_time ON external_event_snapshot(event_key, collected_at_utc);
CREATE INDEX IF NOT EXISTS ix_ext_snap_batch ON external_event_snapshot(batch_id);

CREATE TABLE IF NOT EXISTS external_market_quote (
    quote_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_snapshot_id INTEGER NOT NULL REFERENCES external_event_snapshot(event_snapshot_id),
    bookmaker_key TEXT,
    bookmaker TEXT NOT NULL,
    bookmaker_updated_at TEXT,
    market_key TEXT NOT NULL,
    market_updated_at TEXT,
    outcome_name TEXT NOT NULL,
    outcome_role TEXT,
    price REAL NOT NULL,
    point REAL
);
CREATE INDEX IF NOT EXISTS ix_quote_snapshot_market ON external_market_quote(event_snapshot_id, market_key, bookmaker);

CREATE TABLE IF NOT EXISTS event_link (
    match_key INTEGER NOT NULL REFERENCES match_dim(match_key),
    event_key INTEGER NOT NULL REFERENCES external_event(event_key),
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at_utc TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(match_key, event_key)
);
CREATE INDEX IF NOT EXISTS ix_event_link_match ON event_link(match_key, active);

CREATE TABLE IF NOT EXISTS team_alias (
    sporttery_team TEXT NOT NULL,
    external_team TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '*',
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(sporttery_team, external_team, source)
);

CREATE TABLE IF NOT EXISTS result (
    match_key INTEGER PRIMARY KEY REFERENCES match_dim(match_key),
    goals_home INTEGER NOT NULL,
    goals_away INTEGER NOT NULL,
    known_at_utc TEXT,
    source TEXT,
    inserted_at_utc TEXT NOT NULL
);
"""


class Stage3Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('stage3_version',?)", (VERSION,))
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('governance_status',?)", (GOVERNANCE_STATUS,))
        self.conn.commit()

    def insert_run(self, *, batch_id: str, source: str, collected_at: str, raw_obj: Any,
                   raw_path: Optional[str], source_updated_at: Optional[str] = None,
                   source_context: Optional[Mapping[str, Any]] = None, note: Optional[str] = None) -> str:
        run_id = uuid.uuid4().hex
        self.conn.execute(
            """INSERT INTO capture_run(run_id,batch_id,source,collected_at_utc,source_updated_at,raw_sha256,raw_path,source_context_json,note)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, batch_id, source, collected_at, source_updated_at, sha256_obj(raw_obj), raw_path,
             canonical_json(source_context or {}), note),
        )
        return run_id

    def upsert_match(self, rec: Mapping[str, Any], seen_at: str) -> int:
        sid = str(rec.get("match_id") or "")
        mnd = str(rec.get("match_num_date") or "")
        row = self.conn.execute(
            "SELECT match_key FROM match_dim WHERE sporttery_match_id=? AND match_num_date=?", (sid, mnd)
        ).fetchone()
        vals = (
            str(rec.get("upstream_match_id") or ""), str(rec.get("match_num") or ""), str(rec.get("business_date") or ""),
            rec.get("kickoff_utc"), str(rec.get("match_date") or ""), str(rec.get("match_time") or ""),
            str(rec.get("league") or ""), str(rec.get("league_id") or ""), str(rec.get("home_team") or ""),
            str(rec.get("away_team") or ""), str(rec.get("home_team_id") or ""), str(rec.get("away_team_id") or ""), seen_at,
        )
        if row:
            mk = int(row["match_key"])
            self.conn.execute(
                """UPDATE match_dim SET upstream_match_id=?,match_num=?,business_date=?,kickoff_utc=COALESCE(?,kickoff_utc),
                   match_date_raw=?,match_time_raw=?,league=?,league_id=?,home_team=?,away_team=?,home_team_id=?,away_team_id=?,last_seen_utc=?
                   WHERE match_key=?""", vals + (mk,)
            )
            return mk
        cur = self.conn.execute(
            """INSERT INTO match_dim(sporttery_match_id,upstream_match_id,match_num_date,match_num,business_date,kickoff_utc,
               match_date_raw,match_time_raw,league,league_id,home_team,away_team,home_team_id,away_team_id,first_seen_utc,last_seen_utc)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, vals[0], mnd, vals[1], vals[2], vals[3], vals[4], vals[5], vals[6], vals[7], vals[8], vals[9], vals[10], vals[11], seen_at, seen_at),
        )
        return int(cur.lastrowid)

    def ingest_sporttery(self, raw_payload: Mapping[str, Any], *, batch_id: Optional[str] = None,
                         raw_path: Optional[str] = None, collected_at: Optional[str] = None) -> Dict[str, Any]:
        collected_at = collected_at or utc_now_iso()
        batch_id = batch_id or uuid.uuid4().hex
        parsed = parse_sporttery_payload(raw_payload)
        run_id = self.insert_run(
            batch_id=batch_id, source="sporttery", collected_at=collected_at, raw_obj=raw_payload,
            raw_path=raw_path, source_updated_at=parsed.get("source_updated_at"),
            source_context={"endpoint": parsed.get("source_endpoint"), "pools": SPORTTERY_POOLS},
        )
        inserted = deduped = 0
        complete = 0
        for rec in parsed["matches"]:
            mk = self.upsert_match(rec, collected_at)
            payload_core = {k: rec.get(k) for k in ("had", "hhad", "crs", "ttg", "hafu", "pool_raw")}
            psha = sha256_obj(payload_core)
            try:
                self.conn.execute(
                    """INSERT INTO sporttery_snapshot(run_id,batch_id,match_key,collected_at_utc,source_updated_at,payload_sha256,
                       had_json,hhad_json,crs_json,ttg_json,hafu_json,pool_raw_json,pool_count,five_pool_complete)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, batch_id, mk, collected_at, rec.get("source_updated_at"), psha,
                     canonical_json(rec.get("had")) if rec.get("had") else None,
                     canonical_json(rec.get("hhad")) if rec.get("hhad") else None,
                     canonical_json(rec.get("crs")) if rec.get("crs") else None,
                     canonical_json(rec.get("ttg")) if rec.get("ttg") else None,
                     canonical_json(rec.get("hafu")) if rec.get("hafu") else None,
                     canonical_json(rec.get("pool_raw")), int(rec.get("pool_count") or 0), int(bool(rec.get("five_pool_complete")))),
                )
                inserted += 1
                complete += int(bool(rec.get("five_pool_complete")))
            except sqlite3.IntegrityError:
                deduped += 1
        self.conn.commit()
        return {"batch_id": batch_id, "run_id": run_id, "matches": len(parsed["matches"]), "inserted": inserted,
                "deduped": deduped, "five_pool_complete": complete, "source_updated_at": parsed.get("source_updated_at")}

    def upsert_external_event(self, source: str, rec: Mapping[str, Any], seen_at: str) -> int:
        seid = str(rec.get("source_event_id") or "")
        if not seid:
            seid = sha256_obj({"home": rec.get("home_team"), "away": rec.get("away_team"), "time": rec.get("commence_time_utc")})[:24]
        row = self.conn.execute("SELECT event_key FROM external_event WHERE source=? AND source_event_id=?", (source, seid)).fetchone()
        if row:
            ek = int(row["event_key"])
            self.conn.execute(
                """UPDATE external_event SET sport_key=?,commence_time_utc=COALESCE(?,commence_time_utc),home_team=?,away_team=?,last_seen_utc=? WHERE event_key=?""",
                (str(rec.get("sport_key") or ""), rec.get("commence_time_utc"), str(rec.get("home_team") or ""),
                 str(rec.get("away_team") or ""), seen_at, ek),
            )
            return ek
        cur = self.conn.execute(
            """INSERT INTO external_event(source,source_event_id,sport_key,commence_time_utc,home_team,away_team,first_seen_utc,last_seen_utc)
               VALUES(?,?,?,?,?,?,?,?)""",
            (source, seid, str(rec.get("sport_key") or ""), rec.get("commence_time_utc"), str(rec.get("home_team") or ""),
             str(rec.get("away_team") or ""), seen_at, seen_at),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _role_for_outcome(market_key: str, name: str, event: Mapping[str, Any]) -> str:
        n = (name or "").strip().casefold()
        home = str(event.get("home_team") or "").strip().casefold()
        away = str(event.get("away_team") or "").strip().casefold()
        if market_key == "h2h":
            if n == home: return "H"
            if n == away: return "A"
            if n in ("draw", "tie", "x"): return "D"
        if market_key == "totals":
            if n.startswith("over"): return "OVER"
            if n.startswith("under"): return "UNDER"
        if market_key == "spreads":
            if n == home: return "H"
            if n == away: return "A"
        return name

    def ingest_external(self, parsed_payload: Mapping[str, Any], *, batch_id: Optional[str] = None,
                        raw_obj: Optional[Any] = None, raw_path: Optional[str] = None,
                        collected_at: Optional[str] = None, source_context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        collected_at = collected_at or utc_now_iso()
        batch_id = batch_id or uuid.uuid4().hex
        source = str(parsed_payload.get("source") or "external")
        raw_obj = parsed_payload if raw_obj is None else raw_obj
        run_id = self.insert_run(batch_id=batch_id, source=source, collected_at=collected_at, raw_obj=raw_obj,
                                 raw_path=raw_path, source_context=source_context or {})
        events_inserted = events_deduped = quotes_inserted = direct_links = 0
        for e in parsed_payload.get("events") or []:
            ek = self.upsert_external_event(source, e, collected_at)
            snapshot_core = {"event": {k: e.get(k) for k in ("source_event_id", "commence_time_utc", "home_team", "away_team")},
                             "bookmakers": e.get("bookmakers") or []}
            psha = sha256_obj(snapshot_core)
            try:
                cur = self.conn.execute(
                    """INSERT INTO external_event_snapshot(run_id,batch_id,event_key,collected_at_utc,payload_sha256,bookmaker_count)
                       VALUES(?,?,?,?,?,?)""",
                    (run_id, batch_id, ek, collected_at, psha, len(e.get("bookmakers") or [])),
                )
                esid = int(cur.lastrowid)
                events_inserted += 1
            except sqlite3.IntegrityError:
                events_deduped += 1
                continue
            for b in e.get("bookmakers") or []:
                for m in b.get("markets") or []:
                    mk = str(m.get("market_key") or m.get("key") or "")
                    for o in m.get("outcomes") or []:
                        price = safe_float(o.get("price"))
                        if price is None:
                            continue
                        self.conn.execute(
                            """INSERT INTO external_market_quote(event_snapshot_id,bookmaker_key,bookmaker,bookmaker_updated_at,
                               market_key,market_updated_at,outcome_name,outcome_role,price,point)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (esid, str(b.get("bookmaker_key") or b.get("key") or ""), str(b.get("bookmaker") or b.get("title") or "unknown"),
                             b.get("bookmaker_updated_at") or b.get("last_update"), mk, m.get("market_updated_at") or m.get("last_update"),
                             str(o.get("name") or ""), self._role_for_outcome(mk, str(o.get("name") or ""), e), price, safe_number(o.get("point"))),
                        )
                        quotes_inserted += 1
            direct = e.get("sporttery_match_id")
            if direct:
                row = self.conn.execute("SELECT match_key FROM match_dim WHERE sporttery_match_id=? ORDER BY last_seen_utc DESC LIMIT 1", (str(direct),)).fetchone()
                if row:
                    self._upsert_link(int(row["match_key"]), ek, "direct_match_id", 1.0)
                    direct_links += 1
        self.conn.commit()
        return {"batch_id": batch_id, "run_id": run_id, "events_inserted": events_inserted, "events_deduped": events_deduped,
                "quotes_inserted": quotes_inserted, "direct_links": direct_links}

    def _upsert_link(self, match_key: int, event_key: int, method: str, confidence: float) -> None:
        self.conn.execute(
            """INSERT INTO event_link(match_key,event_key,method,confidence,created_at_utc,active)
               VALUES(?,?,?,?,?,1)
               ON CONFLICT(match_key,event_key) DO UPDATE SET method=excluded.method,confidence=excluded.confidence,active=1""",
            (match_key, event_key, method, float(confidence), utc_now_iso()),
        )

    def add_alias(self, sporttery_team: str, external_team: str, source: str = "*") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO team_alias(sporttery_team,external_team,source,created_at_utc) VALUES(?,?,?,?)",
            (sporttery_team, external_team, source, utc_now_iso()),
        )
        self.conn.commit()

    def ingest_aliases_csv(self, path: str | Path) -> Dict[str, int]:
        inserted = skipped = 0
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                st = str(r.get("sporttery_team") or "").strip()
                ex = str(r.get("external_team") or "").strip()
                src = str(r.get("source") or "*").strip() or "*"
                if not st or not ex:
                    skipped += 1; continue
                self.conn.execute(
                    "INSERT OR REPLACE INTO team_alias(sporttery_team,external_team,source,created_at_utc) VALUES(?,?,?,?)",
                    (st, ex, src, utc_now_iso()),
                )
                inserted += 1
        self.conn.commit()
        return {"inserted": inserted, "skipped": skipped}

    def link_event_direct(self, sporttery_match_id: str, source: str, source_event_id: str) -> Dict[str, Any]:
        m = self.conn.execute(
            "SELECT match_key FROM match_dim WHERE sporttery_match_id=? ORDER BY last_seen_utc DESC LIMIT 1",
            (sporttery_match_id,),
        ).fetchone()
        e = self.conn.execute(
            "SELECT event_key FROM external_event WHERE source=? AND source_event_id=?",
            (source, source_event_id),
        ).fetchone()
        if not m:
            raise ValueError(f"Sporttery match not found: {sporttery_match_id}")
        if not e:
            raise ValueError(f"External event not found: {source}/{source_event_id}")
        self._upsert_link(int(m["match_key"]), int(e["event_key"]), "manual_direct", 1.0)
        self.conn.commit()
        return {"status": "OK", "sporttery_match_id": sporttery_match_id, "source": source, "source_event_id": source_event_id}

    def export_unresolved_links_csv(self, path: str | Path, max_kickoff_gap_hours: float = 4.0, max_candidates: int = 8) -> Dict[str, int]:
        out_path = Path(path); out_path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["match_id","match_num_date","kickoff_utc","league","sporttery_home","sporttery_away",
                  "external_source","source_event_id","external_kickoff_utc","external_home","external_away","kickoff_gap_minutes"]
        rows_written = unresolved = 0
        events = list(self.conn.execute("SELECT * FROM external_event ORDER BY commence_time_utc"))
        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
            for m in self.conn.execute("SELECT * FROM match_dim ORDER BY kickoff_utc,match_key"):
                if self.conn.execute("SELECT 1 FROM event_link WHERE match_key=? AND active=1 LIMIT 1", (m["match_key"],)).fetchone():
                    continue
                unresolved += 1
                mk = iso_to_dt(m["kickoff_utc"], UTC) if m["kickoff_utc"] else None
                candidates = []
                for e in events:
                    ek = iso_to_dt(e["commence_time_utc"], UTC) if e["commence_time_utc"] else None
                    if mk and ek:
                        gap = abs((ek - mk).total_seconds()) / 60.0
                        if gap > max_kickoff_gap_hours * 60.0:
                            continue
                    else:
                        gap = 999999.0
                    candidates.append((gap, e))
                candidates.sort(key=lambda x: x[0])
                for gap, e in candidates[:max_candidates]:
                    w.writerow({
                        "match_id": m["sporttery_match_id"], "match_num_date": m["match_num_date"], "kickoff_utc": m["kickoff_utc"],
                        "league": m["league"], "sporttery_home": m["home_team"], "sporttery_away": m["away_team"],
                        "external_source": e["source"], "source_event_id": e["source_event_id"], "external_kickoff_utc": e["commence_time_utc"],
                        "external_home": e["home_team"], "external_away": e["away_team"],
                        "kickoff_gap_minutes": round(gap, 3) if gap < 999999 else "",
                    })
                    rows_written += 1
        return {"unresolved_matches": unresolved, "candidate_rows": rows_written}

    def _aliases(self) -> Dict[Tuple[str, str], set[str]]:
        out: Dict[Tuple[str, str], set[str]] = {}
        for r in self.conn.execute("SELECT sporttery_team,external_team,source FROM team_alias"):
            out.setdefault((str(r["source"]), normalize_team_name(str(r["sporttery_team"]))), set()).add(normalize_team_name(str(r["external_team"])))
        return out

    def auto_link_events(self, max_kickoff_gap_hours: float = 3.0) -> Dict[str, Any]:
        """Conservative mapper: exact normalized teams or explicit aliases + kickoff proximity.

        No fuzzy string mapping is used for automatic links. This deliberately avoids
        silently linking Chinese aliases to the wrong external event.
        """
        aliases = self._aliases()
        matches = list(self.conn.execute("SELECT * FROM match_dim"))
        events = list(self.conn.execute("SELECT * FROM external_event"))
        linked = ambiguous = unresolved = 0
        for m in matches:
            existing = self.conn.execute("SELECT 1 FROM event_link WHERE match_key=? AND active=1 LIMIT 1", (m["match_key"],)).fetchone()
            if existing:
                continue
            mh = normalize_team_name(m["home_team"]); ma = normalize_team_name(m["away_team"])
            mkick = iso_to_dt(m["kickoff_utc"], UTC) if m["kickoff_utc"] else None
            candidates: List[Tuple[float, sqlite3.Row, str]] = []
            for e in events:
                eh = normalize_team_name(e["home_team"]); ea = normalize_team_name(e["away_team"])
                src = str(e["source"])
                h_ok = eh == mh or eh in aliases.get((src, mh), set()) or eh in aliases.get(("*", mh), set())
                a_ok = ea == ma or ea in aliases.get((src, ma), set()) or ea in aliases.get(("*", ma), set())
                if not (h_ok and a_ok):
                    continue
                ekick = iso_to_dt(e["commence_time_utc"], UTC) if e["commence_time_utc"] else None
                gap = abs((ekick - mkick).total_seconds()) / 3600.0 if ekick and mkick else 0.0
                if ekick and mkick and gap > max_kickoff_gap_hours:
                    continue
                candidates.append((gap, e, "exact_or_alias_team+kickoff"))
            candidates.sort(key=lambda x: x[0])
            if len(candidates) == 1 or (len(candidates) > 1 and candidates[0][0] + 0.25 < candidates[1][0]):
                gap, e, method = candidates[0]
                conf = max(0.80, 1.0 - min(gap, max_kickoff_gap_hours) / max(1.0, max_kickoff_gap_hours) * 0.15)
                self._upsert_link(int(m["match_key"]), int(e["event_key"]), method, conf)
                linked += 1
            elif len(candidates) > 1:
                ambiguous += 1
            else:
                unresolved += 1
        self.conn.commit()
        return {"linked": linked, "ambiguous": ambiguous, "unresolved": unresolved}

    def ingest_results_csv(self, path: str | Path) -> Dict[str, int]:
        inserted = skipped = 0
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                sid = str(r.get("match_id") or r.get("sporttery_match_id") or "").strip()
                try:
                    gh = int(r.get("goals_home")); ga = int(r.get("goals_away"))
                except Exception:
                    skipped += 1; continue
                row = self.conn.execute("SELECT match_key FROM match_dim WHERE sporttery_match_id=? ORDER BY last_seen_utc DESC LIMIT 1", (sid,)).fetchone()
                if not row:
                    skipped += 1; continue
                self.conn.execute(
                    """INSERT INTO result(match_key,goals_home,goals_away,known_at_utc,source,inserted_at_utc)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(match_key) DO UPDATE SET goals_home=excluded.goals_home,goals_away=excluded.goals_away,
                       known_at_utc=excluded.known_at_utc,source=excluded.source,inserted_at_utc=excluded.inserted_at_utc""",
                    (int(row["match_key"]), gh, ga, r.get("known_at_utc"), r.get("source") or "csv", utc_now_iso()),
                )
                inserted += 1
        self.conn.commit()
        return {"inserted": inserted, "skipped": skipped}

    # ---- extraction helpers -------------------------------------------------
    def _sporttery_rows_for_match(self, match_key: int) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM sporttery_snapshot WHERE match_key=? ORDER BY collected_at_utc", (match_key,)
        ))

    def _linked_events(self, match_key: int) -> List[int]:
        return [int(r[0]) for r in self.conn.execute("SELECT event_key FROM event_link WHERE match_key=? AND active=1", (match_key,))]

    def _external_snapshot_near(self, event_keys: Sequence[int], target: datetime, *, not_after: bool = True,
                                max_gap_seconds: Optional[float] = None) -> Optional[sqlite3.Row]:
        if not event_keys:
            return None
        ph = ",".join("?" for _ in event_keys)
        rows = list(self.conn.execute(
            f"SELECT * FROM external_event_snapshot WHERE event_key IN ({ph}) ORDER BY collected_at_utc", tuple(event_keys)
        ))
        best = None; best_gap = None
        for r in rows:
            dt = iso_to_dt(r["collected_at_utc"], UTC)
            if not dt: continue
            if not_after and dt > target: continue
            gap = abs((target - dt).total_seconds())
            if max_gap_seconds is not None and gap > max_gap_seconds: continue
            if best_gap is None or gap < best_gap:
                best, best_gap = r, gap
        return best

    def _external_h2h_books(self, event_snapshot_id: int) -> List[Dict[str, Any]]:
        rows = list(self.conn.execute(
            "SELECT * FROM external_market_quote WHERE event_snapshot_id=? AND market_key='h2h' ORDER BY bookmaker,quote_id",
            (event_snapshot_id,),
        ))
        books: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            key = str(r["bookmaker_key"] or r["bookmaker"])
            b = books.setdefault(key, {"bookmaker": r["bookmaker"], "bookmaker_key": r["bookmaker_key"]})
            role = r["outcome_role"]
            if role == "H": b["home"] = r["price"]
            elif role == "D": b["draw"] = r["price"]
            elif role == "A": b["away"] = r["price"]
        return [b for b in books.values() if all(b.get(k) is not None for k in ("home", "draw", "away"))]

    def _external_all_quotes(self, event_snapshot_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT bookmaker_key,bookmaker,bookmaker_updated_at,market_key,market_updated_at,
                      outcome_name,outcome_role,price,point
               FROM external_market_quote WHERE event_snapshot_id=? ORDER BY bookmaker,market_key,quote_id""",
            (event_snapshot_id,),
        )
        return [dict(r) for r in rows]

    def _sporttery_snapshot_at_or_before(self, rows: Sequence[sqlite3.Row], target: datetime) -> Optional[sqlite3.Row]:
        eligible = []
        for r in rows:
            dt = iso_to_dt(r["collected_at_utc"], UTC)
            if dt and dt <= target:
                eligible.append((dt, r))
        return eligible[-1][1] if eligible else None

    @staticmethod
    def _loads(v: Optional[str]) -> Any:
        return json.loads(v) if v else None

    def export_aligned_jsonl(self, path: str | Path, max_sync_gap_seconds: float = 300.0,
                             require_five_pool: bool = True, require_external: bool = True) -> Dict[str, int]:
        """Export each Sporttery capture joined to the nearest external capture at/before it.

        This is the raw synchronized research layer. It never uses a future external
        snapshot relative to the Sporttery capture timestamp.
        """
        out_path = Path(path); out_path.parent.mkdir(parents=True, exist_ok=True)
        written = skipped_pool = skipped_external = 0
        with out_path.open("w", encoding="utf-8") as f:
            q = """SELECT s.*,m.sporttery_match_id,m.match_num_date,m.kickoff_utc,m.league,m.home_team,m.away_team
                   FROM sporttery_snapshot s JOIN match_dim m ON m.match_key=s.match_key ORDER BY s.collected_at_utc,s.match_key"""
            for s in self.conn.execute(q):
                if require_five_pool and not s["five_pool_complete"]:
                    skipped_pool += 1; continue
                st_dt = iso_to_dt(s["collected_at_utc"], UTC)
                ext = self._external_snapshot_near(self._linked_events(int(s["match_key"])), st_dt, not_after=True,
                                                   max_gap_seconds=max_sync_gap_seconds) if st_dt else None
                if require_external and ext is None:
                    skipped_external += 1; continue
                books = self._external_h2h_books(int(ext["event_snapshot_id"])) if ext else []
                ext_quotes = self._external_all_quotes(int(ext["event_snapshot_id"])) if ext else []
                rec = {
                    "match_key": s["match_key"], "match_id": s["sporttery_match_id"], "match_num_date": s["match_num_date"],
                    "kickoff_utc": s["kickoff_utc"], "league": s["league"], "home_team": s["home_team"], "away_team": s["away_team"],
                    "sporttery_collected_at_utc": s["collected_at_utc"], "sporttery_source_updated_at": s["source_updated_at"],
                    "sporttery": {"had": self._loads(s["had_json"]), "hhad": self._loads(s["hhad_json"]),
                                   "crs": self._loads(s["crs_json"]), "ttg": self._loads(s["ttg_json"]), "hafu": self._loads(s["hafu_json"])},
                    "five_pool_complete": bool(s["five_pool_complete"]),
                    "external": None,
                }
                if ext:
                    ext_dt = iso_to_dt(ext["collected_at_utc"], UTC)
                    rec["external"] = {
                        "event_snapshot_id": ext["event_snapshot_id"], "collected_at_utc": ext["collected_at_utc"],
                        "sync_gap_seconds": (st_dt - ext_dt).total_seconds() if st_dt and ext_dt else None,
                        "h2h_books": books, "h2h_consensus": robust_external_consensus([{**b, "market_key": "h2h"} for b in books]),
                        "quotes": ext_quotes,
                        "market_book_counts": {
                            mk: len({q["bookmaker"] for q in ext_quotes if q["market_key"] == mk})
                            for mk in sorted({q["market_key"] for q in ext_quotes})
                        },
                    }
                f.write(canonical_json(rec) + "\n")
                written += 1
        return {"written": written, "skipped_pool": skipped_pool, "skipped_external": skipped_external}

    def export_anchor_dataset(self, path: str | Path, anchors_minutes: Sequence[int] = (1440, 360, 60, 15),
                              close_buffer_minutes: int = 2, max_external_gap_seconds: float = 600.0,
                              require_five_pool: bool = True, min_external_books: int = 2,
                              max_anchor_staleness_minutes: Optional[float] = 30.0,
                              max_close_staleness_minutes: Optional[float] = 15.0) -> Dict[str, Any]:
        """Leakage-safe anchor dataset for Stage-3 OOS work.

        For each match/lead time, selects the latest captured Sporttery snapshot at
        or before kickoff-lead. Closing snapshot = latest capture at or before
        kickoff-close_buffer. External snapshots are also selected only at/before
        each selected Sporttery timestamp.
        """
        out_path = Path(path); out_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0; reasons: Dict[str, int] = {}
        def skip(k: str): reasons[k] = reasons.get(k, 0) + 1
        with out_path.open("w", encoding="utf-8") as f:
            for m in self.conn.execute("SELECT * FROM match_dim ORDER BY kickoff_utc,match_key"):
                kickoff = iso_to_dt(m["kickoff_utc"], UTC) if m["kickoff_utc"] else None
                if not kickoff:
                    skip("missing_kickoff"); continue
                srows = self._sporttery_rows_for_match(int(m["match_key"]))
                close_target = kickoff - timedelta(minutes=close_buffer_minutes)
                close_s = self._sporttery_snapshot_at_or_before(srows, close_target)
                if not close_s:
                    skip("missing_close"); continue
                close_dt = iso_to_dt(close_s["collected_at_utc"], UTC)
                close_staleness = (close_target - close_dt).total_seconds() / 60.0 if close_dt else None
                if max_close_staleness_minutes is not None and (close_staleness is None or close_staleness > max_close_staleness_minutes):
                    skip("stale_close"); continue
                if require_five_pool and not close_s["five_pool_complete"]:
                    skip("close_incomplete_five_pool"); continue
                event_keys = self._linked_events(int(m["match_key"]))
                close_ext = self._external_snapshot_near(event_keys, close_dt, not_after=True,
                                                         max_gap_seconds=max_external_gap_seconds) if close_dt else None
                close_books = self._external_h2h_books(int(close_ext["event_snapshot_id"])) if close_ext else []
                close_quotes = self._external_all_quotes(int(close_ext["event_snapshot_id"])) if close_ext else []
                close_cons = robust_external_consensus([{**b, "market_key": "h2h"} for b in close_books])
                result = self.conn.execute("SELECT * FROM result WHERE match_key=?", (m["match_key"],)).fetchone()
                for lead in anchors_minutes:
                    target = kickoff - timedelta(minutes=int(lead))
                    s = self._sporttery_snapshot_at_or_before(srows, target)
                    if not s:
                        skip(f"missing_anchor_{lead}"); continue
                    st_dt = iso_to_dt(s["collected_at_utc"], UTC)
                    anchor_staleness = (target - st_dt).total_seconds() / 60.0 if st_dt else None
                    if max_anchor_staleness_minutes is not None and (anchor_staleness is None or anchor_staleness > max_anchor_staleness_minutes):
                        skip(f"stale_anchor_{lead}"); continue
                    if require_five_pool and not s["five_pool_complete"]:
                        skip(f"anchor_{lead}_incomplete_five_pool"); continue
                    ext = self._external_snapshot_near(event_keys, st_dt, not_after=True, max_gap_seconds=max_external_gap_seconds) if st_dt else None
                    books = self._external_h2h_books(int(ext["event_snapshot_id"])) if ext else []
                    quotes = self._external_all_quotes(int(ext["event_snapshot_id"])) if ext else []
                    cons = robust_external_consensus([{**b, "market_key": "h2h"} for b in books])
                    if min_external_books and (not cons or int(cons["n_books"]) < int(min_external_books)):
                        skip(f"anchor_{lead}_external_books_lt_{min_external_books}"); continue

                    had = self._loads(s["had_json"]); close_had = self._loads(close_s["had_json"])
                    p0 = devig_three((had["H"], had["D"], had["A"])) if had else None
                    pc = devig_three((close_had["H"], close_had["D"], close_had["A"])) if close_had else None
                    clv = None
                    if had and close_had and p0 and pc:
                        clv = {
                            "price_ratio_minus1": {k: had[k] / close_had[k] - 1.0 for k in ("H", "D", "A")},
                            "devig_prob_move_pp": {k: 100.0 * (pc[i] - p0[i]) for i, k in enumerate(("H", "D", "A"))},
                        }
                    rec = {
                        "match_key": m["match_key"], "match_id": m["sporttery_match_id"], "match_num_date": m["match_num_date"],
                        "kickoff_utc": m["kickoff_utc"], "league": m["league"], "home_team": m["home_team"], "away_team": m["away_team"],
                        "anchor_minutes_before_kickoff": int(lead), "target_time_utc": target.isoformat(),
                        "anchor_collected_at_utc": s["collected_at_utc"], "anchor_staleness_minutes": anchor_staleness,
                        "close_collected_at_utc": close_s["collected_at_utc"], "close_staleness_minutes": close_staleness,
                        "sporttery": {"had": had, "hhad": self._loads(s["hhad_json"]), "crs": self._loads(s["crs_json"]),
                                       "ttg": self._loads(s["ttg_json"]), "hafu": self._loads(s["hafu_json"])},
                        "sporttery_close": {"had": close_had, "hhad": self._loads(close_s["hhad_json"]), "crs": self._loads(close_s["crs_json"]),
                                             "ttg": self._loads(close_s["ttg_json"]), "hafu": self._loads(close_s["hafu_json"])},
                        "external": {"h2h_books": books, "h2h_consensus": cons, "quotes": quotes,
                                     "snapshot_time_utc": ext["collected_at_utc"] if ext else None,
                                     "sync_gap_seconds": (st_dt - iso_to_dt(ext["collected_at_utc"], UTC)).total_seconds() if ext and st_dt else None},
                        "external_close": {"h2h_books": close_books, "h2h_consensus": close_cons, "quotes": close_quotes,
                                           "snapshot_time_utc": close_ext["collected_at_utc"] if close_ext else None},
                        "clv_ready": clv,
                        "result": ({"goals_home": result["goals_home"], "goals_away": result["goals_away"],
                                    "known_at_utc": result["known_at_utc"]} if result else None),
                    }
                    f.write(canonical_json(rec) + "\n")
                    written += 1
        return {"written": written, "skipped": reasons, "anchors_minutes": list(anchors_minutes),
                "close_buffer_minutes": close_buffer_minutes, "min_external_books": min_external_books,
                "max_anchor_staleness_minutes": max_anchor_staleness_minutes,
                "max_close_staleness_minutes": max_close_staleness_minutes}

    def audit(self, sync_gap_seconds: float = 300.0) -> Dict[str, Any]:
        one = lambda q, a=(): self.conn.execute(q, a).fetchone()[0]
        match_n = one("SELECT COUNT(*) FROM match_dim")
        st_n = one("SELECT COUNT(*) FROM sporttery_snapshot")
        st_complete = one("SELECT COUNT(*) FROM sporttery_snapshot WHERE five_pool_complete=1")
        ext_event_n = one("SELECT COUNT(*) FROM external_event")
        ext_snap_n = one("SELECT COUNT(*) FROM external_event_snapshot")
        quote_n = one("SELECT COUNT(*) FROM external_market_quote")
        link_n = one("SELECT COUNT(*) FROM event_link WHERE active=1")
        result_n = one("SELECT COUNT(*) FROM result")
        duplicate_runs = one("SELECT COUNT(*) FROM capture_run")
        linked_matches = one("SELECT COUNT(DISTINCT match_key) FROM event_link WHERE active=1")

        sync_count = 0; gaps = []
        for s in self.conn.execute("SELECT match_key,collected_at_utc FROM sporttery_snapshot"):
            dt = iso_to_dt(s["collected_at_utc"], UTC)
            ext = self._external_snapshot_near(self._linked_events(int(s["match_key"])), dt, not_after=True,
                                               max_gap_seconds=sync_gap_seconds) if dt else None
            if ext:
                ed = iso_to_dt(ext["collected_at_utc"], UTC)
                sync_count += 1
                gaps.append((dt - ed).total_seconds())
        pool_coverage = {}
        for col, name in (("had_json","HAD"),("hhad_json","HHAD"),("crs_json","CRS"),("ttg_json","TTG"),("hafu_json","HAFU")):
            n = one(f"SELECT COUNT(*) FROM sporttery_snapshot WHERE {col} IS NOT NULL")
            pool_coverage[name] = {"n": n, "rate": n / st_n if st_n else 0.0}
        return {
            "version": VERSION, "governance_status": GOVERNANCE_STATUS, "db": self.db_path,
            "counts": {"capture_runs": duplicate_runs, "matches": match_n, "sporttery_snapshots": st_n,
                       "sporttery_five_pool_complete": st_complete, "external_events": ext_event_n,
                       "external_event_snapshots": ext_snap_n, "external_quotes": quote_n,
                       "event_links": link_n, "linked_matches": linked_matches, "results": result_n},
            "coverage": {"five_pool_complete_rate": st_complete / st_n if st_n else 0.0,
                         "linked_match_rate": linked_matches / match_n if match_n else 0.0,
                         "synchronized_snapshot_rate": sync_count / st_n if st_n else 0.0,
                         "pool": pool_coverage},
            "sync_gap_seconds": {"limit": sync_gap_seconds, "n": len(gaps),
                                 "median": statistics.median(gaps) if gaps else None,
                                 "p95": percentile(gaps, 0.95) if gaps else None,
                                 "max": max(gaps) if gaps else None},
            "promotion_gate": {
                "3_4_staking_baseline": True,
                "3_5_stage3_data_collection": True,
                "external_positive_probability_fusion": False,
                "residual_ml_production": False,
                "dynamic_xg_production": False,
                "score_distribution_had_weight_production": False,
                "new_S_A1_A2_thresholds_frozen": False,
            },
        }


def percentile(vals: Sequence[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


# ---------------------------------------------------------------------------
# Raw archiving / command helpers
# ---------------------------------------------------------------------------

def archive_raw(raw_dir: str | Path, source: str, batch_id: str, obj: Any, collected_at: str) -> str:
    d = Path(raw_dir) / source / collected_at[:10]
    stamp = collected_at.replace(":", "").replace("-", "").replace("+00:00", "Z")
    p = d / f"{source}_{stamp}_{batch_id[:8]}.json"
    atomic_write_json(p, obj)
    return str(p)


def parse_anchor_list(text: str) -> List[int]:
    vals = []
    for x in text.split(","):
        x = x.strip()
        if x:
            vals.append(int(x))
    if not vals:
        raise ValueError("anchors cannot be empty")
    return sorted(set(vals), reverse=True)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def cmd_init(args) -> None:
    s = Stage3Store(args.db)
    print(json.dumps({"status": "OK", "db": args.db, "version": VERSION, "governance": GOVERNANCE_STATUS}, ensure_ascii=False, indent=2))
    s.close()


def cmd_capture_sporttery(args) -> None:
    batch = args.batch_id or uuid.uuid4().hex
    collected = utc_now_iso()
    raw = SportteryClient(timeout=args.timeout).fetch_raw()
    raw_path = archive_raw(args.raw_dir, "sporttery", batch, raw, collected) if args.raw_dir else None
    s = Stage3Store(args.db)
    res = s.ingest_sporttery(raw, batch_id=batch, raw_path=raw_path, collected_at=collected)
    res.update({"raw_path": raw_path, "governance": GOVERNANCE_STATUS})
    print(json.dumps(res, ensure_ascii=False, indent=2))
    s.close()


def cmd_ingest_sporttery(args) -> None:
    raw = load_json(args.input)
    batch = args.batch_id or uuid.uuid4().hex
    s = Stage3Store(args.db)
    res = s.ingest_sporttery(raw, batch_id=batch, raw_path=str(Path(args.input).resolve()), collected_at=args.collected_at or utc_now_iso())
    print(json.dumps(res, ensure_ascii=False, indent=2)); s.close()


def cmd_capture_external(args) -> None:
    batch = args.batch_id or uuid.uuid4().hex
    s = Stage3Store(args.db)
    all_res = []
    client = TheOddsApiClient(timeout=args.timeout)
    for sport_key in args.sport_key:
        collected = utc_now_iso()
        raw = client.fetch(sport_key, regions=args.regions, markets=args.markets)
        raw_path = archive_raw(args.raw_dir, f"the_odds_api_{sport_key}", batch, raw, collected) if args.raw_dir else None
        parsed = parse_the_odds_api(raw, sport_key)
        res = s.ingest_external(parsed, batch_id=batch, raw_obj=raw, raw_path=raw_path, collected_at=collected,
                                source_context={"sport_key": sport_key, "regions": args.regions, "markets": args.markets})
        res["sport_key"] = sport_key; res["raw_path"] = raw_path; all_res.append(res)
    link = s.auto_link_events(max_kickoff_gap_hours=args.max_kickoff_gap_hours)
    print(json.dumps({"batch_id": batch, "sources": all_res, "auto_link": link, "governance": GOVERNANCE_STATUS}, ensure_ascii=False, indent=2))
    s.close()


def cmd_capture_batch(args) -> None:
    """Capture external markets first and Sporttery last under one batch id.

    Sporttery is the execution/decision timestamp anchor. Capturing it last lets
    leakage-safe joins use only external observations already available by that
    primary timestamp.
    """
    batch = args.batch_id or uuid.uuid4().hex
    s = Stage3Store(args.db)
    output: Dict[str, Any] = {"batch_id": batch, "sporttery": None, "external": [], "governance": GOVERNANCE_STATUS}

    client = TheOddsApiClient(timeout=args.timeout)
    for sport_key in args.sport_key:
        collected = utc_now_iso()
        raw = client.fetch(sport_key, regions=args.regions, markets=args.markets)
        raw_path = archive_raw(args.raw_dir, f"the_odds_api_{sport_key}", batch, raw, collected) if args.raw_dir else None
        parsed = parse_the_odds_api(raw, sport_key)
        res = s.ingest_external(parsed, batch_id=batch, raw_obj=raw, raw_path=raw_path, collected_at=collected,
                                source_context={"sport_key": sport_key, "regions": args.regions, "markets": args.markets, "batch_capture": True})
        res["sport_key"] = sport_key; res["raw_path"] = raw_path
        output["external"].append(res)

    # Primary execution market is captured LAST so no external quote in this batch
    # is from the future relative to the Sporttery timestamp used for analysis.
    st_collected = utc_now_iso()
    st_raw = SportteryClient(timeout=args.timeout).fetch_raw()
    st_raw_path = archive_raw(args.raw_dir, "sporttery", batch, st_raw, st_collected) if args.raw_dir else None
    st_res = s.ingest_sporttery(st_raw, batch_id=batch, raw_path=st_raw_path, collected_at=st_collected)
    st_res["raw_path"] = st_raw_path
    output["sporttery"] = st_res

    output["auto_link"] = s.auto_link_events(max_kickoff_gap_hours=args.max_kickoff_gap_hours)
    output["audit"] = s.audit(sync_gap_seconds=args.sync_gap_seconds)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    s.close()


def cmd_ingest_external(args) -> None:
    raw = load_json(args.input)
    if args.format == "the_odds_api":
        if not args.sport_key:
            raise SystemExit("--sport-key is required for --format the_odds_api")
        parsed = parse_the_odds_api(raw, args.sport_key[0])
    else:
        parsed = parse_normalized_external(raw, source=args.source)
    batch = args.batch_id or uuid.uuid4().hex
    s = Stage3Store(args.db)
    res = s.ingest_external(parsed, batch_id=batch, raw_obj=raw, raw_path=str(Path(args.input).resolve()),
                            collected_at=args.collected_at or utc_now_iso(), source_context={"format": args.format})
    res["auto_link"] = s.auto_link_events(max_kickoff_gap_hours=args.max_kickoff_gap_hours)
    print(json.dumps(res, ensure_ascii=False, indent=2)); s.close()


def cmd_add_alias(args) -> None:
    s = Stage3Store(args.db); s.add_alias(args.sporttery_team, args.external_team, args.source)
    link = s.auto_link_events(max_kickoff_gap_hours=args.max_kickoff_gap_hours)
    print(json.dumps({"status": "OK", "auto_link": link}, ensure_ascii=False, indent=2)); s.close()


def cmd_ingest_aliases(args) -> None:
    s = Stage3Store(args.db)
    res = s.ingest_aliases_csv(args.input)
    res["auto_link"] = s.auto_link_events(max_kickoff_gap_hours=args.max_kickoff_gap_hours)
    print(json.dumps(res, ensure_ascii=False, indent=2)); s.close()


def cmd_link_event(args) -> None:
    s = Stage3Store(args.db)
    res = s.link_event_direct(args.match_id, args.source, args.source_event_id)
    print(json.dumps(res, ensure_ascii=False, indent=2)); s.close()


def cmd_export_unresolved(args) -> None:
    s = Stage3Store(args.db)
    res = s.export_unresolved_links_csv(args.output, max_kickoff_gap_hours=args.max_kickoff_gap_hours, max_candidates=args.max_candidates)
    res["output"] = args.output
    print(json.dumps(res, ensure_ascii=False, indent=2)); s.close()


def cmd_ingest_results(args) -> None:
    s = Stage3Store(args.db); res = s.ingest_results_csv(args.input)
    print(json.dumps(res, ensure_ascii=False, indent=2)); s.close()


def cmd_export_aligned(args) -> None:
    s = Stage3Store(args.db)
    res = s.export_aligned_jsonl(args.output, max_sync_gap_seconds=args.max_sync_gap_seconds,
                                 require_five_pool=not args.allow_incomplete_five_pool,
                                 require_external=not args.allow_missing_external)
    res["output"] = args.output
    print(json.dumps(res, ensure_ascii=False, indent=2)); s.close()


def cmd_export_anchors(args) -> None:
    s = Stage3Store(args.db)
    res = s.export_anchor_dataset(args.output, anchors_minutes=parse_anchor_list(args.anchors),
                                  close_buffer_minutes=args.close_buffer_minutes,
                                  max_external_gap_seconds=args.max_external_gap_seconds,
                                  require_five_pool=not args.allow_incomplete_five_pool,
                                  min_external_books=args.min_external_books,
                                  max_anchor_staleness_minutes=args.max_anchor_staleness_minutes,
                                  max_close_staleness_minutes=args.max_close_staleness_minutes)
    res["output"] = args.output
    print(json.dumps(res, ensure_ascii=False, indent=2)); s.close()


def cmd_audit(args) -> None:
    s = Stage3Store(args.db); res = s.audit(sync_gap_seconds=args.sync_gap_seconds)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    print(text); s.close()


def cmd_self_test(args) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        s = Stage3Store(db)
        base = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
        batch = "selftestbatch"
        sport = {
            "value": {"lastUpdateTime": "2026-08-17 18:00:00", "matchInfoList": [{"matchNumDate": "260817", "subMatchList": [{
                "matchNumStr": "周一001", "matchId": "m1", "matchNum": "001", "matchNumDate": "260817",
                "matchDate": "2026-08-17", "matchTime": "20:00:00", "leagueAbbName": "测试联赛",
                "homeTeamAbbName": "Alpha FC", "awayTeamAbbName": "Beta FC",
                "had": {"h": "2.00", "d": "3.20", "a": "3.40"},
                "hhad": {"goalLineValue": "-1", "h": "3.50", "d": "3.60", "a": "1.72"},
                "crs": {"s01s00": "7.0", "s01s01": "6.5", "s1sh": "16.0", "s1sd": "20.0", "s1sa": "18.0"},
                "ttg": {"s0": "9.0", "s1": "4.2", "s2": "3.1", "s3": "3.8", "s4": "5.2", "s5": "8.0", "s6": "12", "s7": "15"},
                "hafu": {"hh": "3.0", "hd": "15", "ha": "30", "dh": "5", "dd": "6", "da": "8", "ah": "30", "ad": "16", "aa": "5"}
            }]}]}, "_jingcai_source_endpoint": "fixture"
        }
        r1 = s.ingest_sporttery(sport, batch_id=batch, collected_at=base.isoformat())
        assert r1["inserted"] == 1 and r1["five_pool_complete"] == 1
        ext = {"source": "fixture_external", "events": [{
            "source_event_id": "e1", "sporttery_match_id": "周一001", "sport_key": "soccer_test",
            "commence_time_utc": "2026-08-17T12:00:00+00:00", "home_team": "Alpha FC", "away_team": "Beta FC",
            "bookmakers": [
                {"bookmaker_key": "b1", "bookmaker": "Book1", "markets": [
                    {"market_key": "h2h", "outcomes": [
                        {"name": "Alpha FC", "price": 2.05}, {"name": "Draw", "price": 3.25}, {"name": "Beta FC", "price": 3.50}]},
                    {"market_key": "totals", "outcomes": [
                        {"name": "Over", "price": 1.92, "point": 2.5}, {"name": "Under", "price": 1.94, "point": 2.5}]},
                    {"market_key": "spreads", "outcomes": [
                        {"name": "Alpha FC", "price": 2.00, "point": -0.5}, {"name": "Beta FC", "price": 1.88, "point": 0.5}]}]},
                {"bookmaker_key": "b2", "bookmaker": "Book2", "markets": [{"market_key": "h2h", "outcomes": [
                    {"name": "Alpha FC", "price": 2.02}, {"name": "Draw", "price": 3.30}, {"name": "Beta FC", "price": 3.55}]}]}
            ]
        }]}
        r2 = s.ingest_external(ext, batch_id=batch, collected_at=(base - timedelta(seconds=30)).isoformat())
        assert r2["events_inserted"] == 1 and r2["quotes_inserted"] == 10 and r2["direct_links"] == 1
        # Unchanged prices at a NEW collection time must still be preserved for CLV/closing evidence.
        r3 = s.ingest_sporttery(sport, batch_id="selftest2", collected_at=(base + timedelta(hours=1)).isoformat())
        assert r3["inserted"] == 1 and r3["deduped"] == 0
        r4 = s.ingest_sporttery(sport, batch_id="selftest3", collected_at=(base + timedelta(hours=1, minutes=50)).isoformat())
        assert r4["inserted"] == 1
        # Preserve external market snapshots near both the 60m anchor and closing window.
        r5 = s.ingest_external(ext, batch_id="selftest2", collected_at=(base + timedelta(minutes=59, seconds=30)).isoformat())
        r6 = s.ingest_external(ext, batch_id="selftest3", collected_at=(base + timedelta(hours=1, minutes=49, seconds=30)).isoformat())
        assert r5["events_inserted"] == 1 and r6["events_inserted"] == 1
        audit = s.audit(sync_gap_seconds=120)
        assert audit["counts"]["sporttery_snapshots"] == 3
        assert audit["coverage"]["five_pool_complete_rate"] == 1.0
        aligned = Path(td) / "aligned.jsonl"
        ex = s.export_aligned_jsonl(aligned, max_sync_gap_seconds=120, require_five_pool=True, require_external=True)
        assert ex["written"] == 3
        row = json.loads(aligned.read_text(encoding="utf-8").splitlines()[0])
        assert row["external"]["h2h_consensus"]["n_books"] == 2
        assert {q["market_key"] for q in row["external"]["quotes"]} == {"h2h", "spreads", "totals"}
        anchors = Path(td) / "anchors.jsonl"
        ax = s.export_anchor_dataset(anchors, anchors_minutes=(60,), close_buffer_minutes=2,
                                     max_external_gap_seconds=120, require_five_pool=True, min_external_books=2,
                                     max_anchor_staleness_minutes=1, max_close_staleness_minutes=10)
        assert ax["written"] == 1
        arow = json.loads(anchors.read_text(encoding="utf-8").strip())
        assert arow["anchor_staleness_minutes"] == 0.0 and arow["close_staleness_minutes"] == 8.0
        assert arow["clv_ready"] is not None
        s.close()
        print(json.dumps({"self_test": "PASS", "version": VERSION, "audit": audit, "aligned_rows": ex["written"],
                          "anchor_rows": ax["written"]}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Jingcai 3.5 Alpha Stage 3 timestamped market data layer")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init", help="initialize SQLite schema")
    x.add_argument("--db", required=True); x.set_defaults(func=cmd_init)

    x = sub.add_parser("capture-sporttery", help="fetch and persist official Sporttery five-pool snapshot")
    x.add_argument("--db", required=True); x.add_argument("--raw-dir"); x.add_argument("--batch-id"); x.add_argument("--timeout", type=float, default=10.0)
    x.set_defaults(func=cmd_capture_sporttery)

    x = sub.add_parser("ingest-sporttery", help="ingest saved raw Sporttery JSON")
    x.add_argument("--db", required=True); x.add_argument("--input", required=True); x.add_argument("--batch-id"); x.add_argument("--collected-at")
    x.set_defaults(func=cmd_ingest_sporttery)

    x = sub.add_parser("capture-external", help="capture The Odds API multi-book markets for one or more sport keys")
    x.add_argument("--db", required=True); x.add_argument("--sport-key", action="append", required=True)
    x.add_argument("--regions", default="eu,uk"); x.add_argument("--markets", default="h2h,spreads,totals")
    x.add_argument("--raw-dir"); x.add_argument("--batch-id"); x.add_argument("--timeout", type=float, default=12.0)
    x.add_argument("--max-kickoff-gap-hours", type=float, default=3.0); x.set_defaults(func=cmd_capture_external)

    x = sub.add_parser("capture-batch", help="capture Sporttery five pools + external multi-book markets under one batch id")
    x.add_argument("--db", required=True); x.add_argument("--sport-key", action="append", required=True)
    x.add_argument("--regions", default="eu,uk"); x.add_argument("--markets", default="h2h,spreads,totals")
    x.add_argument("--raw-dir"); x.add_argument("--batch-id"); x.add_argument("--timeout", type=float, default=12.0)
    x.add_argument("--max-kickoff-gap-hours", type=float, default=3.0); x.add_argument("--sync-gap-seconds", type=float, default=300.0)
    x.set_defaults(func=cmd_capture_batch)

    x = sub.add_parser("ingest-external", help="ingest saved external market JSON")
    x.add_argument("--db", required=True); x.add_argument("--input", required=True); x.add_argument("--format", choices=("normalized", "the_odds_api"), default="normalized")
    x.add_argument("--source", default="external_json"); x.add_argument("--sport-key", action="append"); x.add_argument("--batch-id"); x.add_argument("--collected-at")
    x.add_argument("--max-kickoff-gap-hours", type=float, default=3.0); x.set_defaults(func=cmd_ingest_external)

    x = sub.add_parser("add-alias", help="add explicit Sporttery↔external team alias and retry mapping")
    x.add_argument("--db", required=True); x.add_argument("--sporttery-team", required=True); x.add_argument("--external-team", required=True); x.add_argument("--source", default="*")
    x.add_argument("--max-kickoff-gap-hours", type=float, default=3.0); x.set_defaults(func=cmd_add_alias)

    x = sub.add_parser("ingest-aliases", help="bulk import team aliases CSV: sporttery_team,external_team[,source]")
    x.add_argument("--db", required=True); x.add_argument("--input", required=True); x.add_argument("--max-kickoff-gap-hours", type=float, default=3.0)
    x.set_defaults(func=cmd_ingest_aliases)

    x = sub.add_parser("link-event", help="manually link a Sporttery match to an external event id")
    x.add_argument("--db", required=True); x.add_argument("--match-id", required=True); x.add_argument("--source", required=True); x.add_argument("--source-event-id", required=True)
    x.set_defaults(func=cmd_link_event)

    x = sub.add_parser("export-unresolved", help="export unresolved match/event candidates for manual mapping")
    x.add_argument("--db", required=True); x.add_argument("--output", required=True); x.add_argument("--max-kickoff-gap-hours", type=float, default=4.0); x.add_argument("--max-candidates", type=int, default=8)
    x.set_defaults(func=cmd_export_unresolved)

    x = sub.add_parser("ingest-results", help="ingest results CSV: match_id,goals_home,goals_away[,known_at_utc,source]")
    x.add_argument("--db", required=True); x.add_argument("--input", required=True); x.set_defaults(func=cmd_ingest_results)

    x = sub.add_parser("export-aligned", help="export synchronized Sporttery+external snapshot JSONL")
    x.add_argument("--db", required=True); x.add_argument("--output", required=True); x.add_argument("--max-sync-gap-seconds", type=float, default=300.0)
    x.add_argument("--allow-incomplete-five-pool", action="store_true"); x.add_argument("--allow-missing-external", action="store_true")
    x.set_defaults(func=cmd_export_aligned)

    x = sub.add_parser("export-anchors", help="export leakage-safe pre-kickoff/closing research dataset JSONL")
    x.add_argument("--db", required=True); x.add_argument("--output", required=True); x.add_argument("--anchors", default="1440,360,60,15")
    x.add_argument("--close-buffer-minutes", type=int, default=2); x.add_argument("--max-external-gap-seconds", type=float, default=600.0)
    x.add_argument("--min-external-books", type=int, default=2); x.add_argument("--allow-incomplete-five-pool", action="store_true")
    x.add_argument("--max-anchor-staleness-minutes", type=float, default=30.0)
    x.add_argument("--max-close-staleness-minutes", type=float, default=15.0)
    x.set_defaults(func=cmd_export_anchors)

    x = sub.add_parser("audit", help="report coverage, synchronization, mapping and governance status")
    x.add_argument("--db", required=True); x.add_argument("--sync-gap-seconds", type=float, default=300.0); x.add_argument("--output")
    x.set_defaults(func=cmd_audit)

    x = sub.add_parser("self-test", help="run deterministic offline integrity test")
    x.set_defaults(func=cmd_self_test)
    return p


def main() -> None:
    parser = build_parser(); args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print(json.dumps({"status": "ERROR", "error": "interrupted", "version": VERSION}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc), "type": type(exc).__name__,
                          "version": VERSION, "governance": GOVERNANCE_STATUS}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
