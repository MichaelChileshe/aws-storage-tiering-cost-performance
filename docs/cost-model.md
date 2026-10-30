# Cost: the "small files to Glacier" trap, Zenzele's estate, and this lab

## Prices

`cost_model.py` fetched every price from the **AWS Price List API** (`pricing get-products`, US East (N. Virginia), on-demand) on 30 October 2026 at 08:35 UTC. Nothing is typed in from memory. Every raw record is in [`results/pricing-raw.jsonl`](results/pricing-raw.jsonl), and the prices used are in [`results/prices.json`](results/prices.json).

| Price | US$ |
|---|---|
| S3 Standard (first 50 TB) / Standard-IA / Glacier IR / Glacier Flexible Retrieval, per GB-month | 0.023 / 0.0125 / 0.004 / 0.0036 |
| Intelligent-Tiering: Frequent / Infrequent / Archive Instant per GB-month; monitoring per object-month | 0.023 / 0.0125 / 0.004; 0.0000025 |
| S3 Express One Zone, per GB-month | 0.11 |
| Lifecycle transitions per 1,000: to Glacier Flexible / to Glacier IR / to Standard-IA / to Intelligent-Tiering | 0.03 / 0.02 / 0.01 / 0.01 |
| GET per 1,000; Expedited restore per request; Glacier IR retrieval per GB | 0.0004; 0.01; 0.03 |
| EBS per GB-month: gp2 / gp3 / io2 / st1 / sc1 | 0.10 / 0.08 / 0.125 / 0.045 / 0.015 |
| gp3 extra IOPS per IOPS-month; extra throughput per MiB/s-month | 0.005; 0.04 (listed as 40.96 per GiBps-month) |
| io2 per IOPS-month, up to 32,000 | 0.065 |
| EBS snapshot per GB-month: standard / archive; archive retrieval per GB | 0.05 / 0.0125; 0.03 |
| c7g.xlarge Linux on-demand, per hour | 0.145 |

The transition prices in this table are the corrected ones. As it ran, the lookup took the first of several products sharing one usage type, which gave the Deep Archive transition price for Glacier and the One Zone-IA price for Glacier IR. I recomputed everything below from the same day's raw records with the fixed lookup ([debugging journey](debugging-journey.md)). The original output is in [`results/as-run/`](results/as-run/); screenshots 22–24 show those original numbers.

## The trap: 10 million 20 KiB lab results "archived" to Glacier

Someone proposed a lifecycle rule moving lab results to Glacier Flexible Retrieval after 30 days. For small objects, three things work against it:
- **Glacier Flexible Retrieval adds 40 KiB of metadata to every object:** 32 KiB billed at the Glacier rate and 8 KiB at the Standard rate. For a 20 KiB file that's three times the data.
- **Every object is one transition request,** paid once.
- **Glacier IR and Standard-IA bill every object as at least 128 KiB,** and Intelligent-Tiering never monitors or moves objects under 128 KiB.

Data: [`results/trap.json`](results/trap.json).

| Option (190.7 GiB in 10,000,000 objects) | US$/month | One-time US$ | Saves per month | Payback |
|---|---|---|---|---|
| STANDARD (keep) | 4.39 | 0.00 | | |
| GLACIER via lifecycle | 3.54 | **300.00** | 0.85 | **354.2 months** |
| GLACIER_IR via lifecycle | 4.88 | 200.00 | −0.50 | never |
| STANDARD_IA via lifecycle | 15.26 | 100.00 | −10.87 | never |
| INTELLIGENT_TIERING | 4.39 | 0.00 | 0.00 | never |
| **Bundle into 512 MiB objects → GLACIER** | **0.69** | **4.01** | **3.70** | **1.1 months** |

- **Glacier saves 85 cents a month and costs US$300 up front:** almost 30 years to pay back, with 90-day minimums and restores of minutes to hours on top.
- **Glacier IR and Standard-IA make the bill bigger,** because 20 KiB is billed as 128 KiB.
- **None of the lifecycle options would even run by default.** The bucket's `TransitionDefaultMinimumObjectSize` is `all_storage_classes_128K` ([S3 storage classes](s3-storage-classes.md)), so objects this small don't move. Someone would have to change it to `varies_by_storage_class` to trigger this bill.
- **Bundling is the option that pays:** 382 bundles instead of 10 million objects. The one-time cost is reading every small object once, plus 382 transitions.

## Zenzele's estate

