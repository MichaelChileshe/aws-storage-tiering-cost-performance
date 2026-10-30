# S3 storage classes, S3 Express One Zone and Glacier, from an application's side

A storage class is a price decision with a read-path consequence. Zenzele's PACS viewers and lab systems need results in milliseconds; logs and exports don't. I measured what each class means for a read, from inside the same Availability Zone.

## The setup

- **General purpose bucket** `zenzele-tiering-<suffix>`: SSE-S3, Block Public Access, a bucket policy that denies any request not made over TLS, reached from the VPC through an **S3 gateway endpoint**.
- **Directory bucket** `zenzele-express-<suffix>--use1-az4--x-s3` (S3 Express One Zone) in the **same AZ ID as the instance**. Directory buckets authorise data requests through a session: boto3 calls `CreateSession` itself and refreshes it. The instance role allows `s3express:CreateSession` on that one bucket only.
- The bench instance PUT the same **4 KiB** and **8 MiB** objects as `STANDARD`, `STANDARD_IA`, `INTELLIGENT_TIERING`, `GLACIER_IR` and `GLACIER`, and into the directory bucket. Then it GET each one 3 times to warm up, and **100 times (4 KiB) / 10 times (8 MiB) timed**.
- "TTFB" is the time until boto3's `get_object` returns (the response headers have arrived); "total" is until the last byte is read. "1st GET" is the very first request, including connection setup.

Data: [`results/s3-latency-base.json`](results/s3-latency-base.json), every sample in [`results/raw/s3/`](results/raw/s3/). Screenshot [`17`](screenshots/17-s3-latency.png). Run at 08:26 UTC, boto3 1.40.31, AZ ID use1-az4.

## Results

| Class | Size | PUT ms | 1st GET ms | TTFB p50 / p90 / p99 ms | Total p50 / p99 ms | MiB/s at p50 |
|---|---|---|---|---|---|---|
| STANDARD | 4 KiB | 82.6 | 27.5 | 27.0 / 31.1 / 32.9 | 27.1 / 33.0 | 0.1 |
| STANDARD_IA | 4 KiB | 42.4 | 27.4 | 29.7 / 34.7 / 62.8 | 29.8 / 62.9 | 0.1 |
| INTELLIGENT_TIERING | 4 KiB | 42.4 | 27.2 | 27.6 / 35.8 / 81.3 | 27.7 / 81.4 | 0.1 |
| GLACIER_IR | 4 KiB | 36.2 | 24.5 | 26.9 / 30.2 / 68.7 | 27.0 / 68.8 | 0.1 |
| GLACIER | 4 KiB | 39.3 | refused | `InvalidObjectState` | | |
| **EXPRESS_ONEZONE** | 4 KiB | 65.2 | **6.4** | **6.1 / 6.5 / 6.7** | **6.2 / 6.8** | 0.6 |
| STANDARD | 8 MiB | 129.5 | 91.8 | 24.5 / 30.7 / 33.8 | 89.6 / 93.2 | 89.3 |
| STANDARD_IA | 8 MiB | 111.8 | 89.1 | 27.1 / 30.4 / 34.3 | 89.3 / 90.9 | 89.5 |
| INTELLIGENT_TIERING | 8 MiB | 128.8 | 89.3 | 22.6 / 26.8 / 27.5 | 88.6 / 119.9 | 90.3 |
| GLACIER_IR | 8 MiB | 135.0 | 89.4 | 22.9 / 28.1 / 29.7 | 90.0 / 98.7 | 88.9 |
| GLACIER | 8 MiB | 101.5 | refused | `InvalidObjectState` | | |
| **EXPRESS_ONEZONE** | 8 MiB | 82.4 | **58.5** | **5.0 / 5.2 / 5.5** | **56.0 / 58.2** | **142.9** |

