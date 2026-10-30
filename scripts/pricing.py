"""Current AWS list prices from the Price List API (aws pricing get-products, us-east-1 endpoint),
looked up by exact usage type for US East (N. Virginia), where usage types carry no Region prefix.
Every record used is saved raw (build/results/pricing-raw.jsonl) so the numbers can be checked.
"""
import json
import os

from kit_aws import RESULTS, aws_try

LOCATION = "US East (N. Virginia)"

# key: (service code, usage type, extra exact-match filters, what it is)
CATALOG = {
    # ---- S3 storage (GB-month) ----
    "s3_standard": ("AmazonS3", "TimedStorage-ByteHrs", {}, "S3 Standard storage"),
    "s3_sia": ("AmazonS3", "TimedStorage-SIA-ByteHrs", {}, "S3 Standard-IA storage"),
    "s3_gir": ("AmazonS3", "TimedStorage-GIR-ByteHrs", {}, "S3 Glacier Instant Retrieval storage"),
    "s3_glacier": ("AmazonS3", "TimedStorage-GlacierByteHrs", {}, "S3 Glacier Flexible Retrieval storage"),
    "s3_int_fa": ("AmazonS3", "TimedStorage-INT-FA-ByteHrs", {}, "Intelligent-Tiering Frequent Access"),
    "s3_int_ia": ("AmazonS3", "TimedStorage-INT-IA-ByteHrs", {}, "Intelligent-Tiering Infrequent Access"),
    "s3_int_aia": ("AmazonS3", "TimedStorage-INT-AIA-ByteHrs", {}, "Intelligent-Tiering Archive Instant Access"),
    "s3_xz": ("AmazonS3", "TimedStorage-XZ-ByteHrs", {}, "S3 Express One Zone storage"),
    # ---- S3 per-object / per-request / per-GB-retrieved ----
    "s3_int_monitor": ("AmazonS3", "Monitoring-Automation-INT", {}, "Intelligent-Tiering monitoring, per object-month"),
    "s3_req_tier1": ("AmazonS3", "Requests-Tier1", {}, "PUT/COPY/POST/LIST, S3 Standard"),
    "s3_req_tier2": ("AmazonS3", "Requests-Tier2", {}, "GET and other, S3 Standard"),
    # Requests-Tier3 and Requests-Tier4 each cover several products with DIFFERENT prices (one per
    # destination class), so each lookup pins the operation. Without it the first match wins, and
    # the first Tier3 product is the Deep Archive transition, not the Glacier Flexible one.
    "s3_tr_glacier": ("AmazonS3", "Requests-Tier3", {"operation": "S3-GlacierTransition"},
                      "Lifecycle transitions to Glacier Flexible Retrieval"),
    "s3_tr_gir": ("AmazonS3", "Requests-Tier4", {"operation": "S3-GIRTransition"},
                  "Lifecycle transitions to Glacier Instant Retrieval"),
    "s3_tr_sia": ("AmazonS3", "Requests-Tier4", {"operation": "S3-SIATransition"},
                  "Lifecycle transitions to Standard-IA"),
    "s3_tr_int": ("AmazonS3", "Requests-Tier4", {"operation": "S3-INTTransition"},
                  "Lifecycle transitions to Intelligent-Tiering"),
    "s3_req_tier6": ("AmazonS3", "Requests-Tier6", {}, "Expedited Glacier Flexible restore requests"),
    "s3_req_gir_tier1": ("AmazonS3", "Requests-GIR-Tier1", {}, "PUT/COPY/POST, Glacier Instant Retrieval"),
    "s3_retr_sia": ("AmazonS3", "Retrieval-SIA", {}, "Standard-IA retrieval per GB"),
    "s3_retr_gir": ("AmazonS3", "Retrieval-GIR", {}, "Glacier Instant Retrieval retrieval per GB"),
    "s3_retr_expedited": ("AmazonS3", "Expedited-Retrieval-Bytes", {}, "Expedited Glacier Flexible retrieval per GB"),
    # ---- EBS (GB-month, IOPS-month, throughput-month) ----
    "ebs_gp2": ("AmazonEC2", "EBS:VolumeUsage.gp2", {}, "gp2 storage"),
    "ebs_gp3": ("AmazonEC2", "EBS:VolumeUsage.gp3", {}, "gp3 storage"),
    "ebs_gp3_iops": ("AmazonEC2", "EBS:VolumeP-IOPS.gp3", {}, "gp3 IOPS above 3000"),
    "ebs_gp3_tput": ("AmazonEC2", "EBS:VolumeP-Throughput.gp3", {}, "gp3 throughput above 125 MiB/s"),
    "ebs_io2": ("AmazonEC2", "EBS:VolumeUsage.io2", {}, "io2 storage"),
    "ebs_io2_iops": ("AmazonEC2", "EBS:VolumeP-IOPS.io2", {}, "io2 IOPS up to 32000"),
    "ebs_io2_iops_t2": ("AmazonEC2", "EBS:VolumeP-IOPS.io2.tier2", {}, "io2 IOPS 32001-64000"),
    "ebs_st1": ("AmazonEC2", "EBS:VolumeUsage.st1", {}, "st1 storage"),
    "ebs_sc1": ("AmazonEC2", "EBS:VolumeUsage.sc1", {}, "sc1 storage"),
    "ebs_snap": ("AmazonEC2", "EBS:SnapshotUsage", {}, "EBS snapshot, standard tier"),
    "ebs_snap_archive": ("AmazonEC2", "EBS:SnapshotArchiveStorage", {}, "EBS snapshot, archive tier"),
    "ebs_snap_archive_retrieval": ("AmazonEC2", "EBS:SnapshotArchiveRetrieval", {}, "EBS snapshot archive retrieval per GB"),
}


