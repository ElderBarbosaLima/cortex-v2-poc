# Least-privilege IAM policy for org-brain-service, rendered to infra/iam/*.json
# as a reviewable artefact. Nothing is sent to IAM: the AWS Emulator does not
# enforce IAM ("works locally" is no proof of a permission), and on real AWS the
# policy would be attached by the platform module, not by this PoC.

locals {
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.region
  kms_aliases = "alias/cortex/tenant/*"
}

data "aws_iam_policy_document" "org_brain" {
  # Evidence export: write packs with retention, read them back. No delete.
  statement {
    sid       = "EvidenceArchiveWriteRead"
    actions   = ["s3:PutObject", "s3:PutObjectRetention", "s3:GetObject", "s3:GetObjectRetention"]
    resources = ["${aws_s3_bucket.evidence.arn}/*"]
  }

  statement {
    sid       = "OriginalsReadWrite"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.originals.arn}/*"]
  }

  # Tenant onboarding creates one KMS key per tenant. kms:CreateKey has no
  # resource to scope to, so it is Resource "*"; everything done WITH a key
  # below is restricted to keys behind a tenant alias. (On real AWS a
  # permissions boundary or an aws:RequestTag condition would narrow it further.)
  statement {
    sid       = "CreateTenantKeys"
    actions   = ["kms:CreateKey"]
    resources = ["*"]
  }

  statement {
    sid     = "AliasTenantKeys"
    actions = ["kms:CreateAlias"]
    resources = [
      "arn:aws:kms:${local.region}:${local.account_id}:${local.kms_aliases}",
      "arn:aws:kms:${local.region}:${local.account_id}:key/*",
    ]
  }

  # Envelope encryption and crypto-shredding (tenant erasure disables the key
  # and schedules its deletion, issue 24), only on keys reached through a
  # tenant alias, and only with an encryption context naming the tenant.
  statement {
    sid = "UseTenantKeys"
    actions = [
      "kms:GenerateDataKey",
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:DisableKey",
      "kms:ScheduleKeyDeletion",
    ]
    resources = ["arn:aws:kms:${local.region}:${local.account_id}:key/*"]

    condition {
      test     = "ForAnyValue:StringLike"
      variable = "kms:ResourceAliases"
      values   = [local.kms_aliases]
    }
  }

  statement {
    sid       = "RequireTenantEncryptionContext"
    effect    = "Deny"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "Null"
      variable = "kms:EncryptionContext:tenant_id"
      values   = ["true"]
    }
  }

  statement {
    sid     = "ReadServiceSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    # Name + "-??????" (AWS's documented pattern for the random ARN suffix), so
    # the rendered policy does not change every time the secret is recreated.
    resources = [
      "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:${aws_secretsmanager_secret.consumer_api_keys.name}-??????",
      "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:${aws_secretsmanager_secret.litellm_api_key.name}-??????",
    ]
  }
}

resource "local_file" "org_brain_policy" {
  filename        = "${var.iam_policy_dir}/org-brain-service.json"
  content         = "${data.aws_iam_policy_document.org_brain.json}\n"
  file_permission = "0644"
}
