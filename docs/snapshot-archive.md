# EBS snapshot archive tier

Zenzele keeps monthly database snapshots for seven years. The archive tier costs a quarter of the standard tier per GB, which sounds like an easy saving. It comes with conditions:
- an archived snapshot is a **full copy** of every block written, not the incremental difference;
- it is billed for **at least 90 days**;
- restoring it can take **up to 72 hours**.

I ran the whole cycle on a small volume, and priced the minimum before archiving anything.

## The drill

Data: [`results/snapshot-archive.json`](results/snapshot-archive.json). Screenshots [`11`](screenshots/11-snapshot-archive-start.png) and [`24`](screenshots/24-total-and-snapshot-status.png).

| UTC | Step |
|---|---|
| 07:48:35 | 200 MiB of random data written to the 1 GiB "nightly export" volume, through SSM (1.1 s) |
| | `snapshot_on = true`: Terraform `1 to add`, the snapshot completed after 53 s |
| 07:50:19 | Price check, then `modify-snapshot-tier --storage-tier archive` |
| 07:51:49 | `archival-completed` (AWS's own `LastTieringStartTime` 07:50:19.224 → completed 07:51:49.136: **89.9 s**) |
| 08:35:23 | Status check: `tier=archive`, `archival-completed`, progress 100% |
| destroy | Terraform deleted the archived snapshot in 1 s |

**The price check, before archiving:**
- Full snapshot size (`FullSnapshotSizeInBytes`): **209,715,200 bytes**: exactly the 200 MiB written, not the 1 GiB volume.
- Archive price from the Price List: US$0.0125 per GB-month.
- 90-day minimum: 0.0125 × 0.1953 GiB × 3 months = **US$0.0072**.
- The script refuses to archive if this minimum is above US$0.10. It wasn't.

AWS doesn't document how long archiving takes. For 200 MiB it took 90 seconds; I wouldn't extrapolate that to a 6 TB database snapshot.

Deleting the snapshot while archived was allowed. AWS bills the rest of its 90-day minimum, which is the US$0.0072 computed up front.

## What it means for Zenzele

The cost model compares the two tiers for 84 monthly snapshots of a 6,000 GiB database that changes by 600 GiB a month ([cost model](cost-model.md)):

| Tier | What's stored | US$/month |
|---|---|---|
| Standard (incremental chain) | 6,000 + 83 × 600 = 55,800 GiB | **2,790.00** |
| Archive (full copies) | 84 × 6,000 = 504,000 GiB | 6,315.00 (incl. one restore a year) |

Archive pays only when each snapshot's unique data is more than **25%** of a full copy (the ratio of the two prices). Zenzele's monthly change is 10%, so the compliance snapshots stay in the standard tier. Archive fits a different case: a final snapshot of a decommissioned system, kept for years and almost never restored.
