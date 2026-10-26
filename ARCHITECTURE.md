# Architecture

## Current shape

ASTRA is a dependency-free Python modular monolith. `data` validates CSV datasets that carry a caller-declared provenance label; `research` produces signals and runs event-time simulations; `risk` evaluates every simulated entry independently; `storage` writes results, sealed research plans and audit events to SQLite; `web` serves read-only local views; and `config` fails closed outside simulation and loopback hosting. Strategies do not call storage or web handlers, and the risk engine has no broker methods. Every dataset used so far is synthetic.

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
- `registry.ModelRegistry` receives experiments and candidates from `ml` and research runs. Promotion is a gated human step through the Python API (there is no CLI promote command) and never changes risk limits.
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
- `backup.py`: SQLite online backup with manifest; restore verified, human-only, refused while a service heartbeat is alive, followed by per-account `reconciliation_required` freezes. Backups are not encrypted and do not include the evidence anchor file.
- `brokers/ctrader.py`: reconnect with capped backoff, permanent versus transient errors, client rate limiter, single read-only `retryAfter` retry, token expiry check and store-gated refresh. Tested against a scripted fake server only.

## Quantitative validation (lot 3)

```text
bars + candidates (sma, momentum, breakout)
   -> plan (protocol, candidates, hashes) sealed in SQLite: one plan per dataset identity per store
   -> per fold: validation (selection) -> test (all candidates, PBO input only), causal per block
   -> PBO matrix (5-bar mark-to-market periods x candidates) + DSR sample -> overfitting (CSCV PBO, deflated Sharpe)
   -> optional once-only holdout: selected + SMA reference + flat, cost stress x1.5/x2
   -> holdout_evidence (EVIDENCE_FIELDS) -> registry.record_evaluation -> promotion gates (human promote only)
```

- `strategy_validation` reuses `validation` fold layout and `signal_backtest`; it imports `overfitting` lazily, so a missing module yields `UNAVAILABLE` and unmet gates.
- `overfitting` is pure computation with no storage, web or broker imports.
- The CLI `evaluate-strategies` writes the experiment, the sealed plan and, with `--register`, an inactive candidate; it never promotes and sends no order.

## Research integrity (lot 4)

```text
seal plan -> holdout_reuse: compare with every sealed, logged and anchored holdout
               CONFLICT -> refused; SUSPECT / UNDETERMINED -> sealed, never scorable
   | CLEAR
every scored variant -> attempts (append-only, hash-chained research_attempt_events)
                          -> store-wide trial count + trial Sharpes -> DSR TrialUniverse
   |
holdout scoring: anchor consistency -> STARTED attempt (holdout returns) anchored
                 -> score once -> anchored again
   |
registry.check_promotion(model_id, version, holdout_bars=...)
   re-derive plan, selection, holdout metrics, PBO and DSR from the bars, compare with the stored report
   + attempt chain intact, trial count not understated, statistical floors not lowered
   + evidence anchor present, consistent and bound to this store
     (production grade, or an explicit and audited human waiver for LocalFileAnchor)
   |
promote (human reviewer, Python API only) -> CHAMPION status in the registry
```

- `attempts` (`ATTEMPT_LOG_VERSION` 1.0.0): every `fold_trial`, `walk_forward_run` and `holdout` attempt is an appended event row, recorded by the library functions whenever a store is passed. Triggers refuse `UPDATE`, `DELETE` and an insert that does not link to the chain head; `verify` detects edited, removed and reordered rows. This is tamper-evidence, not tamper-proofing: the file owner can drop the triggers and rewrite the chain consistently, which only a chain head kept elsewhere (`attempts verify --expected-head`) or an evidence anchor detects.
- `research_policy` (`astra-research-policy-0.2.0-provisional`): reuse-detector thresholds and statistical floors in code. They are provisional engineering floors, not calibrated or scientifically universal thresholds; a floor lowered in process is reported at promotion (`statistical_policy_below_floor`, `holdout_reuse_policy_below_floor`).
- `holdout_reuse`: heuristic detector of a new holdout that repeats a sealed price path (window correlation and chance-adjusted match share). Calibrated on synthetic sets only; its known misses are listed in [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md).
- `anchoring`: the `EvidenceAnchor` interface and the only adapter shipped, `LocalFileAnchor` (file named by `ASTRA_EVIDENCE_ANCHOR`), which is **not production grade** (`production_grade = False`; its owner can truncate, replace or delete the file). No external WORM / object-lock anchor is implemented or configured, so production immutability is not established. A store is bound to the first anchor that checkpoints it; a restored older database is detected as behind the anchor.
- Re-derivation at promotion is implemented for `strategy_validation` plans only; SMA `validation` plans fail closed. It proves that the stored numbers follow from the bars supplied, not that the bars are genuine market data.
- Exploration run without a store, in another database file or outside ASTRA is not recorded and not counted; there is no interface to declare it.

