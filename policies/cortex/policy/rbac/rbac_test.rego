# Parity with arbitr-rbac's tests (UAT libraries/arbitr-rbac/tests/test_authorize.py):
# same fictitious "Meridian" tree, same catalog shape, same cases and verdicts.
package cortex.policy.rbac_test

import data.cortex.policy.rbac

now := "2026-10-08T12:00:00Z"

_n(parent, name, type, barrier, path) := {
	"parent": parent, "name": name, "type": type,
	"is_barrier": barrier, "path_up": path,
}

nodes := {
	"root": _n(null, "Meridian Capital Group", "organization", false, ["root"]),
	"japan": _n("root", "Meridian Japan", "tenant", false, ["japan", "root"]),
	"securities": _n("japan", "Securities", "business_unit", false, ["securities", "japan", "root"]),
	"fin-reporting": _n(
		"securities", "Financial Reporting", "department", false,
		["fin-reporting", "securities", "japan", "root"],
	),
	"ib": _n("japan", "Investment Banking", "business_unit", false, ["ib", "japan", "root"]),
	"mna": _n("ib", "M&A Advisory", "team", true, ["mna", "ib", "japan", "root"]),
	"falcon": _n("mna", "Project Falcon", "team", true, ["falcon", "mna", "ib", "japan", "root"]),
	"germany": _n("root", "Meridian Germany", "tenant", false, ["germany", "root"]),
	"private-banking": _n("germany", "Private Banking", "business_unit", false, ["private-banking", "germany", "root"]),
	"tax": _n("private-banking", "Tax & Audit", "department", false, ["tax", "private-banking", "germany", "root"]),
}

roles := {
	"organisation_manager": [
		"org.read", "org.write", "team.read", "user.role.change",
		"node.manage", "project.read", "project.write", "audit.read",
	],
	"tenant_admin": ["org.read", "team.read", "user.role.change", "node.manage", "project.read", "project.write"],
	"viewer": ["org.read", "team.read", "project.read"],
	"final_validator": ["org.read", "project.read", "memory.approve"],
	"auditor": ["org.read", "project.read", "audit.read"],
}

user := {"type": "user", "id": "user-1"}

grant(id, role, node) := {"id": id, "role": role, "node": node, "principal_type": "user", "principal_id": "user-1"}

expiring(id, role, node, at) := object.union(grant(id, role, node), {"expires_at": at})

decide(grants, perm, node) := rbac.decide({"nodes": nodes, "roles": roles, "grants": grants}, user, perm, node, now)

test_ae1_root_organisation_manager_is_blocked_by_barrier_and_named if {
	d := decide([grant("g-alex", "organisation_manager", "root")], "project.read", "mna")
	d.verdict == "barrier"
	not d.allowed
	d.grant_id == "g-alex"
	d.node_id == "mna"
	contains(d.reason, "M&A Advisory")
	contains(d.reason, "organisation_manager")
}

test_barrier_blocks_targets_below_it_too if {
	d := decide([grant("g", "organisation_manager", "root")], "project.read", "falcon")
	d.verdict == "barrier"
	d.node_id == "falcon"
}

test_administrative_permission_crosses_barrier_and_flags_it if {
	d := decide([grant("g", "organisation_manager", "root")], "user.role.change", "mna")
	d.verdict == "inherited-allow"
	d.barrier_crossing == true
	d.grant_id == "g"
}

test_administrative_permission_outside_barrier_is_not_a_crossing if {
	d := decide([grant("g", "organisation_manager", "root")], "user.role.change", "tax")
	d.allowed
	d.barrier_crossing == false
}

test_ae2_grant_on_barrier_node_is_nearest_allow if {
	d := decide([grant("g-yuki", "final_validator", "mna")], "project.read", "mna")
	d.verdict == "nearest-allow"
	d.grant_id == "g-yuki"
}

test_grant_on_barrier_parent_does_not_reach_barrier_itself if {
	d := decide([grant("g", "viewer", "ib")], "project.read", "mna")
	d.verdict == "barrier"
	d.grant_id == "g"
}

test_inherited_allow_names_ancestor_grant if {
	d := decide([grant("g-kenji", "tenant_admin", "japan")], "project.read", "fin-reporting")
	d.verdict == "inherited-allow"
	d.grant_id == "g-kenji"
	d.node_id == "japan"
	contains(d.reason, "Meridian Japan")
}

test_ae3_only_expired_grant_is_expired if {
	d := decide([expiring("g-james", "auditor", "securities", "2026-10-07T12:00:00Z")], "project.read", "fin-reporting")
	d.verdict == "expired"
	d.grant_id == "g-james"
}

test_expiry_equal_to_now_counts_as_expired if {
	decide([expiring("g", "auditor", "securities", now)], "project.read", "securities").verdict == "expired"
}

test_future_expiry_still_allows if {
	decide([expiring("g", "auditor", "securities", "2026-10-08T12:00:01Z")], "project.read", "securities").allowed
}

test_sibling_subtree_only_is_out_of_scope if {
	d := decide([grant("g", "viewer", "germany")], "project.read", "securities")
	d.verdict == "out-of-scope"
	d.grant_id == "g"
}

test_no_role_with_permission_is_no_permission if {
	d := decide([grant("g", "viewer", "root")], "project.write", "tax")
	d.verdict == "no-permission"
	d.grant_id == null
}

test_active_grant_wins_over_expired_one_on_same_path if {
	d := decide(
		[expiring("g-old", "viewer", "securities", "2026-10-08T11:00:00Z"), grant("g-new", "viewer", "japan")],
		"project.read", "fin-reporting",
	)
	d.allowed
	d.grant_id == "g-new"
}

test_unknown_node_is_denied if {
	decide([grant("g", "organisation_manager", "root")], "project.read", "nope").verdict == "no-permission"
}

test_org_level_permission_at_root_denied_for_department_grant if {
	decide([grant("g", "organisation_manager", "tax")], "org.write", "root").verdict == "out-of-scope"
}

test_memory_and_assurance_permissions_are_content if {
	rbac.is_content("memory.read")
	rbac.is_content("memory.approve")
	rbac.is_content("assurance.approve")
	not rbac.is_content("user.role.change")
}

test_another_principal_s_grant_gives_nothing if {
	other := object.union(grant("g", "viewer", "root"), {"principal_id": "user-2"})
	decide([other], "project.read", "tax").verdict == "no-permission"
}
