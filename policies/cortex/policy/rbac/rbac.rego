# METADATA
# title: Enterprise RBAC resolver
# description: |
#   A translation of arbitr-rbac's `authorize` (UAT,
#   libraries/arbitr-rbac/src/arbitr_rbac/resolver.py) into Rego, so the access
#   gate and approvals decide here and no authorisation rule is duplicated in
#   Python (D22). Same procedure:
#   - walk `path_up` from the node to the root; the nearest active grant of a
#     role holding the permission decides (nearest-allow / inherited-allow);
#   - an information barrier stops grants found strictly above it for content
#     permissions; a grant placed on the barrier itself still applies; admin
#     permissions pass through and report barrier_crossing;
#   - expiry is evaluated at `now` (expiry equal to now counts as expired);
#   - otherwise out-of-scope (the permission exists elsewhere), expired, or
#     no-permission.
#   Data per tenant (data.cortex.tenants[<tenant>], pushed by org-brain):
#   nodes{id: {parent, type, is_barrier, path_up}}, roles{code: [perm]},
#   grants[{id, principal_type, principal_id, role, node, expires_at?}].
#   Revoked grants are never sent.
package cortex.policy.rbac

content_families := {"project", "batch", "ft", "segment", "bulk_op", "audit", "memory", "review", "assurance"}

is_content(perm) if split(perm, ".")[0] in content_families

_active(grant, _) if not grant.expires_at

_active(grant, now_ns) if time.parse_rfc3339_ns(grant.expires_at) > now_ns

_expired(grant, now_ns) if time.parse_rfc3339_ns(grant.expires_at) <= now_ns

# Grants of `principal` on `node` whose role holds `perm`, by grant id.
_matching(tenant, principal, perm, node) := [g |
	some id in sort([g.id |
		some g in tenant.grants
		g.node == node
		g.principal_type == principal.type
		g.principal_id == principal.id
		perm in tenant.roles[g.role]
	])
	some g in tenant.grants
	g.id == id
]

_active_at(tenant, principal, perm, node, now_ns) := [g |
	some g in _matching(tenant, principal, perm, node)
	_active(g, now_ns)
]

# Index on the path where a content permission stops: the first barrier.
_stop(tenant, path, perm) := min([i |
	some i, n in path
	tenant.nodes[n].is_barrier
]) if is_content(perm)

_stop(tenant, path, perm) := count(path) - 1 if {
	is_content(perm)
	not _has_barrier(tenant, path)
}

_stop(_, path, perm) := count(path) - 1 if not is_content(perm)

_has_barrier(tenant, path) if {
	some n in path
	tenant.nodes[n].is_barrier
}

_label(tenant, grant) := sprintf("%s at %s", [grant.role, _name(tenant, grant.node)])

_name(tenant, node) := object.get(tenant.nodes[node], "name", node)

# METADATA
# description: |
#   The decision for `principal` holding `perm` at `node` at time `now`
#   (RFC 3339): {allowed, verdict, reason, grant_id, node_id, barrier_crossing}.
decide(tenant, principal, perm, node, now) := _unknown_node if {
	not tenant.nodes[node]
} else := d if {
	now_ns := time.parse_rfc3339_ns(now)
	path := tenant.nodes[node].path_up
	stop := _stop(tenant, path, perm)
	hits := [i | some i, n in path; i <= stop; count(_active_at(tenant, principal, perm, n, now_ns)) > 0]
	count(hits) > 0
	i := min(hits)
	grant := _active_at(tenant, principal, perm, path[i], now_ns)[0]
	crossed := count([j | some j, n in path; j < i; tenant.nodes[n].is_barrier]) > 0
	d := {
		"allowed": true,
		"verdict": _allow_verdict(i),
		"reason": sprintf("Allowed by %s", [_label(tenant, grant)]),
		"grant_id": grant.id,
		"node_id": path[i],
		"barrier_crossing": crossed,
	}
} else := d if {
	now_ns := time.parse_rfc3339_ns(now)
	path := tenant.nodes[node].path_up
	is_content(perm)
	_has_barrier(tenant, path)
	d := _barrier_decision(tenant, principal, perm, path, _stop(tenant, path, perm), now_ns)
} else := _deny_without_path_grant(tenant, principal, perm, node, time.parse_rfc3339_ns(now))

_unknown_node := {
	"allowed": false, "verdict": "no-permission", "reason": "Unknown node",
	"grant_id": null, "node_id": null, "barrier_crossing": false,
}

_allow_verdict(0) := "nearest-allow"

_allow_verdict(i) := "inherited-allow" if i > 0

_barrier_decision(tenant, principal, perm, path, stop, now_ns) := d if {
	above := [g |
		some i, n in path
		i > stop
		some g in _active_at(tenant, principal, perm, n, now_ns)
	]
	count(above) > 0
	d := {
		"allowed": false, "verdict": "barrier",
		"reason": sprintf("Information barrier at %s blocks %s", [_name(tenant, path[stop]), _label(tenant, above[0])]),
		"grant_id": above[0].id, "node_id": path[stop], "barrier_crossing": false,
	}
} else := {
	"allowed": false, "verdict": "barrier",
	"reason": sprintf("Information barrier at %s; no grant inside it allows %s", [_name(tenant, path[stop]), perm]),
	"grant_id": null, "node_id": path[stop], "barrier_crossing": false,
}

# Every active grant of `principal` holding `perm`, anywhere in the tree, by id.
_active_anywhere(tenant, principal, perm, now_ns) := [g |
	some id in sort([h.id |
		some h in tenant.grants
		h.principal_type == principal.type
		h.principal_id == principal.id
		perm in tenant.roles[h.role]
		_active(h, now_ns)
	])
	some g in tenant.grants
	g.id == id
]

_expired_on_path(tenant, principal, perm, node, now_ns) := [g |
	path := tenant.nodes[node].path_up
	stop := _stop(tenant, path, perm)
	some i, n in path
	i <= stop
	some g in _matching(tenant, principal, perm, n)
	_expired(g, now_ns)
]

_denied(verdict, reason, grant) := {
	"allowed": false, "verdict": verdict, "reason": reason,
	"grant_id": grant.id, "node_id": grant.node, "barrier_crossing": false,
}

_deny_without_path_grant(tenant, principal, perm, node, now_ns) := d if {
	elsewhere := _active_anywhere(tenant, principal, perm, now_ns)
	count(elsewhere) > 0
	label := _label(tenant, elsewhere[0])
	d := _denied("out-of-scope", $"{label} does not cover this part of the organisation", elsewhere[0])
} else := d if {
	expired := _expired_on_path(tenant, principal, perm, node, now_ns)
	count(expired) > 0
	label := _label(tenant, expired[0])
	d := _denied("expired", $"{label} has expired", expired[0])
} else := {
	"allowed": false, "verdict": "no-permission", "reason": $"No role grants {perm}",
	"grant_id": null, "node_id": null, "barrier_crossing": false,
}