Rules, failure codes and calibration tables: [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md).

## Target architecture V5: component status

Reference: ASTRA architectural directive V5, section 5. This table records what the code on this branch does; it is not a plan or a promise. **Implemented** = present and tested locally on synthetic data or offline fixtures; **Partial** = some responsibilities present, others missing; **Not implemented** = no code. No component has been exercised with real market data, a real broker connection or a real account, and nothing here is evidence of profitability.

| V5 component | Status | Present in code | Missing |
|---|---|---|---|
| 5.1 Market Data Gateway | Partial | `data`: CSV import with caller-declared source label, UTC-offset timestamps, OHLC and bid/ask consistency, duplicate, order, interval-gap, future-timestamp and staleness checks, size bounds, source-byte SHA-256; canonical bar identity in `validation` ([DATA_POLICY.md](DATA_POLICY.md)) | Data connectors (history download, live feed), the V5 label set (`PUBLIC`, `BROKER_HISTORICAL`, `LIVE_FEED`, `DEMO_EXECUTION`), provenance or licence verification, receipt timestamps and clock drift, outlier and DST checks, immutable raw store, versioned normalized data, parser version |
| 5.2 Instrument & Broker Specification Registry | Partial | Illustrative EURUSD spec (`instruments.DEFAULT_SPECS`); the cTrader adapter maps `ProtoOASymbol` (offline only); unknown specs block orders (`instrument_specs_unknown`) | Persisted, dated specs per broker and account; commissions, swaps, trading calendar and margin schedule from a provider |
| 5.3 Feature & Regime Engine | Partial | Causal versioned features with future-mutation tests; regimes with train-only quantiles and a confidence value | Multi-timeframe alignment; transition and macro-event regimes; per-fold regime fitting inside the sealed walk-forward |
| 5.4 Strategy Research Lab | Partial | `NO_POSITION` and SMA references; momentum, breakout, z-score mean reversion; `StrategyDecision` with `ABSTAIN`; strategies import no broker code (tested) | Mean reversion in the sealed walk-forward; session, spread and volatility filters as candidates; carry |
| 5.5 Model & Probability Engine | Partial | L2 logistic regression vs base rate, purged walk-forward, Brier, log loss, calibration bins | Net-expectancy rule from probability to trade; ML trading evidence after costs; uncertainty intervals; other model families. No reinforcement learning (by design) |
| 5.6 Research & Experiment Ledger | Partial | Experiments; hash-chained attempt log; store-wide trial count; holdout evidence and overfitting statistics re-derived at promotion; anchor interface with store binding and restore detection | Production-grade external anchor (WORM); a way to declare research done outside ASTRA; per-attempt seed and dependency versions (only the ASTRA version and code hash are recorded) |
| 5.7 Validation Engine | Partial | Chronological walk-forward, once-only holdout, purge and embargo (ML baseline), CSCV PBO with the ASTRA selection rule, DSR with the store-wide trial count, `LOW_INFORMATION` guards, 1.5x/2x cost stress, seeded bootstrap of the mean trade | Block bootstrap, per-regime validation, parameter sensitivity, Monte Carlo and perturbation tests; statistical review and calibration of the provisional floors |
| 5.8 Realistic Execution Simulator | Partial | Backtests charge configured spread, slippage and commission; `SimulatedBroker` injects reject, partial fill, timeouts and disconnects for gateway tests | Variable spread, latency, gaps, swaps/financing, currency conversion, margin calls, stop slippage. The simulator is not calibrated and is not described as realistic |
| 5.9 Portfolio Risk Engine | Partial | Per-position risk, daily loss, one position, data age, spread, EURUSD allowlist, margin, kill switch, per-account freezes ([RISK_POLICY.md](RISK_POLICY.md)) | Weekly loss, drawdown duration, currency and correlation exposure, multi-instrument portfolio, per-strategy and per-broker limits, gap/weekend/event risk |
| 5.10 Execution Gateway & Multi-Broker Layer | Partial | Single `OrderGateway`, single-use HMAC permits, provider-sourced environment check, idempotent intents, `UNKNOWN` state without blind resend, reconciliation, foreign-activity freeze, no funds movement; cTrader adapter tested offline ([BROKERS.md](BROKERS.md)) | Read-only check against the real service (not yet run by the user); DEMO execution (disabled); LIVE (locked); a second adapter; a cross-adapter conformance suite; a review against the official Protobuf SDK |
| 5.11 Autonomous Improvement Engine | Not implemented | Building blocks only: registry statuses `CANDIDATE`, `SHADOW`, `CHAMPION`, `REJECTED`, `RETIRED`, pre-registered gates, human `promote`, `rollback` (Python API) | Drift detection, investigation events, sandboxed candidate generation, shadow signal computation and comparison, automatic rollback, change reports |
| 5.12 LLM / Claude Orchestrator | Partial | `governance`: LLM and agent roles cannot submit orders, promote or change limits; `handle_llm_output` validates a strict advisory schema and never executes | Runtime orchestration; isolated execution of generated code. Claude agents are used only as development tools outside the runtime |
| 5.13 Operations, Observability & Web | Partial | GET-only loopback dashboard with host check and security headers; JSON logs; continuous service on the simulator with heartbeat and degraded mode; verified SQLite backup and restore ([OPERATIONS.md](OPERATIONS.md)) | Authentication, backend authorization, TLS, remote access, metrics, alert delivery, external supervision, encrypted and scheduled backups, dashboard views of attempts, anchor state and data quality |

