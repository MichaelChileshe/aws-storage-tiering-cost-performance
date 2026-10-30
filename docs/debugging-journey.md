# Debugging journey

The build itself went cleanly: every plan matched what I expected, every drill finished, and the destroy left nothing behind. The problems were in my own tooling, and I found them by checking the results against the raw data before writing anything up.

## 1. The cost model used the wrong lifecycle transition prices

**What I saw.** The price list my script printed had two lines with a note:

```
s3_req_tier3   Requests-Tier3   5e-05 USD per Requests [3 products matched, first used]
s3_req_tier4   Requests-Tier4   1e-05 USD per Requests [4 products matched, first used]
```

The script carried on and printed a confident trap table: Glacier one-time **US$500.00**, payback 590.4 months.

**What was really in the raw records** ([`results/pricing-raw.jsonl`](results/pricing-raw.jsonl)). One usage type covers several products, each with its own `operation` and price:

| Usage type | Operation | Description | US$ per 1,000 |
|---|---|---|---|
| Requests-Tier3 | `S3-GDATransition` | Lifecycle transitions into Glacier Deep Archive | 0.05 (the one my script used) |
| Requests-Tier3 | `S3-GlacierTransition` | Lifecycle transitions into Glacier | **0.03** |
| Requests-Tier3 | `RestoreObject` | Restore Object | 0.05 |
| Requests-Tier4 | `S3-ZIATransition` | into One Zone-IA | 0.01 (the one my script used) |
| Requests-Tier4 | `S3-INTTransition` | into Intelligent-Tiering | 0.01 |
| Requests-Tier4 | `S3-GIRTransition` | into Glacier Instant Retrieval | **0.02** |
| Requests-Tier4 | `S3-SIATransition` | into Standard-IA | 0.01 |

So every "transition to Glacier" was priced as Deep Archive (67% too high), and every "transition to Glacier IR" as One Zone-IA (half the real price). Transitions to Standard-IA and Intelligent-Tiering happened to be right.

**The fix** ([`scripts/pricing.py`](../scripts/pricing.py), [`scripts/cost_model.py`](../scripts/cost_model.py)):
- one price per destination class, each pinned with a `TERM_MATCH` on `operation`: `s3_tr_glacier`, `s3_tr_gir`, `s3_tr_sia`, `s3_tr_int`;
- any lookup that matches more than one product is now an **error**, listing the operations it found, and `cost_model.py` stops on any missing price unless I pass `--allow-missing`. A price that could be one of several is not a price.

**Recomputing without guessing.** I rebuilt those four prices from the raw records of the same fetch (so nothing changed except the selection), saved them in `prices.json` with a note, and reran `cost_model.py --cached` and `results.py`. Nothing else in the results depends on these prices.

| Figure | As it ran | Corrected |
|---|---|---|
| Trap: Glacier one-time / payback | US$500.00 / 590.4 months | **US$300.00 / 354.2 months** |
| Trap: Glacier IR one-time | US$100.00 | **US$200.00** |
| Trap: bundle one-time | US$4.02 | **US$4.01** |
| PACS, lifecycle to Glacier IR, one-time | US$92.27 | **US$184.55** (not the recommendation either way) |
| Lab results, lifecycle to Glacier, one-time | US$5,207.65 | **US$3,124.59** (fails the requirement either way) |
| Logs, recommended lifecycle, per month / one-time | US$62.84 / 66.44 | **US$59.97 / 40.94** |
| **Estate total after tiering, per month** | US$5,392.59 (saves 1,268.93, 19.0%) | **US$5,389.72 (saves 1,271.80, 19.1%)** |
| One-time total | US$213.04 | **US$187.53** |

None of the recommendations changed. The conclusions didn't either (the trap is still a trap), but the numbers in a cost model have to be right. Both versions are in the repo: corrected in [`results/`](results/), as run in [`results/as-run/`](results/as-run/). Screenshots 22–24 show the as-run numbers.

**Lesson:** a warning printed in the middle of a long output is a warning nobody reads. If a tool can't be sure which number it has, it should stop.

## 2. The burst watcher stopped one minute early

**What I saw.** `burst_watch.py` reported `down 10.0 points (expected from IOPS: 12.06)` and `slope over the run: None points/min`. The series it printed looked healthy: 100, 99, 96, 93, 90.

**Why.**
- The datapoints came back as `2026-10-30T09:54:00+02:00`: the AWS CLI had returned CloudWatch's timestamps in my **local time zone**. EC2's timestamps in the same session came back in UTC (`2026-10-30T08:07:38+00:00`), so I hadn't expected it.
- My script only rewrote `+00:00` to `Z` and then compared timestamps **as strings** with UTC times.
- `"…T09:54…"` is greater than `"…T07:55…"`, so the stop condition ("the last datapoint has reached the end of the run") was true at the first check after the run. The script stopped before the 07:55 datapoint, the run's final (partial) minute, was published.
- The slope filter (`t <= run_end`, also a string comparison) kept no points at all, hence `None`.

**What it did and didn't affect.** The datapoints that were collected are correct, and they show exactly 3 points a minute, which matches the 2,712 credits/s the IOPS imply. The estimate of the time to empty (about 1,991 s for a full bucket) comes from the measured IOPS, not from the series, so it stands. Only the "decline" and the "slope" lines are wrong, and I don't use them.

**The fix** ([`scripts/burst_watch.py`](../scripts/burst_watch.py)): every CloudWatch timestamp is parsed and converted to UTC before it is stored or compared. Tested against both `+02:00` and `Z` inputs: the slope over 96, 93, 90 comes out at −3.0 points per minute.

**Lesson:** never compare timestamps as text unless you control their format end to end.

## 3. The Glacier Deep Archive storage price matched no product

`TimedStorage-GDA-ByteHrs` returned nothing in the Price List lookup (`ERROR no product matched`). No option in the model uses Deep Archive storage (the Intelligent-Tiering archive tiers are configured in the bucket, but the model doesn't price them), so no total was affected. I removed the lookup instead of keeping one that always fails, and noted it in `prices.json`. I haven't established why it didn't match; if a future model needs Deep Archive, that lookup has to be found and verified first.

## Things I checked that were fine

- **The agent that ran is the agent in this repo.** Terraform's `agent_sha256` output, the hash the instance reported, and `sha256sum app/bench_agent.py` all start `9de5007a02066999`.
- **Every volume was the volume I thought it was.** The instance found each NVMe device by volume ID (the NVMe serial number), and all six matched Terraform's IDs before any fio run.
- **Destroying an archived snapshot.** I wasn't sure Terraform could delete a snapshot in the archive tier; it took 1 s.
