"""What AWS documents each EBS volume type can do (Amazon EBS User Guide, volume types pages),
as numbers to put next to what fio measured. Used by fio_bench.py and cost_model.py."""


def spec(vtype, size_gib, iops=None, throughput=None):
    """{iops_base, iops_burst, mibps_base, mibps_burst, note} for one volume. None = not a fixed number."""
    tib = size_gib / 1024.0
    if vtype == "gp2":
        base = min(max(100, 3 * size_gib), 16000)
        burst = 3000 if (size_gib < 1024 and base < 3000) else None
        mib = 128 if size_gib <= 170 else 250
        return {"iops_base": base, "iops_burst": burst, "mibps_base": None, "mibps_burst": mib,
                "note": f"3 IOPS/GiB = {base} baseline" + (", bursts to 3000 on credits" if burst else "")
                        + f"; up to {mib} MiB/s"}
    if vtype == "gp3":
        return {"iops_base": iops or 3000, "iops_burst": None, "mibps_base": throughput or 125, "mibps_burst": None,
                "note": f"{iops or 3000} IOPS / {throughput or 125} MiB/s provisioned, no credits"}
    if vtype == "io2":
        return {"iops_base": iops, "iops_burst": None, "mibps_base": None, "mibps_burst": None,
                "note": f"{iops} IOPS provisioned"}
    if vtype == "st1":
        base, burst = min(40 * tib, 500), min(250 * tib, 500)
        return {"iops_base": None, "iops_burst": None, "mibps_base": round(base, 1), "mibps_burst": round(burst, 1),
                "note": f"40 MiB/s per TiB baseline = {base:.1f}, burst 250/TiB = {burst:.1f} MiB/s; "
                        "every I/O up to 1 MiB costs a 1 MiB credit"}
    if vtype == "sc1":
        base, burst = min(12 * tib, 192), min(80 * tib, 250)
        return {"iops_base": None, "iops_burst": None, "mibps_base": round(base, 1), "mibps_burst": round(burst, 1),
                "note": f"12 MiB/s per TiB baseline = {base:.1f}, burst 80/TiB = {burst:.1f} MiB/s; "
                        "every I/O up to 1 MiB costs a 1 MiB credit"}
    return {"iops_base": None, "iops_burst": None, "mibps_base": None, "mibps_burst": None, "note": "-"}
