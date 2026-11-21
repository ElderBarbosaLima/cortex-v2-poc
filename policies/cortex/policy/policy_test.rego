package cortex.policy_test

import data.cortex.policy

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
