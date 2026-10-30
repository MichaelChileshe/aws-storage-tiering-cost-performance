#!/usr/bin/env python3
"""gp2 burst credits, measured: one long 4 KiB random-read run on the gp2 volume while CloudWatch's
BurstBalance (percent of the 5.4 million I/O credit bucket left) is polled every 30 s.

  python3 scripts/burst_watch.py [--role gp2] [--runtime 240]

A gp2 volume earns 3 credits per GiB per second (its baseline IOPS) and spends one credit per
I/O. Running at the burst ceiling of 3000 IOPS, a 100 GiB volume (300 IOPS baseline) loses
3000 - 300 = 2700 credits a second, so a full bucket lasts 5,400,000 / 2700 = 2000 s.
This run does NOT empty the bucket; it measures the decline and estimates the time to empty.
EBS publishes these metrics every minute; they arrive a few minutes late, so the script keeps
polling for up to --tail minutes after the run.

Saves build/results/burst-<role>.json.
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

from kit_aws import aws, write_result
from lab import agent_send, agent_wait, append_jsonl, now, region, volume_now, volumes

BUCKET = 5_400_000  # I/O credits in a full gp2 bucket (EBS User Guide)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def series(vol, reg, start, end):
    dps = aws("cloudwatch", "get-metric-statistics", "--region", reg, "--namespace", "AWS/EBS",
              "--metric-name", "BurstBalance", "--dimensions", f"Name=VolumeId,Value={vol}",
              "--start-time", iso(start), "--end-time", iso(end), "--period", "60",
              "--statistics", "Average", "Minimum")["Datapoints"]
    # The AWS CLI can hand CloudWatch timestamps back in the LOCAL time zone (+02:00 on my laptop)
    # while every time in this script is UTC. Comparing those as strings ended the polling early,
    # so every timestamp is converted to UTC before it is stored or compared.
    return sorted(({"t": iso(datetime.fromisoformat(d["Timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)),
                    "avg": d["Average"], "min": d["Minimum"]} for d in dps), key=lambda d: d["t"])


def slope_per_min(points):
    """Least-squares slope of BurstBalance (percentage points per minute)."""
    if len(points) < 2:
        return None
    xs = [datetime.fromisoformat(p["t"].replace("Z", "+00:00")).timestamp() / 60 for p in points]
    ys = [p["avg"] for p in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den, 3) if den else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="gp2")
    ap.add_argument("--runtime", type=int, default=240)
    ap.add_argument("--tail", type=int, default=8, help="minutes to keep polling after the run")
    a = ap.parse_args()
    reg, vols = region(), volumes()
    if a.role not in vols:
        sys.exit(f"no {a.role} volume attached")
    v = volume_now(vols[a.role]["id"], reg)
    if v["type"] != "gp2":
        sys.exit(f"{a.role} is {v['type']} now: BurstBalance credits only apply to gp2 (run this before the gp2 -> gp3 migration)")
    baseline = min(max(100, 3 * v["size"]), 16000)
    t_start = datetime.now(timezone.utc)
    before = series(v["id"], reg, t_start - timedelta(minutes=15), t_start)
    print(f"== {a.role} {v['id']} gp2 {v['size']} GiB: baseline {baseline} IOPS, burst 3000")
    print(f"   BurstBalance before: {before[-1]['avg'] if before else 'no datapoint in the last 15 min'}")
    print(f"   starting a {a.runtime} s 4 KiB random-read run (queue depth 32) through SSM ...", flush=True)
    started = time.time()
    cid, iid = agent_send(f"drain --vol {v['id']} --name {a.role} --runtime {a.runtime}", exec_timeout=a.runtime + 300)
    run_start_utc, drain, seen = now(), None, set()
    deadline = started + a.runtime + 120 + a.tail * 60
    run_end = None
    try:
        while time.time() < deadline:
            time.sleep(30)
            pts = series(v["id"], reg, t_start - timedelta(minutes=5), datetime.now(timezone.utc))
            for p in pts:
                if p["t"] not in seen:
                    seen.add(p["t"])
                    print(f"   {p['t']}  BurstBalance {p['avg']:6.2f} %  (min {p['min']:.2f})", flush=True)
            if drain is None and time.time() - started > a.runtime:
                drain = agent_wait(cid, iid, timeout=a.runtime + 300, started=started)
                run_end = datetime.now(timezone.utc)
                r = drain[0]
                print(f"   run finished: {r['iops']:,.0f} IOPS for {r['runtime_ms'] / 1000:.0f} s, p99 {r['clat_p99_ms']} ms; "
                      f"waiting up to {a.tail} min for the last datapoints ...", flush=True)
            if run_end and pts and pts[-1]["t"] >= iso(run_end - timedelta(seconds=60)):
                break
    except KeyboardInterrupt:
        print("\n   stopped early (Ctrl+C): saving what I have", flush=True)
    if drain is None:
        drain = agent_wait(cid, iid, timeout=a.runtime + 300, started=started)
        run_end = datetime.now(timezone.utc)
    pts = series(v["id"], reg, t_start - timedelta(minutes=15), datetime.now(timezone.utc))
    r = drain[0]
    during = [p for p in pts if p["t"] >= iso(t_start - timedelta(minutes=1))]
    first = before[-1]["avg"] if before else (during[0]["avg"] if during else None)
    low = min((p["avg"] for p in during), default=None)
    spend = max(r["iops"] - baseline, 0)
    out = {
        "role": a.role, "volume": v, "baseline_iops": baseline, "bucket_credits": BUCKET,
        "run": r, "run_started_utc": run_start_utc, "series": pts,
        "balance_before_pct": first, "balance_lowest_pct": low,
        "decline_pct_points": round(first - low, 2) if first is not None and low is not None else None,
        "slope_pct_per_min": slope_per_min([p for p in during if not run_end or p["t"] <= iso(run_end)]),
        "expected_decline_pct_points": round(spend * r["runtime_ms"] / 1000 / BUCKET * 100, 2),
        "net_spend_credits_per_s": round(spend, 1),
        "est_seconds_to_empty_from_lowest": round(low / 100 * BUCKET / spend) if spend and low is not None else None,
        "est_seconds_full_bucket": round(BUCKET / spend) if spend else None,
    }
    append_jsonl("fio", [dict(r, role=a.role, volume=v, label="drain")])
    write_result(f"burst-{a.role}", out)
    print(f"\n   BurstBalance {out['balance_before_pct']} % -> lowest {out['balance_lowest_pct']} %: "
          f"down {out['decline_pct_points']} points (expected from IOPS: {out['expected_decline_pct_points']})")
    print(f"   net spend {out['net_spend_credits_per_s']} credits/s -> empty in about "
          f"{out['est_seconds_to_empty_from_lowest']} s from the lowest point; a full bucket lasts "
          f"{out['est_seconds_full_bucket']} s at this rate")
    print(f"   slope over the run: {out['slope_pct_per_min']} points/min")
    print(f"saved build/results/burst-{a.role}.json")


if __name__ == "__main__":
    main()
