#!/usr/bin/env python3
"""Zenzele storage-tiering bench agent. Runs ON the bench instance (Amazon Linux 2023, its system
Python 3.9), always called through SSM by the scripts in scripts/. Every measurement is printed as
one line:  RESULT {json}

  python3 bench_agent.py info
  python3 bench_agent.py fill  --vol vol-... --mib 200
  python3 bench_agent.py fio   --vol vol-... --name gp2 --label base [--runtime 20] [--bucket B]
  python3 bench_agent.py drain --vol vol-... --name gp2 --runtime 240 [--bucket B]
  python3 bench_agent.py s3    --bucket B --express X [--region us-east-1] [--small-reps 100] [--large-reps 10]
  python3 bench_agent.py parse <fio-output.json>      (re-parse a saved raw fio file anywhere)

Safety: a volume is found by its EBS volume ID (Nitro exposes it as the NVMe serial number), and
the agent refuses any device that is mounted (the root volume is), so it can only ever write to
the empty lab volumes.
"""
import argparse
import datetime
import glob
import json
import os
import platform
import subprocess
import sys
import time
import urllib.request

WORK = "/var/tmp/bench"
REGION_SIZE = "128M"  # every job works inside the first 128 MiB of the device, written first
JOBS = {
    # name: (rw, block size, queue depth)
    "randread4k": ("randread", "4k", 32),
    "randwrite4k": ("randwrite", "4k", 32),
    "seqread1m": ("read", "1M", 16),
}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def result(obj):
    print("RESULT " + json.dumps(obj, sort_keys=True), flush=True)


# ---------------------------------------------------------------- fio output parsing

def _pct(clat, q):
    """Completion-latency percentile q (e.g. 99.0) in ms from fio's clat_ns.percentile map."""
    for k, v in (clat.get("percentile") or {}).items():
        try:
            if abs(float(k) - q) < 1e-6:
                return round(v / 1e6, 3)
        except ValueError:
            continue
    return None


