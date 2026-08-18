#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Odds API snapshot collector for Jingcai Stage 3.

Requires ODDS_API_KEY in the environment. Saves an auditable envelope consumed
by jingcai_stage3_external_market_v1.py. No credentials are written to output.
"""
from __future__ import annotations
import argparse, json, os, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.the-odds-api.com/v4"


def get_json(url: str, timeout: float=15.0):
    req = urllib.request.Request(url, headers={"Accept":"application/json","User-Agent":"Jingcai-Stage3-ExternalCollector/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
        headers = {k.lower(): v for k,v in r.headers.items()}
    return body, headers


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output-dir",required=True)
    ap.add_argument("--sport-key",action="append",default=[],help="repeatable sport key, e.g. soccer_epl")
    ap.add_argument("--regions",default="uk,eu,au")
    ap.add_argument("--markets",default="h2h")
    ap.add_argument("--bookmakers",default=None,help="optional comma-separated bookmaker keys; takes priority over regions")
    ap.add_argument("--historical-date",default=None,help="ISO8601 timestamp; paid historical endpoint if account supports it")
    ap.add_argument("--list-soccer-sports",action="store_true")
    ap.add_argument("--timeout",type=float,default=15.0)
    args=ap.parse_args()
    key=os.getenv("ODDS_API_KEY")
    if not key:
        raise SystemExit("ODDS_API_KEY is not set. Keep the key private; do not put it in shared JSON files.")
    outdir=Path(args.output_dir); outdir.mkdir(parents=True,exist_ok=True)
    fetched=datetime.now(timezone.utc).replace(microsecond=0)

    if args.list_soccer_sports:
        q=urllib.parse.urlencode({"apiKey":key})
        data,h=get_json(f"{BASE}/sports?{q}",args.timeout)
        soccer=[x for x in data if str(x.get("group") or "").lower()=="soccer"]
        p=outdir/f"theoddsapi_soccer_sports_{fetched.strftime('%Y%m%dT%H%M%SZ')}.json"
        p.write_text(json.dumps({"provider":"the_odds_api","fetched_at_utc":fetched.isoformat().replace('+00:00','Z'),"data":soccer},ensure_ascii=False,indent=2),encoding="utf-8")
        print(p)
        if not args.sport_key:
            return

    if not args.sport_key:
        raise SystemExit("Provide at least one --sport-key or use --list-soccer-sports")

    all_events=[]; quota=[]
    for sport in args.sport_key:
        params={"apiKey":key,"markets":args.markets,"oddsFormat":"decimal","dateFormat":"iso"}
        if args.bookmakers:
            params["bookmakers"]=args.bookmakers
        else:
            params["regions"]=args.regions
        if args.historical_date:
            params["date"]=args.historical_date
            endpoint=f"{BASE}/historical/sports/{sport}/odds"
        else:
            endpoint=f"{BASE}/sports/{sport}/odds"
        data,h=get_json(endpoint+"?"+urllib.parse.urlencode(params),args.timeout)
        events=(data.get("data") if isinstance(data,dict) and isinstance(data.get("data"),list) else data)
        if isinstance(events,list): all_events.extend(events)
        quota.append({"sport_key":sport,"x_requests_remaining":h.get("x-requests-remaining"),"x_requests_used":h.get("x-requests-used"),"x_requests_last":h.get("x-requests-last")})
    envelope={
        "provider":"the_odds_api",
        "fetched_at_utc":fetched.isoformat().replace('+00:00','Z'),
        "query":{"sport_keys":args.sport_key,"regions":args.regions if not args.bookmakers else None,"bookmakers":args.bookmakers,"markets":args.markets,"historical_date":args.historical_date},
        "quota":quota,
        "data":all_events,
    }
    p=outdir/f"external_theoddsapi_{fetched.strftime('%Y%m%dT%H%M%SZ')}.json"
    p.write_text(json.dumps(envelope,ensure_ascii=False,indent=2),encoding="utf-8")
    print(p)

if __name__=='__main__': main()
