# Screenshots

All 27 screenshots come from the build on 30 October 2026, in the order things happened. The account ID is masked as `<account-id>`. Terminal times are UTC unless the line shows `+02:00` (my local time); the file time in screenshot 27 (`10:41:50`) is also local, 08:41:50 UTC.

| # | What it shows |
|---|---|
| [01](screenshots/01-state-bucket-az-name-suffix.png) | State bucket exists; use1-az4 → us-east-1c, use1-az6 → us-east-1d, use1-az5 → us-east-1f, all available; c7g.xlarge offered in use1-az1, az2, az4, az5, az6; the CLI knows directory buckets (none yet); the random bucket-name suffix |
| [02](screenshots/02-init-and-gates.png) | init with the S3 backend (AWS provider 6.71.0); fmt, validate, tflint clean; checkov 58 passed / 14 failed |
| [03](screenshots/03-base-apply-outputs.png) | `Apply complete! Resources: 21 added`; bucket names saved; AZ use1-az4 = us-east-1c; state list 23 |
| [04](screenshots/04-bench-apply-outputs.png) | `Apply complete! Resources: 13 added`; the instance ID and the volumes' IOPS and throughput |
| [05](screenshots/05-bench-info.png) | State list 36; c7g.xlarge EBS baseline and maximum; fio 3.32, boto3 1.40.31 on the instance; all six volumes mapped to their NVMe devices |
| [06](screenshots/06-fio-gp2-gp3-io2.png) | fio on gp2 (bursting to 2,998 IOPS), gp3 (2,998, 125.1 MiB/s) and io2 (3,998, 1,000 MiB/s) |
| [07](screenshots/07-fio-st1-sc1-summary.png) | fio on st1 and sc1 (31 and 10 IOPS, seconds of latency), and the summary table |
| [08](screenshots/08-io2-off-plan.png) | io2 off: the volume will be destroyed `because key ["io2"] is not in for_each map` |
| [09](screenshots/09-io2-off-applied.png) | `Apply complete! Resources: 0 added, 0 changed, 2 destroyed.` |
| [10](screenshots/10-state-list-34.png) | End of the outputs after io2 came off; state list 34 |
| [11](screenshots/11-snapshot-archive-start.png) | Snapshot full size 209,715,200 bytes; 90-day archive minimum US$0.0072; archive started |
| [12](screenshots/12-gp2-burst-drain.png) | The gp2 burst drill: BurstBalance 100 → 99 → 96 → 93 → 90 (times in +02:00), 3,012 IOPS for 240 s, the estimates. The "10.0 points" and "None" slope come from a script bug ([debugging journey](debugging-journey.md)). |
| [13](screenshots/13-cloudwatch-burstbalance.png) | CloudWatch console, 3-hour window: gp2 BurstBalance dipping during the first fio run and back at 100% at 07:45 UTC, before the burst drill |
| [14](screenshots/14-gp2-to-gp3-plan.png) | gp2 → gp3 plan: `iops 300 -> 3000`, `throughput 0 -> 125`, `type "gp2" -> "gp3"`, 1 to change |
| [15](screenshots/15-gp2-to-gp3-modwatch.png) | The migration: optimizing, fio during and after, AWS StartTime 08:07:38 / EndTime 08:12:41 |
| [16](screenshots/16-gp3-tuned-modwatch.png) | gp3 to 6,000 IOPS / 500 MiB/s: 5,998 IOPS and 500.4 MiB/s already during optimizing |
| [17](screenshots/17-s3-latency.png) | S3 PUT and GET timings per class and in Express One Zone; GLACIER `InvalidObjectState` |
| [18](screenshots/18-glacier-restore.png) | The refused GET, the Expedited restore, restored 77.9 s after the request |
| [19](screenshots/19-s3-config.png) | Lifecycle rules with `all_storage_classes_128K`, the Intelligent-Tiering configuration, Storage Lens |
| [20](screenshots/20-s3-objects-by-class.png) | Objects by storage class in both buckets |
| [21](screenshots/21-storage-lens-console.png) | S3 console: the default dashboard and `zenzele-tiering-lens`, both enabled |
| [22](screenshots/22-trap-drill.png) | The trap drill **as it ran** (Glacier one-time 500.00; corrected: 300.00) |
| [23](screenshots/23-estate-cost-model.png) | The estate options **as they ran** (lab results lifecycle one-time 5,207.65; corrected: 3,124.59) |
| [24](screenshots/24-total-and-snapshot-status.png) | The total **as it ran** (5,392.59/month; corrected: 5,389.72), the lab's hourly rates, the snapshot `archival-completed` |
| [25](screenshots/25-summary-terraform.png) | The Terraform section of the summary: every plan and apply line |
| [26](screenshots/26-destroy-33.png) | `Destroy complete! Resources: 33 destroyed.` |
| [27](screenshots/27-clean-check.png) | End of the clean check: state list 0, an 836-byte empty state |

Screenshots 22–24 show the cost model before I fixed the transition-price lookup. The corrected output is in [`results/cost-model.txt`](results/cost-model.txt); [the debugging journey](debugging-journey.md) explains the difference.
