#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jingcai Stage 3 Parser / Builder v1
==================================

Purpose
-------
Bridge iPhone Shortcuts Collector JSON files into a normalized, auditable
research dataset for Jingcai 3.5 Alpha Stage 3.

Governance
----------
* Jingcai 3.4 remains the staking baseline.
* This tool is data/research only; it does not promote 3.5 Alpha.
* External positive probability fusion remains inactive.
* Residual ML, dynamic team/xG and score-distribution fusion remain Shadow.

Inputs
------
* sporttery_5pools_*.json
* sporttery_results_*.json (including _p2/_p3/... pages)

Outputs
-------
* stage3.db
* snapshot_matches.csv
* odds_long.csv
* results_latest.csv
* snapshot_result_join.csv
* anchor_candidates.csv
* manifest.json

Python 3.10+, standard library only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

VERSION = "1.0.0"
SH_TZ = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
POOLS = ("had", "hhad", "crs", "ttg", "hafu")
ANCHORS_MIN = (1440, 360, 60, 15)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_float(v: Any) -> Optional[float]:
    try:
        x = float(v)
        if not math.isfinite(x) or x <= 0:
            return None
        return x
    except Exception:
        return None


def safe_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return None


def clean_outer_mapping(obj: Any) -> Any:
    """Strip accidental Shortcuts whitespace from wrapper keys/string metadata.

    The embedded `data` string is preserved verbatim until JSON-decoded.
    """
    if not isinstance(obj, dict):
        return obj
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        kk = str(k).strip()
        if kk == "data":
            out[kk] = v
        elif isinstance(v, str):
            out[kk] = v.strip()
        else:
            out[kk] = v
    return out


