# Elastic Volumes: gp2 → gp3 and more gp3 performance, with the volume attached

Zenzele's 12 gp2 database volumes need to become gp3 without a maintenance window. And when a gp3 volume needs more IOPS or throughput, the answer should not be "make it bigger". I measured both changes while the volume stayed attached and in use.

## How it runs

- One Terraform variable per change; the AWS provider treats a type, IOPS or throughput change on `aws_ebs_volume` as an **in-place update**, not a replacement.
- `modwatch.py` then follows the modification (`modifying` → `optimizing` → `completed`) with `describe-volumes-modifications`, runs the same fio jobs **while** the volume is optimizing, and again **after** it completes.
- AWS documents that during `optimizing` the volume's performance is somewhere between the old and the new specification. AWS's own `StartTime`/`EndTime` are the authoritative duration; my observation times only show when I looked.
- Each volume can be modified up to four times in a rolling 24 hours. Each volume here was modified once.

## gp2 → gp3 (3,000 IOPS / 125 MiB/s)

Terraform plan: `aws_ebs_volume.bench["gp2"]` updated in-place, `type "gp2" -> "gp3"`, `iops 300 -> 3000`, `throughput 0 -> 125`, `Plan: 0 to add, 1 to change, 0 to destroy.` The provider returned after **13 s** (`Modifications complete after 13s`): it doesn't wait for the optimization. Screenshots [`14`](screenshots/14-gp2-to-gp3-plan.png) and [`15`](screenshots/15-gp2-to-gp3-modwatch.png). Data: [`results/modify-gp2-migrate.json`](results/modify-gp2-migrate.json).

| Time (UTC) | Event |
|---|---|
| 08:07:38 | AWS `StartTime` |
| 08:08:08 | I see `optimizing`, 3% |
| 08:08:30–08:09:38 | fio **during** optimizing |
| 08:12:41 | AWS `EndTime`: **5 min 3 s** after the start |
| 08:13:20–08:14:28 | fio **after** |

| Run | randread4k IOPS (p99) | randwrite4k IOPS (p99) | seqread1m MiB/s (p99) |
|---|---|---|---|
| Before, as gp2 (bursting) | 2,998 (11.21 ms) | 2,998 (10.81 ms) | 128.1 (126 ms) |
| During optimizing | 2,998 (11.08 ms) | 2,998 (10.81 ms) | 125.2 (132 ms) |
| After, as gp3 | 2,998 (10.95 ms) | 2,998 (10.81 ms) | 125.2 (131 ms) |

The numbers barely move, which is the point: the volume kept serving at the same rate through the change. What changed is underneath: those 3,000 IOPS no longer come out of a credit bucket. The gp2 baseline was 300; the gp3 baseline is 3,000, all day.

## gp3 3,000 / 125 → 6,000 IOPS / 500 MiB/s, same 100 GiB

With gp2 the only way to more IOPS was a bigger volume (3 per GiB). gp3 sells IOPS (up to 500 per GiB) and throughput (up to 0.25 MiB/s per provisioned IOPS) separately from size. My Terraform refuses a throughput above 0.25 × IOPS before AWS would. Screenshot [`16`](screenshots/16-gp3-tuned-modwatch.png). Data: [`results/modify-gp3-tuned.json`](results/modify-gp3-tuned.json).

| Time (UTC) | Event |
|---|---|
| 08:17:10 | AWS `StartTime` (Terraform: 1 to change, returned after 13 s) |
| 08:17:38 | I see `optimizing`, 0% |
| 08:18:01–08:19:09 | fio **during** optimizing |
| 08:22:23 | AWS `EndTime`: **5 min 13 s** after the start |
| 08:23:03–08:24:11 | fio **after** |

| Run | randread4k IOPS (p50 / p99) | randwrite4k IOPS (p50 / p99) | seqread1m MiB/s (p99) |
|---|---|---|---|
| Before (3,000 / 125) | 2,998 (10.68 / 10.81 ms) | 2,998 (10.68 / 10.95 ms) | 125.1 (128 ms) |
| During optimizing | **5,998** (5.34 / 5.67 ms) | **5,998** (5.34 / 6.59 ms) | **500.4** (33 ms) |
| After | 5,998 (5.34 / 5.73 ms) | 5,998 (5.34 / 5.87 ms) | 500.4 (33 ms) |

- **The new performance was there 51 s after the change started,** while AWS still reported `optimizing`. In this run, "somewhere between the old and the new" was already the new.
- **Twice the IOPS halved the queueing latency:** 32 I/Os in flight at 6,000 IOPS wait about 5.3 ms instead of 10.7.
- **500 MiB/s is above the instance's EBS baseline** (156.25 MB/s for a c7g.xlarge) and below its maximum (1,250 MB/s). The 20 s runs fit inside the instance's own burst. A database that needs 500 MiB/s all day needs a bigger instance as well as a faster volume.
