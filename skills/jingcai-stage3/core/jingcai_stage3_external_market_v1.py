#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jingcai Stage 3 External Market Bridge v1
=========================================

Purpose
-------
Normalize multi-bookmaker external market snapshots and align them to the
Sporttery Stage 3 dataset without leaking future information.

Governance
----------
* Jingcai 3.4 remains the real-money / staking baseline.
* Jingcai 3.5 remains Alpha Stage 3 data/research only.
* External markets are for network verification, disagreement detection,
  overheating detection and Risk Gate research.
* External positive probability fusion into the HAD production probability
  remains INACTIVE.
* This bridge must never modify S/A1/A2 grades by itself.

Supported external inputs
-------------------------
1) Canonical Stage 3 external snapshot envelope (recommended):
   {
     "provider": "the_odds_api",
     "fetched_at_utc": "2026-08-17T02:00:00Z",
     "data": [ ...provider event objects... ]
   }
2) The Odds API-style current array: [event, ...]
3) The Odds API historical wrapper: {"timestamp": ..., "data": [event, ...]}
4) Canonical CSV (see README/schema template).

Main outputs
------------
* external_events.csv
* external_quotes_long.csv
* external_match_map.csv
* external_consensus_1x2.csv
* market_risk_gate_candidates.csv
* external_close_1x2.csv
* clv_reference_join.csv
* unmatched_external_events.csv
* manifest_external.json
* stage3.db augmented with external_* tables (optional)

Python 3.10+, standard library only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import sqlite3
import statistics
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

VERSION = "1.0.0"
UTC = timezone.utc
SH_TZ = ZoneInfo("Asia/Shanghai")
OUTCOMES = ("H", "D", "A")

# Candidate diagnostics only. They are exported for later strict OOS validation;
# they are NOT frozen production thresholds and do not change grades.
DEFAULT_MAX_MATCH_TIME_DIFF_MIN = 180.0
DEFAULT_MAX_EXTERNAL_STALENESS_MIN = 360.0
DEFAULT_DIVERGENCE_PP = 4.0
DEFAULT_OVERHEAT_PP = 4.0
DEFAULT_MIN_BOOKMAKERS = 3
DEFAULT_CLOSE_BUFFER_MIN = 2.0


def parse_dt(v: Any, default_tz=UTC) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        # The Odds API can return unix timestamps when requested.
        try:
            return datetime.fromtimestamp(float(v), tz=UTC)
        except Exception:
            return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
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


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_local(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(SH_TZ).replace(microsecond=0).isoformat()


def safe_float(v: Any) -> Optional[float]:
    try:
        x = float(v)
        if not math.isfinite(x) or x <= 0:
            return None
        return x
    except Exception:
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        names: List[str] = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    names.append(k)
        fieldnames = names
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames or []), extrasaction="ignore")
        if fieldnames:
            w.writeheader()
            for r in rows:
                w.writerow(r)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


# ------------------------ de-vig ------------------------------------------

def devig_mult(odds: Sequence[float]) -> Optional[Tuple[float, ...]]:
    vals = [safe_float(x) for x in odds]
    if any(v is None for v in vals):
        return None
    inv = [1.0 / float(v) for v in vals]  # type: ignore[arg-type]
    z = sum(inv)
    if z <= 0:
        return None
    return tuple(x / z for x in inv)


def devig_power(odds: Sequence[float]) -> Optional[Tuple[float, ...]]:
    vals = [safe_float(x) for x in odds]
    if any(v is None for v in vals):
        return None
    q = [1.0 / float(v) for v in vals]  # type: ignore[arg-type]
    # Solve sum(q_i ** k) = 1 by bisection. q_i in (0,1) for decimal odds > 1.
    lo, hi = 0.01, 20.0
    def f(k: float) -> float:
        return sum(x ** k for x in q) - 1.0
    # If an edge case does not bracket, fall back to multiplicative.
    if f(lo) < 0 or f(hi) > 0:
        return devig_mult(odds)
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    p = [x ** k for x in q]
    z = sum(p)
    return tuple(x / z for x in p)


def devig_blend(odds: Sequence[float]) -> Optional[Dict[str, Tuple[float, ...]]]:
    m = devig_mult(odds)
    p = devig_power(odds)
    if not m or not p:
        return None
    b = tuple((m[i] + p[i]) / 2.0 for i in range(len(m)))
    z = sum(b)
    b = tuple(x / z for x in b)
    return {"mult": m, "power": p, "blend": b}


# ------------------------ team normalization -----------------------------

STOP_TOKENS = {
    "fc", "cf", "afc", "sc", "ac", "fk", "sk", "bk", "if", "club", "football", "futbol",
    "de", "the", "calcio", "futebol", "fussball", "sv", "nk", "ks", "cd", "sd",
}


def normalize_team_name(name: Any) -> str:
    s = unicodedata.normalize("NFKC", str(name or "")).lower().strip()
    s = s.replace("&", " and ")
    s = re.sub(r"[\.'’`´\-_/\\()\[\],:;]+", " ", s)
    s = re.sub(r"\b(u\s*\d{2}|women|w|reserves?|ii|b)\b", " ", s)
    toks = [t for t in re.split(r"\s+", s) if t and t not in STOP_TOKENS]
    return "".join(toks)


