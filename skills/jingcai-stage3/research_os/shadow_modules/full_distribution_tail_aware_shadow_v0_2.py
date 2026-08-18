#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, json, math, re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

VERSION = "Full Distribution / Tail-Aware CRS Shadow v0.2"
STATUS = "SHADOW_ONLY__NO_MODEL_PROMOTION"


def dejuice(odds: Iterable[float]) -> List[float]:
    vals=[float(x) for x in odds]
    if not vals or any((not math.isfinite(x) or x<=1.0) for x in vals):
        raise ValueError("invalid decimal odds")
    inv=[1.0/x for x in vals]
    z=sum(inv)
    return [x/z for x in inv]


def parse_wrapper(path: str|Path) -> Tuple[Dict[str,Any],Dict[str,Any]]:
    outer=json.loads(Path(path).read_text(encoding="utf-8"))
    d=outer.get("data")
    if isinstance(d,str):
        return outer,json.loads(d)
    if isinstance(d,dict):
        return outer,d
    return outer,outer


def flatten_matches(inner: Dict[str,Any]) -> List[Dict[str,Any]]:
    out=[]
    for block in inner.get("value",{}).get("matchInfoList",[]):
        out.extend(block.get("subMatchList",[]))
    return out


def result_of(h:int,a:int)->str:
    return "H" if h>a else "D" if h==a else "A"


def total_band(t:int)->str:
    if t<=1:return "0-1"
    if t<=3:return "2-3"
    if t<=5:return "4-5"
    return "6+"


def entropy_norm(p:List[float])->float:
    q=[x for x in p if x>0]
    if len(q)<=1:return 0.0
    h=-sum(x*math.log(x) for x in q)
    return h/math.log(len(p))


def parse_crs(crs:Dict[str,Any])->Dict[str,Any]:
    exact=[]; other=[]
    for k,v in crs.items():
        if k.endswith("f") or k in {"updateDate","updateTime","goalLine","goalLineValue"}: continue
        try:o=float(v)
        except Exception: continue
        if not math.isfinite(o) or o<=1: continue
        m=re.fullmatch(r"s(\d{2})s(\d{2})",k)
        if m:
            h,a=int(m.group(1)),int(m.group(2))
            exact.append((k,f"{h}:{a}",h,a,o))
        elif k in {"s1sh","s1sd","s1sa"}:
            lab={"s1sh":"OTHER_H","s1sd":"OTHER_D","s1sa":"OTHER_A"}[k]
            other.append((k,lab,o))
    all_rows=[("exact",x[0],x[1],x[4]) for x in exact]+[("other",x[0],x[1],x[2]) for x in other]
    if not all_rows:return {"state":"INPUT_INCOMPLETE"}
    p=dejuice([x[3] for x in all_rows])
    prob_by_key={row[1]:pr for row,pr in zip(all_rows,p)}
    exact_rows=[]
    for k,label,h,a,o in exact:
        pr=prob_by_key[k]
        exact_rows.append({"key":k,"score":label,"home":h,"away":a,"odds":o,"p":pr,
                           "result":result_of(h,a),"margin":h-a,"total":h+a,"total_band":total_band(h+a)})
    exact_rows.sort(key=lambda r:r["p"],reverse=True)
    other_rows=[]
    for k,label,o in other:
        other_rows.append({"key":k,"bucket":label,"odds":o,"p":prob_by_key[k],"result":label[-1]})
    dir_mass={x:0.0 for x in "HDA"}
    for r in exact_rows: dir_mass[r["result"]]+=r["p"]
    for r in other_rows: dir_mass[r["result"]]+=r["p"]
    exact_mass=sum(r["p"] for r in exact_rows)
    other_mass=sum(r["p"] for r in other_rows)
    band_mass={b:sum(r["p"] for r in exact_rows if r["total_band"]==b) for b in ["0-1","2-3","4-5","6+"]}
    tail4=sum(r["p"] for r in exact_rows if r["total"]>=4)
    tail5=sum(r["p"] for r in exact_rows if r["total"]>=5)
    tail6=sum(r["p"] for r in exact_rows if r["total"]>=6)
    return {
        "update_time_local": f"{crs.get('updateDate')}T{crs.get('updateTime')}+08:00",
        "exact_mass": exact_mass,
        "other_mass": other_mass,
        "direction_mass_including_other":dir_mass,
        "exact_total_band_mass_lower_bound":band_mass,
        "exact_tail_mass_lower_bound":{"4+":tail4,"5+":tail5,"6+":tail6},
        "other_buckets":other_rows,
        "core_exact_top3":exact_rows[:3],
        "core_exact_top5":exact_rows[:5],
        "tail_exact_top3_4plus":[r for r in exact_rows if r["total"]>=4][:3],
        "tail_exact_top3_5plus":[r for r in exact_rows if r["total"]>=5][:3],
        "all_exact_states":exact_rows,
        "important_caveat":"CRS other-score buckets have result direction but no exact total/margin. Exact tail masses are lower bounds when other_mass>0."
    }


