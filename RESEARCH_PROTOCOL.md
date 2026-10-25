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

`registry.ModelRegistry` stores experiments (rejected ones included), immutable model versions with a hashed manifest, and promotion criteria hashed at registration. Defaults: at least 30 trades, positive net P&L after costs, profit factor at least 1.1, drawdown at most 10%, positive edge versus baseline, non-negative result under cost stress, holdout required, and (lot 3) PBO at most 0.3 and deflated Sharpe probability at least 0.95 with the overfitting check required. A holdout evaluation can be recorded once per version. `promote` requires a human reviewer and refuses, with an audit event, if any criterion fails or the stored criteria or manifest hash no longer match. `rollback` restores the most recent previously promoted version after verifying its manifest hash. Shadow status observes without ordering. These thresholds are starting values, not recommendations.

## Quantitative validation (lot 3)

Status: implemented on branch `feat/validation-quant`; independent QA review pending. Nothing here is accepted until that review and ACCEPTANCE_REPORT.md say so.

### Overfitting statistics (`overfitting`, `astra-overfitting-1.0.0`)

Pure Python, deterministic, no randomness.

- `probability_of_backtest_overfitting(performance, n_slices=8, metric=None)`: CSCV (Bailey, Borwein, Lopez de Prado, Zhu 2014). `performance[t][v]` is the per-period net return or P&L of variant `v` in period `t`, in chronological order. Rows are split into `n_slices` contiguous blocks `[s*T//S, (s+1)*T//S)` (all rows used, sizes differ by at most one). Every combination of `n_slices / 2` blocks is in-sample once, its complement out-of-sample. The best in-sample variant by the Sharpe-like metric (`mean / sample stdev`, `0.0` for a zero-dispersion window; ties go to the lowest index) is ranked out-of-sample (ascending, ties take the average rank), `w = rank / (N + 1)`, `lambda = ln(w / (1 - w))`. PBO is the fraction of combinations with `lambda <= 0`, so a selection tied with the OOS median counts as overfit. Requirements: at least 2 variants, `n_slices` even and at least 2, at least 2 rows per slice, at most `MAX_COMBINATIONS` (20,000) combinations, a finite metric.
- `deflated_sharpe_ratio(returns, n_trials, trial_sharpe_variance=None)`: Bailey and Lopez de Prado 2014. Per-period Sharpe (`mean / sample stdev`, not annualized), population (biased) skewness, non-excess kurtosis (normal = 3). The benchmark is the expected maximum Sharpe of `n_trials` null trials with variance `trial_sharpe_variance`, default `1 / (T - 1)`. With `n_trials = 1` it is the probabilistic Sharpe ratio against 0. Requirements: at least 3 finite returns, non-zero variance, a positive standard-error term.
- Inputs fail closed: ragged rows, non-finite or non-numeric values, booleans, strings and integers below the minimum raise `ValueError` or `TypeError`; no value is silently replaced.

### Multi-family walk-forward (`strategy_validation`, version `1.0.0`)

