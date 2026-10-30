#!/usr/bin/env python3
"""Zenzele's storage bill, before and after tiering, from TODAY's AWS list prices.

  python3 scripts/cost_model.py [--estate estate/zenzele-estate.json] [--cached] [--instance-type c7g.xlarge]

1. Fetches every price it needs from the Price List API (us-east-1 list prices, on-demand) and
   saves them (prices.json; every raw record in pricing-raw.jsonl). --cached reuses prices.json.
2. TRAP DRILL: N small lab-result files kept in S3 Standard vs lifecycled to Glacier Flexible
   Retrieval (40 KiB of metadata per object: 32 KiB at the Glacier rate + 8 KiB at the Standard
   rate, plus a transition request per object) vs Glacier IR / Standard-IA (128 KiB minimum
   billable size) vs Intelligent-Tiering (objects under 128 KiB are never monitored or moved) vs
   bundling the files first.
3. ESTATE: every data set in the estate file, every option, monthly and one-time cost, whether
   the option meets the data set's requirement, and - for EBS - whether this lab's fio runs
   confirmed the volume type delivers its documented performance. The recommendation is the
   cheapest option that meets the requirement and did not measure below spec.
Writes trap.json, cost-model.json, cost-model.csv.
"""
import argparse
import csv
import json
import os

from kit_aws import RESULTS, ROOT, write_result
from pricing import add_instance, fetch_all, instance_key, per_mibps_month, tiered, unit_price
from specs import spec

KIB, MIB = 1.0 / (1024 * 1024), 1.0 / 1024  # in GiB
MIN_DURATION_DAYS = {"STANDARD_IA": 30, "GLACIER_IR": 90, "GLACIER": 90, "DEEP_ARCHIVE": 180}  # S3 User Guide
LAB_PASS = 0.85  # measured / documented below this = the lab did NOT confirm the spec


def money(x):
    return "-" if x is None else f"{x:,.2f}"


# ---------------------------------------------------------------- lab evidence

def lab_evidence():
    """Latest fio row per (role, label, job) from fio.jsonl."""
    p = os.path.join(RESULTS, "fio.jsonl")
    ev = {}
    if os.path.exists(p):
        for line in open(p):
            r = json.loads(line)
            if r.get("kind") == "fio":
                ev[(r["role"], r["label"], r["job"])] = r
    return ev


def lab_check(vtype, ev):
    """(ratio or None, text) - does this lab's measurement confirm the documented performance?"""
    def get(role, label, job):
        return ev.get((role, label, job))
    checks = []
    if vtype == "gp2":
        r = get("gp2", "base", "randread4k")
        if r:
            checks.append((r["iops"] / 3000.0, f"gp2 100 GiB burst {r['iops']:,.0f} of 3,000 IOPS", True))
    elif vtype == "gp3":
        # Only the default 3000/125 run gates the recommendation: the tuned run (6000 IOPS /
        # 500 MiB/s) can be capped by the INSTANCE's EBS limits, which says nothing about gp3.
        for label, iops_spec, mib_spec, gate in (("base", 3000, 125, True), ("tuned-after", None, None, False)):
            rr, sq = get("gp3", label, "randread4k"), get("gp3", label, "seqread1m")
            tag = "" if gate else ", info only"
            if rr:
                isp = iops_spec or rr["volume"]["iops"]
                checks.append((rr["iops"] / isp, f"gp3 {label} {rr['iops']:,.0f} of {isp:,} IOPS{tag}", gate))
            if sq:
                msp = mib_spec or sq["volume"]["throughput"]
                checks.append((sq["mibps"] / msp, f"gp3 {label} {sq['mibps']:,.0f} of {msp} MiB/s{tag}", gate))
    elif vtype == "io2":
        r = get("io2", "base", "randread4k")
        if r:
            checks.append((r["iops"] / r["volume"]["iops"], f"io2 {r['iops']:,.0f} of {r['volume']['iops']:,} IOPS", True))
    elif vtype in ("st1", "sc1"):
        r = get(vtype, "base", "seqread1m")
        if r:
            sp = spec(vtype, r["volume"]["size"])
            # Gate on the documented BASELINE (always owed); the burst depends on credits the
            # volume had at that moment, which AWS doesn't document for a brand-new volume.
            checks.append((r["mibps"] / sp["mibps_burst"], f"{vtype} {r['volume']['size']} GiB {r['mibps']:.1f} of "
                           f"{sp['mibps_burst']} MiB/s burst", False))
            checks.append((r["mibps"] / sp["mibps_base"], f"{r['mibps']:.1f} of {sp['mibps_base']} MiB/s baseline", True))
    if not checks:
        return None, "not measured in this lab"
    gating = [c[0] for c in checks if c[2]]
    worst = min(gating) if gating else None
    return (round(worst, 3) if worst is not None else None), "; ".join(f"{t} ({c * 100:.0f}%)" for c, t, _ in checks)


