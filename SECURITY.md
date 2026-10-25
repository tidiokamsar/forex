# Security boundary

## Implemented controls

- Startup defaults to `SIMULATION`. `DEMO`, `LIVE`, invalid modes, and `ASTRA_LIVE_ENABLED=true` are rejected.
- The only outbound network code is the cTrader adapter's WebSocket to the hard-coded demo host `demo.ctraderapi.com:5036` over TLS (certificate verification on, default context). It is used only by the read-only `ctrader-check` command; no order path uses it while DEMO is non-executable. Plain `ws://` is refused except to loopback. Credentials come from `ASTRA_SECRET_*` environment variables through `SecretVault` and are never printed or logged.
- The dashboard binds only to loopback; browser mutations receive HTTP 405. Responses use a restrictive Content Security Policy, `nosniff`, `no-store`, and frame/referrer protections.
- The dashboard rejects requests whose `Host` does not match the configured loopback host and bound port.
- CSV imports are capped by byte, row, column, field, and issue-count limits.
- Database writes use parameterized SQL. Rendered run data is inserted with DOM `textContent`, not HTML interpolation.
- Risk checks are independent from signal generation and fail closed on uncertain account state, missing/stale data, excessive/unknown spread, missing specifications, margin, duplicate intent, size, kill switch, position cap, and daily loss.
- No secret values are included in the repository or synthetic fixture.
- Role-based permission guard (`governance`): research agents, strategies, models and LLMs cannot read secrets, change risk limits or mode, release the kill switch, clear freezes, promote models or submit order commands. Every grant and denial is an audit event. No role can grant permissions.
- `SecretVault` is the single audited secret access path (environment variables prefixed `ASTRA_SECRET_`, `HUMAN_ADMIN` only); `SecretValue` never prints its value.
- LLM output is parsed by `handle_llm_output` against a strict schema. Any deviation, or any execution field (units, size, order, stop, mode and similar), yields `NO_ACTION`. A valid response is advisory only and has no path to the gateway.
- `OrderGateway` is the only caller of a broker adapter, and the adapter contract has no funds-movement operations.
- Execution boundary: every adapter's `submit_order` requires an HMAC execution permit that only the gateway issues after its checks (process-local random key, 30-second validity, bound to client id, instrument, side, size and purpose). Subclasses cannot override `submit_order`. Without a valid permit, or when the adapter's own environment guard refuses, `ExecutionRefused` is raised and nothing is sent (C-01, tests in `test_ctrader.ExecutionBoundaryTests`).
- cTrader adapter guard: refuses unless the provider reported `isLive: false`, DEMO is in `EXECUTABLE_MODES` and the adapter was explicitly built with `allow_demo_orders=True`. None of these is enabled for orders in this build.
- Read-only check review (2026-10-25): `ctrader-check` builds the adapter with `allow_demo_orders=False`, never calls `submit_order` or `_send_order` (a test fails if either is reached), and counts any `ProtoOANewOrderReq`/`ProtoOAClosePositionReq` sent (must be 0). It is safe to run against a demo account.
- Limitation of the permit: it stops accidental, strategy and LLM bypasses inside ASTRA. Code that deliberately imports the private permit authority could forge one; that requires a reviewed code change.

## Known gaps

- No user authentication, authorization, TLS, network access controls, secret store, signed authorization, or multi-user isolation. The service must remain on loopback.
- Roles are asserted by the calling code or the local CLI user, not authenticated.
- The importer limits total bytes, rows, columns, field length, and stored issue count; a size-limit error rejects the import before data is parsed.
- Reconciliation is implemented and tested against the simulated broker only. No backups, restore evidence, dependency scanner, SBOM, or service supervisor has been implemented.
- The local SQLite audit trail is not tamper-proof or externally replicated.

## Reporting a security issue

Do not include credentials, account numbers, or production trading data in issue reports or test fixtures. Disable the local process and preserve the relevant log/database copy for review.
