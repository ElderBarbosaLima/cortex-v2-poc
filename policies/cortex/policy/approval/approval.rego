# METADATA
# title: Propose, approve and reject decisions
# description: |
#   Who may write into the review queue and who may decide on it.
#   Input: {tenant_id, action: propose | approve | reject,
#   actor: {type: user | consumer, id}, record: {scope, contributors: [id]},
#   now (RFC 3339)}. `contributors` are the people whose corrections or
#   assertions produced the record.
#   - propose needs memory.write at the record's scope;
#   - approve and reject need memory.approve there, by a person (machines
#     propose, people approve: Rev. B rule 6), and the person may not be a
#     contributor (separation of duties, D17: "the corrector can't approve").
package cortex.policy.approval

import data.cortex.policy.rbac

_tenant := data.cortex.tenants[input.tenant_id]

_permission := {"propose": "memory.write", "approve": "memory.approve", "reject": "memory.approve"}[input.action]

default _rbac := null

_rbac := rbac.decide(_tenant, input.actor, _permission, input.record.scope, input.now)

_deciding if input.action in {"approve", "reject"}

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
