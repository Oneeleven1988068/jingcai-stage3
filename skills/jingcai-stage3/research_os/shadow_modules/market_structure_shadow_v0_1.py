#!/usr/bin/env python3
import argparse, json
from datetime import datetime, time
from pathlib import Path

EVIDENCE = {"OBSERVED","DERIVED","MODEL_OUTPUT","INFERRED","HYPOTHESIS","INPUT_INCOMPLETE","UNVERIFIED"}

def dejuice(odds):
    vals = [float(x) for x in odds]
    inv = [1.0/x for x in vals]
    s = sum(inv)
    return [x/s for x in inv]

def parse_wrapper(path):
    outer = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(outer.get("data"), str):
        return outer, json.loads(outer["data"])
    return outer, outer.get("data", outer)

def flatten_matches(inner):
    out=[]
    for block in inner.get("value",{}).get("matchInfoList",[]):
        out.extend(block.get("subMatchList",[]))
    return out

def sales_cutoff_for_business_date(business_date):
    # Monday-Friday 22:00; Saturday-Sunday 23:00, user-confirmed governance rule.
    d=datetime.strptime(business_date, "%Y-%m-%d")
    cutoff=time(22,0,0) if d.weekday() <= 4 else time(23,0,0)
    return f"{business_date}T{cutoff.strftime('%H:%M:%S')}+08:00"

def analyze(match, user_confirmed_unchanged_to_cutoff=False):
    result={
        "module":"Market Structure Shadow v0.1",
        "status":"SHADOW_ONLY__NO_MODEL_PROMOTION",
        "match_num":match.get("matchNumStr"),
        "match_id":match.get("matchId"),
        "fixture":f"{match.get('homeTeamAllName')} vs {match.get('awayTeamAllName')}",
        "evidence_state":"DERIVED",
    }
    had=match.get("had") or {}
    if all(had.get(k) for k in ("h","d","a")):
        hp=dejuice([had["h"],had["d"],had["a"]])
        result["had"]={
            "odds":{"H":float(had["h"]),"D":float(had["d"]),"A":float(had["a"])},
            "dejuiced":{"H":hp[0],"D":hp[1],"A":hp[2]},
            "update_time_local":f"{had.get('updateDate')}T{had.get('updateTime')}+08:00"
        }
    else:
        result["had"]={"state":"INPUT_INCOMPLETE"}

    hhad=match.get("hhad") or {}
    if all(hhad.get(k) for k in ("h","d","a")):
        hhp=dejuice([hhad["h"],hhad["d"],hhad["a"]])
        result["hhad"]={
            "goal_line":hhad.get("goalLine"),
            "odds":{"H":float(hhad["h"]),"D":float(hhad["d"]),"A":float(hhad["a"])},
            "dejuiced":{"H":hhp[0],"D":hhp[1],"A":hhp[2]},
            "update_time_local":f"{hhad.get('updateDate')}T{hhad.get('updateTime')}+08:00"
        }
        if hhad.get("goalLine") == "-1" and "dejuiced" in result.get("had",{}):
            partition=hhp[0]+hhp[1]
            result["hhad_margin_decomposition"]={
                "interpretation":{
                    "H":"home wins by 2+",
                    "D":"home wins by exactly 1",
                    "A":"home draw or loss"
                },
                "p_home_win_by_2plus":hhp[0],
                "p_home_win_exactly_1":hhp[1],
                "p_home_draw_or_loss":hhp[2],
                "p_home_win_partition":partition,
                "had_home_win":result["had"]["dejuiced"]["H"],
                "cross_market_coherence_gap_pp":100*(partition-result["had"]["dejuiced"]["H"]),
                "conditional_on_home_win":{"exactly_1":hhp[1]/partition,"2plus":hhp[0]/partition},
                "research_status":"HYPOTHESIS__OOS_VALIDATION_REQUIRED"
            }

    ttg=match.get("ttg") or {}
    keys=["s0","s1","s2","s3","s4","s5","s6","s7"]
    if all(ttg.get(k) for k in keys):
        odds=[float(ttg[k]) for k in keys]
        p=dejuice(odds)
        mode=max(range(8), key=lambda i:p[i])
        result["ttg"]={
            "odds":{str(i):odds[i] for i in range(8)},
            "dejuiced":{str(i):p[i] for i in range(8)},
            "mode":"7+" if mode==7 else mode,
            "mode_probability":p[mode],
            "tail_mass_4plus":sum(p[4:]),
            "tail_mass_5plus":sum(p[5:]),
            "update_time_local":f"{ttg.get('updateDate')}T{ttg.get('updateTime')}+08:00",
            "research_status":"DERIVED_DIAGNOSTIC"
        }

    bd=match.get("businessDate")
    if bd:
        result["sales_cutoff"]={
            "business_date":bd,
            "cutoff_local":sales_cutoff_for_business_date(bd),
            "rule_source":"USER_CONFIRMED_GOVERNANCE"
        }
        if user_confirmed_unchanged_to_cutoff and "odds" in result.get("had",{}):
            result["closing_quote"]={
                "price":result["had"]["odds"],
                "last_official_update_time_local":result["had"]["update_time_local"],
                "effective_through_cutoff_local":result["sales_cutoff"]["cutoff_local"],
                "confirmation":"USER_OBSERVED_UNCHANGED_TO_CUTOFF",
                "important_caveat":"Not an independently archived official 22:00 snapshot; provenance is user observation + last official quote."
            }
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--match", required=True)
    ap.add_argument("--user-confirmed-unchanged-to-cutoff", action="store_true")
    ap.add_argument("--output")
    args=ap.parse_args()
    _,inner=parse_wrapper(args.input)
    matches=flatten_matches(inner)
    m=next((x for x in matches if x.get("matchNumStr")==args.match),None)
    if not m:
        raise SystemExit(f"match not found: {args.match}")
    out=analyze(m,args.user_confirmed_unchanged_to_cutoff)
    text=json.dumps(out,ensure_ascii=False,indent=2)
    if args.output:
        Path(args.output).write_text(text+"\n",encoding="utf-8")
    print(text)

if __name__=="__main__":
    main()