# ---------------------------------------------------------------- trap drill

def trap_drill(t, P):
    n, kib = t["objects"], t["object_kib"]
    data = n * kib * KIB
    std, gl, gir, sia, fa = (unit_price(P[k]) for k in ("s3_standard", "s3_glacier", "s3_gir", "s3_sia", "s3_int_fa"))
    t_gl, t_gir, t_sia = (unit_price(P[k]) for k in ("s3_tr_glacier", "s3_tr_gir", "s3_tr_sia"))
    rows = []

    def add(option, monthly, one_time, note):
        rows.append({"option": option, "monthly_usd": monthly, "one_time_usd": one_time, "note": note})

    add("STANDARD (keep)", data * std, 0.0, f"{data:,.1f} GiB of data")
    gl_month = data * gl + n * 32 * KIB * gl + n * 8 * KIB * std
    add("GLACIER via lifecycle", gl_month, n * t_gl,
        f"data + {n * 40 * KIB:,.1f} GiB of per-object metadata (32 KiB at Glacier + 8 KiB at Standard); "
        f"one transition request per object; 90-day minimum; restores take minutes to hours; "
        f"needs transition_default_minimum_object_size = varies_by_storage_class (the default blocks it)")
    add("GLACIER_IR via lifecycle", n * 128 * KIB * gir, n * t_gir,
        f"billed at the 128 KiB minimum: {n * 128 * KIB:,.1f} GiB; 90-day minimum; blocked by the 128 KiB default")
    add("STANDARD_IA via lifecycle", n * 128 * KIB * sia, n * t_sia,
        f"billed at the 128 KiB minimum: {n * 128 * KIB:,.1f} GiB; 30-day minimum; blocked by the 128 KiB default")
    add("INTELLIGENT_TIERING", data * fa, 0.0,
        "objects under 128 KiB are not monitored and stay in Frequent Access: no monitoring fee, no saving")
    bundles = max(1, int(data * 1024 / t["bundle_mib"]) + 1)
    add(f"BUNDLE {t['bundle_mib']} MiB -> GLACIER", data * gl + bundles * 40 * KIB * gl, bundles * t_gl + n * unit_price(P["s3_req_tier2"]),
        f"{bundles:,} bundles; one-time = reading every small object once + {bundles:,} transitions")
    base = rows[0]["monthly_usd"]
    for r in rows:
        saving = base - r["monthly_usd"]
        r["monthly_saving_usd"] = saving
        r["payback_months"] = (round(r["one_time_usd"] / saving, 1) if saving > 0 and r["one_time_usd"] else
                               (0.0 if saving > 0 else None))
        r["payback"] = ("-" if r is rows[0] else "never" if r["payback_months"] is None else r["payback_months"])
    return {"objects": n, "object_kib": kib, "data_gib": data, "rows": rows,
            "prices": {"standard": std, "glacier": gl, "gir": gir, "sia": sia, "int_fa": fa,
                       "transition_to_glacier_per_1000": t_gl * 1000, "transition_to_gir_per_1000": t_gir * 1000,
                       "transition_to_sia_per_1000": t_sia * 1000}}


