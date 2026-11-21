output "evidence_bucket" {
  value = aws_s3_bucket.evidence.id
}

output "originals_bucket" {
  value = aws_s3_bucket.originals.id
}

output "consumer_api_keys_secret_arn" {
  value = aws_secretsmanager_secret.consumer_api_keys.arn
}
