#!/usr/bin/env python3
"""Glacier Flexible Retrieval, by hand: the object is listed but can't be read until it's restored.
Run from WSL after s3_bench.py has written bench/GLACIER/4KiB.bin.

  python3 scripts/glacier_restore.py [--key bench/GLACIER/4KiB.bin] [--wait-min 20]
  python3 scripts/glacier_restore.py --status        # look again later (e.g. after a Standard fallback)

1. head-object: storage class GLACIER, no restore yet.
2. get-object: refused with InvalidObjectState (what an application sees).
3. restore-object, Expedited tier, 1 day. Expedited is "subject to demand": if S3 answers
   GlacierExpeditedRetrievalNotAvailable, it falls back to the Standard tier (typically 3-5 hours)
   and only records the request.
4. Polls head-object every 15 s until the Restore field says ongoing-request="false" or the
   deadline passes; then GETs the object to prove it is readable (it stays in GLACIER; the
   readable copy expires after the restore days).
Saves build/results/glacier-restore.json.
"""
import argparse
import json
import os
import tempfile
import time

from kit_aws import RESULTS, aws, aws_try, tf_output, write_result
from lab import now


def head(bucket, key):
    h = aws("s3api", "head-object", "--bucket", bucket, "--key", key)
    return {"storage_class": h.get("StorageClass", "STANDARD"), "restore": h.get("Restore"),
            "bytes": h.get("ContentLength")}


def try_get(bucket, key):
    tmp = os.path.join(tempfile.gettempdir(), "glacier-get.bin")
    t0 = time.time()
    ok, _, err = aws_try("s3api", "get-object", "--bucket", bucket, "--key", key, tmp)
    ms = round((time.time() - t0) * 1000)
    if os.path.exists(tmp):
        os.remove(tmp)
    code = None
    if not ok:
        code = "InvalidObjectState" if "InvalidObjectState" in err else err.strip().splitlines()[-1][:200]
    return {"ok": ok, "error": code, "cli_ms": ms, "utc": now()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="bench/GLACIER/4KiB.bin")
    ap.add_argument("--wait-min", type=int, default=None, help="default 20 (0 with --status)")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.wait_min is None:
        a.wait_min = 0 if a.status else 20
    bucket = tf_output("lab_bucket")
    path = os.path.join(RESULTS, "glacier-restore.json")
    rec = json.load(open(path)) if os.path.exists(path) else {"bucket": bucket, "key": a.key, "polls": []}

    h = head(bucket, a.key)
    print(f"== s3://{bucket}/{a.key}: {h['bytes']} bytes, storage class {h['storage_class']}, Restore={h['restore']}")
    if not a.status:
        g = try_get(bucket, a.key)
        rec["get_before"] = g
        print(f"   GET before restore: {'OK' if g['ok'] else 'refused: ' + str(g['error'])}")
        req = json.dumps({"Days": 1, "GlacierJobParameters": {"Tier": "Expedited"}})
        rec["requested_utc"] = now()
        ok, _, err = aws_try("s3api", "restore-object", "--bucket", bucket, "--key", a.key, "--restore-request", req)
        tier = "Expedited"
        if not ok and "GlacierExpeditedRetrievalNotAvailable" in err:
            print("   Expedited: GlacierExpeditedRetrievalNotAvailable (no capacity right now) -> Standard tier")
            rec["expedited_unavailable"] = True
            tier = "Standard"
            req = json.dumps({"Days": 1, "GlacierJobParameters": {"Tier": "Standard"}})
            rec["requested_utc"] = now()
            ok, _, err = aws_try("s3api", "restore-object", "--bucket", bucket, "--key", a.key, "--restore-request", req)
        if not ok and "RestoreAlreadyInProgress" in err:
            print("   RestoreAlreadyInProgress: a restore was already requested; polling it")
            ok = True
        if not ok:
            write_result("glacier-restore", rec)
            raise SystemExit(f"restore-object failed: {err}")
        rec["tier"] = tier
        write_result("glacier-restore", rec)  # saved before the (slow) polling
        print(f"   restore requested at {rec['requested_utc']} ({tier}, 1 day)")
        if tier == "Standard" and a.wait_min <= 20:
            a.wait_min = 0  # Standard takes hours: record the request, check later with --status
    deadline = time.time() + a.wait_min * 60
    try:
        while True:
            h = head(bucket, a.key)
            rec["polls"].append({"utc": now(), "restore": h["restore"]})
            done = bool(h["restore"]) and 'ongoing-request="false"' in h["restore"]
            print(f"   {now()}  Restore={h['restore']}", flush=True)
            if done:
                rec.setdefault("restored_seen_utc", now())
                break
            if time.time() >= deadline:
                break
            write_result("glacier-restore", rec)
            time.sleep(15)
    except KeyboardInterrupt:
        print("\n   stopped early (Ctrl+C): saved what I have; check later with --status")
    if rec.get("restored_seen_utc"):
        g = try_get(bucket, a.key)
        rec["get_after"] = g
        print(f"   GET after restore: {'OK' if g['ok'] else 'refused: ' + str(g['error'])}; storage class still "
              f"{head(bucket, a.key)['storage_class']}")
        print(f"   requested {rec.get('requested_utc')} -> first seen restored {rec['restored_seen_utc']}")
    else:
        print(f"   not restored yet ({rec.get('tier')}); run again later with --status")
    write_result("glacier-restore", rec)
    print("saved build/results/glacier-restore.json")


if __name__ == "__main__":
    main()