# ---------------------------------------------------------------- S3 options

def s3_option(d, opt, P):
    std_p, a = P["s3_standard"], d.get("age_share", {})
    up = lambda k: unit_price(P[k])  # noqa: E731
    if opt in ("KEEP_ALL", "EXPIRE_AFTER_35"):
        days = d["current_retained_days"] if opt == "KEEP_ALL" else d["needed_days"]
        gib = d["daily_gib"] * days
        return {"monthly": tiered(std_p, gib), "one_time": 0.0, "feasible": True,
                "note": f"{gib:,.0f} GiB in Standard ({days} daily exports)"}
    gib = d["gib"]
    obj_gib = d.get("avg_object_mib", 0) * MIB or d.get("avg_object_kib", 0) * KIB
    objects = gib / obj_gib
    small = obj_gib < 128 * KIB
    hot, warm, old = gib * a.get("0_30", 0), gib * a.get("30_90", 0), gib * a.get("90_plus", 0)
    ms = d.get("needs_millisecond_access", False)
    if opt == "STANDARD":
        return {"monthly": tiered(std_p, gib), "one_time": 0.0, "feasible": True,
                "note": f"{objects:,.0f} objects, all in Standard"}
    if opt == "INTELLIGENT_TIERING":
        if small:
            return {"monthly": gib * up("s3_int_fa"), "one_time": 0.0, "feasible": True,
                    "note": "objects < 128 KiB: never monitored or moved, billed at Frequent Access (= no saving)"}
        mon = objects * up("s3_int_monitor")
        m = hot * up("s3_int_fa") + warm * up("s3_int_ia") + old * up("s3_int_aia") + mon
        return {"monthly": m, "one_time": objects * up("s3_tr_int"), "feasible": True,
                "note": f"FA/IA/Archive Instant by last access; monitoring {money(mon)}/month for {objects:,.0f} objects; "
                        "no retrieval fees"}
    if opt == "LIFECYCLE_GIR_AFTER_90":
        m = tiered(std_p, hot + warm) + old * up("s3_gir") + old * d.get("monthly_read_share_of_old", 0) * up("s3_retr_gir")
        return {"monthly": m, "one_time": old / obj_gib * up("s3_tr_gir"), "feasible": not small,
                "note": "older than 90 days -> Glacier IR (ms access, retrieval fee per GB read, 90-day minimum)"
                        + ("; objects < 128 KiB would not transition" if small else "")}
    if opt == "LIFECYCLE_GLACIER_AFTER_30":
        moved = warm + old
        n = moved / obj_gib
        m = tiered(std_p, hot) + moved * up("s3_glacier") + n * 32 * KIB * up("s3_glacier") + n * 8 * KIB * up("s3_standard")
        return {"monthly": m, "one_time": n * up("s3_tr_glacier"), "feasible": not ms,
                "note": f"{n:,.0f} objects x 40 KiB metadata; restores take minutes to hours"
                        + (" - FAILS the millisecond-access requirement" if ms else "")
                        + ("; objects < 128 KiB only move if the 128 KiB default is overridden" if small else "")}
    if opt == "LIFECYCLE_IA_30_GLACIER_90":
        n_old = old / obj_gib
        m = (tiered(std_p, hot) + warm * up("s3_sia") + old * up("s3_glacier")
             + n_old * 32 * KIB * up("s3_glacier") + n_old * 8 * KIB * up("s3_standard"))
        monthly_objs = d.get("monthly_ingest_gib", 0) / obj_gib
        recurring = monthly_objs * (up("s3_tr_sia") + up("s3_tr_glacier"))
        return {"monthly": m + recurring, "one_time": warm / obj_gib * up("s3_tr_sia") + n_old * up("s3_tr_glacier"),
                "feasible": not ms, "note": f"30 d -> Standard-IA, 90 d -> Glacier Flexible; includes "
                                            f"{money(recurring)}/month of transition requests for new logs"}
    if opt == "BUNDLE_TO_GIR":
        moved = warm + old
        n = moved / obj_gib
        bundles = moved * 1024 / d.get("bundle_mib", 512)
        m = (tiered(std_p, hot) + moved * up("s3_gir")
             + moved * d.get("monthly_read_share_of_old", 0) * up("s3_retr_gir"))
        return {"monthly": m, "one_time": n * up("s3_req_tier2") + bundles * up("s3_req_gir_tier1"), "feasible": True,
                "note": f"results older than 30 days packed into {bundles:,.0f} x {d.get('bundle_mib', 512)} MiB objects in "
                        "Glacier IR; one result = one byte-range GET via an index"}
    return {"monthly": None, "one_time": None, "feasible": False, "note": f"unknown option {opt}"}


