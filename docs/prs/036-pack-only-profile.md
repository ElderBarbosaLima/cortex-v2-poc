# PR 036 — Phase 1 integration: one endpoint that returns a context pack

Branch: `poc/036-pack-only-profile` → `main` · Issue: [docs/tasks.md §36](../tasks.md) · From Nake's review

## What
- **The scope `cortex:pack`** (`identity.PACK_SCOPE`) is the Phase 1 integration profile. `require_scope` now accepts any of several scopes. `request_context_pack`, `get_context_pack` (replaying the served pack) and `whoami` accept `cortex:read` **or** `cortex:pack`. Every other operation keeps requiring `cortex:read` or `cortex:write`, so a pack-only consumer gets `403 insufficient_scope` there.
- **The consumer `ibm-pr-agent`** (seed): tenant `jp`, scopes `["cortex:pack"]`, purpose `payment_reconciliation` (issue 35). Its dev key `local-dev-ibm-pr-key` is in `.env.example`, inside `CORTEX_DEV_API_KEYS`.
- **The contract documents the profile** on both pack operations, and a unit test fixes that.
- **No MCP server in Phase 1.** The three agent operations keep their `x-mcp-tool` markers for later.
- **Verify (D3) stays available,** but only to consumers that hold `cortex:read`, so it is not part of the IBM profile. The README records that, without it, mandatory rules are advisory for that consumer. Whether IBM must call verify before posting is still an open decision.

## Acceptance criteria
```
$ make test-integration ARGS="-rA tests/integration/test_pack_only_profile.py"
PASSED test_the_ibm_consumer_gets_a_context_pack_and_reads_it_back        # 201, then GET = POST; whoami scopes ["cortex:pack"]
PASSED test_everything_else_is_out_of_the_profile[verify | corrections | list-records | propose | review-queue | export | retire]   # 403 insufficient_scope
8 passed
```
`make lint`, `make typecheck` and `make test` pass (including `test_the_pack_only_profile_is_documented_on_the_pack_operations`), and `make test-integration` gives 125 passed.

**For your `.env`:** `make init-env` does not rewrite a key that already exists. To call as `ibm-pr-agent` locally, add `"ibm-pr-agent":"local-dev-ibm-pr-key"` to `CORTEX_DEV_API_KEYS`; a clean clone has it already.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
