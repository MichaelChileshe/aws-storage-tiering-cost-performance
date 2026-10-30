# 0002: Bundle small lab results before archiving; never lifecycle them one by one

**Status:** accepted · **Date:** 30 October 2026

## Context

The proposal was a lifecycle rule moving lab results (about 20 KiB each, over 100 million of them) to Glacier Flexible Retrieval after 30 days. The results also need millisecond access when a clinician opens an old one.

## Decision

- **Don't add per-object lifecycle transitions** for objects under 128 KiB.
- Pack results older than 30 days into **512 MiB objects in Glacier Instant Retrieval**, with an index, so one result is one byte-range GET.
- Leave the bucket's `TransitionDefaultMinimumObjectSize` at `all_storage_classes_128K` as a guardrail.

## Evidence

From the lab's cost model at list prices:
- **10 million 20 KiB files to Glacier:** saves US$0.85 a month and costs US$300 in transition requests, so payback takes 354 months. Each object gains 40 KiB of billed metadata.
- **Glacier IR or Standard-IA:** more expensive than Standard, because every object is billed as 128 KiB.
- **Intelligent-Tiering:** no saving, because objects under 128 KiB are never monitored or moved.
- **Bundling into 512 MiB objects first:** US$4.01 one-time, payback 1.1 months.

Glacier Flexible Retrieval also refuses reads (`InvalidObjectState`) until a restore finishes. An Expedited restore took 77.9 s in the lab, and Standard restores take hours. That fails the millisecond requirement.

For the whole data set, bundling takes the lab results from US$47.10 to US$9.96 a month.

## Consequences

- An index and a small read service have to be built and maintained.
- The bundling job reads every small object once (GET requests).
- Deleting a single result inside a bundle means rewriting the bundle. That matters for retention and privacy requests and has to be designed in.
