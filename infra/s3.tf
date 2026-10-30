# The S3 side of the lab:
#  - one general purpose bucket (SSE-S3, Block Public Access, TLS-only) holding the same test
#    objects in five storage classes, the lifecycle rules Zenzele would run, and an
#    Intelligent-Tiering archive configuration;
#  - one S3 Express One Zone directory bucket in the same AZ as the bench instance;
#  - a free-metrics S3 Storage Lens configuration scoped to the lab bucket.
# The test objects are written by the bench instance, not by Terraform, so both buckets use
# force_destroy: "terraform destroy" empties them first.


locals {
  # A random suffix made once on my machine (infra/name.auto.tfvars): bucket names are global,
  # and nothing in a name may point back to the account ID.
  suffix         = var.name_suffix
  lab_bucket     = "zenzele-tiering-${local.suffix}"
  express_bucket = "zenzele-express-${local.suffix}--${var.az_id}--x-s3"
}

resource "aws_s3_bucket" "lab" {
  bucket        = local.lab_bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "lab" {
  bucket                  = aws_s3_bucket.lab.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lab" {
  bucket = aws_s3_bucket.lab.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Refuse any request that isn't over TLS. The CLI, boto3 and Terraform always use TLS, so this
# can't lock anyone out.
resource "aws_s3_bucket_policy" "lab" {
  bucket = aws_s3_bucket.lab.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TLSOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.lab.arn, "${aws_s3_bucket.lab.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.lab]
}

# The tiering rules Zenzele would run. Nothing in this lab is old enough for a transition to
# happen (the shortest is 30 days): the drill reads the configuration back, it can't watch it act.
resource "aws_s3_bucket_lifecycle_configuration" "lab" {
  bucket = aws_s3_bucket.lab.id

  # The AWS default since September 2024, written out so it is visible: objects smaller than
  # 128 KB are not transitioned to ANY storage class by lifecycle.
  transition_default_minimum_object_size = "all_storage_classes_128K"

  rule {
    id     = "pacs-studies-to-glacier-ir-after-90-days"
    status = "Enabled"
    filter {
      prefix = "pacs/"
    }
    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }
  }

  # THE TRAP, kept on purpose: 20 KB lab results "archived" to Glacier Flexible Retrieval.
  # With the 128 KB default above, these small objects never move. scripts/cost_model.py shows
  # what it would cost if someone overrode the default.
  rule {
    id     = "lab-results-to-glacier-after-30-days-TRAP"
    status = "Enabled"
    filter {
      prefix = "lab-results/"
    }
    transition {
      days          = 30
      storage_class = "GLACIER"
    }
  }

  rule {
    id     = "logs-ia-30-glacier-90-expire-400"
    status = "Enabled"
    filter {
      prefix = "logs/"
    }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
    expiration {
      days = 400
    }
  }

  rule {
    id     = "nightly-exports-expire-after-35-days"
    status = "Enabled"
    filter {
      prefix = "exports/"
    }
    expiration {
      days = 35
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads-7-days"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Intelligent-Tiering's optional archive tiers for de-identified research extracts (objects
# stored in the INTELLIGENT_TIERING class under research/): untouched for 90 days -> Archive
# Access, 180 days -> Deep Archive Access.
resource "aws_s3_bucket_intelligent_tiering_configuration" "research" {
  bucket = aws_s3_bucket.lab.id
  name   = "research-archive-tiers"
  status = "Enabled"

  filter {
    prefix = "research/"
  }

  tiering {
    access_tier = "ARCHIVE_ACCESS"
    days        = 90
  }

  tiering {
    access_tier = "DEEP_ARCHIVE_ACCESS"
    days        = 180
  }
}

# S3 Express One Zone: a directory bucket in ONE Availability Zone (the bench instance's).
resource "aws_s3_directory_bucket" "express" {
  bucket          = local.express_bucket
  data_redundancy = "SingleAvailabilityZone"
  type            = "Directory"
  force_destroy   = true

  location {
    name = var.az_id
    type = "AvailabilityZone"
  }
}

# Storage Lens with FREE metrics only (no advanced metrics, no export), scoped to the lab
# bucket. Dashboards update daily; a new one can take up to 48 hours to show its first data.
resource "aws_s3control_storage_lens_configuration" "lab" {
  config_id = "zenzele-tiering-lens"

  storage_lens_configuration {
    enabled = true

    account_level {
      bucket_level {}
    }

    include {
      buckets = [aws_s3_bucket.lab.arn]
    }
  }
}