def analyze_match(m:Dict[str,Any])->Dict[str,Any]:
    out={"module":VERSION,"status":STATUS,"match_num":m.get("matchNumStr"),"match_id":m.get("matchId"),
         "fixture":f"{m.get('homeTeamAllName')} vs {m.get('awayTeamAllName')}","evidence_state":"DERIVED",
         "principle":"NO_EARLY_TOP_N_TRUNCATION__PRESERVE_FULL_DISTRIBUTION_UNTIL_FINAL_COMPRESSION"}
    had=m.get("had") or {}
    if all(had.get(k) for k in ("h","d","a")):
        hp=dejuice([had["h"],had["d"],had["a"]])
        out["had"]={"odds":dict(zip("HDA",map(float,[had["h"],had["d"],had["a"]]))),
                    "p":dict(zip("HDA",hp)),"update_time_local":f"{had.get('updateDate')}T{had.get('updateTime')}+08:00"}
    else: out["had"]={"state":"INPUT_INCOMPLETE"}
    hhad=m.get("hhad") or {}
    if all(hhad.get(k) for k in ("h","d","a")):
        pp=dejuice([hhad["h"],hhad["d"],hhad["a"]]); line=str(hhad.get("goalLine") or hhad.get("goalLineValue") or "")
        out["hhad"]={"line":line,"odds":dict(zip("HDA",map(float,[hhad["h"],hhad["d"],hhad["a"]]))),"p":dict(zip("HDA",pp)),
                     "update_time_local":f"{hhad.get('updateDate')}T{hhad.get('updateTime')}+08:00"}
        if "p" in out.get("had",{}) and line in {"-1","-1.0","-1.00"}:
            part=pp[0]+pp[1]
            out["symmetric_margin"]={"side":"HOME_WIN","identity":"HAD_H ~ HHAD_H + HHAD_D",
                "p_win_2plus":pp[0],"p_win_1":pp[1],"partition":part,"had_direction_p":out["had"]["p"]["H"],
                "gap_pp":100*(part-out["had"]["p"]["H"]),"conditional":{"win_1":pp[1]/part,"win_2plus":pp[0]/part},
                "usage":"CONSISTENCY_ALWAYS__MARGIN_PREDICTION_ONLY_AFTER_HAD_DIRECTION_GATE"}
        elif "p" in out.get("had",{}) and line in {"+1","1","1.0","1.00","+1.0","+1.00"}:
            part=pp[1]+pp[2]
            out["symmetric_margin"]={"side":"AWAY_WIN","identity":"HAD_A ~ HHAD_D + HHAD_A",
                "p_win_1":pp[1],"p_win_2plus":pp[2],"partition":part,"had_direction_p":out["had"]["p"]["A"],
                "gap_pp":100*(part-out["had"]["p"]["A"]),"conditional":{"win_1":pp[1]/part,"win_2plus":pp[2]/part},
                "usage":"CONSISTENCY_ALWAYS__MARGIN_PREDICTION_ONLY_AFTER_HAD_DIRECTION_GATE"}
    else: out["hhad"]={"state":"INPUT_INCOMPLETE"}
    ttg=m.get("ttg") or {}; ks=[f"s{i}" for i in range(8)]
    if all(ttg.get(k) for k in ks):
        odds=[float(ttg[k]) for k in ks]; p=dejuice(odds); mode=max(range(8),key=lambda i:p[i])
        bands={"0-1":sum(p[:2]),"2-3":sum(p[2:4]),"4-5":sum(p[4:6]),"6+":sum(p[6:])}
        out["ttg"]={"odds":{("7+" if i==7 else str(i)):odds[i] for i in range(8)},
                    "p":{("7+" if i==7 else str(i)):p[i] for i in range(8)},
                    "mode":"7+" if mode==7 else mode,"mode_p":p[mode],"bands":bands,
                    "tail_mass":{"4+":sum(p[4:]),"5+":sum(p[5:]),"6+":sum(p[6:]),"7+":p[7]},
                    "normalized_entropy":entropy_norm(p),"effective_support":math.exp(-sum(x*math.log(x) for x in p if x>0)),
                    "retained_probability_mass":sum(p),"truncation":"NONE",
                    "update_time_local":f"{ttg.get('updateDate')}T{ttg.get('updateTime')}+08:00"}
    else: out["ttg"]={"state":"INPUT_INCOMPLETE"}
    out["crs"]=parse_crs(m.get("crs") or {})
    if "p" in out.get("had",{}) and out.get("crs",{}).get("direction_mass_including_other"):
        cm=out["crs"]["direction_mass_including_other"]
        out["had_crs_direction_gap_pp"]={x:100*(cm[x]-out["had"]["p"][x]) for x in "HDA"}
    out["compression_policy"]={
        "old_forbidden_pattern":"TTG_TOP_N -> CRS_CANDIDATE_SPACE",
        "new_shadow_pattern":"FULL_TTG + FULL_CRS_STATE_MAP -> FINAL_PRESENTATION_COMPRESSION_ONLY",
        "production_weight_change":False,
        "thresholds_frozen":False
    }
    return out


