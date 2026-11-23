# PR 027 — Frontend: `frontend/arbitr` skeleton in a container

Branch: `poc/027-frontend-skeleton` → `main` · Issue: [docs/tasks.md §27](../tasks.md) · Depends on: 1, 16 (merged)

## What
- **A minimal Nuxt 4 app shaped like `PROD:frontend/arbitr`** (discovery 2.3).
  - The stack, with exact pins:
    - `ssr: false`, Nuxt 4.4.2, Nuxt UI 4.6.1, Tailwind 4.2.2;
    - Pinia 3.0.4 (`@pinia/nuxt` 0.11.3), `@nuxtjs/i18n` 10.2.4 (locale `en`), Zod 4.3.6;
    - TypeScript 6.0.2, vue-tsc 3.2.6, Vitest 4.1.4 with `@nuxt/test-utils`.
  - The lockfile is committed.
  - **Copied from the monorepo:**
    - the brand v2 tokens: `app.config.ts` and the four CSS files;
    - the `check-v2-styles.mjs` guard, verbatim;
    - the base of `eslint.config.mjs`: `@nuxt/eslint` plus the `vuejs-accessibility` recommended rules, without the monorepo's file-scoped deferrals, because nothing here is deferred.
- **`app/utils/api/client.ts`** talks to the same-origin `/api/cortex` and never sets `Authorization`.
  - It turns the API's `{error, detail}` shape, validated with Zod, into a `CortexApiError` with a readable message, which also covers a list of problems.
  - It sends `X-End-User-Id` when a person acts.
- **`app/utils/api/cortex.ts`:** `getWhoAmI`, `listReviewCases`, `getReviewCase`, `approveReviewCase`, `rejectReviewCase`. Their types come only from `libraries/cortex-contract/ts/cortex.d.ts` (alias `#cortex-contract`), so a contract change that is not regenerated fails `make typecheck`.
- **Dev proxy** (`server/api/cortex/[...path].ts` and `server/utils/cortexProxy.ts`):
  - it forwards `/api/cortex/*` to org-brain-service `/v1/cortex/*` only;
  - it adds `Authorization: Bearer <key>` **on the server**. The key is `runtimeConfig.cortexApiKey`, which stays empty in `nuxt.config.ts` and is set at runtime from `NUXT_CORTEX_API_KEY`. Compose takes it from `CORTEX_DESK_API_KEY` in `.env`, added to `.env.example`;
  - it forwards only `accept`, `content-type`, `x-end-user-id` and `x-request-id`. A browser-sent `Authorization` or `Cookie` never reaches the API;
  - it refuses a path that leaves `/v1/cortex`, encoded or not (`..%2F`, `%5C`, malformed escapes);
  - it fails closed: 503 without a key or URL, and `502 cortex_unreachable` without a stack trace.
- **`/cortex`:** a home page showing which consumer Cortex sees, with a retry. Test ids follow `cortex-home-<element>-<control-type>`, and the text comes from `en/cortex.json`. `/` redirects to `/cortex`.
- **No calls to third parties:** telemetry and devtools are off, Nuxt UI's font download is off (the CSS falls back to system fonts), and icons come from the installed `@iconify-json/lucide`. The tool container runs with `network_mode: none`, and lint, typecheck, tests and the build all pass there.
- **Compose:**
  - `frontend` (port 5001, hot reload of `app/`, `server/` and `i18n/`, healthcheck) on the `cortex` network;
  - `frontend-tools` (profile tools, same image).
- **Make:**
  - `make lint` adds `npm run lint`;
  - `make typecheck` adds `nuxt typecheck` (vue-tsc);
  - `make test` adds Vitest and `npm run check:bundle`. That check builds with a canary key in the environment and fails if the key is in any built file, browser or server.

## Acceptance criteria — output
Clean clone at `~/.cache/cortex-poc-checks/027`, using the issue's validation `make dev && curl -sI localhost:5001 && make lint && make typecheck && make test`:
```
$ make dev               → … Cortex: http://localhost:5001/cortex
$ curl -sI localhost:5001 → HTTP/1.1 200 OK
$ curl localhost:5001/api/cortex/whoami → {"consumer_id":"decision-desk",…} [200]
$ make lint      → Ruff/Regal … · eslint . && check-v2-styles: clean
$ make typecheck → pyright 0 errors · nuxt typecheck (vue-tsc) exit 0
$ make test      → 280 passed (backend) · Vitest: 3 files, 12 tests passed
                   check:bundle: the key is in none of 192 built files (16 browser files)
$ make test-all  → All test tiers passed · make contract-check → matches · make migrate-check → ok
```
Live probes on the dev stack:
```
GET /api/cortex/whoami                                      → 200, as decision-desk (key added server-side)
GET /api/cortex/whoami  with "Authorization: Bearer forged" → 200, as decision-desk (the browser's header is dropped)
GET /api/cortex/..%2F..%2Fhealth                            → 400 {"error":"invalid_request","detail":"invalid path"}
GET :5008/v1/cortex/whoami with the forged key              → 401 unknown API key
```
**Do the checks catch mistakes?** I broke the code twice on purpose, and both checks failed:
```
-- a field the contract does not have (consumer_name) in the page
error TS2339: Property 'consumer_name' does not exist on type '{ consumer_id: string; … }'   → exit 1
-- the key added to runtimeConfig.public
the API key leaked into the build output:                                                    → exit 1
```
I restored the code afterwards.

## What I found and fixed while testing
- **npm 10.9.0 crashes on this dependency tree.** It is the npm bundled with `node:22.12.0`, and it fails with `Cannot read properties of null (reading 'edgesOut')` while resolving vitest's peer set. The image pins npm 11.6.2 (`npm install -g npm@11.6.2`).
- **Nuxt UI fetched fonts from `api.fontsource.org`** the first time the tests ran. That is disabled with `ui.fonts: false`.
- **The frontend could not reach the API** (`ENOTFOUND`) because it was not on the `cortex` network; the first probe returned 502. Fixed.
- **An encoded `..%2F` slipped past the path check,** and the 502 page carried an h3 stack trace. Both are fixed and the traversal cases are tested.

## Notes
- **`X-End-User-Id` comes from the browser in the dev proxy.** That is how the Decision Desk (issue 28) says who is acting. With the PoC's static identities this is a dev convenience; in production the gateway takes the end user from the person's session, never from a header the browser chooses.
- **Your `.env` changed.** `make init-env` added `CORTEX_DESK_API_KEY` to it. It only adds missing keys and never overwrites one.
- **`@nuxtjs/i18n` 10.2.4 still depends on `vue-i18n` 10,** which npm reports as deprecated. That matches the monorepo; moving to i18n 11 belongs to the monorepo, not this PoC.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
