# Research protocol

## Current baseline

The initial strategy is a transparent simple moving-average crossover. It is included to test data-to-simulation integration, not because its performance is established. Signal logic uses closes available through the signal bar; simulated entries occur on the following bar's open. A trailing range stop uses only bars available by the signal time. Stop execution is evaluated before a strategy exit when bar data cannot establish event order.

The simulation reports the sample size and includes the configured spread, slippage, and commission assumptions in both P&L and run metadata. If a position remains open at the dataset boundary, the simulator closes it at the final close with modeled costs. P&L assumes quote currency equals account currency; spot/CFD contract, leverage, swaps, latency, rejection, and conversion behavior are not represented.

## Minimum rules for future research

1. State a falsifiable hypothesis and mechanism before running variants.
2. Fix dataset hash, time range, strategy version, parameters, costs, and metrics before evaluation.
3. Keep chronological train/validation/test boundaries and use walk-forward evaluation.
4. Fit learned transformations only on training windows and preserve a final independent holdout.
5. Compare to a no-position baseline and a simple reference; include costs, sensitivity, sample size, and uncertainty.
6. Record every variant and failure. Do not tune repeatedly against the final holdout.
7. Reject results whose advantage disappears after costs or is unstable across windows.
8. Never treat historical performance as a guarantee of future returns.

## Implemented research protocol v1

`ASTRA-WF-1.0.0` evaluates expanding chronological training prefixes, fixed validation blocks, and disjoint test blocks. The latest `holdout_bars` remain reserved after the development windows. In each fold, candidate SMA settings are scored on validation bars with `validation net P&L - validation maximum drawdown in cash`; candidates below the per-window minimum trade count are ineligible for that fold's test choice. The final holdout candidate is chosen by mean validation selection score across all validation windows, then aggregate validation net P&L, then stable candidate hash. It must meet the configured minimum trade count summed across validation windows. No test or holdout metric contributes to model selection.

Each validation and test window starts with a fresh simulated account and no carried position. Indicator warm-up uses earlier bars; only a signal from the immediately preceding bar may fill at the first window open. A position still open at a window boundary closes at that window's last close. Training, validation, test, and holdout boundaries are recorded with timestamps and row indices; test blocks do not overlap. Each evaluator truncates its input at the evaluated block end, so later bars cannot affect that result.

Reports include the exact source-byte SHA-256, a separate canonical identity SHA-256 over normalized timestamp/instrument/OHLC/volume/bid/ask bars, the source fingerprint for model contracts/research/risk/validation implementations, protocol/candidate/cost configuration, a canonical plan SHA-256, per-candidate validation outcomes, per-window test metrics, and aggregated metrics. The fixed reference is `sma_crossover@1.0.0`; the flat baseline is `NO_POSITION@1`. Costs are evaluated under the declared base assumptions and 1.5x/2x stress scenarios. Aggregated returns use a fixed initial-equity denominator per independent window; drawdown is summarized as the worst and mean window drawdown. No confidence interval or statistical significance claim is made.

The CLI command `astra walk-forward` seals the plan in local SQLite and leaves the final block `RESERVED_NOT_EVALUATED`. The first sealed plan for a canonical market-bar identity is frozen within that configured store; another plan for that same identity is rejected before test results are returned. `astra walk-forward --evaluate-holdout` recomputes the pre-holdout validation selection, records a `STARTED` attempt, then evaluates the final block once. The store's unique holdout key is the canonical market-bar identity, so alternate CSV formatting, instrument case, or timestamp offsets for identical normalized bars do not create another key. A completed retry returns the stored report; interrupted `STARTED` or failed attempts remain consumed in that SQLite store.

This remains exploratory, non-promotable evidence. The local database owner can edit or replace the file or point `ASTRA_DATABASE` to another store. Direct API use can also inspect test metrics outside this CLI seal. For a confirmatory result, freeze the procedure before accessing tests and use a fresh, independent dataset. The demo command always reads the bundled synthetic fixture and verifies its SHA-256; `ASTRA_DATA_DIR` cannot substitute a different file under the demo label.

