# Bench phase: one Graviton instance and the volumes it benchmarks. Every volume gets the SAME fio
# jobs through SSM (scripts/fio_bench.py). The migration and tuning drills change two of these volumes in place.
# The snapshot archive drill snapshots the small "export" volume.

data "aws_ssm_parameter" "al2023_arm" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  # key = the volume's role in the lab (named after the type it STARTS as)
  bench_volumes = {
    gp2 = {
      type       = var.gp2_to_gp3 ? "gp3" : "gp2"
      size       = 100 # gp2: 3 IOPS/GiB -> 300 IOPS baseline, bursts to 3000 on credits
      iops       = var.gp2_to_gp3 ? 3000 : null
      throughput = var.gp2_to_gp3 ? 125 : null
      device     = "/dev/sdf"
    }
    gp3 = {
      type       = "gp3"
      size       = 100
      iops       = var.gp3_iops
      throughput = var.gp3_throughput
      device     = "/dev/sdg"
    }
    st1 = { type = "st1", size = 125, iops = null, throughput = null, device = "/dev/sdh" }
    sc1 = { type = "sc1", size = 125, iops = null, throughput = null, device = "/dev/sdi" }
    # A pretend nightly-export volume: 1 GiB, ~200 MiB written, then snapshotted and archived.
    small = { type = "gp3", size = 1, iops = 3000, throughput = 125, device = "/dev/sdk" }
  }
  io2_volume = {
    io2 = { type = "io2", size = 100, iops = 4000, throughput = null, device = "/dev/sdj" }
  }
  volumes = merge(
    var.bench_on ? local.bench_volumes : {},
    var.bench_on && var.io2_on ? local.io2_volume : {}
  )
}

resource "aws_instance" "bench" {
  count = var.bench_on ? 1 : 0

  ami                    = data.aws_ssm_parameter.al2023_arm.insecure_value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.lab.id
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.bench.name
  ebs_optimized          = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    encrypted   = true
  }

  # The agent travels gzipped: user data is limited to 16 KB.
  user_data = templatefile("${path.module}/templates/userdata.sh.tftpl", {
    agent_gz_b64 = base64gzip(file("${path.module}/../app/bench_agent.py"))
  })
  user_data_replace_on_change = true

  tags = { Name = "zenzele-bench" }

  depends_on = [
    aws_route.internet,
    aws_route_table_association.lab,
    aws_vpc_security_group_egress_rule.all,
    aws_iam_role_policy_attachment.bench_ssm,
  ]

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_ebs_volume" "bench" {
  for_each = local.volumes

  availability_zone = data.aws_availability_zone.lab.name
  type              = each.value.type
  size              = each.value.size
  iops              = each.value.iops
  throughput        = each.value.throughput
  encrypted         = true
  final_snapshot    = false

  tags = { Name = "zenzele-bench-${each.key}", BenchVolume = each.key }

  lifecycle {
    precondition {
      condition     = coalesce(each.value.throughput, 0) <= coalesce(each.value.iops, 0) * 0.25
      error_message = "gp3 throughput can be at most 0.25 MiB/s per provisioned IOPS (e.g. 500 MiB/s needs at least 2000 IOPS)."
    }
  }
}

resource "aws_volume_attachment" "bench" {
  for_each = local.volumes

  device_name = each.value.device
  volume_id   = aws_ebs_volume.bench[each.key].id
  instance_id = aws_instance.bench[0].id
}

resource "aws_ebs_snapshot" "export" {
  count = var.bench_on && var.snapshot_on ? 1 : 0

  volume_id   = aws_ebs_volume.bench["small"].id
  description = "Zenzele nightly export volume - archive tier drill"
  tags        = { Name = "zenzele-export-snapshot" }

  timeouts {
    create = "30m"
  }

  lifecycle {
    # The archive tier is changed by scripts/snapshot_archive.py, not by Terraform.
    ignore_changes = [storage_tier]
  }
}
