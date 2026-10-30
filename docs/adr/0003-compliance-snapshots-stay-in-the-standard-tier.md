# 0003: Compliance snapshots stay in the standard tier

**Status:** accepted · **Date:** 30 October 2026

## Context

Zenzele keeps 84 monthly snapshots (seven years) of a 6,000 GiB database that changes by about 600 GiB a month. The EBS snapshot archive tier costs a quarter of the standard tier per GB.

## Decision

Keep the monthly compliance snapshots in the **standard tier**. Use the archive tier only for snapshots whose unique data is a large share of a full copy: for example, the final snapshot of a decommissioned system, kept for years and almost never restored.

## Evidence

- In the lab, the archive tier stored a **full copy**: the snapshot of a 1 GiB volume holding 200 MiB was archived as 209,715,200 bytes. Archiving took 89.9 s. Before archiving, the 90-day minimum (US$0.0072) was priced from the Price List.
- Cost model at list prices:
  - **Standard tier**, an incremental chain of 55,800 GiB: US$2,790 a month.
  - **Archive tier**, 84 full copies of 504,000 GiB: US$6,315 a month.
- Archive pays only above 25% unique data per snapshot; here it is 10%.

## Consequences

- No saving here. The retention period itself (seven years) is the lever, and that is a legal and records decision, not a storage one.
- Restores stay fast: standard-tier snapshots restore without the archive tier's wait of up to 72 hours.
