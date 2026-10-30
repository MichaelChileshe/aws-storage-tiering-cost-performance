# gp2 burst credits: why the databases slow down at month end

Zenzele's application databases sit on 12 gp2 volumes of 500 GiB. They are fine most of the month and slow at month end, when reporting runs on top of normal load. I wanted to watch the mechanism, not just quote it.

## How gp2 performance works

- A gp2 volume's **baseline is 3 IOPS per GiB** (minimum 100): 300 for my 100 GiB volume, 1,500 for one of Zenzele's 500 GiB volumes.
- Below 1 TiB it can **burst to 3,000 IOPS** by spending I/O credits from a bucket of **5.4 million**, which refills at the baseline rate.
- At 3,000 IOPS a 100 GiB volume spends 3,000 − 300 = 2,700 credits a second more than it earns, so a full bucket lasts 5,400,000 / 2,700 = 2,000 s. A 500 GiB volume (earning 1,500 a second) lasts 3,600 s.
- When the bucket is empty, the volume drops to its baseline.
- CloudWatch publishes the bucket level as `BurstBalance` (percent), per volume, every minute.

## The drill

`burst_watch.py` ran one 240 s, 4 KiB random-read job at queue depth 32 on the gp2 volume (07:51:53–07:55:53 UTC) while it polled `BurstBalance` every 30 s. It doesn't try to empty the bucket: it measures the decline and estimates the rest. Data: [`results/burst-gp2.json`](results/burst-gp2.json). Screenshots [`12`](screenshots/12-gp2-burst-drain.png) and [`13`](screenshots/13-cloudwatch-burstbalance.png).

**Run:** 3,012 IOPS for 240 s, p50 10.68 ms, p99 11.08 ms (723,016 reads).

| Minute (UTC) | BurstBalance |
|---|---|
| 07:46–07:50 | 100% |
| 07:51 | 99% (the run started at 07:51:53) |
| 07:52 | 96% |
| 07:53 | 93% |
| 07:54 | 90% |
| 07:55 | not collected (see below) |

**The decline is exactly what the arithmetic predicts.** At 3,012 IOPS the volume spends 2,712 credits a second net. Over a minute that's 162,744 credits, 3.01% of 5.4 million. CloudWatch showed **3 points per minute**, three minutes in a row.

| Estimate from the measured rate | Value |
|---|---|
| Net spend | 2,712.4 credits/s |
| A full bucket lasts | **about 1,991 s (33 minutes)** |
| From the lowest point seen (90%) to empty | about 1,792 s |

After that, this volume would drop from 3,000 IOPS to 300: a tenth of the performance, with no error anywhere, just slower queries. For Zenzele's 500 GiB volumes the drop is from 3,000 to 1,500, after about an hour of heavy load. That fits "fine all month, slow at month end".

## What the script got wrong

The script reported **"down 10.0 points (expected 12.06)"** and a slope of `None`. Both are artefacts of a bug, not of EBS:
- the AWS CLI returned CloudWatch timestamps in my local time zone (+02:00), and the script compared them as text with UTC times;
- `"…T09:54:00+02:00"` sorts after `"…T07:55…Z"`, so the script decided the final datapoint had arrived and stopped polling one minute early;
- the 07:55 datapoint, the run's final (partial) minute, was never collected, and the slope filter (also a text comparison) found no points.

The run was still going until 07:55:53, so that minute would most likely have shown another drop of about 3 points; I didn't collect it, so I don't report it. The fixed script converts every timestamp to UTC before comparing; [the debugging journey](debugging-journey.md) has the details.

The CloudWatch console view (screenshot 13, 3-hour window) shows the earlier, smaller dip from the first fio run and the balance back at 100% at 07:45, just before this drill.

## What I'd do about it

gp3 has no credits: 3,000 IOPS and 125 MiB/s at any size, and more can be bought without growing the volume. The move from gp2 is in place ([Elastic Volumes](elastic-volumes.md)). For Zenzele's 12 database volumes, the [cost model](cost-model.md) puts gp3 at US$540 a month against gp2's US$600, while also removing the month-end cliff.
