#!/usr/bin/env python3
"""Regenerates docs/architecture.svg + docs/architecture.png using the OFFICIAL AWS Architecture Icons.

The icon pack is not committed (AWS licenses it for diagrams, not redistribution). Download it from
https://aws.amazon.com/architecture/icons/, unzip, then:

    AWS_ICONS_DIR=/path/to/unzipped-icon-package python3 build_architecture.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from awsdiag import Diagram, GREY, INK

EBS, S3C, OPS, IAC, NET, WARN, OK = ("#3F8624", "#7AA116", "#E7157B", "#7B42BC", "#8C4FFF", "#D13212", "#1D8102")
d = Diagram(1900, 1220)
d.text(40, 40, "Storage tiering, measured: five EBS volume types and five S3 storage classes on one bench, "
               "built and destroyed with Terraform", 19, "bold")
d.text(40, 64, "Zenzele Health  ·  us-east-1, one Availability Zone (AZ ID use1-az4 = us-east-1c in my account)  ·  "
               "VPC 10.29.0.0/16  ·  state in S3 with native locking  ·  30 October 2026", 13, "normal", GREY)


def node(key, x, y, title, lines=(), color=INK, size=44, gap=15):
    d.icon(key, x, y, size)
    d.text(x + size + 10, y + 15, title, 12.5, "bold", color)
    for i, l in enumerate(lines):
        d.text(x + size + 10, y + 32 + i * gap, l, 11, "normal", GREY)


# ---------------------------------------------------------------- my machine
d.box(40, 100, 300, 560, IAC, fill="#F7F2FC", dashed=False)
d.text(56, 126, "My machine (WSL)", 13.5, "bold", IAC)
d.icon("client", 56, 140, 40)
d.text(106, 158, "terraform plan / apply / destroy", 12, "bold", INK)
d.text(106, 174, "phases = lines in phases.auto.tfvars", 11, "normal", GREY)
for i, l in enumerate(["bench_on, io2_on       (bench)", "snapshot_on            (archive drill)",
                       "gp2_to_gp3             (gp2 -> gp3 in place)", "gp3_iops / gp3_throughput (tuning)"]):
    d.text(56, 206 + i * 16, l, 11, "normal", GREY)
d.text(56, 290, "quality gates before any apply:", 12, "bold", INK)
d.text(56, 308, "fmt · validate · tflint · checkov", 11, "normal", GREY)
d.text(56, 336, "the drills run as Python scripts:", 12, "bold", INK)
for i, l in enumerate(["bench_info  (instance EBS limits, devices)", "fio_bench   (same 3 fio jobs per volume)",
                       "burst_watch (gp2 credits in CloudWatch)", "modwatch    (Elastic Volumes timeline)",
                       "s3_bench · glacier_restore · s3_config", "snapshot_archive (cost, start, status)",
                       "cost_model  (trap + whole estate)", "results     (every number, one list)"]):
    d.text(56, 354 + i * 16, l, 11, "normal", GREY)
d.text(56, 498, "estate/zenzele-estate.json:", 12, "bold", INK)
d.text(56, 516, "the hospital group's data sets, sizes,", 11, "normal", GREY)
d.text(56, 532, "access ages and requirements", 11, "normal", GREY)
d.text(56, 562, "no SSH, no key pair, no inbound port:", 12, "bold", INK)
d.text(56, 580, "every command on the instance goes", 11, "normal", GREY)
d.text(56, 596, "through SSM Run Command", 11, "normal", GREY)
d.text(56, 630, "AWS APIs: SSM, EC2, CloudWatch, S3, Pricing", 11, "normal", GREY)

# ---------------------------------------------------------------- AWS
d.group("cloud", 370, 90, 1100, 1110, "AWS Cloud")
node("s3_bucket", 700, 100, "nkosi-tfstate-(account id)", ["state key aws-storage-tiering-cost-performance/"], IAC, 34)
node("data_table", 1110, 100, "Price List API", ["today's us-east-1 list prices"], OPS, 34)
d.group("region", 390, 150, 1060, 1030, "us-east-1")
d.group("vpc", 410, 195, 600, 685, "zenzele-tiering VPC 10.29.0.0/16")
d.box(425, 235, 570, 555, "#00A4A6", fill="none", dashed=True)
d.text(437, 254, "Availability Zone use1-az4 (us-east-1c)", 12.5, "bold", "#00A4A6")
d.group("public", 440, 265, 540, 510, "Public subnet 10.29.1.0/24, security group with NO inbound rules")

node("ec2_instance", 460, 305, "Bench instance c7g.xlarge (Graviton, AL2023)", [
    "EBS-optimized: baseline 156.25 MB/s, 6000 IOPS",
    "maximum 1250 MB/s, 40000 IOPS (burst)",
    "IMDSv2 · encrypted gp3 root · fio 3.32 + boto3"], EBS, 44)
d.text(460, 400, "Six data volumes, attached through Terraform (NVMe, found by volume ID):", 11.5, "bold", INK)
vols = [
    ("ebs_volume", "gp2 100 GiB", "300 IOPS baseline, 3000 burst on credits", "-> gp3 3000/125 in place"),
    ("ebs_gp3", "gp3 100 GiB 3000 / 125 MiB/s", "provisioned, no credits", "-> 6000 IOPS / 500 MiB/s in place"),
    ("ebs_volume", "io2 100 GiB 4000 IOPS", "measured, then removed (US$0.37/h)", ""),
    ("ebs_volume", "st1 125 GiB (HDD)", "throughput-optimised, 1 MiB credits", ""),
    ("ebs_volume", "sc1 125 GiB (HDD)", "cold, cheapest per GB", ""),
    ("ebs_gp3", "gp3 1 GiB 'nightly export'", "200 MiB written, then snapshotted", ""),
]
for i, (ic, t, a, b) in enumerate(vols):
    y = 418 + i * 60
    d.icon(ic, 470, y, 36)
    d.text(514, y + 14, t, 11.8, "bold", EBS)
    d.text(514, y + 30, a, 10.8, "normal", GREY)
    if b:
        d.text(760, y + 22, b, 11, "bold", OPS)
node("ebs_snapshot", 800, 720, "EBS snapshot", ["-> archive tier (full copy)"], EBS, 36)
d.line([(714, 742), (796, 740)], EBS, width=1.8)

node("igw", 440, 810, "Internet gateway", ["SSM, dnf repos, S3 Express"], NET, 34)
node("vpc_endpoints", 700, 810, "S3 gateway endpoint", ["to the general purpose bucket"], NET, 34)

# ---------------------------------------------------------------- regional services
d.box(1030, 195, 405, 300, S3C, fill="#F6FAEF", dashed=False)
node("s3_bucket", 1044, 208, "zenzele-tiering-(suffix)", ["SSE-S3, Block Public Access, TLS-only policy"], S3C, 36)
cls = [("s3_standard", "STANDARD"), ("s3_sia", "STANDARD_IA"), ("s3_it", "INTELLIGENT_TIERING"),
       ("s3_gir", "GLACIER_IR"), ("s3_gfr", "GLACIER")]
for i, (ic, t) in enumerate(cls):
    x = 1046 + (i % 2) * 190
    y = 256 + (i // 2) * 40
    d.icon(ic, x, y, 30)
    d.text(x + 36, y + 19, t, 10.5, "bold", INK)
d.text(1046, 388, "the same 4 KiB + 8 MiB objects in each class", 11, "normal", GREY)
d.text(1046, 408, "5 lifecycle rules (incl. the 30-day Glacier TRAP)", 11, "normal", GREY)
d.text(1046, 424, "transition minimum size: all_storage_classes_128K", 11, "normal", GREY)
d.text(1046, 440, "Intelligent-Tiering archive config (research/)", 11, "normal", GREY)
d.text(1046, 456, "raw fio JSON + S3 samples under raw/", 11, "normal", GREY)

node("s3_express", 1044, 520, "S3 Express One Zone directory bucket", ["zenzele-express-(suffix)--use1-az4--x-s3",
                                                                       "same AZ as the instance, CreateSession auth"],
     S3C, 40)
d.line([(1000, 560), (1040, 545)], S3C, width=1.8, dashed=True)
node("storage_lens", 1044, 610, "S3 Storage Lens", ["zenzele-tiering-lens: free metrics, lab bucket only"], S3C, 36)
node("cloudwatch", 1044, 680, "CloudWatch", ["BurstBalance per gp2 volume, 1-minute"], OPS, 36)
node("run_command", 1044, 750, "SSM Run Command", ["fio, S3 timings, device lookup"], OPS, 36)
node("iam_role", 1044, 820, "Instance role", ["SSM + the two lab buckets only"], IAC, 36)

# ---------------------------------------------------------------- drill sequence
d.box(410, 900, 1025, 265, "#D5DBDB", fill="#fff", dashed=False)
d.text(426, 924, "One sitting, in order (every apply and destroy logged)", 13.5, "bold")
seq = [
    ("1", "base apply", "network, IAM, both buckets, rules, Storage Lens", "21 added"),
    ("2", "bench", "instance + 6 volumes + 6 attachments; fio on every volume", "13 added"),
    ("3", "io2 off", "the most expensive volume removed after its run", "2 destroyed"),
    ("4", "snapshot", "export volume snapshotted, then moved to the archive tier", "1 added"),
    ("5", "gp2 burst", "240 s at 3000 IOPS while BurstBalance is polled", "-"),
    ("6", "gp2 -> gp3", "Elastic Volumes, fio during optimizing and after", "1 changed"),
    ("7", "gp3 tuning", "6000 IOPS / 500 MiB/s on the same 100 GiB", "1 changed"),
    ("8", "S3", "classes + Express timed; Glacier GET refused; Expedited restore", "-"),
    ("9", "cost", "trap drill + whole estate from today's prices", "-"),
    ("10", "destroy", "everything, including the archived snapshot", "33 destroyed"),
]
for i, (n, a, b, c) in enumerate(seq):
    y = 948 + i * 22
    d.text(430, y, n, 11.5, "bold", IAC)
    d.text(460, y, a, 11.5, "bold", INK)
    d.text(560, y, b, 11, "normal", GREY)
    d.text(1420, y, c, 11.5, "bold", EBS, anchor="end")

# ---------------------------------------------------------------- measured panel
bx, by = 1490, 90
d.box(bx, by, 390, 1110, "#D5DBDB", fill="#fff", dashed=False)
d.text(bx + 14, by + 26, "Measured (one build, 30 October 2026)", 14, "bold")
res = [
    ("4 KiB random read, queue depth 32", EBS, ["gp2 2,998 IOPS (burst) · gp3 2,998", "io2 3,998 · st1 31 · sc1 10",
                                                "p99: io2 8.3 ms, gp3 10.8, sc1 5.5 s"]),
    ("1 MiB sequential read", EBS, ["gp2 128.1 · gp3 125.1 · io2 1,000.0 MiB/s", "st1 33.0 · sc1 9.7 MiB/s"]),
    ("gp2 burst credits", WARN, ["3,012 IOPS for 240 s: -3 points/minute", "a full bucket lasts ~1,991 s at that rate",
                                 "then 300 IOPS (this volume's baseline)"]),
    ("Elastic Volumes, attached, no downtime", OK, ["gp2 -> gp3: 5 min 3 s (AWS times)", "gp3 -> 6000/500: 5 min 13 s",
                                                    "new figures already during optimizing"]),
    ("S3 GET from the same AZ, 4 KiB", S3C, ["first byte p50: Express 6.1 ms", "Standard 27.0 · IA 29.7 · GIR 26.9 ms",
                                            "GLACIER: InvalidObjectState"]),
    ("Glacier Expedited restore", S3C, ["first seen restored after 77.9 s", "(polled every ~17 s)"]),
    ("Snapshot archive tier", EBS, ["200 MiB full copy, archived in 89.9 s", "90-day minimum: US$0.0072"]),
    ("Trap: 10 M x 20 KiB to Glacier", WARN, ["saves US$0.85/month", "costs US$300 in transitions",
                                              "payback 354 months; bundling: 1.1"]),
    ("Estate (list prices, my model)", OPS, ["US$6,661.52 -> 5,389.72 per month", "saves 1,271.80 (19.1%), one-time 187.53"]),
    ("Terraform", IAC, ["21 + 13 added · 2 destroyed · 1 added", "2 in-place changes · 33 destroyed",
                        "clean check: nothing left"]),
]
y = by + 58
for title, c, ls in res:
    d.text(bx + 14, y, title, 12, "bold", c)
    for l in ls:
        y += 16
        d.text(bx + 14, y, l, 11.2)
    y += 22

here = os.path.dirname(os.path.abspath(__file__))
d.save(os.path.join(here, "..", "architecture.svg"), os.path.join(here, "..", "architecture.png"))
print("wrote docs/architecture.svg and docs/architecture.png")
