# Architecture

## Current shape

ASTRA is a dependency-free Python modular monolith. `data` validates provenance-bearing CSV datasets; `research` produces signals and runs event-time simulations; `risk` evaluates every simulated entry independently; `storage` writes results and audit events to SQLite; `web` serves read-only local views; and `config` fails closed outside simulation and loopback hosting. Strategies do not call storage or web handlers, and the risk engine has no broker methods.

```text
CSV + provenance
      │
      ▼
Data validation ── unusable ──► abstain / report quality issues
      │ usable
      ▼
SMA signals ──► next-bar simulation ──► RiskEngine ──► simulated fills
                                                 │
                                                 ▼
                                      SQLite run + audit record
                                                 │
                                                 ▼
                                      loopback read-only dashboard
```

## Lot 2 execution and research path

```text
bars -> features -> regimes -> strategies -> StrategyDecision (or ABSTAIN)
                                                  |
                                   ExecutionCommand (schema v1)
                                                  v
OrderGateway: permission -> schema -> idempotency -> kill switch / freezes
              -> connection -> mode verification -> quote age / spread
              -> RiskEngine -> contract size / margin -> persist -> BrokerAdapter
                                                  |                    |
                   ExecutionStore (orders, transitions,                v
                   controls, reconciliations, audit)            SimulatedBroker
                                                  ^                    |
                                  process_events / reconcile <---------+
```

- `execution.OrderState`: CREATED, VALIDATING, RISK_REJECTED, AUTHORIZED, SUBMITTED, ACCEPTED, PARTIALLY_FILLED, FILLED, CANCELLED, BROKER_REJECTED, UNKNOWN. Invalid transitions raise `InvalidTransition`. Stale (lower sequence) or regressive (lower filled quantity) broker events are ignored and audited; a conflicting report on a terminal order freezes entries.
- `registry.ModelRegistry` receives experiments and candidates from `ml` and research runs. Promotion is a gated human step and never changes risk limits.
- `governance` holds the static role table used by controls, registry, gateway and secret access.
- `engine.run_paper_replay` replays bars causally: the previous close's decision executes at the next open, stop exits are engine-managed, and the next decision is taken at the close.
- Design record: [ADR-0003](docs/adr/0003-order-gateway-and-simulated-broker.md).

## Execution boundary (lot 1 corrections)

```text
OrderGateway (all checks) --issues--> ExecutionPermit (HMAC, process key, 30 s, single-use, bound to request)
                                          |
BrokerAdapter.submit_order (not overridable, checked through the MRO)
   1. verify_permit        -> missing / forged / mismatched / expired / already used -> ExecutionRefused
   2. _environment_guard   -> adapter-specific; default refuses
   3. _send_order          -> provider call
```

- The permit binds client order id, instrument, side, size, order type, stop-loss, purpose and mode, and is accepted once (`permit_already_used`).
- `SimulatedBroker` accepts only SIMULATION permits. `CTraderAdapter` accepts only DEMO permits and only when `isLive is False`, `RunMode.DEMO in EXECUTABLE_MODES` (not in this build), `allow_demo_orders=True` and the adapter is `CONNECTED`. A refusal ends the order as `BROKER_REJECTED` with `execution_refused:<reason>`; nothing leaves the process.
- Freezes are scoped per account (`TradingControls(scope=account_ref)`, `OrderGateway(account_ref=...)`); global freezes and the kill switch apply to every account.
- `BrokerRequestError` (provider refused a request) is handled without a connection freeze: on submit the order ends `BROKER_REJECTED` with `broker_request_error:<code>`; during reconciliation the result is `FAILED` and `reconciliation_required` is kept. `BrokerUnavailable` (link, auth, session; cTrader `CTraderConnectionError`) still freezes entries (`connection_lost`) and requires reconciliation.
- `execution.resolution` lets a human resolve an `UNKNOWN` or `SUBMITTED` order with evidence; it never lifts freezes, reconciliation does.

## Operating foundations (F-01 to F-04)

```text
TradingService.tick (one account, one adapter)
   check_connection -> lost: connection_lost + reconciliation_required freezes -> adapter.reconnect()
   reconcile if a reconciliation-owned freeze is active (and at start, every 15 bars)
   process_events (per-account cursor) -> quote -> BarBuilder (bar emitted after close)
   bar close: stop management -> decision on closed bars -> ExecutionCommand (deterministic intent id)
              -> OrderGateway (all checks above) -> persist last bar / stop in service_state
   5 consecutive tick errors -> service_degraded freeze (human clears)
```

- `service.py` never builds a broker; the caller chooses the adapter. The CLI wires only `SimulatedBroker` and `SyntheticQuoteFeed`.
- `logs.py`: JSON lines with correlation id and secret-field redaction; silent (`NullHandler`) until the CLI configures it.
- `backup.py`: SQLite online backup with manifest; restore verified, human-only, followed by per-account `reconciliation_required` freezes.
- `brokers/ctrader.py`: reconnect with capped backoff, permanent versus transient errors, client rate limiter, single read-only `retryAfter` retry, token expiry check and store-gated refresh.

## Structural options considered

| Option | Benefits | Costs and risks | Decision |
|---|---|---|---|
| Python standard library, SQLite, built-in HTTP server | No package download, small local footprint, quick offline tests, easy rollback | Limited concurrency and security features; local-only; migration path needed before multi-user operation | Use for the first offline simulation slice |
| FastAPI plus PostgreSQL and a separate React client | Typed HTTP contracts, mature ecosystem, clearer multi-user growth path | Adds runtime dependencies, deployment/configuration, database service and authentication work before the current empty workspace has a functioning vertical path | Re-evaluate after API and multi-user requirements are evidenced |
| Node.js service with a web client and external database | One ecosystem can serve UI and API; broad package ecosystem | Adds a second project/tooling choice unrelated to the Python research core and more dependencies than the first slice needs | Do not use now |

The choice is intentionally local and reversible. The custom `RiskEngine`, data contracts, and storage boundaries are designed so a later API/database change does not require rewriting strategy logic. The local HTTP server is not an internet-facing deployment architecture.

## State and mode boundaries

- The only executable mode is `SIMULATION`. Configuration defaults to it and rejects `DEMO`/`LIVE` plus any live-enable environment flag; the gateway also re-verifies mode against the adapter on every command.
- A backtest run is immutable after persistence; run metadata contains source type, source name, SHA-256, assumptions, metrics, and risk rejections.
- SQLite stores research records and audit events, not credentials. The `service_state` table holds operational state (service heartbeats, last processed bar, active stop price, broker event cursor per account); it is not audited.
- The dashboard accepts GET only. POST, PUT, and DELETE receive HTTP 405.
- The service binds only to loopback. It has no authentication, TLS, CORS allowance, or remote-access claim.
- Durable idempotency comes from the gateway's `orders` table (intent id primary key, derived client order id unique). The RiskEngine's in-memory set is a second line of defence only.
- The dashboard stays GET-only; operator actions go through the local CLI.

## ADR

See [ADR-0001](docs/adr/0001-local-python-simulation.md) for the options, constraints, and revisit conditions.
