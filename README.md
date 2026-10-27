# ASTRA Forex Intelligence

ASTRA is a local quantitative research and **simulation-only** trading workspace. It imports labelled market bars and checks their quality, computes causal features and interpretable regimes, evaluates several strategy families and a baseline ML model with walk-forward validation and cost stress, records experiments and model candidates in a registry with pre-registered promotion gates, and runs decisions end to end through a durable Order Gateway, an independent RiskEngine and an in-process simulated broker. A French read-only dashboard shows the active mode, data, runs, orders, controls and registry.

`SIMULATION` is the only executable mode. A cTrader Open API demo adapter exists (tested offline) with a read-only connection check, but DEMO order execution is disabled until that check passes on a real demo account; `LIVE` is locked. No order can leave the process. See [BROKERS.md](BROKERS.md).

## Requirements

- Python 3.11 or later, standard library only (developed on 3.14.6)
- No broker credentials or network access, except for the optional read-only `ctrader-check`

## Quick start (PowerShell)

```powershell
$env:PYTHONPATH = "src"
python -m astra status
python -m astra demo
python -m astra paper-replay
python -m astra serve
```

Open `http://127.0.0.1:8765`. All bundled data is **synthetic** (`data/synthetic_eurusd_m15.csv`, 160 bars, SHA-256 pinned).

## Commands

| Command | Purpose |
|---|---|
| `status` | Mode, live lock, configuration |
| `demo` | SMA baseline backtest on the bundled synthetic fixture |
| `backtest --csv ... --source-type ... --exploratory` | Full-history exploratory backtest (non-promotable) |
| `walk-forward --csv ... --hypothesis ...` | Train/validation/test folds, sealed plan, once-only holdout (`--evaluate-holdout`) |
| `evaluate-strategies [--csv ... --source-type ...] [--evaluate-holdout] [--register --model-id ID --version V]` | Sealed walk-forward over SMA, momentum and breakout candidates with PBO and deflated Sharpe; once-only holdout; optional inactive candidate registration; never promotes (see [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md)) |
| `ml-baseline [--csv ...]` | Purged walk-forward logistic regression vs base rate; logs the experiment and registers an inactive candidate |
| `paper-replay [--csv ...] [--strategy sma\|momentum\|breakout]` | Replays bars through Order Gateway + RiskEngine + simulated broker, then reconciles |
| `orders`, `models` | Inspect stored orders, model versions and experiments |
| `orders --resolve INTENT_ID --outcome FILLED\|NOT_EXECUTED --evidence "..." --operator <name> [--filled-units N --price P]` | Human resolution of an `UNKNOWN` or `SUBMITTED` order; never lifts freezes (see [OPERATIONS.md](OPERATIONS.md)) |
| `controls show\|kill\|release\|clear-freeze` | Emergency stop and entry freezes (see [OPERATIONS.md](OPERATIONS.md)) |
| `service run [--strategy ...] [--timeframe N] [--realtime] [--max-ticks N]` | Continuous trading loop on the simulated broker with a `SYNTHETIC` feed (see [OPERATIONS.md](OPERATIONS.md)) |
| `service status` | Last heartbeat per service account (`alive`, age) |
| `backup create [--dir ...] [--keep N]` | Consistent, integrity-checked SQLite backup with SHA-256 manifest (`ASTRA_BACKUP_DIR`, default `var/backups`) |
| `backup verify PATH` | Check a backup's hash, integrity and row counts against its manifest |
| `backup restore PATH --operator <name> --reason "..."` | Human restore, refused while a service heartbeat is alive; every account with orders frozen until a consistent reconciliation |
| `attempts list\|count\|verify [--expected-head ROW_HASH]` | Read-only views of the hash-chained attempt registry: attempts, store-wide trial count, chain verification (see [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md)) |
| `serve` | Read-only loopback dashboard |
| `ctrader-check --operator <name>` | Read-only cTrader DEMO connection check; sends no order (see [BROKERS.md](BROKERS.md)) |

CSV columns are `timestamp,instrument,open,high,low,close`; `volume`, `bid`, `ask` are optional. Timestamps need a UTC offset. Label sources honestly (`HISTORICAL`, `DELAYED`, `LIVE`, `SIMULATED`, `SYNTHETIC`).

Logs are JSON lines on stderr: `ASTRA_LOG_LEVEL` (default `WARNING`), `ASTRA_LOG_DIR` for a rotating `astra.jsonl`. Secret-like fields are redacted.

## Modules

