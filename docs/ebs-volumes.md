# EBS volume types, measured on one instance

Zenzele runs databases on gp2 and io2, and export staging on gp2. Before choosing anything else, I wanted each volume type's real numbers, measured the same way, next to what AWS documents.

## The instance is a ceiling too

`bench_info.py` read the c7g.xlarge's EBS limits from EC2 (`describe-instance-types`) before any test ran. Screenshot [`05`](screenshots/05-bench-info.png). Data: [`results/bench-info.json`](results/bench-info.json).

| c7g.xlarge, EBS-optimized | Bandwidth | Throughput | IOPS |
|---|---|---|---|
| Baseline (sustained) | 1,250 Mbps | 156.25 MB/s | 6,000 |
| Maximum (burst) | 10,000 Mbps | 1,250 MB/s | 40,000 |

AWS documents that sizes like this can burst to the maximum for 30 minutes at least once every 24 hours. Every 20 s run here fits inside that, so a result above the baseline (io2's 1,000 MiB/s, the tuned gp3's 500 MiB/s) shows the instance bursting, not what it could sustain all day.

On the instance: fio 3.32 with the libaio engine, boto3 1.40.31, Python 3.9.25, kernel 6.18 (Amazon Linux 2023, arm64). All six volumes were visible, each found by its volume ID.

## The jobs

For each volume, in turn, through SSM:
- **prefill**: one 128 MiB sequential write, so every later read hits real data;
- **randread4k**: 4 KiB random reads, queue depth 32, 2 s warm-up + 20 s;
- **randwrite4k**: 4 KiB random writes, queue depth 32, 2 s + 20 s;
- **seqread1m**: 1 MiB sequential reads, queue depth 16, 2 s + 20 s.

All direct I/O on the raw device (no filesystem, no page cache), inside the first 128 MiB. Data: [`results/fio-base.json`](results/fio-base.json), every row in [`results/fio.jsonl`](results/fio.jsonl), raw fio JSON per job in [`results/raw/fio/`](results/raw/fio/). Screenshots [`06`](screenshots/06-fio-gp2-gp3-io2.png) and [`07`](screenshots/07-fio-st1-sc1-summary.png).

## Results (07:32–07:40 UTC)

| Volume | randread4k IOPS | p50 / p99 ms | randwrite4k IOPS | p50 / p99 ms | seqread1m MiB/s | p50 / p99 ms |
|---|---|---|---|---|---|---|
| gp2 100 GiB | 2,998 | 10.68 / 11.21 | 2,998 | 10.68 / 10.81 | 128.1 | 119.0 / 126.4 |
| gp3 100 GiB, 3,000 / 125 | 2,998 | 10.68 / 10.81 | 2,998 | 10.68 / 10.95 | 125.1 | 126.4 / 128.5 |
| io2 100 GiB, 4,000 IOPS | 3,998 | 8.03 / 8.29 | 3,998 | 8.03 / 8.16 | 1,000.0 | 15.1 / 16.9 |
| st1 125 GiB | 31 | 1,002 / 1,720 | 31 | 1,002 / 1,720 | 33.0 | 484 / 726 |
| sc1 125 GiB | 10 | 2,902 / 5,469 | 10 | 3,003 / 5,536 | 9.7 | 1,602 / 2,768 |

| Volume | What AWS documents for this type and size | Measured against it |
|---|---|---|
| gp2 100 GiB | 3 IOPS/GiB = **300 baseline**, bursts to 3,000 on credits; up to 128 MiB/s | At the **burst** ceiling, not the baseline |
| gp3 | 3,000 IOPS / 125 MiB/s provisioned, no credits | 100% |
| io2 | 4,000 IOPS provisioned | 100% |
| st1 125 GiB | 40 MiB/s per TiB baseline = 4.9; burst 250 per TiB = 30.5 MiB/s | 33.0 MiB/s, 8% above my per-TiB figure |
| sc1 125 GiB | 12 MiB/s per TiB baseline = 1.5; burst 80 per TiB = 9.8 MiB/s | 9.7 MiB/s, 99% of burst |

## What the numbers say

- **At 100 GiB, gp2 and gp3 look identical, and that is the trap.** gp2 delivered 3,000 IOPS only because it was spending burst credits: CloudWatch showed its BurstBalance at 98% at 07:36–07:37 after these two 20 s runs, back to 100% by 07:41. Its own baseline is 300. The [burst drill](gp2-burst-credits.md) shows how long the credits last.
- **io2 delivered exactly its 4,000 IOPS,** with the lowest latency of the three SSDs at their starting settings, because it had the most IOPS (see the next point). Its 1 MiB reads reached 1,000 MiB/s: 999 reads a second, each counted by EBS as four 256 KiB operations, so it hit its provisioned 4,000 IOPS. That is well above the instance's 156.25 MB/s baseline, so it also used the instance's burst.
- **The latency at queue depth 32 is mostly queueing.** 32 I/Os in flight at 2,998 IOPS means each waits about 32 / 2,998 = 10.7 ms, which is exactly the p50 measured. At 3,998 IOPS it's 8.0 ms, again the p50. These numbers compare volumes with each other; they are not a database's latency.
- **st1 and sc1 charge a full 1 MiB credit for every I/O up to 1 MiB.** The 4 KiB random runs prove it: 31 IOPS on st1 is 31 MiB/s of credits, right at its burst throughput, and 10 IOPS on sc1 is about its 9.8 MiB/s burst. Random I/O on HDD volumes takes seconds per operation (sc1 p99 5.5 s). They are for large sequential reads and writes, and neither can be a boot volume.
- **st1 measured 8% above the burst figure I computed from AWS's per-TiB rate.** I report it as measured; a 20 s run on a brand-new 125 GiB volume is a short sample.
- **The prefill numbers for the SSDs aren't measurements.** gp2 wrote 128 MiB at 735.6 MiB/s, gp3 at 1,084.8 and io2 at 2,782.6, each in under a second and far above what the volumes are documented to sustain. The prefill only exists to put data under the reads. For st1 (33.3 MiB/s over 4.2 s) and sc1 (9.7 MiB/s over 13.7 s) the prefill does match their burst throughput.

## The io2 volume came off first

io2 cost more per hour than everything else in the lab together: US$0.3733 an hour for 100 GiB with 4,000 IOPS (US$0.125 per GB-month plus US$0.065 per IOPS-month, from the Price List). Once its run was saved, I removed `io2_on` from the phases file. Terraform planned exactly `2 to destroy` (the attachment, then the volume) and detached it from the running instance. Screenshots [`08`](screenshots/08-io2-off-plan.png)–[`10`](screenshots/10-state-list-34.png).
