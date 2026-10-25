# ADR-0003: Durable order gateway with an in-process simulated broker

- Status: accepted
- Date: 2026-10-25

## Context

The specification requires an idempotent, persistent order state machine, mode verification that does not trust a configuration flag, reconciliation after reconnect, an emergency stop, and that no strategy or LLM can call a broker. No verified demo broker exists yet, and no credentials may be used.

## Decision

- Keep the dependency-free modular monolith (ADR-0001). Add `brokers` (contract plus `SimulatedBroker`) and `execution` (store, controls, mode verification, gateway) packages, sharing the configured SQLite file.
- `OrderGateway.submit` is the single path to an adapter. It persists every state transition, its transition row and an audit event in one transaction **before** each external call.
- The provider-facing client order id is derived from the intent id (`client_order_id_for`), so a retry can only look the order up, never create a second one.
- A timeout or adapter failure moves the order to `UNKNOWN` and freezes entries; only a consistent reconciliation clears connection/unknown-state/divergence freezes.
- Mode is accepted only when configuration, command, adapter declaration, account report and the source of that report agree. `LIVE` is blocked in all cases; `DEMO` is defined but not executable in this build.
- Permissions are a static role table in code (`governance._POLICY`). No role may grant permissions.
- The paper replay (`engine.run_paper_replay`) drives the real gateway with the simulated broker to give an end-to-end path without any external system.

## Alternatives considered

- Message queue plus separate execution service: rejected for now; one process and SQLite meet current load, and a queue adds failure modes without a demonstrated need.
- Mocking at the RiskEngine level only: rejected; it would not exercise idempotency, persistence, reconciliation or mode checks.

## Consequences and revisit conditions

- Controls (kill switch, freezes) are global to one store, not per account. Revisit when more than one account runs concurrently.
  - Amendment (lot 1 corrections, C-05): freezes and the daily-loss day marker are now per account (`TradingControls(scope=account_ref)`, `OrderGateway(account_ref=...)`); the kill switch remains global to the store.
- SQLite with per-call connections is adequate for one local operator; revisit for multi-process or remote deployment (PostgreSQL candidate).
- The simulated broker is a test double; its fills say nothing about any real provider.