The estate is my model of the hospital group: [`estate/zenzele-estate.json`](../estate/zenzele-estate.json). It holds the sizes, object sizes, how old the data is, and what each data set needs (millisecond access, IOPS, durability). **The model and its assumptions are mine; the prices are AWS's.** Change the file, copy `docs/results/prices.json` to `build/results/`, and rerun `python3 scripts/cost_model.py --cached` to try other assumptions without fetching prices again.

For each data set the model prices every option and recommends the cheapest one that meets the requirement. For EBS it also checks that this lab measured the volume type at 85% or more of its documented performance (all of them passed). Data: [`results/cost-model.json`](results/cost-model.json), [`results/cost-model.csv`](results/cost-model.csv).

| Data set | Today | US$/month | Recommended | US$/month | One-time US$ | Why |
|---|---|---|---|---|---|---|
| PACS archive, 40 TiB, 4 MiB studies, 88% older than 90 days | Standard | 942.08 | **Intelligent-Tiering** | 249.04 | 104.86 | Millisecond access kept, no retrieval fees; monitoring US$26.21/month. Lifecycle to Glacier IR after 90 days: 278.86. |
| Lab results, 2 TiB in 20 KiB files | Standard | 47.10 | **Bundle results older than 30 days into 512 MiB objects in Glacier IR** | 9.96 | 41.74 | One result = one byte-range GET through an index. Lifecycle to Glacier: refused (fails millisecond access). Intelligent-Tiering: no saving under 128 KiB. |
| Logs, 8 TiB | Standard | 188.42 | **Lifecycle: IA at 30 d, Glacier at 90 d** | 59.97 | 40.94 | Includes US$5.73/month of transitions for 700 GiB of new logs a month |
| Nightly exports, 8 GiB a day, kept 730 days | Standard | 134.32 | **Expire after 35 days** | 6.44 | 0.00 | 35 days is what's needed; the rest was never read |
| App databases, 12 × 500 GiB, need 3,000 IOPS and 250 MiB/s | gp2 | 600.00 | **gp3** | 540.00 | 0.00 | gp2's baseline is 1,500 IOPS; 3,000 only while credits last. io2 would be 3,090. |
| EMR database, 2,000 GiB, 20,000 IOPS, needs 99.999% durability | io2 | 1,550.00 | **io2 (keep)** | 1,550.00 | 0.00 | gp3 would cost 260 but is designed for 99.8–99.9% durability |
| Export staging, 2 × 2,048 GiB, 600 GiB dump in an hour (171 MiB/s) | gp2 | 409.60 | **st1** | 184.32 | 0.00 | Sequential only, never booted from. sc1 is too slow (24 MiB/s baseline, 160 burst). |
| Compliance snapshots, 84 monthly, 6,000 GiB full, 600 GiB changed each month | Standard tier | 2,790.00 | **Standard tier (keep)** | 2,790.00 | 0.00 | Archive keeps full copies: 504,000 GiB, US$6,315/month. It only pays when each snapshot's unique data is more than 25% of a full copy; here it's 10%. |
| **Total** | | **6,661.52** | | **5,389.72** | **187.53** | **Saves US$1,271.80 a month (19.1%)** |

**Assumptions to challenge before anyone acts on this:**
- **Age stands in for access.** Intelligent-Tiering moves objects by last access, not age; the model uses the age split as a proxy.
- **st1 for export staging relies on burst.** At 2 TiB, st1's baseline is 80 MiB/s and the dump needs 171. The model assumes each hour-long dump starts with a full credit bucket (about the volume's size in credits), and this lab never ran a 600 GiB dump.
- **Bundling lab results needs an index and a small service** to answer "give me result X" with a byte-range GET. That is engineering work the model doesn't price.
- **List prices only.** No discounts, Savings Plans, free tiers or data transfer.

## What this lab cost

The rates that mattered, from the same Price List fetch: the c7g.xlarge at US$0.145 an hour, and the io2 volume (100 GiB, 4,000 IOPS) at **US$0.3733 an hour**, more than everything else together, so it came off after its run. Everything else was cents: five small volumes, a few MB in S3, one Expedited restore (US$0.01 plus a fraction of a cent per GB), and the snapshot archive's 90-day minimum of US$0.0072.

The instance lived about 73 minutes (07:28 to about 08:41 UTC) and the io2 volume about 15–20, so the whole lab should come to well under a dollar. **I'll confirm the actual figure from Cost Explorer for 30 October,** grouped by usage type.
