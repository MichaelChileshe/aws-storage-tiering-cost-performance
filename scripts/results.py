#!/usr/bin/env python3
"""Every measured number from build/results/ in one list (and build/results/summary.csv).
Anything not measured yet shows as "-".

  python3 scripts/results.py
"""
import csv
import json
import os
import re

from kit_aws import RESULTS


def j(name):
    p = os.path.join(RESULTS, f"{name}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def n(x, nd=0):
    if x is None or x == "":
        return None
    return f"{x:,.{nd}f}" if isinstance(x, (int, float)) else x


def main():
    rows = []

    def add(section, item, value):
        rows.append((section, item, "-" if value in (None, "") else value))

    files = sorted(os.listdir(RESULTS)) if os.path.isdir(RESULTS) else []

    ie = j("instance-ebs")
    if ie:
        o = ie["ebs_optimized_info"]
        for k in ("Baseline", "Maximum"):
            add("Instance EBS limits", f"{ie['instance_type']} {k.lower()}: Mbps / MB/s / IOPS",
                f"{o.get(k + 'BandwidthInMbps')} / {o.get(k + 'ThroughputInMBps')} / {o.get(k + 'Iops')}")
    bi = j("bench-info")
    if bi:
        ag = bi["agent"]
        add("Instance EBS limits", "fio / engine / boto3 / AZ ID", f"{ag['fio']} / {ag['engine']} / {ag['boto3']} / {ag['az_id']}")

    fio = []
    p = os.path.join(RESULTS, "fio.jsonl")
    if os.path.exists(p):
        fio = [json.loads(l) for l in open(p) if l.strip()]
    latest = {}
    for r in fio:
        if r.get("kind") == "fio":
            latest[(r["label"], r["role"], r["job"])] = r
        elif r.get("kind") == "prefill":
            add("fio", f"{r['role']} prefill 128 MiB seq write MiB/s", n(r.get("mibps"), 1))
    for (label, role, job), r in sorted(latest.items()):
        v = r["volume"]
        cfg = f"{v['type']} {v['size']}GiB" + (f" {v['iops']}iops" if v.get("iops") else "") + \
              (f" {v['throughput']}MiB/s" if v.get("throughput") else "")
        add("fio", f"{label} {role} ({cfg}) {job}: IOPS / MiB/s / p50 ms / p99 ms",
            f"{n(r['iops'])} / {n(r['mibps'], 1)} / {r.get('clat_p50_ms')} / {r.get('clat_p99_ms')}")
    for r in fio:
        if r.get("kind") == "drain":
            add("fio", f"drain {r['role']} {r['runtime_ms'] // 1000}s 4k randread: IOPS / p99 ms",
                f"{n(r['iops'])} / {r.get('clat_p99_ms')}")

    b = j("burst-gp2")
    if b:
        add("gp2 burst", "BurstBalance before / lowest / decline (pct points)",
            f"{b['balance_before_pct']} / {b['balance_lowest_pct']} / {b['decline_pct_points']}")
        add("gp2 burst", "expected decline from measured IOPS / slope pts per min",
            f"{b['expected_decline_pct_points']} / {b['slope_pct_per_min']}")
        add("gp2 burst", "net credits/s / est s to empty from lowest / full bucket lasts s",
            f"{b['net_spend_credits_per_s']} / {b['est_seconds_to_empty_from_lowest']} / {b['est_seconds_full_bucket']}")

    for name in files:
        if name.startswith("modify-") and name.endswith(".json"):
            m = j(name[:-5])
            mod = m["modification"]
            add("Elastic Volumes", f"{m['role']} {m['label']}: change",
                f"{mod.get('OriginalVolumeType')} {mod.get('OriginalIops')}/{mod.get('OriginalThroughput')} -> "
                f"{mod.get('TargetVolumeType')} {mod.get('TargetIops')}/{mod.get('TargetThroughput')}")
            add("Elastic Volumes", f"{m['role']} {m['label']}: AWS StartTime / EndTime / state",
                f"{mod.get('StartTime')} / {mod.get('EndTime')} / {mod.get('ModificationState')}")
            add("Elastic Volumes", f"{m['role']} {m['label']}: states seen",
                ", ".join(f"{e['seen_utc'][11:19]} {e['state']} {e['progress']}%" for e in m["events"]))
            if m.get("fio_during"):
                add("Elastic Volumes", f"{m['role']} {m['label']}: fio during ran while",
                    f"{m['fio_during']['state_at_start']} -> {m['fio_during']['state_at_end']}")

    for name in files:
        if name.startswith("s3-latency-") and name.endswith(".json"):
            s = j(name[:-5])
            for r in s["rows"]:
                t, tot = r.get("ttfb_ms", {}), r.get("total_ms", {})
                add("S3 latency", f"{s['label']} {r['target']} {r['size']}: PUT / TTFB p50 p90 p99 / total p50 p99 ms",
                    r.get("get_error") or r.get("error") or
                    f"{r.get('put_ms')} / {t.get('p50')} {t.get('p90')} {t.get('p99')} / {tot.get('p50')} {tot.get('p99')}")
    g = j("glacier-restore")
    if g:
        add("Glacier restore", "GET before restore", (g.get("get_before") or {}).get("error") or "OK")
        add("Glacier restore", "tier / expedited unavailable", f"{g.get('tier')} / {g.get('expedited_unavailable', False)}")
        add("Glacier restore", "requested / first seen restored", f"{g.get('requested_utc')} / {g.get('restored_seen_utc')}")
        add("Glacier restore", "GET after restore", (g.get("get_after") or {}).get("ok"))

    c = j("s3-config")
    if c:
        lc = c.get("lifecycle", {})
        add("S3 config", "TransitionDefaultMinimumObjectSize", lc.get("TransitionDefaultMinimumObjectSize"))
        add("S3 config", "lifecycle rules", len(lc.get("Rules", [])))
        add("S3 config", "Storage Lens configurations", ", ".join(c.get("storage_lens_ids", [])))
        add("S3 config", "objects in bench/ by class",
            ", ".join(sorted({o["class"] for o in c.get("objects", [])})))

    sa = j("snapshot-archive")
    if sa:
        if sa.get("cost"):
            add("Snapshot archive", "full size bytes / 90-day minimum USD",
                f"{sa['cost']['full_size_bytes']} / {sa['cost']['min_charge_usd_90_days']}")
        add("Snapshot archive", "archive requested / TieringStartTime",
            f"{sa.get('archive_requested_utc')} / {sa.get('tiering_start_time')}")
        if sa.get("observations"):
            o = sa["observations"][-1]
            add("Snapshot archive", "last seen: tier / status / progress / completed",
                f"{o['utc']}: {o['storage_tier']} / {o['status']} / {o['progress']} / {o['archival_complete']}")

    t = j("trap")
    if t and t.get("rows"):
        for r in t["rows"]:
            add("Trap drill", f"{t['objects']:,} x {t['object_kib']} KiB {r['option']}: USD/month / one-time / payback",
                f"{n(r['monthly_usd'], 2)} / {n(r['one_time_usd'], 2)} / {r.get('payback', '-')}")
    cm = j("cost-model")
    if cm:
        for r in cm["rows"]:
            if r["current"] or r["recommended"]:
                tag = ("current+recommended" if r["current"] and r["recommended"] else
                       "current" if r["current"] else "recommended")
                add("Cost model", f"{r['item']} {tag}: {r['option']} USD/month", n(r["monthly_usd"], 2))
        tot = cm["total"]
        add("Cost model", "TOTAL before / after / saving USD/month (%) / one-time",
            f"{n(tot['before_usd_month'], 2)} / {n(tot['after_usd_month'], 2)} / {n(tot['saving_usd_month'], 2)} "
            f"({tot['saving_pct']}%) / {n(tot['one_time_usd'], 2)}")

    for name in files:
        if name.startswith("tf-") and name.endswith(".txt"):
            line = None
            for ln in open(os.path.join(RESULTS, name), errors="replace"):
                if re.search(r"^(Plan:|Destroy complete!|Apply complete!|No changes\.)", ln.strip()):
                    line = ln.strip()
            add("Terraform", name, line)
    if not rows:
        print("nothing measured yet")
        return
    w = max(len(r[1]) for r in rows)
    sec = None
    for s, i, v in rows:
        if s != sec:
            print(f"\n== {s}")
            sec = s
        print(f"  {i:<{w}}  {v}")
    with open(os.path.join(RESULTS, "summary.csv"), "w", newline="") as f:
        csv.writer(f).writerows([("section", "item", "value"), *rows])
    print("\nsaved build/results/summary.csv")


if __name__ == "__main__":
    main()
