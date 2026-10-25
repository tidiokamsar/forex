# Testing and evidence

## Local commands

Run from the project directory in PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall -q src tests
node --check src/astra/static/app.js
python -m ruff check src tests
```

The suite is standard-library `unittest`; there are no test dependencies. Ruff is a separately installed development tool configured in `pyproject.toml`; it is not a runtime or test dependency. It should cover configuration fail-closed behavior, data provenance/quality, signal timing, transaction costs, risk denials, persistence, and read-only API behavior.

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

Record the exact command, exit code, and observed summary in `ACCEPTANCE_REPORT.md`. A skipped, interrupted, or unavailable check is not a pass.
