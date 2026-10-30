#!/usr/bin/env python3
"""EBS Snapshots Archive drill on the small "export" volume.

  python3 scripts/snapshot_archive.py fill              # write 200 MiB of random data to the volume
  python3 scripts/snapshot_archive.py cost              # price of archiving it, 90-day minimum included
  python3 scripts/snapshot_archive.py start [--max-usd 0.10]
  python3 scripts/snapshot_archive.py status [--wait-min 0]

An archived snapshot is a FULL copy (all blocks written to the volume), billed for at least 90
days even if deleted earlier; restoring one can take up to 72 hours. "start" refuses to archive
if the 90-day minimum for this snapshot would cost more than --max-usd.
Saves build/results/snapshot-archive.json (every observation appended).
"""
import argparse
import json
import os
import sys
import time

from kit_aws import RESULTS, aws, tf_output, write_result
from lab import agent, now, region, volumes
from pricing import fetch, unit_price

MIN_DAYS = 90


def load():
    p = os.path.join(RESULTS, "snapshot-archive.json")
    return json.load(open(p)) if os.path.exists(p) else {"observations": []}


def snapshot(reg):
    sid = tf_output("export_snapshot_id")
    if not sid:
        sys.exit("no snapshot yet: set snapshot_on = true and apply first")
    s = aws("ec2", "describe-snapshots", "--region", reg, "--snapshot-ids", sid)["Snapshots"][0]
    return sid, s


def cost(reg, rec):
    sid, s = snapshot(reg)
    full = s.get("FullSnapshotSizeInBytes")
    gib = full / 2 ** 30 if full else float(s["VolumeSize"])
    p = fetch("ebs_snap_archive")
    price = unit_price(p)
    if price is None:
        sys.exit(f"no price for EBS:SnapshotArchiveStorage: {p.get('error')}")
    usd = gib * price * MIN_DAYS / (365 / 12.0)
    rec["cost"] = {"snapshot_id": sid, "state": s["State"], "full_size_bytes": full, "volume_size_gib": s["VolumeSize"],
                   "billed_gib_basis": round(gib, 4), "archive_usd_per_gb_month": price,
                   "min_charge_usd_90_days": round(usd, 4),
                   "basis": "FullSnapshotSizeInBytes" if full else "volume size (upper bound: no full size reported)"}
    print(f"== {sid} ({s['State']}): full size {full if full is not None else 'not reported'} bytes, volume "
          f"{s['VolumeSize']} GiB")
    print(f"   archive {price} USD per GB-month x {gib:.4f} GiB x {MIN_DAYS} days = {usd:.4f} USD minimum "
          f"({rec['cost']['basis']})")
    return sid, s, usd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["fill", "cost", "start", "status"])
    ap.add_argument("--max-usd", type=float, default=0.10)
    ap.add_argument("--wait-min", type=int, default=0)
    ap.add_argument("--mib", type=int, default=200)
    a = ap.parse_args()
    reg, rec = region(), load()
    if a.action == "fill":
        vol = volumes()["small"]["id"]
        r = agent(f"fill --vol {vol} --mib {a.mib}", timeout=300)[0]
        rec["fill"] = r
        print(f"== wrote {r['mib']} MiB of random data to {vol} ({r['dev']}) in {r['seconds']} s")
    elif a.action == "cost":
        cost(reg, rec)
    elif a.action == "start":
        sid, s, usd = cost(reg, rec)
        if s["State"] != "completed":
            sys.exit(f"snapshot is {s['State']}: only completed snapshots can be archived - wait and run again")
        if usd > a.max_usd:
            write_result("snapshot-archive", rec)
            sys.exit(f"the 90-day minimum would be {usd:.4f} USD > {a.max_usd}: not archiving (recorded the number)")
        r = aws("ec2", "modify-snapshot-tier", "--region", reg, "--snapshot-id", sid, "--storage-tier", "archive")
        rec["archive_requested_utc"] = now()
        rec["tiering_start_time"] = r.get("TieringStartTime")
        rec["archive_requested"] = True
        print(f"   archive started: TieringStartTime {r.get('TieringStartTime')}")
    else:
        sid = tf_output("export_snapshot_id")
        if not sid:
            sys.exit("no snapshot in the state")
        deadline = time.time() + a.wait_min * 60
        while True:  # every observation is saved at once (Ctrl+C loses nothing)
            st = aws("ec2", "describe-snapshot-tier-status", "--region", reg,
                     "--filters", f"Name=snapshot-id,Values={sid}").get("SnapshotTierStatuses", [])
            st = st[0] if st else {}
            obs = {"utc": now(), "storage_tier": st.get("StorageTier"), "status": st.get("LastTieringOperationStatus"),
                   "progress": st.get("LastTieringProgress"), "start": st.get("LastTieringStartTime"),
                   "archival_complete": st.get("ArchivalCompleteTime"),
                   "detail": st.get("LastTieringOperationStatusDetail")}
            rec["observations"].append(obs)
            write_result("snapshot-archive", rec)
            print(f"   {obs['utc']}  tier={obs['storage_tier']}  {obs['status']}  progress={obs['progress']}%  "
                  f"started={obs['start']}  completed={obs['archival_complete']}", flush=True)
            if obs["status"] == "archival-completed" or time.time() >= deadline:
                break
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                print("\n   stopped (Ctrl+C); every observation so far is saved")
                break
    write_result("snapshot-archive", rec)
    print("saved build/results/snapshot-archive.json")


if __name__ == "__main__":
    main()