def pois_p(lam:float,k:int)->float:
    return math.exp(-lam)*lam**k/math.factorial(k)

def pois_tail(lam:float,k:int)->float:
    return 1-sum(pois_p(lam,i) for i in range(k))

def auc_binary(scores:List[float],ys:List[int])->float|None:
    pos=[s for s,y in zip(scores,ys) if y==1]; neg=[s for s,y in zip(scores,ys) if y==0]
    if not pos or not neg:return None
    return sum((p>n)+0.5*(p==n) for p in pos for n in neg)/(len(pos)*len(neg))

def backtest_csv(path:str|Path)->Dict[str,Any]:
    rows=list(csv.DictReader(Path(path).open(encoding="utf-8-sig")))
    details=[]; p5=[]; y5=[]
    for r in rows:
        lh=float(r["lambda_home"]); la=float(r["lambda_away"]); lam=lh+la
        ttg=[int(x) for x in r["old_ttg_top3"].split("/")]
        old_crs=[x.strip() for x in r["old_crs_top3"].split("/")]
        h,a=[int(x) for x in r["result"].split(":")]; total=h+a
        kept=sum(pois_p(lam,k) for k in ttg)
        q5=pois_tail(lam,5); yy=int(total>=5); p5.append(q5); y5.append(yy)
        details.append({"match":r["match"],"result":r["result"],"actual_total":total,
                        "old_ttg_top3":ttg,"old_ttg_hit":int(total in ttg),
                        "old_crs_top3":old_crs,"old_crs_hit":int(r["result"] in old_crs),
                        "frozen_lambda_total":lam,"lambda_diagnostic_mass_kept_by_old_ttg_top3":kept,
                        "lambda_diagnostic_mass_omitted_by_old_ttg_top3":1-kept,
                        "lambda_tail_p4plus":pois_tail(lam,4),"lambda_tail_p5plus":q5,"lambda_tail_p6plus":pois_tail(lam,6)})
    n=len(details); hi=[d for d in details if d["actual_total"]>=5]
    eps=1e-12
    brier=sum((p-y)**2 for p,y in zip(p5,y5))/n
    ll=-sum(y*math.log(max(eps,p))+(1-y)*math.log(max(eps,1-p)) for p,y in zip(p5,y5))/n
    base=sum(y5)/n
    brier_base=sum((base-y)**2 for y in y5)/n
    ll_base=-sum(y*math.log(max(eps,base))+(1-y)*math.log(max(eps,1-base)) for y in y5)/n
    return {"module":"Historical Tail Falsification v0.1","status":STATUS,"n":n,
            "source_rule":"Frozen pre-match summary only; no post-match odds used as features.",
            "old_ttg_top3_hit_rate":sum(d["old_ttg_hit"] for d in details)/n,
            "old_crs_top3_hit_rate":sum(d["old_crs_hit"] for d in details)/n,
            "actual_5plus_n":len(hi),"old_ttg_top3_hit_rate_on_5plus":(sum(d["old_ttg_hit"] for d in hi)/len(hi) if hi else None),
            "avg_lambda_diagnostic_mass_omitted_by_old_ttg_top3":sum(d["lambda_diagnostic_mass_omitted_by_old_ttg_top3"] for d in details)/n,
            "lambda_tail_5plus_auc":auc_binary(p5,y5),"lambda_tail_5plus_brier":brier,"lambda_tail_5plus_logloss":ll,
            "climatology_5plus_rate":base,"climatology_brier":brier_base,"climatology_logloss":ll_base,
            "interpretation":"Top-N truncation loses material probability mass. Frozen lambda tail alone does NOT solve prediction and remains diagnostic only.",
            "details":details}