# ---------------------------------------------------------------- EBS options

def ebs_option(d, vtype, P, ev):
    n, gib, need = d["count"], d["gib"], d["need"]
    up = lambda k: unit_price(P[k])  # noqa: E731
    seq_need = need["dump_gib"] * 1024 / (need["window_hours"] * 3600) if need.get("dump_gib") else None
    note, ok = "", True
    if vtype == "gp2":
        sp = spec("gp2", gib)
        cost = n * gib * up("ebs_gp2")
        if need.get("iops") and sp["iops_base"] < need["iops"]:
            ok, note = False, f"baseline {sp['iops_base']} IOPS < {need['iops']} needed; 3000 only while credits last"
        else:
            note = f"baseline {sp['iops_base']} IOPS, up to {sp['mibps_burst']} MiB/s"
        if seq_need and seq_need > sp["mibps_burst"]:
            ok, note = False, f"{seq_need:.0f} MiB/s needed > {sp['mibps_burst']}"
    elif vtype == "gp3":
        iops = max(3000, need.get("iops") or 3000)
        mib = max(125, need.get("mibps") or 0, seq_need or 0)
        iops = max(iops, int(-(-mib // 0.25)))  # throughput needs at least 4 IOPS per MiB/s
        tp = per_mibps_month(P["ebs_gp3_tput"])
        cost = n * (gib * up("ebs_gp3") + (iops - 3000) * up("ebs_gp3_iops") + (mib - 125) * tp)
        note = f"{iops:,} IOPS / {mib:,.0f} MiB/s provisioned"
        if iops > min(80000, 500 * gib) or mib > 2000:
            ok, note = False, note + " - beyond gp3 limits for this size"
        if need.get("durability_99_999"):
            ok, note = False, note + " - gp3 is designed for 99.8-99.9% durability, requirement is 99.999% (io2)"
    elif vtype == "io2":
        iops = need.get("iops") or 3000
        t1 = min(iops, 32000)
        t2 = max(0, min(iops, 64000) - 32000)
        cost = n * (gib * up("ebs_io2") + t1 * up("ebs_io2_iops") + t2 * (up("ebs_io2_iops_t2") or 0))
        note = f"{iops:,} IOPS provisioned, 99.999% durability"
        if iops > 1000 * gib:
            ok, note = False, note + " - over 1000 IOPS per GiB"
    else:  # st1 / sc1
        sp = spec(vtype, gib)
        cost = n * gib * up(f"ebs_{vtype}")
        if not need.get("sequential_only"):
            ok, note = False, "HDD: random I/O counts every I/O as a 1 MiB credit - not for databases"
        else:
            fits = seq_need <= sp["mibps_base"] or (seq_need <= sp["mibps_burst"] and need["dump_gib"] <= gib)
            ok = fits
            note = (f"needs {seq_need:.0f} MiB/s: baseline {sp['mibps_base']}, burst {sp['mibps_burst']} MiB/s "
                    f"(bucket ~{gib} GiB of credits)" + ("" if fits else " - too slow"))
    ratio, lab = lab_check(vtype, ev)
    if ratio is not None and ratio < LAB_PASS:
        ok, note = False, note + f" - lab measured below spec ({ratio * 100:.0f}%)"
    return {"monthly": cost, "one_time": 0.0, "feasible": ok, "note": note, "lab_check": lab, "lab_ratio": ratio}


def snapshot_option(d, opt, P):
    up = lambda k: unit_price(P[k])  # noqa: E731
    if opt == "standard":
        gib = d["full_gib"] + (d["count"] - 1) * d["incremental_gib_each"]
        return {"monthly": gib * up("ebs_snap"), "one_time": 0.0, "feasible": True,
                "note": f"incremental chain: {gib:,.0f} GiB stored"}
    gib = d["count"] * d["full_gib"]
    restore = d.get("restores_per_year", 0) * d["full_gib"] * (up("ebs_snap_archive_retrieval") or 0) / 12
    breakeven = up("ebs_snap_archive") / up("ebs_snap")
    return {"monthly": gib * up("ebs_snap_archive") + restore, "one_time": 0.0, "feasible": True,
            "note": f"archive keeps FULL copies: {gib:,.0f} GiB; pays only when each snapshot's unique data is more than "
                    f"{breakeven * 100:.0f}% of a full copy (here {d['incremental_gib_each'] / d['full_gib'] * 100:.0f}%); "
                    "90-day minimum; restore up to 72 h"}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--estate", default=os.path.join(ROOT, "estate", "zenzele-estate.json"))
    ap.add_argument("--cached", action="store_true")
    ap.add_argument("--instance-type", default="c7g.xlarge")
    ap.add_argument("--allow-missing", action="store_true",
                    help="carry on when a price is missing (affected options show '-'); default: stop")
    a = ap.parse_args()
    est = json.load(open(a.estate))
    add_instance(a.instance_type)
    cache = os.path.join(RESULTS, "prices.json")
    if a.cached and os.path.exists(cache):
        P = json.load(open(cache))["prices"]
        print(f"== prices from {cache} (cached)")
    else:
        print("== list prices, US East (N. Virginia), from the Price List API")
        P = fetch_all()
        errs = [p for p in P.values() if p.get("error")]
        if len(errs) == len(P):
            raise SystemExit(f"\nthe Price List API answered no price at all: {errs[0]['error']}\n"
                             "(prices.json left as it was)")
        from lab import now
        write_result("prices", {"fetched_utc": now(), "location": "US East (N. Virginia)", "prices": P})
    missing = [k for k, p in P.items() if p.get("error")]
    if missing:
        print(f"\n!! no price for: {', '.join(missing)}")
        for k in missing:
            print(f"   {k}: {P[k].get('error')}")
        if not a.allow_missing:
            raise SystemExit("\nstopping: a cost model with a missing or ambiguous price is not a cost model.\n"
                             "Fix the lookup in scripts/pricing.py (or rerun with --allow-missing to see the rest).")
        print("   --allow-missing: options that need them show '-' and are left out of the totals")

    def safe(fn, *args):
        try:
            return fn(*args)
        except TypeError:  # a needed price is missing (None in the arithmetic)
            return {"monthly": None, "one_time": None, "feasible": False, "note": "a needed price is missing"}

    # ---- trap drill
    trap = safe(trap_drill, est["trap_drill"], P)
    write_result("trap", trap)
    if "rows" in trap:
        print(f"\n== TRAP DRILL: {trap['objects']:,} lab results of {trap['object_kib']} KiB = {trap['data_gib']:,.1f} GiB")
        print(f"   {'option':<28} {'USD/month':>11} {'one-time USD':>13} {'saves/month':>12} {'payback months':>15}")
        for r in trap["rows"]:
            pb = r["payback"]
            print(f"   {r['option']:<28} {money(r['monthly_usd']):>11} {money(r['one_time_usd']):>13} "
                  f"{money(r['monthly_saving_usd']):>12} {pb!s:>15}")
        for r in trap["rows"]:
            print(f"   - {r['option']}: {r['note']}")

    # ---- estate
    ev = lab_evidence()
    items = []
    for d in est["s3"]:
        items.append(("S3", d, {o: safe(s3_option, d, o, P) for o in d["options"]}, d["current"]))
    for d in est["ebs"]:
        items.append(("EBS", d, {o: safe(ebs_option, d, o, P, ev) for o in d["options"]}, d["current"]["type"]))
    s = est["snapshots"]
    items.append(("Snapshots", s, {o: safe(snapshot_option, s, o, P) for o in s["options"]}, s["current"]))

    rows, before, after, one_time, left_out = [], 0.0, 0.0, 0.0, []
    print("\n== ESTATE: monthly cost per option (USD, list prices)")
    for kind, d, opts, cur in items:
        feas = {o: r for o, r in opts.items() if r["feasible"] and r["monthly"] is not None}
        rec = min(feas, key=lambda o: feas[o]["monthly"]) if feas else cur
        print(f"\n   [{kind}] {d['id']}: {d['desc']}")
        for o, r in opts.items():
            mark = ("CURRENT " if o == cur else "        ") + ("-> RECOMMENDED" if o == rec else "")
            print(f"     {o:<28} {money(r['monthly']):>12}/month  one-time {money(r['one_time']):>10}  "
                  f"{'ok ' if r['feasible'] else 'NO '} {mark}")
            print(f"        {r['note']}")
            if r.get("lab_check"):
                print(f"        lab: {r['lab_check']}")
            rows.append({"kind": kind, "item": d["id"], "desc": d["desc"], "option": o, "current": o == cur,
                         "recommended": o == rec, "feasible": r["feasible"], "monthly_usd": r["monthly"],
                         "one_time_usd": r["one_time"], "lab_check": r.get("lab_check", ""), "note": r["note"]})
        if opts[cur]["monthly"] is not None and opts[rec]["monthly"] is not None:
            before += opts[cur]["monthly"]
            after += opts[rec]["monthly"]
            one_time += opts[rec]["one_time"] or 0.0
        else:
            left_out.append(d["id"])
    total = {"before_usd_month": before, "after_usd_month": after, "saving_usd_month": before - after,
             "saving_pct": round((before - after) / before * 100, 1) if before else None,
             "one_time_usd": one_time, "left_out_missing_prices": left_out}
    lab_hour = None
    if not P.get(instance_key(a.instance_type), {}).get("error"):
        lab_hour = {"instance_usd_h": unit_price(P[instance_key(a.instance_type)]),
                    "io2_100gib_4000iops_usd_h": (100 * unit_price(P["ebs_io2"]) + 4000 * unit_price(P["ebs_io2_iops"])) / 730}
    write_result("cost-model", {"estate": est["name"], "rows": rows, "total": total, "lab_hourly": lab_hour,
                                "lab_pass_ratio": LAB_PASS, "min_duration_days": MIN_DURATION_DAYS})
    with open(os.path.join(RESULTS, "cost-model.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n== TOTAL: {money(before)} USD/month today -> {money(after)} USD/month recommended "
          f"(saves {money(before - after)}/month, {total['saving_pct']}%), one-time {money(one_time)} USD")
    if left_out:
        print(f"   left out of the totals (a price is missing): {', '.join(left_out)}")
    if lab_hour:
        print(f"   this lab: {a.instance_type} {lab_hour['instance_usd_h']:.4f} USD/h; the io2 volume "
              f"{lab_hour['io2_100gib_4000iops_usd_h']:.4f} USD/h")
    print("saved build/results/trap.json, cost-model.json, cost-model.csv")


if __name__ == "__main__":
    main()