def instance_key(itype):
    return f"ec2_{itype}"


def add_instance(itype):
    CATALOG[instance_key(itype)] = ("AmazonEC2", f"BoxUsage:{itype}",
                                    {"operatingSystem": "Linux", "tenancy": "Shared", "preInstalledSw": "NA",
                                     "capacitystatus": "Used"}, f"{itype} Linux on-demand per hour")


def _filters(usagetype, extra):
    f = {"location": LOCATION, "usagetype": usagetype, **extra}
    return [f"Type=TERM_MATCH,Field={k},Value={v}" for k, v in f.items()]


def fetch(key):
    """-> {"key", "usagetype", "what", "unit", "dims": [{"usd", "begin", "end", "unit", "desc"}], "sku"} or error."""
    svc, ut, extra, what = CATALOG[key]
    ok, data, err = aws_try("pricing", "get-products", "--region", "us-east-1", "--service-code", svc,
                            "--format-version", "aws_v1", "--filters", *_filters(ut, extra))
    if not ok:
        return {"key": key, "usagetype": ut, "what": what, "error": err[:300]}
    items = [json.loads(p) if isinstance(p, str) else p for p in data.get("PriceList", [])]
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "pricing-raw.jsonl"), "a") as f:
        for it in items:
            f.write(json.dumps({"key": key, "record": it}) + "\n")
    if not items:
        return {"key": key, "usagetype": ut, "what": what, "error": "no product matched"}
    if len(items) > 1:
        # Several products = several prices. Picking one silently is how a wrong price gets in.
        ops = sorted({(i.get("product", {}).get("attributes", {}).get("operation") or "?") for i in items})
        return {"key": key, "usagetype": ut, "what": what,
                "error": f"{len(items)} products matched (operations: {', '.join(ops)}): add a filter"}
    it = items[0]
    dims = []
    for term in it.get("terms", {}).get("OnDemand", {}).values():
        for d in term.get("priceDimensions", {}).values():
            end = d.get("endRange", "Inf")
            dims.append({"usd": float(d["pricePerUnit"]["USD"]), "unit": d.get("unit"),
                         "begin": float(d.get("beginRange", 0)), "end": None if end == "Inf" else float(end),
                         "desc": d.get("description", "")})
    dims.sort(key=lambda d: d["begin"])
    return {"key": key, "usagetype": ut, "what": what, "unit": dims[0]["unit"] if dims else None, "dims": dims,
            "sku": it.get("product", {}).get("sku"), "matches": len(items)}


def fetch_all(keys=None):
    """Fetch every price (prints one line each); starts pricing-raw.jsonl afresh."""
    os.makedirs(RESULTS, exist_ok=True)
    open(os.path.join(RESULTS, "pricing-raw.jsonl"), "w").close()
    out = {}
    for k in keys or list(CATALOG):
        out[k] = fetch(k)
        p = out[k]
        if p.get("error"):
            print(f"   {k:<28} {p['usagetype']:<34} ERROR {p['error']}")
        else:
            first = p["dims"][0]
            tiers = f" (+{len(p['dims']) - 1} more tiers)" if len(p["dims"]) > 1 else ""
            print(f"   {k:<28} {p['usagetype']:<34} {first['usd']:.10g} USD per {first['unit']}{tiers}", flush=True)
    return out


def unit_price(p):
    """Price of the first PAID tier (S3 Standard's first 50 TB, io2's first IOPS tier...)."""
    if not p or p.get("error") or not p.get("dims"):
        return None
    paid = [d for d in p["dims"] if d["usd"] > 0]
    return (paid or p["dims"])[0]["usd"]


def tiered(p, qty):
    """Cost of qty units across the price tiers (begin/end ranges)."""
    if not p or p.get("error") or not p.get("dims"):
        return None
    cost = 0.0
    for d in p["dims"]:
        lo, hi = d["begin"], d["end"] if d["end"] is not None else float("inf")
        if qty > lo:
            cost += (min(qty, hi) - lo) * d["usd"]
    return cost


def per_mibps_month(p):
    """gp3 throughput price per MiB/s-month, whatever unit the Price List uses for it."""
    u = unit_price(p)
    if u is None:
        return None
    unit = (p.get("unit") or "").lower()
    return u / 1024.0 if "gibps" in unit else u
