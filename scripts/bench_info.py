#!/usr/bin/env python3
"""Is the bench ready, and what can this instance type do with EBS at all?

  python3 scripts/bench_info.py

1. From EC2 (describe-instance-types): the instance type's EBS-optimized baseline and maximum
   bandwidth, throughput and IOPS. Every volume result in this lab is capped by these.
2. On the instance (SSM, waits for the boot script to finish): fio and boto3 versions, the AZ,
   and the NVMe device of every attached EBS volume.
3. Every bench volume Terraform created must be visible on the instance.
Saves build/results/instance-ebs.json and bench-info.json.
"""
import sys

from kit_aws import aws, result_lines, tf_output, write_result
from lab import AGENT, instance_id, now, region, volume_now, volumes
from ssm_run import run


def main():
    reg, itype, iid = region(), tf_output("instance_type"), instance_id()
    it = aws("ec2", "describe-instance-types", "--region", reg, "--instance-types", itype)["InstanceTypes"][0]
    ebs = it.get("EbsInfo", {})
    opt = ebs.get("EbsOptimizedInfo", {})
    inst = {"instance_type": itype, "ebs_optimized_support": ebs.get("EbsOptimizedSupport"),
            "nvme_support": ebs.get("NvmeSupport"), "ebs_optimized_info": opt, "fetched_utc": now()}
    write_result("instance-ebs", inst)
    print(f"== {itype}: EBS-optimized {ebs.get('EbsOptimizedSupport')}, NVMe {ebs.get('NvmeSupport')}")
    print(f"   {'':<10} {'bandwidth Mbps':>15} {'throughput MB/s':>16} {'IOPS':>8}")
    for k in ("Baseline", "Maximum"):
        print(f"   {k:<10} {opt.get(k + 'BandwidthInMbps', '-'):>15} {opt.get(k + 'ThroughputInMBps', '-'):>16} "
              f"{opt.get(k + 'Iops', '-'):>8}")

    print("\n== on the instance (waits for the boot script to finish) ...", flush=True)
    cmd = (f"cloud-init status --wait > /dev/null 2>&1; {AGENT} info; "
           f"echo agent_sha256=$(sha256sum /opt/bench/bench_agent.py | cut -c1-16)")
    out = run([iid], cmd, reg, echo=False, timeout=600)[iid]
    info = result_lines(out)[0]
    sha = [l.split("=", 1)[1] for l in out.splitlines() if l.startswith("agent_sha256=")]
    print(f"   ready={info['ready']}  {info['fio']}  engine={info['engine']}  boto3={info['boto3']}  "
          f"python={info['python']}")
    print(f"   instance_type={info['instance_type']}  az={info['az']} ({info['az_id']})  "
          f"agent sha256={sha[0] if sha else '-'}")

    print("\n== bench volumes: Terraform -> EC2 -> device on the instance")
    vols, rows, missing = volumes(), [], []
    for role in sorted(vols):
        v = volume_now(vols[role]["id"], reg)
        dev = info["ebs_devices"].get(v["id"])
        if not dev:
            missing.append(role)
        rows.append({"role": role, **v, "device": dev})
        print(f"   {role:<6} {v['id']:<22} {v['type']:<4} {v['size']:>4} GiB  iops={v['iops'] or '-':<6} "
              f"throughput={v['throughput'] or '-':<5} -> {dev or 'NOT VISIBLE'}")
    write_result("bench-info", {"agent": info, "agent_sha256_16": sha[0] if sha else None, "volumes": rows,
                                "utc": now()})
    if not info["ready"] or not info["fio"]:
        sys.exit("\nthe boot script has not finished installing fio: wait a minute and run this again")
    if missing:
        sys.exit(f"\nnot visible on the instance yet: {', '.join(missing)} - wait a minute and run this again")
    print("\nall bench volumes visible; saved instance-ebs.json and bench-info.json")


if __name__ == "__main__":
    main()
