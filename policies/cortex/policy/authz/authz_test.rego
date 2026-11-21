package cortex.policy.authz_test

import data.cortex.policy.authz

now := "2026-12-03T09:00:00+09:00"

# The synthetic tenant (data/synthetic/tenant.json): org → jp → jp/finance →
# q2c-invoicing | q2c-cash-application; and a second tenant "other".
_n(parent, type, path) := {"parent": parent, "type": type, "is_barrier": false, "path_up": path}

_grant(id, type, principal, role, node) := {
	"id": id, "principal_type": type, "principal_id": principal,
	"role": role, "node": node,
}

tenant := {
	"nodes": {
		"org": _n(null, "organization", ["org"]),
		"jp": _n("org", "tenant", ["jp", "org"]),
		"jp/finance": _n("jp", "team", ["jp/finance", "jp", "org"]),
		"jp/finance/q2c-invoicing": _n(
			"jp/finance", "team",
			["jp/finance/q2c-invoicing", "jp/finance", "jp", "org"],
		),
		"jp/finance/q2c-cash-application": _n(
			"jp/finance", "team",
			["jp/finance/q2c-cash-application", "jp/finance", "jp", "org"],
		),
	},
	"roles": {
		"cortex_consumer": ["memory.read", "memory.write"],
		"reviewer": ["memory.read", "memory.write"],
		"final_validator": ["memory.read", "memory.write", "memory.approve"],
	},
	"grants": [
		_grant("g-agent", "consumer", "askq2c-agent", "cortex_consumer", "jp/finance"),
		_grant("g-r1", "user", "R-001", "reviewer", "jp/finance/q2c-invoicing"),
		_grant("g-own", "user", "OWN-001", "final_validator", "jp/finance"),
	],
	"consumers": {"askq2c-agent": {"purposes": {"invoice_drafting": {"sensitivity_ceiling": "confidential"}}}},
}

tenants := {"t-jp": tenant}

bundles := {"/bundles/cortex.tar.gz": {"manifest": {"revision": "sha256:test", "roots": ["cortex/policy"]}}}

read(overrides) := d if {
	request := object.union(
		{
			"tenant_id": "t-jp", "consumer": {"id": "askq2c-agent"}, "end_user": null,
			"purpose": "invoice_drafting", "scope_node": "jp/finance/q2c-invoicing", "now": now,
		},
		overrides,
	)
	d := authz.decision with input as request
		with data.cortex.tenants as tenants
		with data.system.bundles as bundles
}

test_consumer_with_a_grant_above_reads_upward_scopes_and_the_ceiling if {
	d := read({})
	d.allow
	d.consumer.verdict == "inherited-allow"
	d.readable_scopes == ["jp/finance/q2c-invoicing", "jp/finance", "jp"] # never the org root, never siblings
	d.sensitivity_ceiling == "confidential"
	d.policy_revision == "sha256:test"
}

test_end_user_must_also_be_allowed if {
	allowed := read({"end_user": {"id": "R-001"}})
	allowed.allow
	allowed.end_user.verdict == "nearest-allow"

	denied := read({"end_user": {"id": "R-001"}, "scope_node": "jp/finance/q2c-cash-application"})
	not denied.allow
	startswith(denied.reason, "end user:")
	denied.end_user.verdict == "out-of-scope"
	denied.readable_scopes == []
}

test_unregistered_purpose_is_denied if {
	d := read({"purpose": "marketing"})
	not d.allow
	contains(d.reason, "purpose \"marketing\" is not registered")
	d.sensitivity_ceiling == null
}

test_unknown_consumer_is_denied if {
	d := read({"consumer": {"id": "someone-else"}})
	not d.allow
}

test_unknown_tenant_and_foreign_scope_are_denied if {
	read({"tenant_id": "t-nope"}).reason == "unknown tenant"
	read({"scope_node": "elsewhere"}).reason == "scope is not a node of this tenant"
	read({"scope_node": "org"}).reason == "scope is not a node of this tenant"
}

test_missing_or_malformed_now_is_denied_not_undefined if {
	read({"now": null}).reason == "now is required (RFC 3339)"
	read({"now": "yesterday"}).reason == "now is required (RFC 3339)"
	not read({"now": "yesterday"}).allow
}

test_expired_consumer_grant_is_denied_with_the_reason if {
	expired := object.union(tenant, {"grants": [object.union(tenant.grants[0], {"expires_at": now})]})
	d := authz.decision with input as {
		"tenant_id": "t-jp", "consumer": {"id": "askq2c-agent"}, "end_user": null,
		"purpose": "invoice_drafting", "scope_node": "jp/finance/q2c-invoicing", "now": now,
	}
		with data.cortex.tenants as {"t-jp": expired}
		with data.system.bundles as bundles
	not d.allow
	d.consumer.verdict == "expired"
}
