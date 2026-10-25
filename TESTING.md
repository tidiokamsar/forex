# Testing and evidence

## Local commands

Run from the project directory in PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

The suite is standard-library `unittest`; there are no test dependencies. It should cover configuration fail-closed behavior, data provenance/quality, signal timing, transaction costs, risk denials, persistence, and read-only API behavior.

## Evidence boundary

Passing local tests prove only the functions exercised with local code and fixtures. Synthetic data is not market evidence. Execution tests run against the in-process `SimulatedBroker` with scripted faults: timeouts before and after acceptance, rejection, partial fills, disconnects, reversed event delivery, injected external positions and false environment reports. There is no broker connection, external account test, demo execution, production database, deployment, performance claim, or backup/restore proof in this delivery. The dashboard was viewed once in a desktop browser pane; no device testing was done.

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

Record the exact command, exit code, and observed summary in `ACCEPTANCE_REPORT.md`. A skipped, interrupted, or unavailable check is not a pass.
