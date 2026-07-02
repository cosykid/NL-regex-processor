locals {
  # Auto-generate a globally-unique bucket name unless one is given explicitly.
  bucket_name = coalesce(
    var.bucket_name != "" ? var.bucket_name : null,
    "${var.name_prefix}-${var.environment}-${random_id.suffix.hex}",
  )
}

resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "files" {
  bucket        = local.bucket_name
  force_destroy = var.force_destroy
}

# Disable ACLs entirely — the bucket owner owns every object. This is the AWS
# security best practice and removes a whole class of ACL misconfigurations.
resource "aws_s3_bucket_ownership_controls" "files" {
  bucket = aws_s3_bucket.files.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Belt and braces: block ALL public access at the bucket level. Uploaded user
# data must never be world-readable; the app serves it through presigned URLs
# or by proxying, never via public objects.
resource "aws_s3_bucket_public_access_block" "files" {
  bucket                  = aws_s3_bucket.files.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "files" {
  bucket = aws_s3_bucket.files.id
  versioning_configuration {
    status = var.enable_versioning ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "files" {
  bucket = aws_s3_bucket.files.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.sse_algorithm
      kms_master_key_id = var.sse_algorithm == "aws:kms" && var.kms_key_arn != "" ? var.kms_key_arn : null
    }
    bucket_key_enabled = var.sse_algorithm == "aws:kms"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "files" {
  bucket = aws_s3_bucket.files.id

  # Reclaim storage from failed multipart uploads (large CSVs upload in parts).
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_incomplete_multipart_days
    }
  }

  # Trim superseded versions so versioning doesn't grow unbounded.
  rule {
    id     = "expire-noncurrent-versions"
    status = var.enable_versioning ? "Enabled" : "Disabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }
  }

  # Optional retention on the uploads/ prefix.
  dynamic "rule" {
    for_each = var.uploads_expiration_days > 0 ? [1] : []
    content {
      id     = "expire-uploads"
      status = "Enabled"
      filter {
        prefix = "uploads/"
      }
      expiration {
        days = var.uploads_expiration_days
      }
    }
  }

  # Optional retention on the results/ prefix (Spark output).
  dynamic "rule" {
    for_each = var.results_expiration_days > 0 ? [1] : []
    content {
      id     = "expire-results"
      status = "Enabled"
      filter {
        prefix = "results/"
      }
      expiration {
        days = var.results_expiration_days
      }
    }
  }
}

# Only created when cors_allowed_origins is non-empty (direct browser<->S3).
resource "aws_s3_bucket_cors_configuration" "files" {
  count  = length(var.cors_allowed_origins) > 0 ? 1 : 0
  bucket = aws_s3_bucket.files.id
  cors_rule {
    allowed_methods = ["GET", "PUT", "HEAD"]
    allowed_origins = var.cors_allowed_origins
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}
