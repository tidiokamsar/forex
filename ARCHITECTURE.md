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
- SQLite stores research records and audit events, not credentials or broker state.
- The dashboard accepts GET only. POST, PUT, and DELETE receive HTTP 405.
- The service binds only to loopback. It has no authentication, TLS, CORS allowance, or remote-access claim.
- Durable idempotency comes from the gateway's `orders` table (intent id primary key, derived client order id unique). The RiskEngine's in-memory set is a second line of defence only.
- The dashboard stays GET-only; operator actions go through the local CLI.

## ADR

See [ADR-0001](docs/adr/0001-local-python-simulation.md) for the options, constraints, and revisit conditions.
