#!/usr/bin/env python3
"""Read-only Sporttery <-> Polymarket alignment. No orders."""
from __future__ import annotations
import json, re, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UA = "JingcaiAlign/1.0"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SPORTTERY = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?poolCode=hhad,had,ttg&channel=c"

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def http_json(url: str, method: str = "GET", body: Any = None, timeout: int = 20) -> Any:
    data = None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else None

def sf(v: Any):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None

def maybe_json(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "[{":
        try: return json.loads(v)
        except json.JSONDecodeError: return v
    return v

def norm(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("é","e"),("á","a"),("í","i"),("ó","o"),("ú","u")): s = s.replace(a,b)
    s = re.sub(r"\b(fc|cf|afc|sc|ac|as|the|de|united|city|hotspur)\b", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)).strip()

def devig(odds: dict[str, float]) -> dict[str, Any]:
    implied = {k: (1/o if o and o > 1 else None) for k, o in odds.items()}
    s = sum(p for p in implied.values() if p)
    if s <= 0: return {"implied": implied, "devig": {k: None for k in odds}, "overround": None, "return_rate": None}
    return {"implied": implied, "devig": {k: implied[k]/s if implied[k] else None for k in odds}, "overround": s, "return_rate": 1/s}

class Aliases:
    def __init__(self, raw: dict[str, Any]):
        self.m = {}
        for c, names in (raw.get("aliases") or {}).items():
            cc = norm(c); self.m[cc] = cc
            for n in names: self.m[norm(n)] = cc
    def canon(self, name: str) -> str:
        n = norm(name)
        if n in self.m: return self.m[n]
        for a, c in self.m.items():
            if a and (a in n or n in a): return c
        return n
