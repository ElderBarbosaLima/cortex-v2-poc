#!/bin/sh
# Run by the `terraform` service (make provision / make tf-plan).
#   provision.sh apply   init + apply (idempotent)
#   provision.sh plan    init + plan; exit 0 = no changes, 2 = changes pending
set -eu

cd /workspace/infra/terraform/aws
terraform init -input=false -lockfile=readonly >/dev/null

case "${1:-apply}" in
  apply) exec terraform apply -input=false -auto-approve ;;
  plan)  exec terraform plan -input=false -detailed-exitcode ;;
  *) echo "usage: provision.sh apply|plan" >&2; exit 64 ;;
esac
