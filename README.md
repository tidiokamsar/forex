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
| `ml-baseline [--csv ...]` | Purged walk-forward logistic regression vs base rate; logs the experiment and registers an inactive candidate |
| `paper-replay [--csv ...] [--strategy sma\|momentum\|breakout]` | Replays bars through Order Gateway + RiskEngine + simulated broker, then reconciles |
| `orders`, `models` | Inspect stored orders, model versions and experiments |
| `orders --resolve INTENT_ID --outcome FILLED\|NOT_EXECUTED --evidence "..." --operator <name> [--filled-units N --price P]` | Human resolution of an `UNKNOWN` or `SUBMITTED` order; never lifts freezes (see [OPERATIONS.md](OPERATIONS.md)) |
| `controls show\|kill\|release` | Emergency stop and entry freezes (see [OPERATIONS.md](OPERATIONS.md)) |
| `serve` | Read-only loopback dashboard |
| `ctrader-check --operator <name>` | Read-only cTrader DEMO connection check; sends no order (see [BROKERS.md](BROKERS.md)) |

CSV columns are `timestamp,instrument,open,high,low,close`; `volume`, `bid`, `ask` are optional. Timestamps need a UTC offset. Label sources honestly (`HISTORICAL`, `DELAYED`, `LIVE`, `SIMULATED`, `SYNTHETIC`).

## Modules

| Module | Role |
|---|---|
| `config`, `models` | Fail-closed configuration, shared contracts |
| `data` | CSV import, provenance, quality checks |
| `features`, `regimes` | Versioned causal features; train-only-fitted regime thresholds with confidence |
| `strategies`, `research`, `signal_backtest` | Momentum, breakout, mean reversion, SMA baseline; event-time backtests with costs |
| `validation`, `ml` | Walk-forward/holdout protocol; purged walk-forward ML baseline |
| `registry` | Experiment log, model versions, pre-registered criteria, promotion, rollback |
| `risk` | Independent RiskEngine with hard-capped policy |
| `instruments` | Contract specs (illustrative), size rounding, margin |
| `brokers` | Broker contract and `SimulatedBroker` |
| `brokers.ctrader`, `brokers.websocket` | cTrader Open API demo adapter (JSON over WebSocket) and its standard-library RFC 6455 client |
| `permits` | Execution permits issued by the gateway and verified at `BrokerAdapter.submit_order` |
| `execution` | Order store/state machine, controls, mode verification, gateway, reconciliation |
| `execution.resolution` | Human resolution of undetermined orders with evidence |
| `governance` | Roles, permission guard, secret access, two-person risk change, LLM output validation |
| `engine` | Paper replay end-to-end path |
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
- The cTrader demo adapter is tested offline only; its read-only `ctrader-check` has not been run against a real demo account, and DEMO order execution is disabled. No authentication, dashboard TLS, backups/restore proof, service supervisor or alert delivery.
- Instrument specifications and costs are illustrative; no swaps or financing are modelled.
- The kill switch is global to a store; entry freezes and daily-loss state are per account. The SQLite audit trail is mutable by its owner.
- The dashboard binds to loopback only; do not expose it.

Further reading: [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), [RISK_POLICY.md](RISK_POLICY.md), [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md), [TASKS.md](TASKS.md), [ROADMAP.md](ROADMAP.md).