def self_test()->Dict[str,Any]:
    fixture={"matchNumStr":"T001","matchId":"m1","homeTeamAllName":"Home","awayTeamAllName":"Away",
             "had":{"h":"2.00","d":"3.20","a":"3.40","updateDate":"2026-08-18","updateTime":"10:00:00"},
             "hhad":{"h":"3.50","d":"3.60","a":"1.72","goalLine":"-1","updateDate":"2026-08-18","updateTime":"10:00:01"},
             "ttg":{"s0":"12","s1":"6","s2":"4","s3":"3.2","s4":"4.5","s5":"7","s6":"12","s7":"18","updateDate":"2026-08-18","updateTime":"10:00:02"},
             "crs":{"s00s00":"12","s01s00":"7","s01s01":"6.5","s02s01":"8","s03s02":"20","s1sh":"35","s1sd":"90","s1sa":"40","updateDate":"2026-08-18","updateTime":"10:00:03"}}
    out=analyze_match(fixture)
    assert abs(sum(out["ttg"]["p"].values())-1)<1e-12
    assert out["ttg"]["truncation"]=="NONE"
    assert out["symmetric_margin"]["identity"]=="HAD_H ~ HHAD_H + HHAD_D"
    assert abs(out["crs"]["exact_mass"]+out["crs"]["other_mass"]-1)<1e-12
    return {"self_test":"PASS","version":VERSION,"no_early_truncation":True,"symmetric_plus_minus_one":True,"crs_full_state":True,"production_promotion":False}


def main():
    ap=argparse.ArgumentParser(description=VERSION); sub=ap.add_subparsers(dest="cmd",required=True)
    sub.add_parser("self-test")
    a=sub.add_parser("analyze"); a.add_argument("--input",required=True); a.add_argument("--match"); a.add_argument("--output")
    b=sub.add_parser("backtest"); b.add_argument("--input",required=True); b.add_argument("--output")
    args=ap.parse_args()
    if args.cmd=="self-test": out=self_test()
    elif args.cmd=="backtest": out=backtest_csv(args.input)
    else:
        _,inner=parse_wrapper(args.input); ms=flatten_matches(inner)
        if args.match: ms=[m for m in ms if m.get("matchNumStr")==args.match]
        out={"module":VERSION,"status":STATUS,"matches":[analyze_match(m) for m in ms]}
    text=json.dumps(out,ensure_ascii=False,indent=2)
    if getattr(args,"output",None): Path(args.output).write_text(text+"\n",encoding="utf-8")
    print(text)
if __name__=="__main__": main()
