# Testing and evidence

## Local commands

Run from the project directory in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements/web.txt   # web tests (test_webapp)
$env:PYTHONPATH = "src"
.venv\Scripts\python -m unittest discover -s tests -v
python -m compileall -q src tests
node --check src/astra/static/app.js
python -m ruff check src tests
```

The suite is standard-library `unittest`; there are no test dependencies. Ruff is a separately installed development tool configured in `pyproject.toml`; it is not a runtime or test dependency. It should cover configuration fail-closed behavior, data provenance/quality, signal timing, transaction costs, risk denials, persistence, and read-only API behavior.

## Continuous integration

The same checks are defined to run in GitHub Actions (`.github/workflows/ci.yml`); as of Gate B no CI run has executed, because GitHub refuses to start jobs while the account is locked for a billing issue. The checks are: Ruff, compileall, `node --check`, the secret scan, the targeted multi-venue tests and the full suite, followed by a strict check of the expected failures. [docs/CI.md](docs/CI.md) lists the triggers, the required checks and the exact commands to reproduce a run locally. A CI run is offline like the local suite: it is not evidence about any broker, exchange or market.

## Evidence boundary

Passing local tests prove only the functions exercised with local code and fixtures. Synthetic data is not market evidence. Execution tests run against the in-process `SimulatedBroker` with scripted faults: timeouts before and after acceptance, rejection, partial fills, disconnects, reversed event delivery, injected external positions and false environment reports. The cTrader adapter is tested against a scripted fake cTrader server (`tests/ctrader_fixture.py`, labelled as not recorded provider traffic) and the WebSocket client against a loopback WebSocket server; both are local and offline, and no test reaches the real cTrader service. The foundations tests are also local: `test_logs` (in-memory streams and a temporary log folder), `test_backup` (temporary SQLite files and the simulator), `test_ctrader_resilience` (the same fake cTrader server with scripted one-shot errors, injected sleep and clock) and `test_service` (simulator with the `SYNTHETIC` quote feed). There is no broker connection, external account test, demo execution, production database, deployment, supervised long-running service, performance claim, or restore drill against a real broker in this delivery. The dashboard was viewed once in a desktop browser pane; no device testing was done.

## Mandatory negative tests (spec 15.2)

| # | Case | Tests |
|---|---|---|
| 1 | No mode configured: no external order | `test_config`, `test_cli.test_status_reports_simulation_and_no_broker`, `test_execution.test_default_gateway_mode_is_simulation` |
| 2 | Declared demo, provider reports live | `test_execution.test_declared_demo_but_account_live_blocks`, `test_account_reporting_live_blocks_and_freezes` |
| 3 | Unknown account status | `test_execution.test_unknown_account_environment_blocks` |
| 4 | Stale data | `test_execution.test_stale_quote_blocks`, `test_risk` |
| 5 | Spread above limit | `test_execution.test_extreme_spread_blocks` |
| 6 | Daily risk exceeded | `test_execution.test_daily_loss_limit_blocks_new_entries` |
| 7 | Duplicate after timeout | `test_execution.test_timeout_after_accept_is_not_blindly_retried`, `test_timeout_before_accept_resolves_to_not_received` |
| 8 | Network loss with open position | `test_execution.test_connection_loss_freezes_entries_until_reconciled`, `test_reconnection_with_divergent_position_keeps_freeze` |
| 9 | Invalid LLM response | `test_governance.test_invalid_llm_output_means_no_action`, `test_execution.test_llm_and_strategy_roles_cannot_submit` |
| 10 | Agent reads a secret or changes a limit | `test_governance.test_agent_secret_read_is_refused_and_audited`, `test_agent_cannot_modify_risk_limits` |
| 11 | Unvalidated candidate promotion | `test_registry.test_unvalidated_candidate_cannot_be_promoted`, `test_agent_cannot_promote` |
| 12 | Rollback to a known version | `test_registry.test_promotion_then_rollback_restores_known_version` |

No-lookahead coverage: `test_features`, `test_regimes`, `test_strategies` and `test_ml` mutate bars after index `i` and assert nothing at or before `i` changes; `test_engine.test_decision_function_sees_only_past_bars` checks the replay.

## Lot 1 corrections evidence

| Item | Tests |
|---|---|
| C-01, D-1, D-2 execution boundary (permit, environment guard, MRO check) | `test_ctrader.ExecutionBoundaryTests`, `test_execution.test_refusal_at_adapter_boundary_is_recorded_and_nothing_sent` |
| C-03 human resolution | `test_lot1_fixes.ResolutionTests` |
| C-04 single event from cTrader `lookup_order` | `test_lot1_fixes.LookupEventTests` |
| C-05 per-account freezes and daily-loss state | `test_lot1_fixes.AccountScopedControlTests` |
| C-07 request errors versus connection loss | `test_lot1_fixes.ErrorClassificationTests` |

## Foundations evidence (F-01 to F-04)

| Item | Tests |
|---|---|
| F-01 structured logs, correlation id, redaction, gateway events | `test_logs.JsonLoggingTests`, `test_logs.GatewayLoggingTests` |
| F-02 continuous service, bar builder, restart idempotency, reconnect, degraded mode | `test_service.ServiceRunTests`, `test_service.BarBuilderTests`, `test_cli_operations.test_service_run_and_status_on_simulator` |
| F-03 cTrader reconnect, permanent errors, rate limit, token handling | `test_ctrader_resilience.ResilienceTests`, `test_ctrader_resilience.RateLimiterTests` |
| F-04 backup, verify, restore, stale backup, human-only restore | `test_backup.BackupRestoreTests` |

## Lot 3 quantitative validation evidence (in review)

Independent QA pending. All inputs are synthetic or hand-built matrices.

| Item | Tests |
|---|---|
| Q-01 PBO: hand-computed logits, floor bounds, ties, zero-dispersion windows, noise vs persistent edge, determinism, invalid inputs, combination cap | `test_overfitting.HandComputedPBOTest`, `StatisticalBehaviourPBOTest`, `PBOValidationTest` |
| Q-01 deflated Sharpe: PSR at `n_trials = 1`, more trials lower probability, numeric check, expected maximum, invalid inputs, JSON output | `test_overfitting.DeflatedSharpeTest`, `SerializationTest` |
| Q-01 registry overfitting gates, legacy opt-out only at registration | `test_registry.test_missing_overfitting_evidence_blocks_promotion`, `test_high_pbo_blocks_promotion`, `test_low_deflated_sharpe_probability_blocks_promotion`, `test_overfitting_gate_can_be_disabled_only_at_registration`, `test_good_evidence_meets_every_gate` |
| P-02 trials per family, candidate id, fold layout, no lookahead, validation-only selection, holdout isolation, PBO matrix order and slice count, `UNAVAILABLE` without the statistics module | `test_strategy_validation.StrategyWalkForwardTests` |
| P-02 sealed plan, once-only and failed holdout attempts, dataset mismatch, evidence fields feeding the gates | `test_strategy_validation.SealedHoldoutTests` |
| P-02/Q-01 real statistics module on the development matrix | `test_strategy_validation.OverfittingModuleIntegrationTests` |
| CLI `evaluate-strategies`: register requires holdout, inactive candidate, blockers, retry returns stored holdout, second plan refused | `test_cli_operations.test_evaluate_strategies_seals_plan_and_records_inactive_candidate` |

## Lot 4 research-integrity evidence

All inputs are synthetic, hand-built or produced by the local simulator; nothing ran against real market data or a broker.

| Item | Tests |
|---|---|
| Independent security review (agent A): fingerprint evasion, legacy plans, minted holdouts, verifier override, detector evasions, restore | `test_lot4_security_review` |
| Adversarial baseline and scenarios (agent D) | `test_lot4_adversarial` |
| Verification of the integrated fixes (agent D) | `test_lot4_verification` |
| Re-derivation of holdout evidence from bars, anchored promotion, restore of an older backup, reuse verdicts | `test_lot4_integration` |
| Reuse detector calibration (positive and negative synthetic sets) | `test_holdout_reuse_calibration` |
| Attempt registry: recording, statuses, store-wide count, tamper detection | `test_attempts` |
| Evidence anchor: checkpoint chain, file tampering, restore, chain rewrite | `test_anchoring` |
| PBO/CSCV with the selection rule, DSR paper example, trial universe, guards | `test_overfitting`, `test_strategy_validation` |

Remaining expected failures document open residuals; each carries a `CONFIRMED DEFECT` or `RESIDUAL` comment, and an unexpected success means a marker must be removed after checking.

Record the exact command, exit code, and observed summary in `ACCEPTANCE_REPORT.md`. A skipped, interrupted, or unavailable check is not a pass.
