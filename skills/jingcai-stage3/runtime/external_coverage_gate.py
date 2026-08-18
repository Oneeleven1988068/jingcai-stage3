#!/usr/bin/env python3
"""Reality-First target-event coverage gate for external odds captures.

This helper intentionally does not guess team aliases. It performs conservative
normalized-name matching and reports missing/ambiguous targets for human or
browser fallback resolution.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path


def norm(s: str) -> str:
    s = (s or "").casefold()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s)
    return s


def load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_events(obj):
    # Supports plain list/dict envelopes and the current TheOdds stage3 string snapshots.
    if isinstance(obj, list):
        for x in obj:
            yield from iter_events(x)
    elif isinstance(obj, dict):
        if "home_team" in obj and "away_team" in obj:
            yield obj
        for v in obj.values():
            if isinstance(v, (dict, list)):
                yield from iter_events(v)
            elif isinstance(v, str):
                # Current Stage3 captures may store newline-delimited JSON envelopes
                # whose nested `data` field is itself a JSON string. Parse conservatively.
                for line in v.splitlines():
                    line = line.strip()
                    if not line.startswith(("{", "[")):
                        continue
                    try:
                        x = json.loads(line)
                    except Exception:
                        continue
                    yield from iter_events(x)


def match_target(target, events):
    th, ta = norm(target["home"]), norm(target["away"])
    hits = []
    for e in events:
        eh, ea = norm(e.get("home_team", "")), norm(e.get("away_team", ""))
        if th == eh and ta == ea:
            hits.append(e)
    if len(hits) == 1:
        return "MATCHED", hits[0]
    if len(hits) > 1:
        return "AMBIGUOUS", hits
    return "MISSING", None


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: external_coverage_gate.py targets.json external_capture.json")
    targets = load_json(sys.argv[1])
    ext = load_json(sys.argv[2])
    events = list(iter_events(ext))
    out = {"target_count": len(targets), "external_event_count_seen": len(events), "targets": []}
    missing = 0
    for t in targets:
        status, hit = match_target(t, events)
        if status != "MATCHED":
            missing += 1
        out["targets"].append({
            "id": t.get("id"), "home": t["home"], "away": t["away"],
            "status": status,
            "external_event_id": hit.get("id") if isinstance(hit, dict) else None,
            "commence_time": hit.get("commence_time") if isinstance(hit, dict) else None,
        })
    out["matched_count"] = len(targets) - missing
    out["missing_or_ambiguous_count"] = missing
    out["EXTERNAL_COVERAGE_GATE"] = "PASS" if missing == 0 else "FAIL"
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
