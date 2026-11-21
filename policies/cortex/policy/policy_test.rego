package cortex.policy_test

import data.cortex.policy

test_allow_is_denied_by_default if {
	not policy.allow
}

test_allow_stays_denied_whatever_the_input if {
	not policy.allow with input as {"consumer": "askq2c", "purpose": "invoice_drafting"}
}

test_revision_comes_from_the_bundle_that_owns_cortex_policy if {
	bundles := {
		"/bundles/other.tar.gz": {"manifest": {"revision": "other", "roots": ["other"]}},
		"/bundles/cortex.tar.gz": {"manifest": {"revision": "sha256:abc", "roots": ["cortex/policy"]}},
	}
	policy.revision == "sha256:abc" with data.system.bundles as bundles
}

test_revision_is_undefined_without_a_bundle if {
	not policy.revision with data.system.bundles as {}
}