Ordinary `astra backtest` runs require the explicit `--exploratory` flag and are classified `EXPLORATORY_FULL_HISTORY_NON_PROMOTABLE`. They score the whole CSV, which may include any nominal holdout, and must not be used to tune a sealed protocol or claim out-of-sample performance. Reports are simulation evidence only and do not authorize DEMO or LIVE execution. The SMA walk-forward evaluator has no learned transformations.

## Strategy families, features and regimes (lot 2)

- `features` (`astra-features-1.0.0`): log returns, rolling volatility, Wilder ATR and RSI, rolling z-score, Donchian channel of the *prior* bars, Kaufman efficiency ratio, SMA slope. Value `i` uses bars `0..i` only; tests mutate later bars and assert earlier values are unchanged for every feature. `TrainOnlyScaler` refuses to transform before fit and cannot be refit.
- `regimes` (`astra-regimes-1.0.0`): trend/range from efficiency ratio and slope, LOW/NORMAL/HIGH volatility from quantiles fitted on a training slice only, spread state only from real bid/ask (otherwise `UNKNOWN`), approximate UTC sessions. Each estimate carries a confidence in [0, 1] and reasons; it is a hypothesis, not a fact. Transition and scheduled-event regimes are not implemented.
- `strategies`: time-series momentum, Donchian breakout, z-score mean reversion (RANGE regime required) and the SMA baseline. Each emits a `StrategyDecision` per bar with a direction or `ABSTAIN`, an invalidation (stop), estimated cost, uncertainty and reason. Abstention is the normal output during warm-up, below thresholds, on regime mismatch or when estimated cost exceeds a move proxy. Strategies import no storage, web, broker or execution code (tested).
- `signal_backtest`: generic event-time backtester for precomputed signals, tested as equivalent to the SMA backtester on the same signals. `extended_metrics` adds expectancy, average win/loss, median, per-trade Sharpe-like and Sortino-like ratios (not annualized), max consecutive losses and a seeded bootstrap interval for the mean trade, always with the trade count.

None of these strategies has been shown to be profitable. All parameters are illustrative and untuned, and all tests use synthetic data.

## ML baseline (lot 2)

`ml.run_purged_walk_forward` compares an L2 logistic regression with a base-rate classifier on the sign of the `h`-bar forward return. Training rows whose label window reaches into the validation block (plus `embargo_bars`) are purged, and the scaler is fitted per fold on training rows only. Each fold reports accuracy, Brier score, log loss, their base-rate equivalents, `edge_vs_baseline` (base-rate Brier minus model Brier) and calibration bins. The manifest records dataset identity, feature set and spec hash, columns, parameters, code SHA-256, Python version, periods and known limitations.

`astra ml-baseline` opens an experiment (hypothesis, mechanism, plan, variants tested) before running. It closes the experiment as `NEEDS_MORE_RESEARCH` only if every fold beats the base rate (otherwise `REJECTED`) and registers the model as an inactive `CANDIDATE`. A classification edge is not trading evidence; the candidate cannot be promoted without pre-registered trading evidence after costs. On the bundled synthetic fixture the edge is large because that fixture is artificially regular; it says nothing about markets.

## Registry and promotion gates

`registry.ModelRegistry` stores experiments (rejected ones included), immutable model versions with a hashed manifest, and promotion criteria hashed at registration. Defaults: at least 30 trades, positive net P&L after costs, profit factor at least 1.1, drawdown at most 10%, positive edge versus baseline, non-negative result under cost stress, holdout required. A holdout evaluation can be recorded once per version. `promote` requires a human reviewer and refuses, with an audit event, if any criterion fails or the stored criteria or manifest hash no longer match. `rollback` restores the most recent previously promoted version after verifying its manifest hash. Shadow status observes without ordering. These thresholds are starting values, not recommendations.
