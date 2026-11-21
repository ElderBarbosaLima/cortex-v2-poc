#!/bin/sh
# Builds the Cortex policy bundle (run by the one-shot `opa-bundle` service).
#
# Revision = sha256 over the path and content of every policy file (tests
# excluded), so the same policies always give the same revision and any change
# gives a new one. OPA exposes it under data.system.bundles; the policy returns
# it as data.cortex.policy.revision, and every context pack stores it.
set -eu

src=/policies
out=/bundles/cortex.tar.gz

revision="sha256:$(
	cd "$src"
	find . -type f \( -name '*.rego' -o -name '.manifest' \) ! -name '*_test.rego' | sort |
		xargs sha256sum | sha256sum | cut -c1-16
)"

/opa build --bundle "$src" --revision "$revision" --ignore '*_test.rego' --ignore '.regal' --output "$out"
chmod 0644 "$out"
echo "Built $out revision=$revision"
