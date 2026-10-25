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

## Known gaps

- No user authentication, authorization, TLS, network access controls, secret store, signed authorization, or multi-user isolation. The service must remain on loopback.
- Roles are asserted by the calling code or the local CLI user, not authenticated.
- The importer limits total bytes, rows, columns, field length, and stored issue count; a size-limit error rejects the import before data is parsed.
- Reconciliation is implemented and tested against the simulated broker only. No backups, restore evidence, dependency scanner, SBOM, or service supervisor has been implemented.
- The local SQLite audit trail is not tamper-proof or externally replicated.

## Reporting a security issue

Do not include credentials, account numbers, or production trading data in issue reports or test fixtures. Disable the local process and preserve the relevant log/database copy for review.