- Families: `sma`, `momentum`, `breakout`. Mean reversion is excluded because it needs regime thresholds fitted on each fold's training slice, which this protocol version does not implement; the exclusion is written into the plan.
- `default_candidates()` is an illustrative, untuned grid of 7 variants (3 SMA, 2 momentum, 2 breakout), not a recommendation. `candidate_id` is the SHA-256 of the canonical family, strategy name, strategy version and config. At least 2 unique candidates are required, at most `validation.MAX_CANDIDATES`, and `minimum_training_bars` must cover every candidate's and the reference's warm-up.
- Folds follow `WalkForwardProtocol`. Each block is evaluated causally: signals are computed from bars up to the block end only, then simulated with `run_signal_backtest` from the block start.
- Selection uses validation metrics only (validation selection score, eligibility by `minimum_candidate_trades`, tie-break by validation net P&L then candidate id). The final candidate has the best mean validation score across folds. Every candidate is also scored on the test blocks, after the fold's selection, only to build the PBO matrix.
- PBO matrix: one row per `PBO_PERIOD_BARS` (5) bar period of each development window (fold validation, then fold test, in order), one column per candidate; each trade's net P&L goes to the period of its exit time. `n_slices` is the largest even number not above `min(8, rows // 2)`, so every slice has at least two rows.
- DSR inputs: per-trade net return (net P&L / initial equity) of the final selected candidate on the development test windows, plus its holdout trades once the holdout is evaluated; `n_trials` = variants tested; `trial_sharpe_variance` = sample variance across candidates of the per-trade Sharpe-like ratio on validation windows (candidates with fewer than 2 trades or zero dispersion are omitted; with fewer than 2 values the module default applies).
- Overfitting status: `COMPLETED` when both statistics were computed; `LOW_INFORMATION` when a minimum-information guard refused to compute one (fewer than `MIN_DSR_RETURNS` = 30 returns for the deflated Sharpe, or a PBO matrix with fewer than `MIN_PBO_NONZERO_ROW_FRACTION` = 50% non-zero rows or fewer distinct rows than slices); `INCOMPLETE` when a computation raised (errors recorded); `UNAVAILABLE` when `astra.overfitting` cannot be imported. Missing values stay `None` and keep the registry gates unmet.
- Zero variance is tested with a relative tolerance (`1e-9` of the values' scale): float noise no longer produces enormous Sharpe ratios (it is treated as zero variance).
- Baselines: flat `NO_POSITION@1` and the SMA reference (`SmaBaselineConfig()` defaults). `edge_vs_baseline` = selected net P&L minus the larger of the flat and reference net P&L.
- Cost stress: on the holdout, signals keep base costs and simulated spread, slippage and commission are scaled by each protocol multiplier (default 1.0, 1.5, 2.0). `cost_stress_net_pnl` is the worst selected net P&L among multipliers above 1.0.
- Sealing: with a store, the plan is sealed before any window is scored. One plan per canonical dataset identity per store; the table is shared with `walk-forward`, so a different plan for the same bars in the same store is refused whichever command sealed first. The holdout is scored once: selection is recomputed from bars and must reproduce the sealed plan, a `STARTED` record is written first, a completed retry returns the stored report, and a `STARTED` or failed attempt stays consumed.
- The implementation hash covers `models`, `research`, `risk`, `features`, `regimes`, `strategies`, `signal_backtest`, `validation`, `strategy_validation` and `overfitting.py`; a change after sealing makes the holdout refuse to run.
- `holdout_evidence` returns exactly the registry `EVIDENCE_FIELDS`.

### Registry gates

New evidence fields: `pbo`, `deflated_sharpe_probability`, `variants_tested`. New criteria: `max_pbo` (0.3), `min_deflated_sharpe_probability` (0.95), `require_overfitting_check` (true). New failures: `overfitting_check_missing` (any of the three missing or non-finite, or `variants_tested < 1`), `pbo_above_threshold`, `deflated_sharpe_below_threshold`. Criteria stored before lot 3 lack these keys and load with the new defaults, so legacy candidates without overfitting evidence fail closed. The check can be disabled only at registration (`require_overfitting_check=False`).

### Evidence provenance

Holdout evidence is never trusted as numbers. `record_evaluation(kind="holdout")` requires an authorized actor and the identifier of the stored holdout evaluation. At promotion, `verify_holdout_provenance` requires that this evaluation is `COMPLETED`, belongs to the plan (`plan_sha256`) and selected the candidate (`candidate_id`) recorded in the version manifest, started **after** the version was registered (criteria frozen before the holdout was seen), and that the evidence recomputed from the stored report equals the recorded values. Failures: `evidence_provenance_missing`, `holdout_evaluation_not_found_or_incomplete`, `holdout_from_another_plan`, `holdout_selected_another_candidate`, `criteria_registered_after_holdout_was_scored`, `evidence_does_not_match_holdout_report`. The CLI refuses `--register` once the plan's holdout has been scored. Limit: whoever can rewrite the SQLite file could forge both the report and the evidence.

### CLI `evaluate-strategies`

Flags: `--csv`, `--source-type` (required with `--csv`), `--source-name`, `--interval-seconds`, `--folds` (2), `--minimum-training-bars` (60), `--validation-bars` (20), `--holdout-bars` (20), `--minimum-candidate-trades` (1), `--evaluate-holdout`, `--register` (requires `--evaluate-holdout`, `--model-id`, `--version`). Without `--csv` it uses the bundled synthetic fixture after its SHA-256 check. It opens an experiment before running and closes it as `NEEDS_MORE_RESEARCH` or `REJECTED` (failed runs are kept as rejected). With `--register` it records an inactive `CANDIDATE` with default criteria frozen before the holdout is scored, then the holdout evidence, and returns the `promotion_blockers`. It never promotes, returns `external_orders: false`, and adds a `SYNTHETIC` warning for synthetic data.

### Limitations

- Synthetic data only; nothing here is evidence about real markets.
- Small samples make PBO and DSR weak. With CLI defaults on the bundled fixture the assessment is now `LOW_INFORMATION` (a handful of trades; the 16 x 7 PBO matrix is mostly zero rows), so `pbo` and `deflated_sharpe_probability` are not reported and the gate stays unmet. Before the guards (independent QA, 2026-10-25), the same run reported PBO 0.0 and a DSR of 0.99997 from 3 near-identical trades: both numbers were meaningless.
- PBO uses P&L realised at trade exit per 5-bar period (structural zeros and ties), and ranks variants by a per-period Sharpe-like ratio, while the actual selection rule is validation net P&L minus drawdown cash. Mark-to-market periods and a CSCV metric aligned with the selection rule are open improvements.
- Holdout reuse: a new plan whose holdout period overlaps an already sealed holdout in the same store is refused, whatever the dataset identity (plans do not record the instrument yet, so the check is conservative across instruments).
- Thresholds are starting values, not recommendations.
- The SQLite seal and holdout lock are advisory: the store owner can edit or replace the file or point `ASTRA_DATABASE` elsewhere, and direct API calls can inspect test windows outside the seal.
- PBO counts only the declared variants; DSR depends on an honest `n_trials` and asymptotic approximations.
