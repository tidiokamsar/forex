# Changelog

## Lot 4 independent review, quantitative integrity, attempt registry · branch `feat/lot4-integrite-quant` · 2026-10-25

- `attempts.py`: append-only, hash-chained research attempt registry (every fold trial, run and holdout; selected, rejected, failed, interrupted, abandoned); store-wide trial count over every period of the instrument; CLI `attempts list|count|verify`.
- `overfitting.py` 1.1.0 and `strategy_validation.py` 1.1.0: mark-to-market PBO matrix, CSCV selection with the ASTRA rule, conservative in-sample ties, abstention, `TrialUniverse`; holdout DSR uses the store-wide count; guards for indistinguishable variants, trades per PBO variant and trades in the DSR sample; `rederive_holdout_evidence`.
- `holdout_reuse.py`: refusal of holdouts that reuse a sealed price path (label, dates, scale, quote and time direction, two-bar resampling); seal requires the holdout returns; minimum 16 returns.
- Registry: re-derivation of holdout evidence from bars at promotion (`holdout_bars`), report binding to plan, code and attempt log, one version per holdout at promotion, append-only triggers.
- Limits: synthetic data only; residuals in `reports/LOT_04_REPORT.md`.

## Lot 3 quantitative validation (P-02, Q-01) · branch `feat/validation-quant` · 2026-10-25

Independent QA review pending; not accepted yet.

- `overfitting.py` (`astra-overfitting-1.0.0`): CSCV probability of backtest overfitting (contiguous slices, in-sample selection by Sharpe-like metric with lowest-index ties, OOS average rank, overfit when `lambda <= 0`, at most 20,000 combinations) and deflated Sharpe ratio (per-period Sharpe, population skewness, non-excess kurtosis, null variance `1 / (T - 1)` by default; `n_trials = 1` gives the PSR against 0). Invalid inputs raise.
- `strategy_validation.py`: sealed walk-forward over SMA, momentum and breakout candidates (mean reversion excluded), causal per-block evaluation, validation-only selection, every candidate scored on test blocks for the PBO matrix only (rows = 5-bar periods by trade exit time), DSR inputs, flat and SMA reference baselines, `edge_vs_baseline`, holdout cost stress x1.5/x2 (`cost_stress_net_pnl` = worst), one plan per dataset per store, once-only holdout, `COMPLETED`/`INCOMPLETE`/`UNAVAILABLE` overfitting status. The implementation hash includes `overfitting.py`.
- Registry: evidence fields `pbo`, `deflated_sharpe_probability`, `variants_tested`; criteria `max_pbo` 0.3, `min_deflated_sharpe_probability` 0.95, `require_overfitting_check`; failures `overfitting_check_missing`, `pbo_above_threshold`, `deflated_sharpe_below_threshold`. Candidates registered before lot 3 fail closed.
- CLI `evaluate-strategies` (`--evaluate-holdout`, `--register --model-id --version`): logs the experiment, optionally registers an inactive candidate with holdout evidence, never promotes, `external_orders: false`, warns on synthetic data.
- Limits: synthetic data only; on the bundled fixture DSR is `INCOMPLETE` in development and PBO runs on a sparse matrix; thresholds are starting values; the SQLite lock is advisory.
- Verification: see ACCEPTANCE_REPORT.md.

## Foundations (F-01 to F-04) · branch `feat/fondations-exploitation` · 2026-10-25

