# Cortex PoC resources on AWS (Rev. C mapping: COS → S3, Secrets Manager → Secrets Manager).
# KMS keys are NOT here: there is one per tenant and the application creates
# it when a tenant is onboarded (issue 14).

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

# ── Evidence archive: exported context packs (issue 23) ─────────────────────

resource "aws_s3_bucket" "evidence" {
  bucket              = "${var.name_prefix}-evidence"
  object_lock_enabled = true
}

resource "aws_s3_bucket_versioning" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.evidence_retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.evidence]
}

# ── Originals: source files referenced by records ───────────────────────────

resource "aws_s3_bucket" "originals" {
  bucket = "${var.name_prefix}-originals"
}

resource "aws_s3_bucket_versioning" "originals" {
  bucket = aws_s3_bucket.originals.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ── Both buckets: private, encrypted at rest ────────────────────────────────

locals {
  buckets = {
    evidence  = aws_s3_bucket.evidence.id
    originals = aws_s3_bucket.originals.id
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = each.value
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ── Consumer API key hashes (read by org-brain, issue 13) ───────────────────

resource "aws_secretsmanager_secret" "consumer_api_keys" {
  name                    = "${var.name_prefix}/consumer-api-keys"
  description             = "sha256 of each consumer's static API key (PoC identity, no raw keys)."
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "consumer_api_keys" {
  secret_id = aws_secretsmanager_secret.consumer_api_keys.id
  secret_string = jsonencode({
    for consumer, key in var.consumer_api_keys : consumer => { sha256 = sha256(key) }
  })
}