| Module | Role |
|---|---|
| `config`, `models` | Fail-closed configuration, shared contracts |
| `data` | CSV import, provenance, quality checks |
| `features`, `regimes` | Versioned causal features; train-only-fitted regime thresholds with confidence |
| `strategies`, `research`, `signal_backtest` | Momentum, breakout, mean reversion, SMA baseline; event-time backtests with costs |
| `validation`, `ml` | Walk-forward/holdout protocol; purged walk-forward ML baseline |
| `strategy_validation` | Sealed multi-family walk-forward (SMA, momentum, breakout), baselines, cost stress, once-only holdout, registry evidence |
| `overfitting` | CSCV probability of backtest overfitting and deflated Sharpe ratio (pure Python) |
| `registry` | Experiment log, model versions, pre-registered criteria (including PBO and deflated Sharpe gates), promotion with holdout re-derivation from the bars, rollback (Python API; no CLI promote) |
| `attempts` | Append-only, hash-chained attempt registry; store-wide trial count for the deflated Sharpe ratio |
| `research_policy` | Versioned, provisional research-integrity floors (`astra-research-policy-0.5.0-provisional`) |
| `holdout_reuse` | Heuristic detection of a holdout that repeats a sealed price path |
| `anchoring` | Evidence anchor interface; `LocalFileAnchor` only, not production grade |
| `risk` | Independent RiskEngine with hard-capped policy |
| `instruments` | Contract specs (illustrative), size rounding, margin |
| `brokers` | Broker contract and `SimulatedBroker` |
| `brokers.ctrader`, `brokers.websocket` | cTrader Open API demo adapter (JSON over WebSocket) and its standard-library RFC 6455 client |
| `permits` | Execution permits issued by the gateway and verified at `BrokerAdapter.submit_order` |
| `execution` | Order store/state machine, controls, mode verification, gateway, reconciliation |
| `execution.resolution` | Human resolution of undetermined orders with evidence |
| `governance` | Roles, permission guard, secret access, two-person risk change, LLM output validation |
| `engine` | Paper replay end-to-end path |
| `logs` | Structured JSON logging, correlation ids, secret-field redaction |
| `backup` | SQLite backup, verification and human restore that forces reconciliation |
| `service` | Continuous trading loop, bar builder, heartbeat, degraded mode |
| `web`, `cli` | Dashboard and command line |

## Verify

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall -q src tests
node --check src/astra/static/app.js
python -m ruff check src tests
```

Ruff is a development tool installed separately (`pyproject.toml` holds its configuration); it is not a runtime dependency.

Latest recorded result: see [ACCEPTANCE_REPORT.md](ACCEPTANCE_REPORT.md).

## Limits

- Synthetic data only; no result here is evidence about real markets. Historical performance does not guarantee future results, and nothing here is financial advice.
- Overfitting checks (PBO, deflated Sharpe) need enough information: with CLI defaults on the small bundled fixture the assessment is `LOW_INFORMATION`, no PBO or DSR is reported and the promotion gate stays unmet. Thresholds and research-policy floors are provisional engineering values, not calibrated recommendations.
- Lot 4 integrity controls (attempt registry, holdout reuse detection, re-derivation at promotion, evidence anchoring) are tamper-evident, not tamper-proof: the SQLite file owner can rewrite them. The only anchor shipped, `LocalFileAnchor`, is not production grade, and no external WORM anchor exists. Exploration outside a store is not counted. Promotion has never been exercised on real data.
- Strategy candidates in the registry do not drive execution: the service and paper replay use `--strategy`, and `SHADOW` is a status only.
- The cTrader demo adapter is tested offline only; its read-only `ctrader-check` has not been run against a real demo account, and DEMO order execution is disabled. Its reconnection, rate limiting and token handling are also tested offline only.
- Backup/restore and the continuous service exist and are tested locally with the simulator only; the service CLI runs on the simulated broker and a synthetic feed. No restore drill against a real broker account.
- Missing: external service supervision (restart, start at boot), alert delivery, authentication, dashboard TLS, encrypted backups, CI, drift monitoring and the autonomous improvement loop. V5 component status: [ARCHITECTURE.md](ARCHITECTURE.md).
- No profitability claim: no strategy or model has been shown to be profitable.
- Instrument specifications and costs are illustrative; no swaps or financing are modelled.
- The kill switch is global to a store; entry freezes and daily-loss state are per account. The SQLite audit trail is mutable by its owner.
- The dashboard binds to loopback only; do not expose it.

Further reading: [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), [RISK_POLICY.md](RISK_POLICY.md), [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md), [TASKS.md](TASKS.md), [ROADMAP.md](ROADMAP.md).
