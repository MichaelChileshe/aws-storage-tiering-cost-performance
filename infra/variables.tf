variable "region" {
  description = "Region for everything in this lab."
  type        = string
  default     = "us-east-1"
}

variable "az_id" {
  description = "Availability Zone ID (not name) for the subnet, the bench instance, its volumes and the S3 Express One Zone directory bucket. Must be an AZ ID that supports S3 Express One Zone in us-east-1 (use1-az4, use1-az5, use1-az6) and offers the instance type."
  type        = string
  default     = "use1-az4"

  validation {
    condition     = contains(["use1-az4", "use1-az5", "use1-az6"], var.az_id)
    error_message = "az_id must be one of the S3 Express One Zone AZ IDs in us-east-1: use1-az4, use1-az5, use1-az6."
  }
}

variable "instance_type" {
  description = "Bench instance (Graviton, arm64). Its EBS-optimized limits cap what any attached volume can show."
  type        = string
  default     = "c7g.xlarge"
}

variable "vpc_cidr" {
  description = "CIDR of the lab VPC."
  type        = string
  default     = "10.29.0.0/16"
}

# ---- phases and drills: change in phases.auto.tfvars, then apply ----

variable "bench_on" {
  description = "Bench phase: the bench instance plus its gp2, gp3, st1, sc1 and small (export) volumes."
  type        = bool
  default     = false
}

variable "io2_on" {
  description = "Bench phase: the io2 volume (the expensive one: switch it off as soon as its fio run is saved). Only created when bench_on is also true."
  type        = bool
  default     = false
}

variable "gp2_to_gp3" {
  description = "gp2 -> gp3 migration: change the gp2 volume's type to gp3 in place (Elastic Volumes), 3000 IOPS / 125 MiB/s."
  type        = bool
  default     = false
}

variable "gp3_iops" {
  description = "gp3 tuning: provisioned IOPS of the gp3 volume (3000 is the included baseline)."
  type        = number
  default     = 3000

  validation {
    condition     = var.gp3_iops >= 3000 && var.gp3_iops <= 50000
    error_message = "gp3_iops must be between 3000 and 50000 (500 IOPS per GiB on the 100 GiB volume)."
  }
}

variable "gp3_throughput" {
  description = "gp3 tuning: provisioned throughput (MiB/s) of the gp3 volume (125 is the included baseline)."
  type        = number
  default     = 125

  validation {
    condition     = var.gp3_throughput >= 125 && var.gp3_throughput <= 2000
    error_message = "gp3_throughput must be between 125 and 2000 MiB/s."
  }
}

variable "snapshot_on" {
  description = "Snapshot archive: a snapshot of the small export volume (archived afterwards with scripts/snapshot_archive.py). Only created when bench_on is also true."
  type        = bool
  default     = false
}

variable "name_suffix" {
  description = "8 random lowercase hex characters that make the bucket names unique. Generated once into infra/name.auto.tfvars (see the README)."
  type        = string

  validation {
    condition     = can(regex("^[a-f0-9]{8}$", var.name_suffix))
    error_message = "name_suffix must be 8 lowercase hex characters (generate it once: see the README)."
  }
}
