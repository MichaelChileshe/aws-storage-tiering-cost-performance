#!/usr/bin/env python3
"""Read back what Terraform configured on S3 (lifecycle, Intelligent-Tiering archive tiers, Storage
Lens) and what objects the lab wrote, with their storage classes.

  python3 scripts/s3_config.py

Lifecycle transitions can't be watched in a lab: the shortest rule here acts after 30 days (and
S3 Standard-IA transitions need at least 30 days anyway). This shows the rules S3 holds, not
transitions happening. Storage Lens dashboards are updated daily and a new one can take up to
48 hours for its first data: this lists the configurations, it does not read metrics.
Saves build/results/s3-config.json.
"""
from kit_aws import aws, aws_try, tf_output, write_result
from lab import now


def main():
    bucket, express, lens = tf_output("lab_bucket"), tf_output("express_bucket"), tf_output("storage_lens_config_id")
    out = {"bucket": bucket, "express_bucket": express, "utc": now()}

    lc = aws("s3api", "get-bucket-lifecycle-configuration", "--bucket", bucket)
    out["lifecycle"] = lc
    print(f"== lifecycle on {bucket}  (TransitionDefaultMinimumObjectSize = "
          f"{lc.get('TransitionDefaultMinimumObjectSize', 'not returned by this CLI version')})")
    for r in lc.get("Rules", []):
        flt = r.get("Filter", {})
        scope = flt.get("Prefix", flt.get("And", {}).get("Prefix", "(whole bucket)")) if flt else "(whole bucket)"
        acts = [f"{t['Days']} d -> {t['StorageClass']}" for t in r.get("Transitions", [])]
        if r.get("Expiration"):
            acts.append(f"expire after {r['Expiration'].get('Days')} d")
        if r.get("AbortIncompleteMultipartUpload"):
            acts.append(f"abort incomplete MPU after {r['AbortIncompleteMultipartUpload']['DaysAfterInitiation']} d")
        print(f"   {r['ID']:<45} {r['Status']:<8} {scope or '(whole bucket)':<14} {'; '.join(acts)}")

    it = aws("s3api", "get-bucket-intelligent-tiering-configuration", "--bucket", bucket,
             "--id", "research-archive-tiers").get("IntelligentTieringConfiguration", {})
    out["intelligent_tiering"] = it
    print(f"\n== Intelligent-Tiering configuration {it.get('Id')} ({it.get('Status')}), prefix "
          f"{it.get('Filter', {}).get('Prefix')}: " +
          ", ".join(f"{t['AccessTier']} after {t['Days']} d" for t in it.get("Tierings", [])))

    acct = aws("sts", "get-caller-identity")["Account"]  # used in the call only, never printed
    ok, ls, err = aws_try("s3control", "list-storage-lens-configurations", "--account-id", acct)
    names = [c.get("Id") for c in ls.get("StorageLensConfigurationList", [])] if ok else []
    out["storage_lens_ids"] = names
    print(f"\n== Storage Lens configurations in this account (Home Region us-east-1 here): "
          f"{', '.join(names) if ok else 'ERROR ' + err[:200]}")
    ok, g, err = aws_try("s3control", "get-storage-lens-configuration", "--account-id", acct, "--config-id", lens)
    if ok:
        c = g.get("StorageLensConfiguration", {})
        out["storage_lens"] = {"id": c.get("Id"), "enabled": c.get("IsEnabled"), "include": c.get("Include"),
                               "account_level_keys": sorted(c.get("AccountLevel", {}).keys())}
        print(f"   {lens}: enabled={c.get('IsEnabled')}, scope {c.get('Include', {}).get('Buckets')}, "
              f"advanced metrics: {'none (free tier)' if not c.get('AccountLevel', {}).get('ActivityMetrics') else 'ON'}")
    print("   (metrics appear once a day; a new dashboard can take up to 48 h - nothing to read during this lab)")

    objs = aws("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", "bench/").get("Contents", [])
    out["objects"] = [{"key": o["Key"], "bytes": o["Size"], "class": o.get("StorageClass")} for o in objs]
    print(f"\n== objects under bench/ in {bucket}")
    for o in out["objects"]:
        print(f"   {o['key']:<40} {o['bytes']:>9}  {o['class']}")
    ok, ex, err = aws_try("s3api", "list-objects-v2", "--bucket", express)
    out["express_objects"] = [{"key": o["Key"], "bytes": o["Size"], "class": o.get("StorageClass")}
                              for o in ex.get("Contents", [])] if ok else err[:200]
    print(f"\n== objects in the directory bucket {express}")
    if ok:
        for o in out["express_objects"]:
            print(f"   {o['key']:<40} {o['bytes']:>9}  {o['class']}")
    else:
        print(f"   ERROR (an AWS CLI older than directory-bucket support?): {err[:200]}")
    write_result("s3-config", out)
    print("\nsaved build/results/s3-config.json")


if __name__ == "__main__":
    main()
