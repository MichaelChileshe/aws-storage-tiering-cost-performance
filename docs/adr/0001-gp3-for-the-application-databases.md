# 0001: gp3 for the application databases, migrated in place

**Status:** accepted · **Date:** 30 October 2026

## Context

Zenzele's 12 application databases run on 500 GiB gp2 volumes and slow down at month end. A gp2 volume's baseline is 3 IOPS per GiB: 1,500 IOPS at 500 GiB. It reaches 3,000 only while its burst bucket lasts.

In the lab, a 100 GiB gp2 volume at 3,012 IOPS lost 3 points of BurstBalance a minute; a full bucket lasts about 33 minutes at that rate. The databases need 3,000 IOPS and 250 MiB/s through month-end reporting, not for half an hour.

## Decision

Move all 12 volumes to **gp3 with 3,000 IOPS and 250 MiB/s**, in place, with Elastic Volumes. Raise IOPS or throughput later without growing the volume if the workload needs it, checking the instance's EBS limits first.

## Evidence

- In the lab, gp2 → gp3 was `1 to change` in Terraform, and AWS completed it in 5 min 3 s. The volume stayed attached, and fio during the optimization delivered the same 2,998 IOPS as before and after.
- Raising a 100 GiB gp3 volume to 6,000 IOPS / 500 MiB/s took 5 min 13 s, and the new figures were there during optimizing.
- At list prices: gp3 US$540 a month for the 12 volumes, gp2 US$600, io2 US$3,090.

## Consequences

- No month-end credit cliff: gp3's 3,000 IOPS are a baseline.
- 10% cheaper than today.
- 250 MiB/s is above some instance types' EBS baselines; the database instances' limits have to be checked too.
- Each volume can be modified up to four times in 24 hours, so migrations and later tuning have to be planned per volume.
