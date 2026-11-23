# METADATA
# title: Propose, approve and reject decisions
# description: |
#   Who may write into the review queue and who may decide on it.
#   Input: {tenant_id, action: propose | approve | reject | retire,
#   actor: {type: user | consumer, id}, record: {scope, contributors: [id]},
#   now (RFC 3339)}. `contributors` are the people whose corrections or
#   assertions produced the record.
#   - propose needs memory.write at the record's scope;
#   - approve, reject and retire (withdraw an approved record) need
#     memory.approve there, by a person (machines
#     propose, people approve: Rev. B rule 6), and the person may not be a
#     contributor (separation of duties, D17: "the corrector can't approve"),
#     and must be the named owner or backup of the nearest owned node at or
#     above the record's scope (doc 06; data.cortex.tenants[t].owners).
#   - A record about a customer with its own owner (record.entities, e.g.
#     customer:CUST-0101; data.cortex.tenants[t].entity_owners) is decided by
#     that customer's owner or backup instead: its cash specialist (issue 45,
#     slide 18). A record naming several owned customers needs a person who
#     owns or backs up every one of them.
package cortex.policy.approval

import data.cortex.policy.rbac

_tenant := data.cortex.tenants[input.tenant_id]

_permission := {
	"propose": "memory.write",
	"approve": "memory.approve",
	"reject": "memory.approve",
	"retire": "memory.approve",
}[input.action]

default _rbac := null

_rbac := rbac.decide(_tenant, input.actor, _permission, input.record.scope, input.now)

_deciding if input.action in {"approve", "reject", "retire"}

# Owned nodes from the record's scope up to the root, nearest first.
_owned := [n |
	some n in object.get(_tenant.nodes, input.record.scope, {"path_up": []}).path_up
	object.get(_tenant, "owners", {})[n]
]

_named := _tenant.owners[_owned[0]]

# Owners of the customers the record names (issue 45).
_entity_owners := [o |
	some e in object.get(input.record, "entities", [])
	o := object.get(_tenant, "entity_owners", {})[e]
]

_owns(o) if input.actor.id == o.owner

_owns(o) if {
	o.backup != null
	input.actor.id == o.backup
}

_is_named if {
	count(_entity_owners) > 0
	every o in _entity_owners {
		_owns(o)
	}
}

_is_named if {
	count(_entity_owners) == 0
	_owns(_named)
}

_denial := "unknown tenant" if {
	not _tenant
} else := sprintf("unknown action %q", [input.action]) if {
	not _permission
} else := "now is required (RFC 3339)" if {
	not _valid_now
} else := "only a person can approve or reject" if {
	_deciding
	input.actor.type != "user"
} else := "separation of duties: a contributor to this record cannot decide on it" if {
	_deciding
	input.actor.id in object.get(input.record, "contributors", [])
} else := _rbac.reason if {
	not _rbac.allowed
} else := "no named owner for this scope" if {
	_deciding
	count(_entity_owners) == 0
	count(_owned) == 0
} else := "only the named owner or backup of this customer can decide" if {
	_deciding
	count(_entity_owners) > 0
	not _is_named
} else := "only the named owner or backup of this scope can decide" if {
	_deciding
	not _is_named
}

# A time that does not parse would make every rule below undefined; deny instead.
_valid_now if is_number(time.parse_rfc3339_ns(input.now))

default _revision := null

_revision := data.cortex.policy.revision

# METADATA
# description: The decision for one propose / approve / reject action.
# entrypoint: true
decision := {
	"allow": false,
	"reason": _denial,
	"rbac": _rbac,
	"policy_revision": _revision,
} if {
	_denial
} else := {
	"allow": true,
	"reason": _rbac.reason,
	"rbac": _rbac,
	"policy_revision": _revision,
}
