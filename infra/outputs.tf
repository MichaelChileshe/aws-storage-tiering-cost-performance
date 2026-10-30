output "region" {
  description = "Region of everything in the lab."
  value       = var.region
}

output "az" {
  description = "The lab's AZ: ID (same in every account) and name (this account's label for it)."
  value       = { id = var.az_id, name = data.aws_availability_zone.lab.name }
}

output "lab_bucket" {
  description = "General purpose bucket: storage-class tests, lifecycle rules, Intelligent-Tiering config."
  value       = aws_s3_bucket.lab.bucket
}

output "express_bucket" {
  description = "S3 Express One Zone directory bucket (same AZ as the bench instance)."
  value       = aws_s3_directory_bucket.express.bucket
}

output "storage_lens_config_id" {
  description = "Free-metrics Storage Lens configuration scoped to the lab bucket."
  value       = aws_s3control_storage_lens_configuration.lab.config_id
}

output "instance_type" {
  description = "Bench instance type (its EBS limits cap every volume's result)."
  value       = var.instance_type
}

output "bench_instance_id" {
  description = "Bench instance ID (null when bench_on = false)."
  value       = one(aws_instance.bench[*].id)
}

output "volumes" {
  description = "Bench volumes by role: volume ID, type, size, IOPS, throughput as Terraform knows them."
  value = { for k, v in aws_ebs_volume.bench : k => {
    id = v.id, type = v.type, size = v.size, iops = v.iops, throughput = v.throughput
  } }
}

output "export_snapshot_id" {
  description = "Snapshot of the small export volume (null until snapshot_on = true)."
  value       = one(aws_ebs_snapshot.export[*].id)
}

output "agent_sha256" {
  description = "SHA-256 of app/bench_agent.py: compare with the copy on the instance."
  value       = filesha256("${path.module}/../app/bench_agent.py")
}
