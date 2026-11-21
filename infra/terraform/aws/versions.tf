terraform {
  required_version = "~> 1.17.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.73.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "2.6.1"
    }
  }

  # Local state on a volume that lives and dies with the emulator stack
  # (docs/discovery.md 6.9; the emulator keeps everything in memory).
  backend "local" {
    path = "/state/terraform.tfstate"
  }
}
