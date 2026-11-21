# METADATA
# title: Access gate read decision
# description: |
#   May this consumer (and its end user, when there is one) read Cortex
#   knowledge at `scope_node`, for this purpose, and what may the query see?
#   Input: {tenant_id, consumer: {id}, end_user: {id} | null, purpose,
#   scope_node, permission (default memory.read), now (RFC 3339)}.
#   Both the consumer and the end user must be allowed (intersection). A read
#   at a scope resolves upward, never sideways or down, so the readable scopes
#   are the requested node and its ancestors inside the tenant. The decision
#   becomes query filters; Postgres RLS still enforces the tenant.
package cortex.policy.authz

import data.cortex.policy.rbac

default _permission := "memory.read"

_permission := input.permission

_tenant := data.cortex.tenants[input.tenant_id]

_purpose := _tenant.consumers[input.consumer.id].purposes[input.purpose]

default _consumer := null

_consumer := rbac.decide(_tenant, _consumer_principal, _permission, input.scope_node, input.now)

_consumer_principal := {"type": "consumer", "id": input.consumer.id}

default _end_user := null

_end_user := rbac.decide(_tenant, _user_principal, _permission, input.scope_node, input.now)

_user_principal := {"type": "user", "id": input.end_user.id}

_end_user_ok if not input.end_user.id

_end_user_ok if _end_user.allowed

_scope_in_tenant if _tenant.nodes[input.scope_node].type != "organization"

# Ancestors-or-self of the requested scope, excluding the organisation root
# (knowledge is tenant-owned; nothing is scoped above a tenant).
_readable_scopes := [n |
	some n in _tenant.nodes[input.scope_node].path_up
	_tenant.nodes[n].type != "organization"
]

_denial := "unknown tenant" if {
	not _tenant
} else := "now is required (RFC 3339)" if {
	not _valid_now
} else := "scope is not a node of this tenant" if {
	not _scope_in_tenant
} else := sprintf("purpose %q is not registered for consumer %q", [input.purpose, input.consumer.id]) if {
	not _purpose
} else := sprintf("consumer: %s", [_consumer.reason]) if {
	not _consumer.allowed
} else := sprintf("end user: %s", [_end_user.reason]) if {
	not _end_user_ok
}

# A time that does not parse would make every rule below undefined; deny instead.
_valid_now if is_number(time.parse_rfc3339_ns(input.now))

default _revision := null

_revision := data.cortex.policy.revision

# METADATA
# description: The read decision the access gate turns into query filters.
# entrypoint: true
decision := {
	"allow": false,
	"reason": _denial,
	"consumer": _consumer,
	"end_user": _end_user,
	"readable_scopes": [],
	"sensitivity_ceiling": null,
	"policy_revision": _revision,
} if {
	_denial
} else := {
	"allow": true,
	"reason": _consumer.reason,
	"consumer": _consumer,
	"end_user": _end_user,
	"readable_scopes": _readable_scopes,
	"sensitivity_ceiling": _purpose.sensitivity_ceiling,
	"policy_revision": _revision,
}