def load_aliases(path: Optional[Path]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    if not path or not path.exists():
        return aliases
    if path.suffix.lower() == ".json":
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(obj, dict):
            for k, v in obj.items():
                aliases[normalize_team_name(k)] = normalize_team_name(v)
        return aliases
    for r in read_csv(path):
        a = r.get("alias") or r.get("external_team") or r.get("from")
        c = r.get("canonical") or r.get("sporttery_team") or r.get("to")
        if a and c:
            aliases[normalize_team_name(a)] = normalize_team_name(c)
    return aliases


def canon_team(name: Any, aliases: Mapping[str, str]) -> str:
    n = normalize_team_name(name)
    return aliases.get(n, n)


def token_similarity(a: str, b: str) -> float:
    if a == b and a:
        return 1.0
    if not a or not b:
        return 0.0
    # Character bigram Jaccard works reasonably across compact normalized names.
    def grams(x: str) -> set[str]:
        if len(x) <= 2:
            return {x}
        return {x[i:i+2] for i in range(len(x)-1)}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / max(1, len(ga | gb))


# ------------------------ external parsing -------------------------------

def infer_capture_time(path: Path, obj: Any) -> Tuple[Optional[datetime], str]:
    if isinstance(obj, dict):
        for k in ("fetched_at_utc", "capture_time_utc", "snapshot_time", "timestamp"):
            dt = parse_dt(obj.get(k), UTC)
            if dt:
                return dt, k
    # Common filename stamps: 20260817_031434 or 20260817T031434Z
    m = re.search(r"(20\d{6})[_T-]?(\d{6})(Z)?", path.name)
    if m:
        try:
            dt = datetime.strptime(m.group(1)+m.group(2), "%Y%m%d%H%M%S")
            dt = dt.replace(tzinfo=UTC if m.group(3) else SH_TZ)
            return dt.astimezone(UTC), "filename"
        except Exception:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC), "file_mtime"


def parse_the_odds_api_file(path: Path, provider_default="the_odds_api") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    capture, capture_source = infer_capture_time(path, obj)
    provider = provider_default
    events_obj: Any = obj
    if isinstance(obj, dict):
        provider = str(obj.get("provider") or provider_default)
        events_obj = obj.get("data") if isinstance(obj.get("data"), list) else obj.get("events", obj)
    if not isinstance(events_obj, list):
        return [], [], {"file": str(path), "valid": False, "reason": "no event array", "provider": provider}

    events: List[Dict[str, Any]] = []
    quotes: List[Dict[str, Any]] = []
    for ev in events_obj:
        if not isinstance(ev, dict):
            continue
        event_id = str(ev.get("id") or ev.get("event_id") or "").strip()
        home = str(ev.get("home_team") or "").strip()
        away = str(ev.get("away_team") or "").strip()
        commence = parse_dt(ev.get("commence_time"), UTC)
        sport_key = ev.get("sport_key")
        sport_title = ev.get("sport_title")
        if not event_id or not home or not away:
            continue
        base_ev = {
            "provider": provider,
            "capture_time_utc": iso_utc(capture),
            "capture_time_local": iso_local(capture),
            "capture_time_source": capture_source,
            "external_event_id": event_id,
            "sport_key": sport_key,
            "sport_title": sport_title,
            "commence_time_utc": iso_utc(commence),
            "commence_time_local": iso_local(commence),
            "home_team_external": home,
            "away_team_external": away,
            "raw_file": str(path),
            "raw_sha256": sha256_file(path),
        }
        events.append(base_ev)
        for book in ev.get("bookmakers") or []:
            if not isinstance(book, dict):
                continue
            bookmaker_key = str(book.get("key") or "").strip()
            bookmaker = str(book.get("title") or bookmaker_key or "unknown").strip()
            book_update = parse_dt(book.get("last_update"), UTC)
            for market in book.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                market_key = str(market.get("key") or "").strip()
                market_update = parse_dt(market.get("last_update"), UTC) or book_update
                for out in market.get("outcomes") or []:
                    if not isinstance(out, dict):
                        continue
                    name = str(out.get("name") or "").strip()
                    desc = str(out.get("description") or "").strip()
                    price = safe_float(out.get("price"))
                    point_raw = out.get("point")
                    point = None
                    try:
                        if point_raw is not None and str(point_raw).strip() != "":
                            point = float(point_raw)
                    except Exception:
                        pass
                    if price is None:
                        continue
                    selection = name
                    market_norm = market_key.upper()
                    if market_key in ("h2h", "h2h_3_way"):
                        market_norm = "1X2"
                        if name == home:
                            selection = "H"
                        elif name == away:
                            selection = "A"
                        elif name.lower() == "draw":
                            selection = "D"
                    elif market_key == "spreads":
                        market_norm = "SPREAD"
                        if name == home:
                            selection = "H"
                        elif name == away:
                            selection = "A"
                    elif market_key == "totals":
                        market_norm = "TOTAL"
                        selection = name.upper()
                    elif market_key == "btts":
                        market_norm = "BTTS"
                        selection = name.upper()
                    elif market_key == "correct_score":
                        market_norm = "CRS"
                        selection = name or desc
                    elif market_key == "halftime_fulltime":
                        market_norm = "HAFU"
                        selection = name or desc
                    quotes.append({
                        **base_ev,
                        "bookmaker_key": bookmaker_key,
                        "bookmaker": bookmaker,
                        "bookmaker_last_update_utc": iso_utc(book_update),
                        "market_last_update_utc": iso_utc(market_update),
                        "market_key_raw": market_key,
                        "market": market_norm,
                        "selection": selection,
                        "selection_raw": name,
                        "description": desc,
                        "line": point,
                        "price": price,
                    })
    return events, quotes, {"file": str(path), "valid": True, "provider": provider, "capture_time_utc": iso_utc(capture), "capture_time_source": capture_source}


