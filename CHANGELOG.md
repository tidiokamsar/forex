# Changelog

## Unreleased · 0.2.0 · 2026-10-25

### cTrader demo connector (offline)

- Added `brokers/websocket.py`, a standard-library RFC 6455 client (masked frames, fragmentation, ping/pong, loss-free timeouts, ws:// only to loopback).
- Added `brokers/ctrader.py`: cTrader Open API JSON session (clientMsgId correlation, heartbeat) and `CTraderAdapter` for the demo host only, with `isLive` refusal, provider-derived specs, quotes, account values, market orders, single-position exits and foreign-activity detection. Payload numbers and fields come from the official docs read 2026-10-25.
- `BrokerAdapter.instrument_specs()` lets provider specs override the illustrative ones in the gateway.
- CLI `ctrader-check --operator <name>`: read-only demo connection check; never sends orders. Connector errors exit with code 4.
- ADR-0004; BROKERS.md section with assumptions to confirm and the check procedure; `.env.example` lists the variable names.
- DEMO order execution stays disabled.

- Added `instruments` (illustrative EURUSD contract spec, size rounding down to step, margin with fail-closed currency conversion).
- Added the broker contract `brokers.base.BrokerAdapter` (interface v1, capabilities, no funds-movement operations) and `SimulatedBroker` with fault injection.
- Added `execution`: persistent order state machine, idempotent `OrderGateway` (permission, schema, idempotency, controls, connection, mode verification, quote, RiskEngine, contract and margin checks), event processing robust to stale and out-of-order reports, reconciliation, kill switch and entry freezes.
- Added `governance`: static role table, audited permission guard, `SecretVault`, two-person risk-limit change within hard caps, strict LLM output validation (`NO_ACTION` by default).
- Added `registry`: experiment log keeping negative results, immutable model versions, hashed pre-registered promotion criteria, once-only holdout evidence, human promotion, rollback with manifest verification.
- Added research modules (built by a delegated agent, reviewed and integrated by the coordinator): `features`, `regimes`, `strategies` (momentum, breakout, mean reversion, SMA baseline), `signal_backtest` with extended metrics, `ml` (purged walk-forward logistic regression vs base rate).
- Added `engine.run_paper_replay` and CLI commands `paper-replay`, `ml-baseline`, `orders`, `models`, `controls show|kill|release`.
- Dashboard: read-only routes `/api/orders`, `/api/controls`, `/api/reconciliations`, `/api/models`, `/api/experiments` and French panels for controls, registry and simulated orders.
- Docs: BROKERS.md evaluation matrix with official documentation links (checked 2026-10-25, untested), ADR-0003, updated policies, mandatory negative-test map in TESTING.md.
- Verification: 192 tests passed; see ACCEPTANCE_REPORT.md.

## 0.1.0 · 2026-10-25

- Audited the empty project directory and documented a dependency-free Python modular-monolith decision.
- Added simulation-only configuration, a fail-closed risk contract, and initial security/data/research policies.
- Completed the first locally verifiable vertical slice: source-aware CSV quality checks, a cost-aware event-time SMA simulation, independent risk checks, SQLite experiment/audit history, and a French read-only dashboard.
- Integrated verification: 74 tests passed; Python `compileall` and dashboard JavaScript syntax checks passed. See `ACCEPTANCE_REPORT.md` for the exact command, review dispositions, and remaining acceptance limits.
- Added protocol v1 walk-forward evaluation with expanding train prefixes, fixed validation windows, disjoint test windows, validation-based candidate selection, flat/reference baselines, and cost stress reporting.
- Added additive SQLite protocol and holdout records. The final block stays reserved by default; an explicit holdout flag burns one attempt before scoring. The key is the canonical identity of normalized market bars, while the source-file SHA-256 remains provenance. The lock is scoped to one editable local SQLite store.
- Required `--exploratory` for raw full-history CLI backtests and labelled the output as full-history, non-promotable.
- Added validation-boundary, canonical-identity, legacy-database row-preservation, lock/idempotency, and CLI regression tests. Integrated test results are recorded in `ACCEPTANCE_REPORT.md`.
- Froze the first walk-forward plan per canonical normalized dataset in each configured local store, pinned `demo` to the SHA-256-verified bundled fixture, and normalized direct API bar timestamps/instruments before evaluation. The final integrated verification passed with 74 tests; read-only delivery and security reviews are recorded with reservations in `ACCEPTANCE_REPORT.md`.
