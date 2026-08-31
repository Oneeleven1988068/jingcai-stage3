#!/usr/bin/env python3
"""Run on a host that can reach webapi.sporttery.cn."""
from __future__ import annotations

import align_engine as engine

if __name__ == "__main__":
    pack = engine.fetch_sporttery()
    if pack.get("source") != "cache":
        engine.save_json(engine.DATA / "jc_cache.json", pack)
    print("source=", pack.get("source"), "ok=", pack.get("ok"), "n=", len(pack.get("fixtures") or []))
    if pack.get("error"):
        print("error=", pack["error"])
