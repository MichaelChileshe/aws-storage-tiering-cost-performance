#!/usr/bin/env python3
"""The SAME three fio jobs on every bench volume, one volume at a time, through SSM:
  randread4k  (4 KiB random read,  queue depth 32)  -> IOPS
  randwrite4k (4 KiB random write, queue depth 32)  -> IOPS
  seqread1m   (1 MiB sequential read, queue depth 16) -> MiB/s
Direct I/O on the raw device (no filesystem, no page cache), inside the first 128 MiB, which the
agent writes once first. Each job runs 2 s warm-up + --runtime seconds.

  python3 scripts/fio_bench.py --label base                      # every volume except "small"
  python3 scripts/fio_bench.py --label tuned --only gp3 [--runtime 20]

Appends to build/results/fio.jsonl and writes build/results/fio-<label>.json.
"""
import argparse

from kit_aws import tf_output, write_result
from lab import agent, append_jsonl, now, region, volume_now, volumes
from specs import spec

ORDER = ["gp2", "gp3", "io2", "st1", "sc1"]


def fmt(x, nd=0):
    return "-" if x is None else (f"{x:,.{nd}f}")


def run_one(role, label, runtime, vols, reg):
    """fio on one volume (prefill first if needed); prints and appends to fio.jsonl; returns the rows."""
    bucket = tf_output("lab_bucket")  # raw fio JSON is copied there (raw/fio/...)
    timeout = 3 * (runtime + 15) + 240
    v = volume_now(vols[role]["id"], reg)
    sp = spec(v["type"], v["size"], v["iops"], v["throughput"])
    print(f"== {role}: {v['id']} {v['type']} {v['size']} GiB iops={v['iops']} throughput={v['throughput']}  "
          f"[{label}] (about {3 * (runtime + 3)} s) ...", flush=True)
    res = agent(f"fio --vol {v['id']} --name {role} --label {label} --runtime {runtime} --bucket {bucket}",
                timeout=timeout)
    for r in res:
        r.update({"role": role, "volume": v, "spec": sp, "label": label, "saved_utc": now()})
        if r["kind"] == "prefill":
            print(f"   prefill 128 MiB sequential write: {fmt(r.get('mibps'), 1)} MiB/s ({r['wall_s']} s)")
        else:
            print(f"   {r['job']:<12} {fmt(r['iops'])} IOPS  {fmt(r['mibps'], 1)} MiB/s  "
                  f"p50 {fmt(r['clat_p50_ms'], 2)} ms  p99 {fmt(r['clat_p99_ms'], 2)} ms")
    print(f"   spec: {sp['note']}")
    append_jsonl("fio", res)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--runtime", type=int, default=20)
    a = ap.parse_args()
    vols, reg = volumes(), region()
    roles = [r for r in ORDER if r in vols and (not a.only or r in a.only.split(","))]
    if not roles:
        raise SystemExit(f"none of {a.only or ORDER} is attached right now (volumes: {', '.join(sorted(vols))})")
    rows = []
    for role in roles:
        rows += run_one(role, a.label, a.runtime, vols, reg)
    write_result(f"fio-{a.label}", {"label": a.label, "runtime_s": a.runtime, "rows": rows})

    print(f"\n== {a.label}: summary (IOPS, MiB/s, completion latency p99 in ms)")
    print(f"   {'role':<5} {'type':<4} {'GiB':>4} | {'randread IOPS':>13} {'p99':>7} | {'randwrite IOPS':>14} {'p99':>7} | "
          f"{'seqread MiB/s':>13} {'p99':>7}")
    by = {}
    for r in rows:
        if r["kind"] == "fio":
            by.setdefault(r["role"], {})[r["job"]] = r
    for role in roles:
        j, v = by.get(role, {}), next(r["volume"] for r in rows if r["role"] == role)
        rr, rw, sq = j.get("randread4k", {}), j.get("randwrite4k", {}), j.get("seqread1m", {})
        print(f"   {role:<5} {v['type']:<4} {v['size']:>4} | {fmt(rr.get('iops')):>13} {fmt(rr.get('clat_p99_ms'), 2):>7} | "
              f"{fmt(rw.get('iops')):>14} {fmt(rw.get('clat_p99_ms'), 2):>7} | {fmt(sq.get('mibps'), 1):>13} "
              f"{fmt(sq.get('clat_p99_ms'), 2):>7}")
    print(f"\nsaved build/results/fio-{a.label}.json (+ fio.jsonl)")


if __name__ == "__main__":
    main()
