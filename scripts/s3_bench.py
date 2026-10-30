#!/usr/bin/env python3
"""S3 storage classes and S3 Express One Zone, measured from the bench instance (same Region, and
for Express the same AZ). Runs the bench agent's s3 test through SSM:
  - the same 4 KiB and 8 MiB objects PUT as STANDARD, STANDARD_IA, INTELLIGENT_TIERING,
    GLACIER_IR and GLACIER in the lab bucket, and into the Express One Zone directory bucket;
  - 3 warm-up GETs, then N timed GETs each: time to the response headers (boto3 get_object
    returns, ~ time to first byte) and to the last byte; nearest-rank p50/p90/p99;
  - GLACIER (Flexible Retrieval): the GET fails until the object is restored - the error code is
    the result.

  python3 scripts/s3_bench.py [--small-reps 100] [--large-reps 10] [--label base]

Saves build/results/s3-latency-<label>.json (raw samples go to the lab bucket under raw/s3/).
"""
import argparse

from kit_aws import tf_output, write_result
from lab import agent, now


def f(x):
    return "-" if x is None else f"{x:.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--small-reps", type=int, default=100)
    ap.add_argument("--large-reps", type=int, default=10)
    ap.add_argument("--label", default="base")
    a = ap.parse_args()
    bucket, express, reg = tf_output("lab_bucket"), tf_output("express_bucket"), tf_output("region")
    print(f"== PUT + timed GETs from the bench instance: {bucket} and {express} (a minute or two) ...", flush=True)
    res = agent(f"s3 --bucket {bucket} --express {express} --region {reg} --small-reps {a.small_reps} "
                f"--large-reps {a.large_reps}", timeout=600)
    rows = [r for r in res if r["kind"] == "s3"]
    meta = next((r for r in res if r["kind"] == "s3-raw"), {})
    print(f"   boto3 {meta.get('boto3')} on the instance, AZ ID {meta.get('az_id')}; raw samples: {meta.get('raw_key')}")
    print(f"\n   {'target':<20} {'size':<5} {'PUT ms':>7} {'1st GET':>8} | {'TTFB p50':>8} {'p90':>6} {'p99':>6} | "
          f"{'total p50':>9} {'p99':>7} | {'MiB/s':>6}  note")
    for r in rows:
        t, tot = r.get("ttfb_ms", {}), r.get("total_ms", {})
        note = r.get("get_error") or r.get("error") or ""
        print(f"   {r['target']:<20} {r['size']:<5} {f(r.get('put_ms')):>7} {f(r.get('first_request_ms')):>8} | "
              f"{f(t.get('p50')):>8} {f(t.get('p90')):>6} {f(t.get('p99')):>6} | {f(tot.get('p50')):>9} "
              f"{f(tot.get('p99')):>7} | {f(r.get('mibps_at_p50')):>6}  {note}")
    write_result(f"s3-latency-{a.label}", {"label": a.label, "bucket": bucket, "express_bucket": express,
                                           "meta": meta, "rows": rows, "saved_utc": now()})
    print(f"\nsaved build/results/s3-latency-{a.label}.json")


if __name__ == "__main__":
    main()
