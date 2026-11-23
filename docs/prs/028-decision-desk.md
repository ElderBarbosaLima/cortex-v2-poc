# PR 028 — The Decision Desk

Branch: `poc/028-decision-desk` → `main` · Issue: [docs/tasks.md §28](../tasks.md) · Depends on: 27, 18, 20 (merged)

## What
- **`app/pages/cortex/decision-desk.vue`** with `CortexDecisionDeskToolbar`, `CortexDecisionDeskCaseList`, `CortexDecisionDeskCaseDetail` and `CortexDecisionDeskDecisionForm`, plus the store `app/stores/decisionDesk.ts`.
  - **Toolbar:** a **dev user selector**, a scope and a status filter (open | decided), and refresh.
  - **Case list:** each case shows its title, read from the sealed body by the API, its proposal count and its scope.
  - **Case detail:**
    - the rule text and condition (CEL), targets, entities, how it was proposed (enforcement · authority);
    - its **source**: who suggested it, which work cases, which correctors (shown by their seed labels);
    - the **affected cases**;
    - each proposal's **`outcome`**, the decisions so far, the named owner and backup.
  - **Decision form:**
    - approve with the **final authority** (0–40) and, for a rule, the **final enforcement**;
    - reject with a **reason**, which the form requires.
  - **OPA's refusals are shown with their reason:** an alert with the code and the detail. A button the policy denies is disabled, linked through `aria-describedby` to "You cannot approve: <OPA's reason>".
- **`can_decide` in `GET /v1/cortex/review-cases/{id}`** (backend, contract regenerated).
  - It carries `{approve, reject}`, each `{allowed, reason, policy_revision}`: the approval policy asked ahead of time for the caller (the end user, or the consumer alone). A decided case answers "this case is already decided".
  - The decision itself is still checked by OPA. `can_decide` only lets the screen say *why* before you click, and it never invents a rule of its own.
  - Tested in `test_the_case_says_ahead_who_may_decide_and_why_not`: the corrector is denied with the separation-of-duties reason, the owner may decide, the agent alone is denied with "only a person…".
- **Dev user selector (documented in the UI and the README).**
  - `GET /api/dev/seed` (Nitro, dev-only) reads the seed's people and scopes from `data/synthetic/tenant.json`, which is mounted into the dev container. People are role labels only, no names. With no `NUXT_DEV_SEED_FILE` it answers 404.
  - The selected person goes out as `X-End-User-Id` through the proxy. In production it comes from the person's session.
- **Test ids:** every control uses `data-test-id="cortex-decision-desk-<element>-<control-type>"`.
  - That is the repo's attribute (PROD has 3,913 uses of `data-test-id` and none of `data-testid`). The issue 27 home page moves to it as well.
  - Playwright uses `testIdAttribute: 'data-test-id'`, as PROD's config does.
- **i18n:** English only (`en/cortex.json`). Japanese data is shown as it came, which leaves the Japanese UI as the recorded gap (P7).
- **Playwright in a container** (`frontend/arbitr/e2e/`, its own package with `@playwright/test` 1.59.1, image `mcr.microsoft.com/playwright:v1.59.1-noble` pinned by digest): `make test-e2e-ui` runs it against the running `make dev` stack on the compose network.
  - **Setup through the real API:** the agent asks for a pack and the reviewer sends a correction, using `CORTEX_E2E_AGENT_KEY` from `.env.example`.
  - **The desk's own calls go through the frontend proxy.**
  - **Cleanup:** each test starts with no open case at the scope, so reruns are clean.

## Acceptance criteria — output
Clean clone at `~/.cache/cortex-poc-checks/028`, using the issue's validation `make dev && make seed && make test-e2e-ui && make lint && make test`:
```
$ make seed        → pack cash_application: 8 rules added · pack custom_invoices: 7 rules added
$ make test-e2e-ui
  ✓ 1 [chromium] › a correction becomes a case, the owner approves it, and the next pack carries the rule (7.8s)
  ✓ 2 [chromium] › the corrector sees approve disabled, with the policy reason (3.5s)
  2 passed
$ make lint        → Ruff/Regal … · eslint (incl. vuejs-accessibility) · check-v2-styles: clean
$ make test        → 280 passed (backend) · Vitest 4 files, 18 tests passed · check:bundle: key in none of 196 files
$ make typecheck · make test-integration (99 passed) · make test-all · make contract-check · make migrate-check → all ok
```
1. **E2E 1.**
   - R-001 corrects INV-0007 through the API, and a case opens.
   - On screen, OWN-001 picks the case, sees `invoice.customer_id == "CUST-0042"` and the outcome *Pending*, sets enforcement *mandatory* and authority *30*, and approves. The notice shows and the outcome becomes *Approved*.
   - The pack for **INV-0021** (the next applicable case) carries the rule as a **constraint**. **INV-0024** (CUST-0042 by email) does not carry it.
2. **E2E 2.** R-002 corrects INV-0012. Acting as R-002, approve is **disabled**, with *"You cannot approve: separation of duties: a contributor to this record cannot decide on it"*. Acting as OWN-001 on the same case, approve is enabled.
3. **Vitest** (`tests/unit/decision-desk.spec.ts`):
   - the evidence is rendered;
   - an owner's approval posts `{authority: 30, enforcement: "mandatory"}` with `x-end-user-id`;
   - a corrector sees a disabled button with the reason;
   - an OPA 403 is shown with its reason;
   - reject needs a reason;
   - **a DOM scan: every `button`, `input`, `select`, `textarea`, `a` and `[role=button]` carries a `cortex-decision-desk-…-<control-type>` test id.**

   ESLint with `vuejs-accessibility` has no violations, and neither does the v2 brand guard.

**Do the tests catch mistakes?** I made the approve button ignore `can_decide`, and e2e 2 failed (`toBeDisabled` failed), both through hot reload and after a frontend restart. I restored the code afterwards, and the suite is green again on a second, consecutive run.

## Found and fixed: hot reload did not work (issue 27)
The first mutation run *passed*: the dev server never saw the change. Host bind mounts under Colima deliver no file events into the container. The dev container now polls (`vite.server.watch.usePolling` when `NUXT_DEV_POLL=true`, set in compose), and the mutation is now caught through hot reload too.

## Notes
- **`[Icon] failed to load icon lucide:loader-circle` in Vitest** is a test-environment message: the Nuxt test environment does not load the icon bundle. In the browser, icons come from the installed `@iconify-json/lucide`.
- **`make init-env` added `CORTEX_E2E_AGENT_KEY` to your `.env`** (it only adds missing keys).
- **Not in this PR:** a Japanese UI (gap P7).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
