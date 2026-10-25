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
- Order submission reaches a broker adapter only through `OrderGateway`; the read-only `ctrader-check` command calls the adapter's read methods (connect, account, quote, specs, positions) directly and never submits. The adapter contract has no funds-movement operations.
- Execution boundary: every adapter's `submit_order` requires an HMAC execution permit that only the gateway issues after its checks (process-local random key, 30-second validity, single-use, bound to client id, instrument, side, size, order type, stop-loss, purpose and mode; a reused permit is refused with `permit_already_used`). Subclasses cannot override `submit_order`; the check resolves through the MRO, so a mixin cannot replace it either. A refusal ends the order `BROKER_REJECTED` with `execution_refused:<reason>`. Without a valid permit, or when the adapter's own environment guard refuses, `ExecutionRefused` is raised and nothing is sent (C-01, tests in `test_ctrader.ExecutionBoundaryTests`).
- cTrader adapter guard: refuses unless the provider reported `isLive: false`, DEMO is in `EXECUTABLE_MODES`, the adapter was explicitly built with `allow_demo_orders=True` and it is `CONNECTED`. None of these is enabled for orders in this build.
- Read-only check review (2026-10-25): `ctrader-check` builds the adapter with `allow_demo_orders=False`, never calls `submit_order` or `_send_order` (a test fails if either is reached), and counts any `ProtoOANewOrderReq`/`ProtoOAClosePositionReq` sent (must be 0). It is safe to run against a demo account.
- Structured logs (`logs.py`) replace the values of fields whose names look like secrets (`secret`, `token`, `password`, `passwd`, `api_key`, `authorization`, `credential`) with `****`, recursively. Every value of at least 8 characters revealed through `SecretValue.reveal()` is also registered and masked inside the text values of a log line (message, fields, exception text) before JSON encoding. Values never revealed through `SecretValue` are protected only by field names, so code must still not log secrets.
- `backup restore` requires `HUMAN_ADMIN` or `HUMAN_OPERATOR` (`Action.RESTORE_BACKUP`) and a reason, verifies the backup's SHA-256 and integrity before replacing anything, keeps the previous database, and freezes every account with orders until a consistent reconciliation.
- cTrader token refresh is refused unless the caller passes a `TokenStore`; none is bundled, so rotated tokens are never written to an unchosen location.
- Limitation of the permit: it stops accidental, strategy and LLM bypasses inside ASTRA. Code that deliberately imports the private permit authority could forge one; that requires a reviewed code change.

- Research attempt registry (`attempts`): every scored variant (selected, rejected, failed, interrupted) is appended to `research_attempt_events` with a hash chain (`prev_hash`, `row_hash`). Triggers refuse UPDATE, DELETE and unlinked INSERT; `astra attempts verify` detects edited, removed and reordered rows, missing triggers and a truncated tail. Promotion is refused with `attempt_log_tampered` if the chain does not verify and with `trial_count_understated` if the evidence's `variants_tested` is below the store-wide trial count.
- Research integrity (lot 4): sealed plans, holdout signatures, finished holdout evaluations, model evaluations and experiments are append-only through SQLite triggers. Promotion re-derives holdout evidence from the market bars (`holdout_bars`), binds the report to its plan, the running code and the attempt log, and allows one version per holdout. A new holdout that reuses a sealed price path is refused (heuristic detector, see RESEARCH_PROTOCOL.md).

## Known gaps

- No user authentication, authorization, dashboard TLS, network access controls, secret store, external or human-signed order authorization, or multi-user isolation. (The internal HMAC execution permit proves only that the gateway's checks ran in this process; it is not a human signature.) The service must remain on loopback.
- Roles are asserted by the calling code or the local CLI user, not authenticated.
- The importer limits total bytes, rows, columns, field length, and stored issue count; a size-limit error rejects the import before data is parsed.
- Reconciliation is implemented and tested against the simulated broker only. Backup and restore are tested locally with the simulator only; backups are unencrypted copies of the database and must be protected like it. No dependency scanner, SBOM, secure token store, or external service supervisor has been implemented.
- The local SQLite audit trail is not tamper-proof or externally replicated.
- The attempt hash chain is tamper-evident, not tamper-proof: whoever can write the database file can drop the triggers and rewrite the whole chain consistently (hashes included), or replace the file. Only a chain head kept outside the database (from `astra attempts list|count|verify`, stored elsewhere) and passed as `--expected-head` detects that, and only for rows up to the anchor. Attempts run without a store or against another database file are not recorded.
- Lot 4 residuals: the triggers live in the same file and can be dropped by its owner; restoring an older backup brings a spent holdout back; the reuse detector is heuristic (noise, resampling factors other than two, short runs); `ModelRegistry(evidence_verifier=...)` lets in-process code replace the default verifier (and with it the re-derivation); re-derivation proves numbers follow from the bars, not that the bars are genuine.

## Reporting a security issue

Do not include credentials, account numbers, or production trading data in issue reports or test fixtures. Disable the local process and preserve the relevant log/database copy for review.
