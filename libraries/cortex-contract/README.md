# cortex-contract

The single specification of the Cortex consumer API (D15): `openapi.json`, generated from the
org-brain-service code (`make contract`), and the TypeScript types generated from it
(`ts/cortex.d.ts`, `openapi-typescript`), which the Decision Desk uses.

Do not edit either file by hand. `make contract-check` (and `make test`) fail when they drift from
the code. Operations an agent should get as MCP tools carry an `x-mcp-tool` extension
(`request_context_pack`, `verify_output`, `submit_correction`); the MCP server itself is stage 3.
