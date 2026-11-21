# The provider is pointed at the AWS Emulator only through environment
# variables (AWS_ENDPOINT_URL, AWS_CA_BUNDLE, AWS_SHARED_CREDENTIALS_FILE,
# AWS_CONFIG_FILE, AWS_REGION; see x-aws-client-env in docker-compose.yml).
# Nothing here names the emulator: the same module runs against real AWS.
provider "aws" {
  # Avoid the STS / IMDS calls the provider makes by default before any resource.
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  # The emulator only accepts path-style S3 requests; harmless on real AWS.
  s3_use_path_style = true

  default_tags {
    tags = {
      project = "cortex-v2-poc"
    }
  }
}