- F-01: `logs.py`, structured JSON logs on stderr (level `ASTRA_LOG_LEVEL`, default `WARNING` in the CLI) and optionally a rotating `astra.jsonl` in `ASTRA_LOG_DIR`. Correlation id from the order command; secret-like field names redacted recursively. Events for order results, reconciliation, connection loss, freezes, kill switch, cTrader, service and backup.
- F-04: `backup.py` and CLI `backup create|verify|restore`. Online SQLite backup, integrity check, SHA-256 manifest, retention (`--keep`, default 14), default folder `ASTRA_BACKUP_DIR` or `var/backups`. Restore is human-only (`Action.RESTORE_BACKUP`) with a reason, verifies before replacing, keeps the previous file as `*.pre-restore-*`, and freezes every account with orders with `reconciliation_required`. A stale backup is caught by the next reconciliation (`DIVERGENT`).
- F-03: cTrader `reconnect` with capped exponential backoff (5 attempts, 1 s doubling to 30 s), permanent errors raised without retry, client rate limiter (40 requests/s), one `retryAfter` retry for read-only requests only (orders never retried), access-token expiry check (expired refused before any message, warning under 3 days), `refresh_access_token` only with an explicit `TokenStore` (none bundled). Reconnecting lifts no freeze.
- F-02: `service.py` and CLI `service run|status`. `TradingService` loop with a bar builder (bar emitted after close), deterministic intent ids, persisted last bar, stop price and throttled heartbeat (new `service_state` table, not audited), reconciliation at start, every 15 bars and after reconnect, degraded mode after 5 consecutive tick errors (`service_degraded` freeze, cleared by a human), graceful stop on SIGINT/SIGTERM/SIGBREAK. The CLI runs it on the simulated broker with a `SYNTHETIC` feed only.
- Fix: the broker event cursor is now per account (`broker_event_cursor:<account_ref>`) and stored as operational state instead of an audited control.
- Not provided: external supervision, alert delivery, a secure token store, a restore drill against a real broker.
- Verification: see ACCEPTANCE_REPORT.md.

## Lot 1 corrections · branch `fix/lot1-securite` · 2026-10-25

- C-01: execution permits are verified at `BrokerAdapter.submit_order`, the single execution boundary. The gateway issues an HMAC permit (process-local key, 30 s validity) after its checks; a missing, forged, mismatched or expired permit, or a refusal by the adapter's environment guard, raises `ExecutionRefused` and the order ends `BROKER_REJECTED` with `execution_refused:<reason>`. The cTrader guard requires `isLive: false`, DEMO in `EXECUTABLE_MODES` (not in this build), `allow_demo_orders=True` and a `CONNECTED` adapter.
- D-1: the permit also binds order type and stop-loss, and is single-use (`permit_already_used`).
- D-2: the `submit_order` override check resolves through the MRO, so a mixin cannot replace the boundary either.
- C-03: human resolution of `UNKNOWN` or `SUBMITTED` orders with evidence (`execution/resolution.py`, CLI `orders --resolve`). Roles `HUMAN_ADMIN`/`HUMAN_OPERATOR`; evidence of at least 8 characters; refused when the adapter still reports the order. Resolution never lifts freezes; a consistent reconciliation does.
- C-04: duplicate events from cTrader `lookup_order`: not a defect; a test shows one event.
- C-05: freezes and the daily-loss day marker are per account (`TradingControls(scope=account_ref)`, `OrderGateway(account_ref=...)`); the kill switch stays global; a gateway without `account_ref` writes global freezes. `controls show` and `/api/controls` also return `entry_freezes_by_account`.
- C-07: `BrokerRequestError` (provider refused a request) no longer freezes the connection. On submit it gives `BROKER_REJECTED` with `broker_request_error:<code>`; during reconciliation the result is `FAILED` and `reconciliation_required` is kept. cTrader authentication/session error codes raise `CTraderConnectionError` and are handled as `connection_lost`.
- C-08: ruff configuration in `pyproject.toml`; `python -m ruff check src tests`. Ruff is installed separately as a development tool, not a runtime dependency.
- Git repository initialised on top of the remote's `Initial commit`; secret scan of tracked files before publication; `.gitattributes` keeps `data/` byte-exact.
- Verification: see ACCEPTANCE_REPORT.md.

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
- Verification: 192 tests passed (the count at that time); see ACCEPTANCE_REPORT.md.

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