def load_collector_file(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    outer = clean_outer_mapping(raw)
    data = outer.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            return {
                "path": str(path), "outer": outer, "payload": None,
                "valid": False, "reason": f"embedded data JSON decode error: {exc}",
            }
    elif isinstance(data, dict):
        pass
    elif isinstance(raw, dict) and "value" in raw:
        # Raw upstream payload without Shortcuts envelope.
        data = raw
        outer = {"endpoint": "unknown"}
    else:
        return {"path": str(path), "outer": outer, "payload": None, "valid": False, "reason": "missing data payload"}

    if not isinstance(data, dict):
        return {"path": str(path), "outer": outer, "payload": None, "valid": False, "reason": "payload is not a dict"}

    success = data.get("success")
    if success is False:
        return {
            "path": str(path), "outer": outer, "payload": data, "valid": False,
            "reason": f"upstream failure {data.get('errorCode')}: {data.get('errorMessage')}",
        }

    fetched = outer.get("fetched_at_local")
    return {
        "path": str(path), "outer": outer, "payload": data, "valid": True,
        "reason": None, "fetched_at_local": fetched,
        "endpoint": str(outer.get("endpoint") or "").strip().lower(),
        "sha256": sha256_file(path),
    }


def parse_dt(text: Optional[str], default_tz=SH_TZ) -> Optional[datetime]:
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except Exception:
                pass
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt


def iso_local(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(SH_TZ).replace(microsecond=0).isoformat()


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_kickoff(sm: Mapping[str, Any]) -> Optional[datetime]:
    d = str(sm.get("matchDate") or "").strip()
    t = str(sm.get("matchTime") or "").strip()
    if not d:
        return None
    if not t:
        t = "00:00:00"
    return parse_dt(f"{d} {t}", SH_TZ)


def parse_pool_update(pool: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not pool:
        return None
    d = str(pool.get("updateDate") or "").strip()
    t = str(pool.get("updateTime") or "").strip()
    if not d or not t:
        return None
    return iso_local(parse_dt(f"{d} {t}", SH_TZ))


def score_selection(code: str) -> Optional[str]:
    m = re.fullmatch(r"s(\d{2})s(\d{2})", code)
    if m:
        return f"{int(m.group(1))}-{int(m.group(2))}"
    return {"s1sh": "H_OTHER", "s1sd": "D_OTHER", "s1sa": "A_OTHER"}.get(code)


def ttg_selection(code: str) -> Optional[str]:
    m = re.fullmatch(r"s([0-7])", code)
    if not m:
        return None
    n = int(m.group(1))
    return "7+" if n == 7 else str(n)


def hafu_selection(code: str) -> Optional[str]:
    if code not in {"hh","hd","ha","dh","dd","da","ah","ad","aa"}:
        return None
    return code.upper()


def split_score(s: Any) -> Tuple[Optional[int], Optional[int]]:
    text = str(s or "").strip()
    m = re.fullmatch(r"(\d+)\s*:\s*(\d+)", text)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def had_outcome(gh: int, ga: int) -> str:
    return "H" if gh > ga else "A" if gh < ga else "D"


def hhad_outcome(gh: int, ga: int, line: Optional[float]) -> Optional[str]:
    if line is None:
        return None
    adj = gh + line - ga
    if abs(adj) < 1e-9:
        return "D"
    return "H" if adj > 0 else "A"


def hafu_role(gh: int, ga: int) -> str:
    return had_outcome(gh, ga)


def flatten_fivepool_matches(payload: Mapping[str, Any], meta: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    value = payload.get("value") or {}
    groups = value.get("matchInfoList") or []
    fetched_dt = parse_dt(meta.get("fetched_at_local"), SH_TZ)
    source_last_update = value.get("lastUpdateTime")
    match_rows: List[Dict[str, Any]] = []
    quote_rows: List[Dict[str, Any]] = []

    for group in groups:
        for sm in group.get("subMatchList") or []:
            upstream_match_id = str(sm.get("matchId") or "").strip()
            match_num_str = str(sm.get("matchNumStr") or "").strip()
            match_num_date = str(sm.get("matchNumDate") or group.get("matchNumDate") or "").strip()
            business_date = str(sm.get("businessDate") or group.get("businessDate") or "").strip()
            kickoff = parse_kickoff(sm)
            t_minus_min = None
            if fetched_dt and kickoff:
                t_minus_min = (kickoff - fetched_dt).total_seconds() / 60.0

            pools_raw: Dict[str, Optional[Mapping[str, Any]]] = {
                p: sm.get(p) if isinstance(sm.get(p), dict) else None for p in POOLS
            }
            presence = {p: bool(pools_raw[p]) for p in POOLS}
            pool_count = sum(presence.values())

            hhad = pools_raw["hhad"] or {}
            line = hhad.get("goalLineValue") or hhad.get("goalLine")
            try:
                line_num = float(line) if str(line).strip() else None
            except Exception:
                line_num = None

            had = pools_raw["had"] or {}
            row = {
                "capture_time_local": iso_local(fetched_dt),
                "capture_time_utc": iso_utc(fetched_dt),
                "source_last_update": source_last_update,
                "raw_file": meta.get("path"),
                "raw_sha256": meta.get("sha256"),
                "match_id": upstream_match_id,
                "match_num_str": match_num_str,
                "match_num": sm.get("matchNum"),
                "match_num_date": match_num_date,
                "business_date": business_date,
                "match_date": sm.get("matchDate"),
                "match_time": sm.get("matchTime"),
                "kickoff_local": iso_local(kickoff),
                "kickoff_utc": iso_utc(kickoff),
                "t_minus_min": round(t_minus_min, 3) if t_minus_min is not None else None,
                "league_id": sm.get("leagueId"),
                "league": sm.get("leagueAbbName") or sm.get("leagueAllName"),
                "home_team_id": sm.get("homeTeamId"),
                "away_team_id": sm.get("awayTeamId"),
                "home_team": sm.get("homeTeamAbbName") or sm.get("homeTeamAllName"),
                "away_team": sm.get("awayTeamAbbName") or sm.get("awayTeamAllName"),
                "home_rank": sm.get("homeRank"),
                "away_rank": sm.get("awayRank"),
                "match_status": sm.get("matchStatus"),
                "sell_status": sm.get("sellStatus"),
                "had_present": int(presence["had"]),
                "hhad_present": int(presence["hhad"]),
                "crs_present": int(presence["crs"]),
                "ttg_present": int(presence["ttg"]),
                "hafu_present": int(presence["hafu"]),
                "pool_count": pool_count,
                "five_pool_complete": int(pool_count == 5),
                "had_h": safe_float(had.get("h")),
                "had_d": safe_float(had.get("d")),
                "had_a": safe_float(had.get("a")),
                "had_update_local": parse_pool_update(pools_raw["had"]),
                "hhad_line": line_num,
                "hhad_h": safe_float(hhad.get("h")),
                "hhad_d": safe_float(hhad.get("d")),
                "hhad_a": safe_float(hhad.get("a")),
                "hhad_update_local": parse_pool_update(pools_raw["hhad"]),
                "crs_update_local": parse_pool_update(pools_raw["crs"]),
                "ttg_update_local": parse_pool_update(pools_raw["ttg"]),
                "hafu_update_local": parse_pool_update(pools_raw["hafu"]),
            }
            match_rows.append(row)

            base = {
                "capture_time_local": row["capture_time_local"],
                "capture_time_utc": row["capture_time_utc"],
                "raw_file": row["raw_file"],
                "match_id": upstream_match_id,
                "match_num_str": match_num_str,
                "kickoff_local": row["kickoff_local"],
                "t_minus_min": row["t_minus_min"],
                "league": row["league"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
            }

            # HAD / HHAD
            for p in ("had", "hhad"):
                dct = pools_raw[p] or {}
                p_line = None
                if p == "hhad":
                    p_line = line_num
                for sel, key in (("H","h"),("D","d"),("A","a")):
                    price = safe_float(dct.get(key))
                    if price is not None:
                        quote_rows.append({**base, "pool": p.upper(), "selection": sel, "price": price,
                                           "goal_line": p_line, "pool_update_local": parse_pool_update(dct)})

            # TTG
            dct = pools_raw["ttg"] or {}
            for k, v in dct.items():
                sel = ttg_selection(str(k))
                price = safe_float(v)
                if sel and price is not None:
                    quote_rows.append({**base, "pool": "TTG", "selection": sel, "price": price,
                                       "goal_line": None, "pool_update_local": parse_pool_update(dct)})

            # CRS
            dct = pools_raw["crs"] or {}
            for k, v in dct.items():
                sel = score_selection(str(k))
                price = safe_float(v)
                if sel and price is not None:
                    quote_rows.append({**base, "pool": "CRS", "selection": sel, "price": price,
                                       "goal_line": None, "pool_update_local": parse_pool_update(dct)})

            # HAFU
            dct = pools_raw["hafu"] or {}
            for k, v in dct.items():
                sel = hafu_selection(str(k))
                price = safe_float(v)
                if sel and price is not None:
                    quote_rows.append({**base, "pool": "HAFU", "selection": sel, "price": price,
                                       "goal_line": None, "pool_update_local": parse_pool_update(dct)})

    return match_rows, quote_rows


def flatten_results(payload: Mapping[str, Any], meta: Mapping[str, Any]) -> List[Dict[str, Any]]:
    value = payload.get("value") or {}
    fetched_dt = parse_dt(meta.get("fetched_at_local"), SH_TZ)
    rows: List[Dict[str, Any]] = []
    for r in value.get("matchResult") or []:
        ft_h, ft_a = split_score(r.get("sectionsNo999"))
        ht_h, ht_a = split_score(r.get("sectionsNo1"))
        rows.append({
            "result_capture_time_local": iso_local(fetched_dt),
            "result_capture_time_utc": iso_utc(fetched_dt),
            "result_source_last_update": value.get("lastUpdateTime"),
            "raw_file": meta.get("path"),
            "raw_sha256": meta.get("sha256"),
            "page_no": value.get("pageNo") or meta.get("outer", {}).get("page_no"),
            "match_id": str(r.get("matchId") or "").strip(),
            "match_num_str": r.get("matchNumStr"),
            "match_num": r.get("matchNum"),
            "match_date": r.get("matchDate"),
            "league_id": r.get("leagueId"),
            "league": r.get("leagueNameAbbr") or r.get("leagueName"),
            "home_team_id": r.get("homeTeamId"),
            "away_team_id": r.get("awayTeamId"),
            "home_team": r.get("homeTeam") or r.get("allHomeTeam"),
            "away_team": r.get("awayTeam") or r.get("allAwayTeam"),
            "result_status": r.get("matchResultStatus"),
            "pool_status": r.get("poolStatus"),
            "win_flag": r.get("winFlag"),
            "result_goal_line": r.get("goalLine"),
            "ht_score": r.get("sectionsNo1"),
            "ft_score": r.get("sectionsNo999"),
            "ht_home": ht_h,
            "ht_away": ht_a,
            "ft_home": ft_h,
            "ft_away": ft_a,
            "settled": int(ft_h is not None and ft_a is not None),
        })
    return rows


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: List[str] = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k); keys.append(k)
        fieldnames = keys
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def dedupe_snapshot_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in rows:
        key = (str(r.get("match_id") or ""), str(r.get("capture_time_local") or ""))
        # Prefer the row with more pools if duplicate collector files exist.
        if key not in best or int(r.get("pool_count") or 0) > int(best[key].get("pool_count") or 0):
            best[key] = r
    return sorted(best.values(), key=lambda r: (r.get("capture_time_local") or "", r.get("match_id") or ""))


def dedupe_quotes(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set(); out=[]
    for r in rows:
        key=(r.get("match_id"),r.get("capture_time_local"),r.get("pool"),r.get("selection"),r.get("goal_line"))
        if key in seen: continue
        seen.add(key); out.append(r)
    return sorted(out, key=lambda r:(r.get("capture_time_local") or "", r.get("match_id") or "", r.get("pool") or "", r.get("selection") or ""))


def latest_results(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    def rank(r: Dict[str, Any]) -> Tuple[int, str]:
        return (int(r.get("settled") or 0), str(r.get("result_capture_time_local") or ""))
    for r in rows:
        mid=str(r.get("match_id") or "")
        if not mid: continue
        if mid not in best or rank(r) > rank(best[mid]): best[mid]=r
    return sorted(best.values(), key=lambda r:(r.get("match_date") or "", r.get("match_num") or ""))


def derive_labels(snapshot: Mapping[str, Any], result: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not result or not result.get("settled"):
        return {
            "label_settled": 0, "label_had": None, "label_hhad": None, "label_crs": None,
            "label_ttg": None, "label_hafu": None, "label_ht_score": None, "label_ft_score": None,
        }
    gh,ga=int(result["ft_home"]),int(result["ft_away"])
    hh,ha=result.get("ht_home"),result.get("ht_away")
    ttg=gh+ga
    label_ttg="7+" if ttg>=7 else str(ttg)
    label_hafu=None
    if hh is not None and ha is not None:
        label_hafu=hafu_role(int(hh),int(ha))+had_outcome(gh,ga)
    return {
        "label_settled":1,
        "label_had":had_outcome(gh,ga),
        "label_hhad":hhad_outcome(gh,ga,snapshot.get("hhad_line")),
        "label_crs":f"{gh}-{ga}",
        "label_ttg":label_ttg,
        "label_hafu":label_hafu,
        "label_ht_score":result.get("ht_score"),
        "label_ft_score":result.get("ft_score"),
    }


def build_join(snapshots: Sequence[Dict[str, Any]], results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rmap={str(r.get("match_id")):r for r in results}
    out=[]
    for s in snapshots:
        r=rmap.get(str(s.get("match_id")))
        x=dict(s)
        x.update(derive_labels(s,r))
        if r:
            x["result_capture_time_local"]=r.get("result_capture_time_local")
            x["result_status"]=r.get("result_status")
            x["pool_status"]=r.get("pool_status")
        else:
            x["result_capture_time_local"]=None; x["result_status"]=None; x["pool_status"]=None
        out.append(x)
    return out


def select_anchor_candidates(snapshots: Sequence[Dict[str, Any]], anchors: Sequence[int]=ANCHORS_MIN, close_buffer_min: int=2) -> List[Dict[str, Any]]:
    by_match: Dict[str, List[Dict[str, Any]]] = {}
    for s in snapshots:
        if not s.get("capture_time_local") or not s.get("kickoff_local"):
            continue
        by_match.setdefault(str(s.get("match_id")), []).append(s)
    out=[]
    targets=list(anchors)+[close_buffer_min]
    labels=[f"T-{m}m" for m in anchors]+["CLOSE"]
    for mid,rows in by_match.items():
        rows=sorted(rows,key=lambda r:r["capture_time_local"])
        kickoff=parse_dt(rows[-1]["kickoff_local"],SH_TZ)
        if not kickoff: continue
        for label,lead in zip(labels,targets):
            target=kickoff.timestamp()-lead*60
            eligible=[]
            for r in rows:
                c=parse_dt(r["capture_time_local"],SH_TZ)
                if c and c.timestamp()<=target:
                    eligible.append((c,r))
            if not eligible: continue
            c,r=eligible[-1]
            anchor_time=datetime.fromtimestamp(target,tz=kickoff.tzinfo)
            stale=(anchor_time-c).total_seconds()/60.0
            out.append({
                "match_id":mid,"match_num_str":r.get("match_num_str"),"league":r.get("league"),
                "home_team":r.get("home_team"),"away_team":r.get("away_team"),
                "kickoff_local":r.get("kickoff_local"),"anchor":label,"target_lead_min":lead,
                "selected_capture_time_local":r.get("capture_time_local"),
                "selected_t_minus_min":r.get("t_minus_min"),"staleness_min":round(stale,3),
                "had_h":r.get("had_h"),"had_d":r.get("had_d"),"had_a":r.get("had_a"),
                "hhad_line":r.get("hhad_line"),"hhad_h":r.get("hhad_h"),"hhad_d":r.get("hhad_d"),"hhad_a":r.get("hhad_a"),
                "pool_count":r.get("pool_count"),"five_pool_complete":r.get("five_pool_complete"),
                "raw_file":r.get("raw_file"),
            })
    return out


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS collector_run(
  raw_sha256 TEXT PRIMARY KEY, raw_file TEXT NOT NULL, endpoint TEXT, fetched_at_local TEXT,
  valid INTEGER NOT NULL, reason TEXT
);
CREATE TABLE IF NOT EXISTS match_dim(
  match_id TEXT PRIMARY KEY, match_num_str TEXT, match_num TEXT, league_id TEXT, league TEXT,
  home_team_id TEXT, away_team_id TEXT, home_team TEXT, away_team TEXT, kickoff_local TEXT
);
CREATE TABLE IF NOT EXISTS market_snapshot(
  match_id TEXT NOT NULL, capture_time_local TEXT NOT NULL, capture_time_utc TEXT,
  raw_sha256 TEXT NOT NULL, t_minus_min REAL, pool_count INTEGER, five_pool_complete INTEGER,
  had_h REAL, had_d REAL, had_a REAL, hhad_line REAL, hhad_h REAL, hhad_d REAL, hhad_a REAL,
  had_update_local TEXT, hhad_update_local TEXT, crs_update_local TEXT, ttg_update_local TEXT, hafu_update_local TEXT,
  PRIMARY KEY(match_id,capture_time_local)
);
CREATE TABLE IF NOT EXISTS market_quote(
  match_id TEXT NOT NULL, capture_time_local TEXT NOT NULL, pool TEXT NOT NULL, selection TEXT NOT NULL,
  goal_line REAL, price REAL NOT NULL, pool_update_local TEXT,
  PRIMARY KEY(match_id,capture_time_local,pool,selection,goal_line)
);
CREATE TABLE IF NOT EXISTS result_latest(
  match_id TEXT PRIMARY KEY, result_capture_time_local TEXT, result_source_last_update TEXT,
  match_num_str TEXT, match_date TEXT, league TEXT, home_team TEXT, away_team TEXT,
  result_status TEXT, pool_status TEXT, win_flag TEXT, result_goal_line TEXT,
  ht_score TEXT, ft_score TEXT, ht_home INTEGER, ht_away INTEGER, ft_home INTEGER, ft_away INTEGER, settled INTEGER
);
"""


def build_sqlite(db_path: Path, files_meta: Sequence[Dict[str, Any]], snapshots: Sequence[Dict[str, Any]], quotes: Sequence[Dict[str, Any]], results: Sequence[Dict[str, Any]]) -> None:
    if db_path.exists(): db_path.unlink()
    con=sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    for m in files_meta:
        con.execute("INSERT OR REPLACE INTO collector_run VALUES(?,?,?,?,?,?)",(
            m.get("sha256") or sha256_file(Path(m["path"])),m.get("path"),m.get("endpoint"),m.get("fetched_at_local"),int(bool(m.get("valid"))),m.get("reason")))
    for s in snapshots:
        con.execute("""INSERT OR REPLACE INTO match_dim(match_id,match_num_str,match_num,league_id,league,home_team_id,away_team_id,home_team,away_team,kickoff_local)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",(
            s.get("match_id"),s.get("match_num_str"),str(s.get("match_num") or ""),str(s.get("league_id") or ""),s.get("league"),
            str(s.get("home_team_id") or ""),str(s.get("away_team_id") or ""),s.get("home_team"),s.get("away_team"),s.get("kickoff_local")))
        con.execute("""INSERT OR REPLACE INTO market_snapshot(match_id,capture_time_local,capture_time_utc,raw_sha256,t_minus_min,pool_count,five_pool_complete,
                       had_h,had_d,had_a,hhad_line,hhad_h,hhad_d,hhad_a,had_update_local,hhad_update_local,crs_update_local,ttg_update_local,hafu_update_local)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            s.get("match_id"),s.get("capture_time_local"),s.get("capture_time_utc"),s.get("raw_sha256"),s.get("t_minus_min"),s.get("pool_count"),s.get("five_pool_complete"),
            s.get("had_h"),s.get("had_d"),s.get("had_a"),s.get("hhad_line"),s.get("hhad_h"),s.get("hhad_d"),s.get("hhad_a"),s.get("had_update_local"),s.get("hhad_update_local"),s.get("crs_update_local"),s.get("ttg_update_local"),s.get("hafu_update_local")))
    for q in quotes:
        con.execute("""INSERT OR REPLACE INTO market_quote(match_id,capture_time_local,pool,selection,goal_line,price,pool_update_local)
                       VALUES(?,?,?,?,?,?,?)""",(q.get("match_id"),q.get("capture_time_local"),q.get("pool"),q.get("selection"),q.get("goal_line"),q.get("price"),q.get("pool_update_local")))
    for r in results:
        con.execute("""INSERT OR REPLACE INTO result_latest(match_id,result_capture_time_local,result_source_last_update,match_num_str,match_date,league,home_team,away_team,
                       result_status,pool_status,win_flag,result_goal_line,ht_score,ft_score,ht_home,ht_away,ft_home,ft_away,settled)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            r.get("match_id"),r.get("result_capture_time_local"),r.get("result_source_last_update"),r.get("match_num_str"),r.get("match_date"),r.get("league"),r.get("home_team"),r.get("away_team"),
            str(r.get("result_status") or ""),r.get("pool_status"),r.get("win_flag"),r.get("result_goal_line"),r.get("ht_score"),r.get("ft_score"),r.get("ht_home"),r.get("ht_away"),r.get("ft_home"),r.get("ft_away"),r.get("settled")))
    con.commit(); con.close()


def discover_files(input_dir: Path, explicit: Sequence[str]) -> List[Path]:
    paths: List[Path]=[]
    if input_dir:
        paths.extend(sorted(input_dir.glob("sporttery_5pools_*.json")))
        paths.extend(sorted(input_dir.glob("sporttery_results_*.json")))
    for x in explicit:
        p=Path(x)
        if p.is_file(): paths.append(p)
    # preserve unique resolved path
    seen=set(); out=[]
    for p in paths:
        rp=str(p.resolve())
        if rp not in seen:
            seen.add(rp); out.append(p)
    return out


def build(input_dir: Path, output_dir: Path, explicit: Sequence[str]) -> Dict[str, Any]:
    files=discover_files(input_dir, explicit)
    output_dir.mkdir(parents=True, exist_ok=True)
    metas=[]; snapshots=[]; quotes=[]; result_history=[]
    invalid=[]
    for p in files:
        meta=load_collector_file(p)
        metas.append(meta)
        if not meta.get("valid"):
            invalid.append({"file":str(p),"reason":meta.get("reason")})
            continue
        payload=meta["payload"]
        endpoint=meta.get("endpoint")
        # fallback by filename / payload shape
        if endpoint == "calculator" or "5pools" in p.name or ((payload.get("value") or {}).get("matchInfoList") is not None):
            a,b=flatten_fivepool_matches(payload,meta); snapshots.extend(a); quotes.extend(b)
        elif endpoint == "results" or "results" in p.name or ((payload.get("value") or {}).get("matchResult") is not None):
            result_history.extend(flatten_results(payload,meta))

    snapshots=dedupe_snapshot_rows(snapshots)
    quotes=dedupe_quotes(quotes)
    results=latest_results(result_history)
    joined=build_join(snapshots,results)
    anchors=select_anchor_candidates(snapshots)

    write_csv(output_dir/"snapshot_matches.csv",snapshots)
    write_csv(output_dir/"odds_long.csv",quotes)
    write_csv(output_dir/"results_latest.csv",results)
    write_csv(output_dir/"snapshot_result_join.csv",joined)
    write_csv(output_dir/"anchor_candidates.csv",anchors)
    build_sqlite(output_dir/"stage3.db",metas,snapshots,quotes,results)

    mids={s["match_id"] for s in snapshots if s.get("match_id")}
    settled={r["match_id"] for r in results if r.get("settled")}
    complete=sum(int(s.get("five_pool_complete") or 0) for s in snapshots)
    had_missing=sum(1 for s in snapshots if not s.get("had_present"))
    tmins=[s.get("t_minus_min") for s in snapshots if isinstance(s.get("t_minus_min"),(int,float))]
    manifest={
        "parser_builder_version":VERSION,
        "governance":{
            "staking_baseline":"Jingcai 3.4",
            "jingcai_3_5_status":"Alpha Stage3 data/research only",
            "external_positive_fusion":"inactive",
            "residual_ml_dynamic_xg_score_fusion":"shadow_only",
        },
        "inputs":{"files_discovered":len(files),"valid_files":sum(1 for m in metas if m.get("valid")),"invalid_files":invalid},
        "outputs":{
            "snapshot_rows":len(snapshots),"unique_snapshot_matches":len(mids),"quote_rows":len(quotes),
            "result_history_rows":len(result_history),"latest_results":len(results),"settled_latest_results":sum(int(r.get("settled") or 0) for r in results),
            "snapshot_rows_with_final_label":sum(1 for j in joined if j.get("label_settled")),"anchor_candidate_rows":len(anchors),
        },
        "coverage":{
            "five_pool_complete_rows":complete,
            "five_pool_complete_rate":(complete/len(snapshots) if snapshots else None),
            "had_missing_rows_preserved":had_missing,
            "snapshot_match_ids_with_settled_result":len(mids & settled),
            "snapshot_match_result_link_rate":(len(mids & settled)/len(mids) if mids else None),
            "min_t_minus_min":min(tmins) if tmins else None,
            "max_t_minus_min":max(tmins) if tmins else None,
        },
        "notes":[
            "All five-pool matches are preserved even when HAD is absent.",
            "Numeric upstream matchId is the canonical join key; matchNumStr is retained for display.",
            "Final result fields are labels only and must never be used as pre-match features.",
            "Anchor candidates are leakage-safe selections at or before each target; staleness is exported, not silently accepted.",
        ],
    }
    (output_dir/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return manifest


def self_test() -> None:
    # Core settlement logic
    assert had_outcome(2,1)=="H" and had_outcome(1,1)=="D" and had_outcome(0,1)=="A"
    assert hhad_outcome(2,1,-1)=="D"
    assert hhad_outcome(3,1,-1)=="H"
    assert hhad_outcome(1,1,-1)=="A"
    assert score_selection("s02s01")=="2-1" and score_selection("s1sh")=="H_OTHER"
    assert ttg_selection("s7")=="7+"
    print(json.dumps({"self_test":"PASS","version":VERSION},ensure_ascii=False))


def main() -> None:
    ap=argparse.ArgumentParser(description="Jingcai Stage 3 Parser / Builder v1")
    sub=ap.add_subparsers(dest="cmd",required=True)
    x=sub.add_parser("build",help="build normalized Stage 3 dataset from Collector JSON files")
    x.add_argument("--input-dir",default=".")
    x.add_argument("--output-dir",required=True)
    x.add_argument("--input",action="append",default=[],help="extra explicit JSON file; repeatable")
    sub.add_parser("self-test")
    args=ap.parse_args()
    if args.cmd=="self-test": self_test(); return
    m=build(Path(args.input_dir),Path(args.output_dir),args.input)
    print(json.dumps(m,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
