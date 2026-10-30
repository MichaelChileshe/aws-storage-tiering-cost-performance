#!/usr/bin/env python3
"""Watch an Elastic Volumes modification (started by "terraform apply") from modifying through
optimizing to completed, and run the same fio jobs DURING it and AFTER it.

  python3 scripts/modwatch.py --role gp2 --label migrate [--runtime 20] [--deadline-min 40] [--no-fio]

- Polls describe-volumes-modifications every 10 s and logs every state/progress change with the
  time I saw it; AWS's own StartTime/EndTime are the authoritative duration.
- The first time the state is "modifying" or "optimizing", runs fio (label <label>-during).
  After "completed", runs fio again (label <label>-after).
- Saves after every change, so Ctrl+C or the deadline never loses what was seen. If the
  modification isn't completed by the deadline, run the same command again later: it picks up
  the same modification, keeps the earlier observations and only runs the "after" fio.
Saves build/results/modify-<role>-<label>.json.
"""
import argparse
import json
import os
import sys
import time

from fio_bench import run_one
from kit_aws import RESULTS, aws, write_result
from lab import now, region, volume_now, volumes


def latest_mod(vol, reg):
    mods = aws("ec2", "describe-volumes-modifications", "--region", reg, "--volume-ids", vol).get(
        "VolumesModifications", [])
    return sorted(mods, key=lambda m: m.get("StartTime", ""))[-1] if mods else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--runtime", type=int, default=20)
    ap.add_argument("--deadline-min", type=int, default=40)
    ap.add_argument("--no-fio", action="store_true")
    a = ap.parse_args()
    reg, vols = region(), volumes()
    if a.role not in vols:
        sys.exit(f"no {a.role} volume attached")
    vol = vols[a.role]["id"]
    m = latest_mod(vol, reg)
    if not m:
        sys.exit(f"{vol} has no modification: did the apply change it? (terraform plan should show it in-place)")
    print(f"== {a.role} {vol}: {m.get('OriginalVolumeType')} {m.get('OriginalIops')} IOPS "
          f"{m.get('OriginalThroughput')} MiB/s -> {m.get('TargetVolumeType')} {m.get('TargetIops')} IOPS "
          f"{m.get('TargetThroughput')} MiB/s; started {m.get('StartTime')}")

    rec = {"role": a.role, "volume_id": vol, "label": a.label, "modification": m, "events": [],
           "fio_during": None, "fio_after": None}
    path = os.path.join(RESULTS, f"modify-{a.role}-{a.label}.json")
    if os.path.exists(path):  # a re-run for the same modification keeps the earlier observations
        prev = json.load(open(path))
        if prev.get("modification", {}).get("StartTime") == m.get("StartTime"):
            rec["events"] = prev.get("events", [])
            rec["fio_during"] = prev.get("fio_during")
            print(f"   continuing: {len(rec['events'])} earlier observation(s) kept")

    def save():
        rec["modification"] = m
        rec["completed"] = m["ModificationState"] == "completed"
        rec["saved_utc"] = now()
        write_result(f"modify-{a.role}-{a.label}", rec)

    last = None
    deadline = time.time() + a.deadline_min * 60
    try:
        while time.time() < deadline:
            m = latest_mod(vol, reg)
            key = (m["ModificationState"], m.get("Progress"))
            if key != last:
                ev = {"seen_utc": now(), "state": key[0], "progress": key[1]}
                rec["events"].append(ev)
                print(f"   {ev['seen_utc']}  {ev['state']:<10} progress {ev['progress']} %", flush=True)
                last = key
                save()
            state = m["ModificationState"]
            if state == "failed":
                save()
                sys.exit(f"modification failed: {m.get('StatusMessage')}")
            if state == "completed":
                if rec["fio_during"] is None and len(rec["events"]) == 1:
                    print("   already completed the first time I looked: no 'during' run is possible (recorded)")
                break
            if state in ("modifying", "optimizing") and rec["fio_during"] is None and not a.no_fio:
                print(f"   running fio while the volume is {state} ...", flush=True)
                during = {"state_at_start": state, "volume_at_start": volume_now(vol, reg),
                          "rows": run_one(a.role, f"{a.label}-during", a.runtime, vols, reg)}
                during["state_at_end"] = latest_mod(vol, reg)["ModificationState"]
                rec["fio_during"] = during
                save()
                continue
            time.sleep(10)
        m = latest_mod(vol, reg)
        save()
        if m["ModificationState"] == "completed" and not a.no_fio:
            print("   completed; running fio on the final configuration ...", flush=True)
            rec["fio_after"] = {"volume": volume_now(vol, reg),
                                "rows": run_one(a.role, f"{a.label}-after", a.runtime, vols, reg)}
            save()
    except KeyboardInterrupt:
        print("\n   stopped early (Ctrl+C): saved what I have", flush=True)
        save()
    print(f"\n   AWS StartTime {m.get('StartTime')}  EndTime {m.get('EndTime', '-')}  state {m['ModificationState']}")
    if m["ModificationState"] != "completed":
        print("   not completed yet: run the same command again later (earlier observations are kept)")
    print(f"saved build/results/modify-{a.role}-{a.label}.json")


if __name__ == "__main__":
    main()
