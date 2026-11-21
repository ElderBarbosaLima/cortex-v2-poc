# PR 012 — Rego policies: read and approval, translated from arbitr-rbac

Branch: `poc/012-rego-policies` → `main` · Issue: [docs/tasks.md §12](../tasks.md#12-políticas-rego-leitura-e-aprovação--testes) · Depends on: 004 (merged)

## What
- `policies/cortex/policy/rbac/rbac.rego` — `rbac.decide(tenant, principal, perm, node, now)`, a translation of arbitr-rbac's `authorize` (UAT `libraries/arbitr-rbac/src/arbitr_rbac/resolver.py`):
  - walk `path_up`; the nearest active grant decides (`nearest-allow` / `inherited-allow`), ties broken by grant id;
  - an information barrier stops grants **strictly above** it for content permissions (`memory.*`, `project.*`, `assurance.*` …); a grant **on** the barrier still applies; admin permissions pass through and report `barrier_crossing`;
  - expiry at `now` (`expires_at == now` is expired);
  - otherwise `out-of-scope` (the permission exists elsewhere), then `expired`, then `no-permission`; an unknown node is `no-permission`.
- `policies/cortex/policy/authz/authz.rego` — **`data.cortex.policy.authz.decision`** (read). Input: `{tenant_id, consumer, end_user|null, purpose, scope_node, permission?, now}`.
  - The consumer **and** the end user must be allowed (intersection).
  - The purpose must be registered for the consumer, and gives the `sensitivity_ceiling`.
  - `readable_scopes` = the requested node and its ancestors inside the tenant: never the org root, never siblings or children.
  - Output: `{allow, reason, consumer, end_user, readable_scopes, sensitivity_ceiling, policy_revision}`.
- `policies/cortex/policy/approval/approval.rego` — **`data.cortex.policy.approval.decision`**.
  - `propose` needs `memory.write`; `approve`/`reject` need `memory.approve` at the record's scope.
  - **Only a person** decides (machines propose, people approve).
  - **Separation of duties:** someone in `record.contributors` cannot decide, even if they hold `memory.approve`.
- The placeholder `allow` from issue 4 is removed; revision tests kept.
- `policies/.regal/config.yaml`: relaxations each with a reason.
  - `unresolved-reference` for `data.system.bundles` and `data.cortex.tenants` (both filled at runtime).
  - `with-outside-test-context` and `external-reference` only in `*_test.rego` (test helpers).
- `docs/discovery.md` §7.3 records what was implemented and where it differs from the design note. README has a policy table.

## Acceptance criteria — output

**1. `opa test` covers every resolver verdict** (`rbac/rbac_test.rego`). It uses the same fictitious "Meridian" tree and catalog shape as `arbitr_rbac.testing`, with the same test names as `tests/test_authorize.py`:

| Verdict | Tests |
|---|---|
| nearest-allow | `test_ae2_grant_on_barrier_node_is_nearest_allow` |
| inherited-allow | `test_inherited_allow_names_ancestor_grant`, `test_active_grant_wins_over_expired_one_on_same_path` |
| out-of-scope | `test_sibling_subtree_only_is_out_of_scope`, `test_org_level_permission_at_root_denied_for_department_grant` |
| no-permission | `test_no_role_with_permission_is_no_permission`, `test_unknown_node_is_denied`, `test_another_principal_s_grant_gives_nothing` |
| barrier | `test_ae1_root_organisation_manager_is_blocked_by_barrier_and_named` (grant above blocked, named), `test_barrier_blocks_targets_below_it_too`, `test_grant_on_barrier_parent_does_not_reach_barrier_itself` |
| barrier crossing | `test_administrative_permission_crosses_barrier_and_flags_it`, `…_outside_barrier_is_not_a_crossing` |
| expired | `test_ae3_only_expired_grant_is_expired`, **`test_expiry_equal_to_now_counts_as_expired`**, `test_future_expiry_still_allows` |

Not ported: `internal_admin` (no Straker admin in the PoC) and `revoked` (revoked grants are never sent, per the payload format).

**2. The corrector cannot approve the rule from their own correction** — `test_the_corrector_cannot_approve_the_rule_from_their_own_correction` (the corrector is given `final_validator` too, and separation of duties still wins). Also: a reviewer without `memory.approve` → `no-permission`; the agent (a consumer) → "only a person can approve or reject".

**3. A purpose not registered for the consumer → deny** — `test_unregistered_purpose_is_denied`. Also an unknown consumer, an unknown tenant, a scope outside the tenant (including the org root), an end user out of scope (`startswith(reason, "end user:")`), and an expired consumer grant.

**4. `regal lint` with no violations**
```
$ make lint
All checks passed!                       (ruff)
69 files already formatted
3 files linted…                          (opa check --strict: exit 0)
8 files linted. No violations found.     (regal)
$ make test-policies
PASS: 33/33
```

**Live check** (stack up, tenant pushed through the Data API, decisions over REST as org-brain will call them in issue 13):
```
PUT /v1/data/cortex/tenants/t-live -> 204
POST /v1/data/cortex/policy/authz/decision
  {'allow': True, 'reason': 'Allowed by cortex_consumer at jp/finance',
   'readable_scopes': ['jp/finance/q2c-invoicing', 'jp/finance', 'jp'],
   'sensitivity_ceiling': 'confidential', 'policy_revision': 'sha256:26cd5b8aedad9d2b'}
POST /v1/data/cortex/policy/approval/decision   (OWN-001 approving a record they contributed to)
  {'allow': False, 'reason': 'separation of duties: a contributor to this record cannot decide on it', …}
```

**Definition of done** (clean clone at `~/.cache/cortex-poc-checks/012`): `make test-all` → All test tiers passed (33/33 policies); `make lint` exit 0; `make typecheck` 0 errors; `make dev` → Stack up; `git status` empty.

## Found while testing
- **`"now": null` made the read decision undefined, not denied.** `not input.now` is false for `null`, and `time.parse_rfc3339_ns(null)` then left everything undefined. A client would have failed closed anyway (issue 13 treats a missing `result` as deny), but a policy must say why. Fixed with an explicit `_valid_now` check in both policies, and tested with `null`, `"yesterday"` and `42`.
- `with` cannot be used in a rule head. Test helpers use it in their body.
- Regal also caught real issues: an unassigned built-in return value, redundant existence checks, pointless imports and re-assignments. All fixed in the policy files, not relaxed.

## Size
343 lines of Rego, 339 of Rego tests.
