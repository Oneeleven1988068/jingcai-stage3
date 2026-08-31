#!/usr/bin/env python3
"""Jingcai x Polymarket consensus terminal — local read-only server."""
from __future__ import annotations

import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import align_engine as engine

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
HOST = os.environ.get("ALIGN_HOST", "0.0.0.0")
PORT = int(os.environ.get("ALIGN_PORT", "8765"))

_cache: dict = {"snapshot": None, "lock": threading.Lock()}


def refresh_snapshot() -> dict:
    snap = engine.build_snapshot()
    with _cache["lock"]:
        _cache["snapshot"] = snap
    (engine.DATA / "last_snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False), encoding="utf-8"
    )
    return snap


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print("[http]", args[0] if args else fmt)

    def _json(self, code: int, obj) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json(200, {"ok": True, "read_only": True, "service": "pm-jc-align"})
        if path == "/api/snapshot":
            with _cache["lock"]:
                snap = _cache["snapshot"]
            if snap is None:
                snap = refresh_snapshot()
            return self._json(200, snap)
        if path == "/api/refresh":
            try:
                return self._json(200, refresh_snapshot())
            except Exception as exc:
                return self._json(500, {"ok": False, "error": str(exc)})
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except Exception:
            return self._json(400, {"ok": False, "error": "invalid json"})
        if path == "/api/ingest-jc":
            return self._json(200, engine.ingest_jc_payload(body))
        if path == "/api/verify":
            return self._json(
                200,
                engine.verify_binding(
                    str(body.get("jc_match_id") or ""),
                    str(body.get("pm_event_id") or ""),
                    bool(body.get("verified", True)),
                ),
            )
        return self._json(404, {"ok": False})


def main() -> None:
    print(f"pm-jc-align reading {engine.DATA}")
    print("warming snapshot (Polymarket public APIs)…")
    try:
        snap = refresh_snapshot()
        print(
            f"ready  jc={snap.get('jc_count')} pm={snap.get('pm_count')} "
            f"aligned={len(snap.get('aligned') or [])} source={snap.get('jc_source')}"
        )
    except Exception as exc:
        print("warmup warning:", exc)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"open  http://127.0.0.1:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