The registry's `CHAMPION` status is not read by the service or the paper replay: both run the strategy named by `--strategy`. `SHADOW` is a registry status only; no component computes shadow signals.

## Structural options considered

| Option | Benefits | Costs and risks | Decision |
|---|---|---|---|
| Python standard library, SQLite, built-in HTTP server | No package download, small local footprint, quick offline tests, easy rollback | Limited concurrency and security features; local-only; migration path needed before multi-user operation | Use for the first offline simulation slice |
| FastAPI plus PostgreSQL and a separate React client | Typed HTTP contracts, mature ecosystem, clearer multi-user growth path | Adds runtime dependencies, deployment/configuration, database service and authentication work before the current empty workspace has a functioning vertical path | Re-evaluate after API and multi-user requirements are evidenced |
| Node.js service with a web client and external database | One ecosystem can serve UI and API; broad package ecosystem | Adds a second project/tooling choice unrelated to the Python research core and more dependencies than the first slice needs | Do not use now |

The choice is intentionally local and reversible. The custom `RiskEngine`, data contracts, and storage boundaries are designed so a later API/database change does not require rewriting strategy logic. The local HTTP server is not an internet-facing deployment architecture.

## State and mode boundaries

- The only executable mode is `SIMULATION`. Configuration defaults to it and rejects `DEMO`/`LIVE` plus any live-enable environment flag; the gateway also re-verifies mode against the adapter on every command.
- A backtest run is not updated by the code after persistence, but no trigger protects `backtest_runs`; run metadata contains source type, source name, SHA-256, assumptions, metrics, and risk rejections.
- Research-integrity tables are append-only through SQLite triggers: `research_protocols`, `holdout_signatures`, `holdout_reuse_checks`, `research_attempt_events` and `evidence_anchor_bindings`; a `holdout_evaluations` row cannot be deleted, nor changed once `COMPLETED` or `FAILED`. Triggers stop the API and casual edits, not the owner of the file.
- SQLite stores research records and audit events, not credentials. The `service_state` table holds operational state (service heartbeats, last processed bar, active stop price, broker event cursor per account); it is not audited.
- The dashboard accepts GET only. POST, PUT, and DELETE receive HTTP 405.
- The service binds only to loopback and checks the `Host` header. It has no authentication, TLS, CORS allowance, or remote-access claim, and must not be exposed.
- Durable idempotency comes from the gateway's `orders` table (intent id primary key, derived client order id unique). The RiskEngine's in-memory set is a second line of defence only.
- The dashboard stays GET-only; operator actions go through the local CLI.

## ADR

See [ADR-0001](docs/adr/0001-local-python-simulation.md) for the options, constraints, and revisit conditions.