def parse_fio(text):
    """fio --output-format=json output -> {"fio_version", "results": [one dict per job and direction]}.
    Tolerates text before the JSON (fio can print warnings first)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in fio output")
    data = json.loads(text[start:])
    out = []
    for job in data.get("jobs", []):
        for direction in ("read", "write"):
            s = job.get(direction) or {}
            ios = s.get("total_ios", 0)
            if not ios and not s.get("io_bytes"):
                continue
            clat = s.get("clat_ns") or {}
            lat = s.get("lat_ns") or {}
            out.append({
                "job": job.get("jobname"),
                "dir": direction,
                "error": job.get("error", 0),
                "iops": round(s.get("iops", 0.0), 1),
                "mibps": round(s.get("bw_bytes", 0) / 1048576.0, 2),
                "lat_mean_ms": round(lat.get("mean", 0.0) / 1e6, 3),
                "clat_p50_ms": _pct(clat, 50.0),
                "clat_p99_ms": _pct(clat, 99.0),
                "clat_p999_ms": _pct(clat, 99.9),
                "clat_max_ms": round(clat.get("max", 0) / 1e6, 3),
                "runtime_ms": s.get("runtime"),
                "total_ios": ios,
            })
    return {"fio_version": data.get("fio version"), "results": out}


# ---------------------------------------------------------------- devices

def device_for(vol):
    """EBS volume ID -> /dev/nvmeXn1 on a Nitro instance."""
    serial = vol.replace("-", "")
    link = "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_" + serial
    if os.path.exists(link):
        return os.path.realpath(link)
    for ctrl in glob.glob("/sys/class/nvme/nvme*"):
        try:
            if open(os.path.join(ctrl, "serial")).read().strip() == serial:
                return "/dev/" + os.path.basename(ctrl) + "n1"
        except OSError:
            continue
    sys.exit("volume %s is not attached to this instance (no NVMe device with that serial)" % vol)


def refuse_if_mounted(dev):
    base = os.path.basename(dev)
    for line in open("/proc/mounts"):
        src = line.split()[0]
        if os.path.basename(os.path.realpath(src)).startswith(base):
            sys.exit("%s (or a partition of it) is mounted: refusing to touch it" % dev)


def all_ebs_devices():
    out = {}
    for link in sorted(glob.glob("/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol*")):
        if "-part" in link:
            continue
        vid = link.rsplit("_", 1)[1]
        out["vol-" + vid[3:]] = os.path.realpath(link)
    return out


# ---------------------------------------------------------------- fio runs

_ENGINE = []


def engine():
    """The fastest async I/O engine this fio build has (cached)."""
    if _ENGINE:
        return _ENGINE[0]
    _ENGINE.append(_pick_engine())
    return _ENGINE[0]


def _pick_engine():
    try:
        helptext = subprocess.run(["fio", "--enghelp"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "psync"
    for e in ("libaio", "io_uring"):
        if e in helptext.split():
            return e
    return "psync"


def run_fio(dev, name, rw, bs, depth, runtime, out_file, ramp=2, time_based=True):
    cmd = ["fio", "--name=" + name, "--filename=" + dev, "--direct=1", "--ioengine=" + engine(),
           "--rw=" + rw, "--bs=" + bs, "--iodepth=%d" % depth, "--numjobs=1", "--size=" + REGION_SIZE,
           "--offset=0", "--randrepeat=0", "--norandommap=1", "--group_reporting=1",
           "--output-format=json", "--output=" + out_file]
    if time_based:
        cmd += ["--time_based=1", "--runtime=%d" % runtime, "--ramp_time=%d" % ramp]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=runtime + ramp + 180)
    if r.returncode != 0:
        sys.exit("fio %s failed (%d): %s %s" % (name, r.returncode, r.stdout[-500:], r.stderr[-1500:]))
    parsed = parse_fio(open(out_file).read())
    parsed["wall_s"] = round(time.time() - t0, 1)
    return parsed


def upload(bucket, path, key):
    if not bucket:
        return None
    try:
        import boto3
        boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1")).upload_file(path, bucket, key)
        return key
    except Exception as e:  # the raw copy is a convenience: never fail a measurement on it
        print("raw upload skipped: %s" % e)
        return None


def cmd_fio(a):
    dev = device_for(a.vol)
    refuse_if_mounted(dev)
    os.makedirs(os.path.join(WORK, "raw"), exist_ok=True)
    marker = os.path.join(WORK, "prefilled-" + a.vol)
    if not os.path.exists(marker):
        # Write the test region once, so reads hit blocks that hold data.
        out = os.path.join(WORK, "raw", "%s-prefill.json" % a.vol)
        p = run_fio(dev, "prefill", "write", "1M", 8, 0, out, time_based=False)
        open(marker, "w").write(now())
        r = p["results"][0] if p["results"] else {}
        result({"kind": "prefill", "name": a.name, "vol": a.vol, "dev": dev, "mibps": r.get("mibps"),
                "wall_s": p["wall_s"], "fio_version": p["fio_version"]})
    jobs = a.jobs.split(",") if a.jobs else list(JOBS)
    for job in jobs:
        rw, bs, depth = JOBS[job]
        out = os.path.join(WORK, "raw", "%s-%s-%s.json" % (a.label, a.name, job))
        started = now()
        p = run_fio(dev, job, rw, bs, depth, a.runtime, out)
        key = upload(a.bucket, out, "raw/fio/%s/%s-%s.json" % (a.label, a.name, job))
        for r in p["results"]:
            r.update({"kind": "fio", "label": a.label, "name": a.name, "vol": a.vol, "dev": dev,
                      "engine": engine(), "queue_depth": depth, "block": bs, "started_utc": started,
                      "ended_utc": now(), "fio_version": p["fio_version"], "raw_key": key})
            result(r)


def cmd_drain(a):
    """One long 4k random-read run: spends gp2 burst credits so BurstBalance visibly drops."""
    dev = device_for(a.vol)
    refuse_if_mounted(dev)
    os.makedirs(os.path.join(WORK, "raw"), exist_ok=True)
    out = os.path.join(WORK, "raw", "drain-%s.json" % a.name)
    started = now()
    p = run_fio(dev, "drain", "randread", "4k", 32, a.runtime, out, ramp=0)
    key = upload(a.bucket, out, "raw/fio/drain/%s-drain.json" % a.name)
    for r in p["results"]:
        r.update({"kind": "drain", "name": a.name, "vol": a.vol, "dev": dev, "started_utc": started,
                  "ended_utc": now(), "fio_version": p["fio_version"], "raw_key": key})
        result(r)


def cmd_fill(a):
    dev = device_for(a.vol)
    refuse_if_mounted(dev)
    t0 = time.time()
    r = subprocess.run(["dd", "if=/dev/urandom", "of=" + dev, "bs=1M", "count=%d" % a.mib, "oflag=direct",
                        "status=none"], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        sys.exit("dd failed: " + r.stderr)
    subprocess.run(["sync"])
    result({"kind": "fill", "vol": a.vol, "dev": dev, "mib": a.mib, "seconds": round(time.time() - t0, 1),
            "utc": now()})


# ---------------------------------------------------------------- instance info

def imds(path):
    try:
        req = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
                                     headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
        tok = urllib.request.urlopen(req, timeout=2).read().decode()
        req = urllib.request.Request("http://169.254.169.254/latest/meta-data/" + path,
                                     headers={"X-aws-ec2-metadata-token": tok})
        return urllib.request.urlopen(req, timeout=2).read().decode()
    except Exception:
        return None


def cmd_info(a):
    try:
        fio_v = subprocess.run(["fio", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except OSError:
        fio_v = None
    try:
        import boto3
        b3 = boto3.__version__
    except ImportError:
        b3 = None
    result({"kind": "info", "ready": os.path.exists(os.path.join(WORK, "ready")), "fio": fio_v,
            "engine": engine() if fio_v else None, "boto3": b3, "python": platform.python_version(),
            "kernel": platform.release(), "instance_type": imds("instance-type"),
            "az": imds("placement/availability-zone"), "az_id": imds("placement/availability-zone-id"),
            "ebs_devices": all_ebs_devices()})


# ---------------------------------------------------------------- S3 latency

def pct(values, q):
    """Nearest-rank percentile."""
    v = sorted(values)
    if not v:
        return None
    k = max(1, int(-(-q * len(v) // 100)))  # ceil(q/100 * n)
    return round(v[min(k, len(v)) - 1], 2)


def stats(values):
    return {"p50": pct(values, 50), "p90": pct(values, 90), "p99": pct(values, 99),
            "min": round(min(values), 2) if values else None, "max": round(max(values), 2) if values else None}


def cmd_s3(a):
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError
    except ImportError:
        sys.exit("boto3 is not installed on this instance (dnf install -y python3-boto3)")
    cfg = Config(retries={"max_attempts": 2, "mode": "standard"}, max_pool_connections=4)
    s3 = boto3.client("s3", region_name=a.region, config=cfg)
    sizes = [("4KiB", 4096, a.small_reps), ("8MiB", 8 * 1048576, a.large_reps)]
    targets = [(c, a.bucket, c) for c in ("STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING", "GLACIER_IR", "GLACIER")]
    if a.express:
        targets.append(("EXPRESS_ONEZONE", a.express, None))
    raw = {"started_utc": now(), "boto3": boto3.__version__, "az_id": imds("placement/availability-zone-id"),
           "samples": {}}
    for size_name, size, reps in sizes:
        payload = os.urandom(size)
        for target, bucket, sc in targets:
            key = "bench/%s/%s.bin" % (target, size_name)
            row = {"kind": "s3", "target": target, "size": size_name, "bytes": size, "reps": reps}
            try:
                t0 = time.perf_counter()
                kw = {"Bucket": bucket, "Key": key, "Body": payload}
                if sc:
                    kw["StorageClass"] = sc
                s3.put_object(**kw)
                row["put_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            except Exception as e:
                row["error"] = "PUT: %s" % e
                result(row)
                continue
            ttfb, total = [], []
            for i in range(reps + 3):  # the first 3 are warm-up (connection, CreateSession for Express)
                t0 = time.perf_counter()
                try:
                    r = s3.get_object(Bucket=bucket, Key=key)
                except ClientError as e:
                    row["get_error"] = e.response.get("Error", {}).get("Code")
                    row["get_error_message"] = e.response.get("Error", {}).get("Message")
                    break
                t1 = time.perf_counter()
                body = r["Body"].read()
                t2 = time.perf_counter()
                if len(body) != size:
                    row["error"] = "short read %d of %d" % (len(body), size)
                    break
                if i == 0:
                    row["first_request_ms"] = round((t2 - t0) * 1000, 2)
                if i >= 3:
                    ttfb.append((t1 - t0) * 1000)
                    total.append((t2 - t0) * 1000)
            if ttfb:
                row["ttfb_ms"] = stats(ttfb)
                row["total_ms"] = stats(total)
                row["mibps_at_p50"] = round(size / 1048576.0 / (row["total_ms"]["p50"] / 1000.0), 2)
                row["storage_class_reported"] = r.get("StorageClass", "STANDARD")
            raw["samples"]["%s/%s" % (target, size_name)] = {"ttfb_ms": [round(x, 3) for x in ttfb],
                                                             "total_ms": [round(x, 3) for x in total]}
            result(row)
    raw["ended_utc"] = now()
    path = os.path.join(WORK, "s3-samples.json")
    os.makedirs(WORK, exist_ok=True)
    json.dump(raw, open(path, "w"))
    key = upload(a.bucket, path, "raw/s3/s3-samples-%s.json" % raw["started_utc"].replace(":", ""))
    result({"kind": "s3-raw", "raw_key": key, "boto3": raw["boto3"], "az_id": raw["az_id"]})


def cmd_parse(a):
    print(json.dumps(parse_fio(open(a.file).read()), indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    p = sub.add_parser("fill")
    p.add_argument("--vol", required=True)
    p.add_argument("--mib", type=int, default=200)
    p = sub.add_parser("fio")
    p.add_argument("--vol", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--runtime", type=int, default=20)
    p.add_argument("--jobs", default="")
    p.add_argument("--bucket", default="")
    p = sub.add_parser("drain")
    p.add_argument("--vol", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--runtime", type=int, default=240)
    p.add_argument("--bucket", default="")
    p = sub.add_parser("s3")
    p.add_argument("--bucket", required=True)
    p.add_argument("--express", default="")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--small-reps", type=int, default=100)
    p.add_argument("--large-reps", type=int, default=10)
    p = sub.add_parser("parse")
    p.add_argument("file")
    a = ap.parse_args(argv)
    {"info": cmd_info, "fill": cmd_fill, "fio": cmd_fio, "drain": cmd_drain, "s3": cmd_s3,
     "parse": cmd_parse}[a.cmd](a)


if __name__ == "__main__":
    main()