def parse_canonical_csv(path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    rows = read_csv(path)
    events_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    quotes: List[Dict[str, Any]] = []
    for r in rows:
        event_id = str(r.get("external_event_id") or r.get("event_id") or "").strip()
        if not event_id:
            continue
        capture = parse_dt(r.get("capture_time_utc") or r.get("capture_time_local"), UTC)
        commence = parse_dt(r.get("commence_time_utc") or r.get("commence_time_local"), UTC)
        provider = str(r.get("provider") or "canonical_csv")
        home = str(r.get("home_team_external") or r.get("home_team") or "")
        away = str(r.get("away_team_external") or r.get("away_team") or "")
        ev = {
            "provider": provider,
            "capture_time_utc": iso_utc(capture),
            "capture_time_local": iso_local(capture),
            "capture_time_source": "csv",
            "external_event_id": event_id,
            "sport_key": r.get("sport_key"),
            "sport_title": r.get("sport_title"),
            "commence_time_utc": iso_utc(commence),
            "commence_time_local": iso_local(commence),
            "home_team_external": home,
            "away_team_external": away,
            "raw_file": str(path),
            "raw_sha256": sha256_file(path),
        }
        events_map[(provider, event_id, ev["capture_time_utc"] or "")] = ev
        q = dict(ev)
        q.update({
            "bookmaker_key": r.get("bookmaker_key") or r.get("bookmaker"),
            "bookmaker": r.get("bookmaker") or r.get("bookmaker_key"),
            "bookmaker_last_update_utc": r.get("bookmaker_last_update_utc"),
            "market_last_update_utc": r.get("market_last_update_utc"),
            "market_key_raw": r.get("market_key_raw") or r.get("market"),
            "market": str(r.get("market") or "").upper(),
            "selection": r.get("selection"),
            "selection_raw": r.get("selection_raw") or r.get("selection"),
            "description": r.get("description"),
            "line": float(r["line"]) if str(r.get("line") or "").strip() else None,
            "price": safe_float(r.get("price")),
        })
        if q["price"] is not None:
            quotes.append(q)
    return list(events_map.values()), quotes, {"file": str(path), "valid": True, "provider": "canonical_csv"}


def discover_external(input_dir: Path, explicit: Sequence[str]) -> List[Path]:
    paths: List[Path] = []
    if input_dir and input_dir.exists():
        for pat in ("*.json", "*.csv"):
            paths.extend(sorted(input_dir.glob(pat)))
    for x in explicit:
        p = Path(x)
        if p.is_file():
            paths.append(p)
    out: List[Path] = []
    seen = set()
    for p in paths:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


# ------------------------ mapping ----------------------------------------

def load_sporttery_matches(stage3_dir: Path) -> List[Dict[str, Any]]:
    p = stage3_dir / "snapshot_matches.csv"
    if not p.exists():
        raise FileNotFoundError(f"missing {p}")
    rows = read_csv(p)
    # One dimension row per match; prefer earliest available metadata snapshot.
    best: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        mid = str(r.get("match_id") or "")
        if mid and mid not in best:
            best[mid] = r
    return list(best.values())


def load_league_aliases(path: Optional[Path]) -> Dict[Tuple[str, str], Dict[str, str]]:
    out: Dict[Tuple[str, str], Dict[str, str]] = {}
    if not path or not path.exists():
        return out
    for r in read_csv(path):
        provider = str(r.get("provider") or "").strip()
        sport_key = str(r.get("sport_key") or "").strip()
        if provider and sport_key:
            out[(provider, sport_key)] = {
                "league_id": str(r.get("sporttery_league_id") or "").strip(),
                "league": str(r.get("sporttery_league") or "").strip(),
            }
    return out


def map_external_events(events: Sequence[Dict[str, Any]], sporttery_matches: Sequence[Dict[str, Any]], aliases: Mapping[str, str], league_aliases: Mapping[Tuple[str, str], Dict[str, str]], max_time_diff_min: float) -> List[Dict[str, Any]]:
    dims = []
    for s in sporttery_matches:
        dims.append({
            **s,
            "home_norm": canon_team(s.get("home_team"), aliases),
            "away_norm": canon_team(s.get("away_team"), aliases),
            "kickoff_dt": parse_dt(s.get("kickoff_utc") or s.get("kickoff_local"), SH_TZ),
        })
    rows: List[Dict[str, Any]] = []
    seen_event_capture = set()
    for ev in events:
        key = (ev.get("provider"), ev.get("external_event_id"), ev.get("capture_time_utc"))
        if key in seen_event_capture:
            continue
        seen_event_capture.add(key)
        eh = canon_team(ev.get("home_team_external"), aliases)
        ea = canon_team(ev.get("away_team_external"), aliases)
        et = parse_dt(ev.get("commence_time_utc") or ev.get("commence_time_local"), UTC)
        league_rule = league_aliases.get((str(ev.get("provider") or ""), str(ev.get("sport_key") or "")))
        candidates = dims
        if league_rule:
            lid = str(league_rule.get("league_id") or "")
            lname = str(league_rule.get("league") or "")
            candidates = [s for s in dims if (lid and str(s.get("league_id") or "") == lid) or (lname and str(s.get("league") or "") == lname)]
        timed_candidates = []
        best = None
        for s in candidates:
            st = s["kickoff_dt"]
            if not et or not st:
                continue
            dt_min = abs((et - st.astimezone(UTC)).total_seconds()) / 60.0
            if dt_min > max_time_diff_min:
                continue
            timed_candidates.append((dt_min, s))
            hs = token_similarity(eh, s["home_norm"])
            aas = token_similarity(ea, s["away_norm"])
            rev_hs = token_similarity(eh, s["away_norm"])
            rev_as = token_similarity(ea, s["home_norm"])
            normal_score = 0.45 * hs + 0.45 * aas + 0.10 * max(0.0, 1.0 - dt_min/max_time_diff_min)
            reverse_score = 0.45 * rev_hs + 0.45 * rev_as + 0.10 * max(0.0, 1.0 - dt_min/max_time_diff_min)
            orientation = "normal" if normal_score >= reverse_score else "reversed"
            score = max(normal_score, reverse_score)
            cand = (score, -dt_min, orientation, s, hs, aas, rev_hs, rev_as)
            if best is None or cand[:2] > best[:2]:
                best = cand
        matched = False
        match_method = "alias+name+kickoff"
        if best:
            score, neg_dt, orientation, s, hs, aas, rev_hs, rev_as = best
            # Conservative lexical match first.
            pair_min = min(hs, aas) if orientation == "normal" else min(rev_hs, rev_as)
            matched = bool(score >= 0.67 and pair_min >= 0.45)
            # Cross-language fallback: only when provider sport_key is explicitly mapped
            # to a Sporttery league and exactly one candidate is within 10 minutes.
            if not matched and league_rule:
                near = [(d, ss) for d, ss in timed_candidates if d <= 10.0]
                if len(near) == 1:
                    neg_dt = -near[0][0]
                    s = near[0][1]
                    orientation = "normal"
                    matched = True
                    score = 0.70 + 0.30*max(0.0, 1.0-near[0][0]/10.0)
                    pair_min = 0.0
                    match_method = "league_alias+unique_kickoff"
            rows.append({
                "provider": ev.get("provider"),
                "external_event_id": ev.get("external_event_id"),
                "external_capture_time_utc": ev.get("capture_time_utc"),
                "external_commence_time_utc": ev.get("commence_time_utc"),
                "external_home": ev.get("home_team_external"),
                "external_away": ev.get("away_team_external"),
                "match_id": s.get("match_id") if matched else "",
                "match_num_str": s.get("match_num_str") if matched else "",
                "sporttery_home": s.get("home_team") if matched else "",
                "sporttery_away": s.get("away_team") if matched else "",
                "sporttery_kickoff_utc": s.get("kickoff_utc") if matched else "",
                "orientation": orientation if matched else "",
                "time_diff_min": round(-neg_dt, 3),
                "name_match_score": round(score, 4),
                "pair_min_score": round(pair_min, 4),
                "matched": int(matched),
                "mapping_method": match_method,
            })
        else:
            rows.append({
                "provider": ev.get("provider"), "external_event_id": ev.get("external_event_id"),
                "external_capture_time_utc": ev.get("capture_time_utc"), "external_commence_time_utc": ev.get("commence_time_utc"),
                "external_home": ev.get("home_team_external"), "external_away": ev.get("away_team_external"),
                "match_id": "", "matched": 0, "mapping_method": "no_candidate",
            })
    return rows


# ------------------------ consensus --------------------------------------

def build_1x2_book_rows(quotes: Sequence[Dict[str, Any]], maps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mp = {(m.get("provider"), m.get("external_event_id"), m.get("external_capture_time_utc")): m for m in maps if m.get("matched")}
    grouped: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for q in quotes:
        if q.get("market") != "1X2" or q.get("selection") not in OUTCOMES:
            continue
        m = mp.get((q.get("provider"), q.get("external_event_id"), q.get("capture_time_utc")))
        if not m:
            continue
        key = (str(m.get("match_id")), str(q.get("capture_time_utc")), str(q.get("bookmaker_key") or q.get("bookmaker")), str(q.get("provider")))
        g = grouped.setdefault(key, {
            "match_id": m.get("match_id"), "match_num_str": m.get("match_num_str"),
            "capture_time_utc": q.get("capture_time_utc"), "capture_time_local": q.get("capture_time_local"),
            "provider": q.get("provider"), "bookmaker_key": q.get("bookmaker_key"), "bookmaker": q.get("bookmaker"),
            "H": None, "D": None, "A": None,
        })
        sel = str(q.get("selection"))
        # If external orientation was reversed (rare provider issue), swap H/A.
        if m.get("orientation") == "reversed":
            sel = "A" if sel == "H" else "H" if sel == "A" else sel
        g[sel] = q.get("price")
    out: List[Dict[str, Any]] = []
    for g in grouped.values():
        if all(safe_float(g[k]) for k in OUTCOMES):
            dv = devig_blend((float(g["H"]), float(g["D"]), float(g["A"])))
            if not dv:
                continue
            rec = dict(g)
            rec.update({
                "odds_h": float(g["H"]), "odds_d": float(g["D"]), "odds_a": float(g["A"]),
                "p_mult_h": dv["mult"][0], "p_mult_d": dv["mult"][1], "p_mult_a": dv["mult"][2],
                "p_power_h": dv["power"][0], "p_power_d": dv["power"][1], "p_power_a": dv["power"][2],
                "p_blend_h": dv["blend"][0], "p_blend_d": dv["blend"][1], "p_blend_a": dv["blend"][2],
            })
            out.append(rec)
    return out


def median(xs: Sequence[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def mad(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    m = statistics.median(xs)
    return statistics.median([abs(x-m) for x in xs])


def aggregate_consensus(book_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in book_rows:
        groups[(str(r.get("match_id")), str(r.get("capture_time_utc")))].append(r)
    out: List[Dict[str, Any]] = []
    for (mid, cap), rows in groups.items():
        ph = [float(r["p_blend_h"]) for r in rows]
        pd = [float(r["p_blend_d"]) for r in rows]
        pa = [float(r["p_blend_a"]) for r in rows]
        mh, md, ma = median(ph), median(pd), median(pa)
        if None in (mh, md, ma):
            continue
        z = float(mh + md + ma)  # type: ignore[operator]
        mh, md, ma = float(mh)/z, float(md)/z, float(ma)/z
        out.append({
            "match_id": mid,
            "capture_time_utc": cap,
            "capture_time_local": rows[0].get("capture_time_local"),
            "n_bookmakers": len(rows),
            "bookmakers": "|".join(sorted({str(r.get("bookmaker") or r.get("bookmaker_key")) for r in rows})),
            "ext_p_h": mh, "ext_p_d": md, "ext_p_a": ma,
            "ext_fair_odds_h": 1.0/mh if mh > 0 else None,
            "ext_fair_odds_d": 1.0/md if md > 0 else None,
            "ext_fair_odds_a": 1.0/ma if ma > 0 else None,
            "mad_h": mad(ph), "mad_d": mad(pd), "mad_a": mad(pa),
            "max_mad_pp": 100.0 * max(mad(ph) or 0, mad(pd) or 0, mad(pa) or 0),
            "devig_method": "median(bookmaker mean[multiplicative,power])",
            "role": "audit_risk_gate_only_no_positive_fusion",
        })
    return sorted(out, key=lambda r: (r["match_id"], r["capture_time_utc"]))


# ------------------------ risk / closing references ----------------------

def nearest_past_consensus(consensus_by_match: Mapping[str, Sequence[Dict[str, Any]]], mid: str, target: datetime) -> Optional[Dict[str, Any]]:
    best = None
    best_dt = None
    for r in consensus_by_match.get(mid, []):
        dt = parse_dt(r.get("capture_time_utc"), UTC)
        if dt and dt <= target.astimezone(UTC) and (best_dt is None or dt > best_dt):
            best, best_dt = r, dt
    return best


def build_risk_gate(stage3_dir: Path, consensus: Sequence[Dict[str, Any]], max_staleness_min: float, divergence_pp: float, overheat_pp: float, min_bookmakers: int) -> List[Dict[str, Any]]:
    snaps = read_csv(stage3_dir / "snapshot_matches.csv")
    by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in consensus:
        by[str(c.get("match_id"))].append(c)
    out: List[Dict[str, Any]] = []
    for s in snaps:
        mid = str(s.get("match_id") or "")
        cap = parse_dt(s.get("capture_time_utc") or s.get("capture_time_local"), SH_TZ)
        ho, do, ao = safe_float(s.get("had_h")), safe_float(s.get("had_d")), safe_float(s.get("had_a"))
        if not mid or not cap:
            continue
        ext = nearest_past_consensus(by, mid, cap)
        base = {
            "match_id": mid, "match_num_str": s.get("match_num_str"),
            "sporttery_capture_time_utc": iso_utc(cap), "sporttery_capture_time_local": s.get("capture_time_local"),
            "kickoff_utc": s.get("kickoff_utc"), "home_team": s.get("home_team"), "away_team": s.get("away_team"),
            "had_present": s.get("had_present"), "sporttery_had_h": ho, "sporttery_had_d": do, "sporttery_had_a": ao,
            "external_role": "audit_risk_gate_only_no_positive_fusion",
        }
        if not ext:
            out.append({**base, "external_available": 0, "candidate_risk_flag": "NO_EXTERNAL_PAST_SNAPSHOT"})
            continue
        ext_dt = parse_dt(ext.get("capture_time_utc"), UTC)
        stale = (cap.astimezone(UTC) - ext_dt).total_seconds()/60.0 if ext_dt else None
        rec = {
            **base,
            "external_available": 1,
            "external_capture_time_utc": ext.get("capture_time_utc"),
            "external_staleness_min": round(stale,3) if stale is not None else None,
            "n_bookmakers": ext.get("n_bookmakers"),
            "ext_p_h": ext.get("ext_p_h"), "ext_p_d": ext.get("ext_p_d"), "ext_p_a": ext.get("ext_p_a"),
            "external_max_mad_pp": ext.get("max_mad_pp"),
        }
        flags: List[str] = []
        if stale is not None and stale > max_staleness_min:
            flags.append("STALE_EXTERNAL")
        if int(ext.get("n_bookmakers") or 0) < min_bookmakers:
            flags.append("LOW_BOOK_COVERAGE")
        if ho and do and ao:
            sp = devig_blend((ho, do, ao))
            if sp:
                ph, pd, pa = sp["blend"]
                delta = {
                    "H": 100.0*(ph-float(ext["ext_p_h"])),
                    "D": 100.0*(pd-float(ext["ext_p_d"])),
                    "A": 100.0*(pa-float(ext["ext_p_a"])),
                }
                rec.update({
                    "sporttery_p_h": ph, "sporttery_p_d": pd, "sporttery_p_a": pa,
                    "delta_pp_h": delta["H"], "delta_pp_d": delta["D"], "delta_pp_a": delta["A"],
                    "max_abs_delta_pp": max(abs(x) for x in delta.values()),
                })
                if rec["max_abs_delta_pp"] >= divergence_pp:
                    flags.append("DIVERGENCE")
                # Candidate overheating signal = Sporttery is materially more bullish
                # than the external multi-book median on H or A. Diagnostic only.
                hot_side = max(("H", "A"), key=lambda k: delta[k])
                hot_pp = delta[hot_side]
                rec["overheat_side"] = hot_side if hot_pp >= overheat_pp else ""
                rec["overheat_signal_pp"] = hot_pp if hot_pp >= overheat_pp else 0.0
                if hot_pp >= overheat_pp:
                    flags.append("OVERHEAT_CANDIDATE")
        else:
            flags.append("SPORTTERY_HAD_MISSING")
        rec["candidate_risk_flag"] = "|".join(flags) if flags else "OK"
        rec["candidate_thresholds_not_frozen"] = 1
        out.append(rec)
    return out


def build_external_close(stage3_dir: Path, consensus: Sequence[Dict[str, Any]], close_buffer_min: float) -> List[Dict[str, Any]]:
    dims = load_sporttery_matches(stage3_dir)
    by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in consensus:
        by[str(c.get("match_id"))].append(c)
    out = []
    for s in dims:
        mid = str(s.get("match_id") or "")
        ko = parse_dt(s.get("kickoff_utc") or s.get("kickoff_local"), SH_TZ)
        if not mid or not ko:
            continue
        target = ko.astimezone(UTC).timestamp() - close_buffer_min*60
        best = None; best_dt = None
        for c in by.get(mid, []):
            dt = parse_dt(c.get("capture_time_utc"), UTC)
            if dt and dt.timestamp() <= target and (best_dt is None or dt > best_dt):
                best, best_dt = c, dt
        if best:
            out.append({
                "match_id": mid, "match_num_str": s.get("match_num_str"), "kickoff_utc": iso_utc(ko),
                "external_close_time_utc": best.get("capture_time_utc"),
                "close_lead_min": round((ko.astimezone(UTC)-best_dt).total_seconds()/60.0,3),
                "n_bookmakers": best.get("n_bookmakers"),
                "close_p_h": best.get("ext_p_h"), "close_p_d": best.get("ext_p_d"), "close_p_a": best.get("ext_p_a"),
                "close_fair_odds_h": best.get("ext_fair_odds_h"), "close_fair_odds_d": best.get("ext_fair_odds_d"), "close_fair_odds_a": best.get("ext_fair_odds_a"),
                "label_only_for_clv": 1,
                "feature_use_forbidden": 1,
            })
    return out


def build_clv_reference_join(stage3_dir: Path, closes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    close_by = {str(r.get("match_id")): r for r in closes}
    snaps = read_csv(stage3_dir / "snapshot_matches.csv")
    out = []
    for s in snaps:
        c = close_by.get(str(s.get("match_id")))
        if not c:
            continue
        out.append({
            "match_id": s.get("match_id"), "match_num_str": s.get("match_num_str"),
            "sporttery_capture_time_utc": s.get("capture_time_utc"), "t_minus_min": s.get("t_minus_min"),
            "sporttery_had_h": s.get("had_h"), "sporttery_had_d": s.get("had_d"), "sporttery_had_a": s.get("had_a"),
            **{k: c.get(k) for k in ("external_close_time_utc","close_lead_min","n_bookmakers","close_p_h","close_p_d","close_p_a","close_fair_odds_h","close_fair_odds_d","close_fair_odds_a")},
            "label_only_for_clv": 1,
            "feature_use_forbidden": 1,
        })
    return out


# ------------------------ SQLite -----------------------------------------

EXTERNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS external_event(
  provider TEXT, external_event_id TEXT, capture_time_utc TEXT,
  commence_time_utc TEXT, home_team_external TEXT, away_team_external TEXT,
  sport_key TEXT, sport_title TEXT, raw_sha256 TEXT,
  PRIMARY KEY(provider, external_event_id, capture_time_utc)
);
CREATE TABLE IF NOT EXISTS external_quote(
  provider TEXT, external_event_id TEXT, capture_time_utc TEXT,
  bookmaker_key TEXT, bookmaker TEXT, market TEXT, selection TEXT, line REAL, price REAL,
  market_last_update_utc TEXT,
  PRIMARY KEY(provider, external_event_id, capture_time_utc, bookmaker_key, market, selection, line)
);
CREATE TABLE IF NOT EXISTS external_match_map(
  provider TEXT, external_event_id TEXT, external_capture_time_utc TEXT,
  match_id TEXT, matched INTEGER, orientation TEXT, time_diff_min REAL,
  name_match_score REAL, pair_min_score REAL,
  PRIMARY KEY(provider, external_event_id, external_capture_time_utc)
);
CREATE TABLE IF NOT EXISTS external_consensus_1x2(
  match_id TEXT, capture_time_utc TEXT, n_bookmakers INTEGER,
  ext_p_h REAL, ext_p_d REAL, ext_p_a REAL, max_mad_pp REAL,
  role TEXT,
  PRIMARY KEY(match_id, capture_time_utc)
);
CREATE TABLE IF NOT EXISTS external_close_1x2(
  match_id TEXT PRIMARY KEY, external_close_time_utc TEXT, close_lead_min REAL,
  n_bookmakers INTEGER, close_p_h REAL, close_p_d REAL, close_p_a REAL,
  label_only_for_clv INTEGER, feature_use_forbidden INTEGER
);
"""


def augment_sqlite(stage3_dir: Path, output_dir: Path, events, quotes, maps, consensus, closes) -> Optional[str]:
    src = stage3_dir / "stage3.db"
    if not src.exists():
        return None
    dst = output_dir / "stage3.db"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    con = sqlite3.connect(dst)
    con.executescript(EXTERNAL_SCHEMA)
    for e in events:
        con.execute("INSERT OR REPLACE INTO external_event VALUES(?,?,?,?,?,?,?,?,?)", (
            e.get("provider"), e.get("external_event_id"), e.get("capture_time_utc"), e.get("commence_time_utc"),
            e.get("home_team_external"), e.get("away_team_external"), str(e.get("sport_key") or ""), str(e.get("sport_title") or ""), e.get("raw_sha256")))
    for q in quotes:
        con.execute("INSERT OR REPLACE INTO external_quote VALUES(?,?,?,?,?,?,?,?,?,?)", (
            q.get("provider"), q.get("external_event_id"), q.get("capture_time_utc"), q.get("bookmaker_key"), q.get("bookmaker"),
            q.get("market"), q.get("selection"), q.get("line"), q.get("price"), q.get("market_last_update_utc")))
    for m in maps:
        con.execute("INSERT OR REPLACE INTO external_match_map VALUES(?,?,?,?,?,?,?,?,?)", (
            m.get("provider"), m.get("external_event_id"), m.get("external_capture_time_utc"), m.get("match_id"), m.get("matched"),
            m.get("orientation"), m.get("time_diff_min"), m.get("name_match_score"), m.get("pair_min_score")))
    for c in consensus:
        con.execute("INSERT OR REPLACE INTO external_consensus_1x2 VALUES(?,?,?,?,?,?,?,?)", (
            c.get("match_id"), c.get("capture_time_utc"), c.get("n_bookmakers"), c.get("ext_p_h"), c.get("ext_p_d"), c.get("ext_p_a"), c.get("max_mad_pp"), c.get("role")))
    for c in closes:
        con.execute("INSERT OR REPLACE INTO external_close_1x2 VALUES(?,?,?,?,?,?,?,?,?)", (
            c.get("match_id"), c.get("external_close_time_utc"), c.get("close_lead_min"), c.get("n_bookmakers"),
            c.get("close_p_h"), c.get("close_p_d"), c.get("close_p_a"), c.get("label_only_for_clv"), c.get("feature_use_forbidden")))
    con.commit(); con.close()
    return str(dst)


# ------------------------ build / tests ----------------------------------

def build(stage3_dir: Path, external_input_dir: Path, output_dir: Path, explicit: Sequence[str], aliases_path: Optional[Path], league_aliases_path: Optional[Path],
          max_time_diff_min: float, max_staleness_min: float, divergence_pp: float, overheat_pp: float,
          min_bookmakers: int, close_buffer_min: float) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = discover_external(external_input_dir, explicit)
    events: List[Dict[str, Any]] = []
    quotes: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    for p in files:
        try:
            if p.suffix.lower() == ".csv":
                ev, qt, meta = parse_canonical_csv(p)
            else:
                ev, qt, meta = parse_the_odds_api_file(p)
            events.extend(ev); quotes.extend(qt); audit.append(meta)
        except Exception as exc:
            audit.append({"file": str(p), "valid": False, "reason": repr(exc)})

    # Deduplicate exact raw records.
    ev_best = {}
    for e in events:
        ev_best[(e.get("provider"), e.get("external_event_id"), e.get("capture_time_utc"))] = e
    events = sorted(ev_best.values(), key=lambda e: (str(e.get("capture_time_utc")), str(e.get("external_event_id"))))
    q_best = {}
    for q in quotes:
        key = (q.get("provider"), q.get("external_event_id"), q.get("capture_time_utc"), q.get("bookmaker_key"), q.get("market"), q.get("selection"), q.get("line"))
        q_best[key] = q
    quotes = list(q_best.values())

    aliases = load_aliases(aliases_path)
    league_aliases = load_league_aliases(league_aliases_path)
    sporttery_matches = load_sporttery_matches(stage3_dir)
    maps = map_external_events(events, sporttery_matches, aliases, league_aliases, max_time_diff_min)
    book_rows = build_1x2_book_rows(quotes, maps)
    consensus = aggregate_consensus(book_rows)
    risk = build_risk_gate(stage3_dir, consensus, max_staleness_min, divergence_pp, overheat_pp, min_bookmakers)
    closes = build_external_close(stage3_dir, consensus, close_buffer_min)
    clv = build_clv_reference_join(stage3_dir, closes)
    unmatched = [m for m in maps if not int(m.get("matched") or 0)]
    alias_suggestions = []
    for m in maps:
        if int(m.get("matched") or 0) and m.get("mapping_method") == "league_alias+unique_kickoff":
            alias_suggestions.extend([
                {"alias": m.get("external_home"), "canonical": m.get("sporttery_home"), "match_id": m.get("match_id"), "evidence": "league_alias+unique_kickoff"},
                {"alias": m.get("external_away"), "canonical": m.get("sporttery_away"), "match_id": m.get("match_id"), "evidence": "league_alias+unique_kickoff"},
            ])

    write_csv(output_dir/"external_events.csv", events)
    write_csv(output_dir/"external_quotes_long.csv", quotes)
    write_csv(output_dir/"external_match_map.csv", maps)
    write_csv(output_dir/"external_1x2_bookmaker_devig.csv", book_rows)
    write_csv(output_dir/"external_consensus_1x2.csv", consensus)
    write_csv(output_dir/"market_risk_gate_candidates.csv", risk)
    write_csv(output_dir/"external_close_1x2.csv", closes)
    write_csv(output_dir/"clv_reference_join.csv", clv)
    write_csv(output_dir/"unmatched_external_events.csv", unmatched)
    write_csv(output_dir/"team_alias_suggestions.csv", alias_suggestions)
    db = augment_sqlite(stage3_dir, output_dir, events, quotes, maps, consensus, closes)

    matched_maps = sum(int(m.get("matched") or 0) for m in maps)
    manifest = {
        "external_bridge_version": VERSION,
        "governance": {
            "staking_baseline": "Jingcai 3.4",
            "jingcai_3_5_status": "Alpha Stage3 data/research only",
            "external_market_role": "network validation + divergence + overheating + Risk Gate",
            "external_positive_probability_fusion": "inactive",
            "candidate_risk_thresholds_frozen": False,
            "closing_reference_is_label_only": True,
        },
        "inputs": {"external_files": len(files), "audit": audit, "alias_entries": len(aliases), "league_alias_entries": len(league_aliases)},
        "outputs": {
            "external_event_snapshots": len(events), "external_quote_rows": len(quotes),
            "mapping_rows": len(maps), "matched_mapping_rows": matched_maps, "unmatched_mapping_rows": len(unmatched),
            "bookmaker_1x2_rows": len(book_rows), "consensus_1x2_rows": len(consensus),
            "risk_gate_candidate_rows": len(risk), "external_close_rows": len(closes), "clv_reference_rows": len(clv), "team_alias_suggestion_rows": len(alias_suggestions),
            "sqlite": db,
        },
        "candidate_thresholds_for_research_only": {
            "max_match_time_diff_min": max_time_diff_min,
            "max_external_staleness_min": max_staleness_min,
            "divergence_pp": divergence_pp,
            "overheat_pp": overheat_pp,
            "min_bookmakers": min_bookmakers,
            "close_buffer_min": close_buffer_min,
        },
        "notes": [
            "Only external snapshots at or before a Sporttery snapshot are used for contemporaneous Risk Gate comparison.",
            "External closing consensus is exported only for CLV evaluation and explicitly forbidden as a pre-match feature for earlier snapshots.",
            "Sporttery HHAD is not directly equated with two-way external spread markets; spreads are preserved as diagnostic quotes.",
            "Multiplicative and power de-vig are both computed per bookmaker; consensus uses the median of their per-book average.",
            "No S/A1/A2 grade is changed by this bridge.",
        ],
    }
    (output_dir/"manifest_external.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def self_test() -> None:
    d = devig_blend((2.0, 3.5, 4.0))
    assert d and abs(sum(d["blend"])-1.0) < 1e-10
    assert normalize_team_name("Manchester City FC") == normalize_team_name("Manchester-City")
    # Future leakage guard test.
    c = {"x": [
        {"capture_time_utc": "2026-08-17T01:00:00Z", "ext_p_h": .5, "ext_p_d": .25, "ext_p_a": .25},
        {"capture_time_utc": "2026-08-17T03:00:00Z", "ext_p_h": .6, "ext_p_d": .2, "ext_p_a": .2},
    ]}
    got = nearest_past_consensus(c, "x", parse_dt("2026-08-17T02:00:00Z", UTC))
    assert got and got["capture_time_utc"] == "2026-08-17T01:00:00Z"
    print(json.dumps({"self_test": "PASS", "version": VERSION}, ensure_ascii=False))


def write_templates(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory/"external_snapshot_envelope.example.json").write_text(json.dumps({
        "provider": "the_odds_api",
        "fetched_at_utc": "2026-08-17T02:00:00Z",
        "data": [{
            "id": "provider-event-id",
            "sport_key": "soccer_example",
            "sport_title": "Example League",
            "commence_time": "2026-08-17T03:00:00Z",
            "home_team": "Home Team",
            "away_team": "Away Team",
            "bookmakers": [{
                "key": "book_a", "title": "Book A", "last_update": "2026-08-17T01:59:00Z",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Home Team", "price": 2.10}, {"name": "Draw", "price": 3.30}, {"name": "Away Team", "price": 3.60}
                ]}]
            }]
        }]
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["provider","capture_time_utc","external_event_id","sport_key","sport_title","commence_time_utc","home_team_external","away_team_external","bookmaker_key","bookmaker","market","selection","line","price","market_last_update_utc"]
    write_csv(directory/"external_quotes_template.csv", [], fields)
    write_csv(directory/"team_aliases_template.csv", [], ["alias","canonical"])
    write_csv(directory/"league_aliases_template.csv", [], ["provider","sport_key","sporttery_league_id","sporttery_league"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Jingcai Stage 3 External Market Bridge v1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")
    t = sub.add_parser("write-templates")
    t.add_argument("--output-dir", required=True)
    b = sub.add_parser("build")
    b.add_argument("--stage3-dir", required=True, help="Parser/Builder output containing snapshot_matches.csv and stage3.db")
    b.add_argument("--external-input-dir", required=True)
    b.add_argument("--output-dir", required=True)
    b.add_argument("--input", action="append", default=[])
    b.add_argument("--aliases", default=None)
    b.add_argument("--league-aliases", default=None)
    b.add_argument("--max-match-time-diff-min", type=float, default=DEFAULT_MAX_MATCH_TIME_DIFF_MIN)
    b.add_argument("--max-external-staleness-min", type=float, default=DEFAULT_MAX_EXTERNAL_STALENESS_MIN)
    b.add_argument("--divergence-pp", type=float, default=DEFAULT_DIVERGENCE_PP)
    b.add_argument("--overheat-pp", type=float, default=DEFAULT_OVERHEAT_PP)
    b.add_argument("--min-bookmakers", type=int, default=DEFAULT_MIN_BOOKMAKERS)
    b.add_argument("--close-buffer-min", type=float, default=DEFAULT_CLOSE_BUFFER_MIN)
    args = ap.parse_args()
    if args.cmd == "self-test":
        self_test(); return
    if args.cmd == "write-templates":
        write_templates(Path(args.output_dir)); return
    m = build(Path(args.stage3_dir), Path(args.external_input_dir), Path(args.output_dir), args.input,
              Path(args.aliases) if args.aliases else None, Path(args.league_aliases) if args.league_aliases else None,
              args.max_match_time_diff_min, args.max_external_staleness_min, args.divergence_pp,
              args.overheat_pp, args.min_bookmakers, args.close_buffer_min)
    print(json.dumps(m, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
