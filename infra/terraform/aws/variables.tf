variable "name_prefix" {
  description = "Prefix for every resource name."
  type        = string
  default     = "cortex-poc"
}

variable "evidence_retention_days" {
  description = "Default Object Lock COMPLIANCE retention for exported evidence (Rev. C: immutable retention)."
  type        = number
  default     = 1
}

variable "consumer_api_keys" {
  description = <<-EOT
    Static API key per consumer (PoC stand-in for OIDC, docs/discovery.md P3).
    Only sha256 hashes are stored in Secrets Manager. Values come from the local,
    git-ignored .env (TF_VAR_consumer_api_keys); never commit real values.
  EOT
  type        = map(string)
  sensitive   = true
}

variable "iam_policy_dir" {
  description = "Where the rendered IAM policy documents are written (versioned in the repo)."
  type        = string
  default     = "/workspace/infra/iam"
}

variable "litellm_api_key" {
  description = "LiteLLM API key, from the git-ignored .env (TF_VAR_litellm_api_key). Empty: AI stays off."
  type        = string
  default     = ""
  sensitive   = true
}

variable "example_api_keys" {
  description = <<-EOT
    Keys of the example sets' consumers (issue 39: the English example tenant's
    Decision Desk and agent). Kept apart from consumer_api_keys so `make init-env`
    can add them to an existing .env. Same rules: only hashes are stored.
  EOT
  type        = map(string)
  sensitive   = true
  default     = {}
}
