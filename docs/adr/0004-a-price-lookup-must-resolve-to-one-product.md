# 0004: A price lookup must resolve to exactly one product

**Status:** accepted · **Date:** 30 October 2026

## Context

The cost model reads every price from the AWS Price List API by usage type. In the run, `Requests-Tier3` matched three products and `Requests-Tier4` four, each with a different price. The lookup took the first match and printed a note that nobody acts on in a long output.

The Glacier transitions were priced as Deep Archive (US$0.05 per 1,000 instead of US$0.03), and the Glacier IR transitions as One Zone-IA (US$0.01 instead of US$0.02).

## Decision

- Every price lookup names enough attributes to match **one** product. For S3 transitions that means the `operation` (`S3-GlacierTransition`, `S3-GIRTransition`, `S3-SIATransition`, `S3-INTTransition`).
- A lookup that matches more than one product returns an **error**, listing the operations it found, and the cost model **stops** with the missing price and the reason. `--allow-missing` lets it carry on (the affected options show "-" and are left out of the totals) when I want to see the rest.
- Every raw record stays in `pricing-raw.jsonl`, so any price can be traced to its SKU.

## Consequences

- A renamed or split product in the Price List stops the model loudly instead of skewing it quietly.
- Correcting this run was possible without fetching again: the raw records already held the right products, and I rebuilt the four prices from them.
