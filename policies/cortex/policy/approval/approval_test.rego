package cortex.policy.approval_test

import data.cortex.policy.approval
import data.cortex.policy.authz_test

decide(action, actor, contributors) := decide_at("jp/finance/q2c-invoicing", action, actor, contributors)

decide_at(scope, action, actor, contributors) := d if {
	request := {
		"tenant_id": "t-jp", "action": action, "actor": actor, "now": authz_test.now,
		"record": {"scope": scope, "contributors": contributors},
	}
	d := approval.decision with input as request
		with data.cortex.tenants as authz_test.tenants
		with data.system.bundles as authz_test.bundles
}

owner := {"type": "user", "id": "OWN-001"}

reviewer := {"type": "user", "id": "R-001"}

agent := {"type": "consumer", "id": "askq2c-agent"}

test_a_reviewer_and_the_agent_may_propose if {
	decide("propose", reviewer, []).allow
	decide("propose", agent, []).allow
}

test_the_named_owner_approves_and_rejects if {
	d := decide("approve", owner, ["R-001"])
	d.allow
	d.rbac.grant_id == "g-own"
	d.policy_revision == "sha256:test"
	decide("reject", owner, ["R-001"]).allow
}

test_the_corrector_cannot_approve_the_rule_from_their_own_correction if {
	# Give the corrector approval rights too: separation of duties must still win.
	tenants := {"t-jp": object.union(authz_test.tenant, {"grants": array.concat(authz_test.tenant.grants, [
		{"id": "g-r1-fv", "principal_type": "user", "principal_id": "R-001", "role": "final_validator", "node": "jp"},
	])})}
	d := approval.decision with input as {
		"tenant_id": "t-jp", "action": "approve", "actor": reviewer, "now": authz_test.now,
		"record": {"scope": "jp/finance/q2c-invoicing", "contributors": ["R-001"]},
	}
		with data.cortex.tenants as tenants
		with data.system.bundles as authz_test.bundles
	not d.allow
	d.reason == "separation of duties: a contributor to this record cannot decide on it"
}

test_a_reviewer_without_memory_approve_cannot_approve if {
	d := decide("approve", reviewer, ["R-002"])
	not d.allow
	d.rbac.verdict == "no-permission"
}

test_a_machine_never_approves if {
	d := decide("approve", agent, [])
	not d.allow
	d.reason == "only a person can approve or reject"
}

test_unknown_action_is_denied if {
	not decide("delete", owner, []).allow
}

test_malformed_now_is_denied if {
	d := approval.decision with input as {
		"tenant_id": "t-jp", "action": "approve", "actor": owner, "now": 42,
		"record": {"scope": "jp/finance/q2c-invoicing", "contributors": []},
	}
		with data.cortex.tenants as authz_test.tenants
	d.reason == "now is required (RFC 3339)"
}

test_the_backup_decides_too if {
	decide("approve", {"type": "user", "id": "OWN-002"}, ["R-001"]).allow
	decide("reject", {"type": "user", "id": "OWN-002"}, ["R-001"]).allow
}

test_a_final_validator_who_is_not_the_named_owner_cannot_decide if {
	# FV-009 holds memory.approve above, and owns jp/finance, but q2c-invoicing
	# has its own named owner: the nearest owned node wins.
	d := decide("approve", {"type": "user", "id": "FV-009"}, [])
	not d.allow
	d.rbac.allowed
	d.reason == "only the named owner or backup of this scope can decide"
}

test_a_scope_without_its_own_owner_uses_the_nearest_owner_above if {
	decide_at("jp/finance/q2c-cash-application", "approve", {"type": "user", "id": "FV-009"}, []).allow
	not decide_at("jp/finance/q2c-cash-application", "approve", owner, []).allow
}

test_no_named_owner_anywhere_up_the_tree_denies if {
	tenants := {"t-jp": object.remove(authz_test.tenant, ["owners"])}
	d := approval.decision with input as {
		"tenant_id": "t-jp", "action": "approve", "actor": owner, "now": authz_test.now,
		"record": {"scope": "jp/finance/q2c-invoicing", "contributors": []},
	}
		with data.cortex.tenants as tenants
		with data.system.bundles as authz_test.bundles
	not d.allow
	d.reason == "no named owner for this scope"
}

test_proposing_needs_no_named_owner if {
	decide_at("jp/finance/q2c-cash-application", "propose", agent, []).allow
}