**What it shows:**
- **Standard, Standard-IA, Intelligent-Tiering and Glacier Instant Retrieval all answer in milliseconds,** about 27 ms to first byte for a small object here. From a reader's point of view they are the same; the differences are in the bill: IA and Glacier IR add a per-GB retrieval fee and bill objects as at least 128 KiB, and they have 30- and 90-day minimums.
- **Express One Zone was about 4.4 times faster to first byte** (6.1 ms against 27.0 for Standard) and its tail was tight (p99 6.7 ms). For 8 MiB it moved 142.9 MiB/s against about 89 for the general purpose classes.
- **The p99 for single small reads varied** between classes (33 to 81 ms) over 100 samples. I wouldn't read a class difference into that; it's a tail over one short run.
- **Glacier Flexible Retrieval doesn't answer at all.** The object is listed, has a size and a class, and a GET returns `InvalidObjectState`.

Express One Zone stores data in **one AZ**: it is for hot, re-creatable data next to the compute that reads it (a working set, a cache, intermediate results), not for the only copy of a patient record. Its storage costs US$0.11 per GB-month against Standard's US$0.023.

My Express traffic went out through the internet gateway, because I only added a gateway endpoint for general purpose S3. AWS also offers a gateway endpoint for S3 Express One Zone; I'd add it next time.

## What "archived" means: the Expedited restore

`glacier_restore.py` tried to read the 4 KiB object in `GLACIER`, requested an **Expedited** restore (1 day) and polled `head-object`, sleeping 15 s between checks (after the first check, 4.9 s in, they landed 17–22 s apart). Data: [`results/glacier-restore.json`](results/glacier-restore.json). Screenshot [`18`](screenshots/18-glacier-restore.png).

| UTC | Event |
|---|---|
| 08:28:16.726 | GET refused: `InvalidObjectState`. Expedited restore requested. |
| 08:28:21 – 08:29:17 | `Restore: ongoing-request="true"` (4 polls) |
| 08:29:34.648 | `ongoing-request="false"`, expiry Sun 1 Nov 2026 00:00 GMT: **first seen restored after 77.9 s** |
| 08:29:36.764 | GET OK. The object's storage class is **still `GLACIER`**. |

The restore finished somewhere between 60.8 s and 77.9 s after the request (between two polls). Expedited is documented as typically 1–5 minutes and **subject to demand**; without provisioned capacity it can be refused. Standard restores take hours. A restore makes a temporary copy; the object itself never leaves its class. For an application that means a read becomes a workflow: request, wait, then read.

## The tiering rules S3 now holds

My rules act at 30 and 90 days (and S3 requires objects to be at least 30 days old before they move to Standard-IA), so a one-day lab can't watch one happen. What I can prove is what S3 holds. `s3_config.py` read it back. Data: [`results/s3-config.json`](results/s3-config.json). Screenshots [`19`](screenshots/19-s3-config.png)–[`21`](screenshots/21-storage-lens-console.png).

| Rule | Prefix | Action |
|---|---|---|
| `pacs-studies-to-glacier-ir-after-90-days` | `pacs/` | 90 d → GLACIER_IR |
| `lab-results-to-glacier-after-30-days-TRAP` | `lab-results/` | 30 d → GLACIER |
| `logs-ia-30-glacier-90-expire-400` | `logs/` | 30 d → STANDARD_IA; 90 d → GLACIER; expire after 400 d |
| `nightly-exports-expire-after-35-days` | `exports/` | expire after 35 d |
| `abort-incomplete-multipart-uploads-7-days` | whole bucket | abort incomplete multipart uploads after 7 d |

- **`TransitionDefaultMinimumObjectSize = all_storage_classes_128K`.** This has been the default for new lifecycle configurations since September 2024; I wrote it out in Terraform so it is visible. It means lifecycle **never transitions an object smaller than 128 KB**, to any class. The proposed lab-results rule is in the bucket and would do nothing to 20 KiB files. The [cost model](cost-model.md) prices what would happen if someone overrode the default.
- **Intelligent-Tiering archive configuration** `research-archive-tiers` (Enabled, prefix `research/`): Archive Access after 90 days, Deep Archive Access after 180. These optional tiers are not instant: objects in them need a restore, like Glacier.
- **Objects by class:** 10 objects under `bench/` in the five classes, 2 in the directory bucket as `EXPRESS_ONEZONE`.
- **Storage Lens:** `default-account-dashboard` and my `zenzele-tiering-lens` (enabled, scoped to the lab bucket, free metrics only). Dashboards update once a day and a new one can take up to 48 hours to show data, so there was nothing to read on the day. The console (screenshot 21) shows both enabled.
