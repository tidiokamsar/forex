# Tasks and acceptance status

Statuses: **DONE** = implemented and verified by tests run in this checkout; **PARTIAL** = implemented with stated gaps; **BLOCKED** = needs a human decision or external access; **TODO** = not implemented.

| ID | Scope | Status | Evidence / next step |
|---|---|---|---|
| ASTRA-001 | Audit workspace, stack, instructions | DONE | Not a Git repository; Python 3.14.6, Node 24.18.1, Docker 29.6.2 available |
| ASTRA-002 | Architecture decision | DONE | ADR-0001, ADR-0002, ADR-0003 |
| ASTRA-003 | Simulation-only configuration, independent risk gate | DONE | `test_config`, `test_risk` |
| ASTRA-004 | CSV provenance and quality checks | DONE | `test_data` |
| ASTRA-005 | Cost-aware SMA event-time simulation | DONE | `test_research` |
| ASTRA-006 | Experiment and audit persistence | DONE | `test_storage` |
| ASTRA-007 | French read-only dashboard | DONE | `test_web`, `test_web_operations`; visual check in desktop browser pane |
| ASTRA-008 | Walk-forward, baselines, cost stress, protected holdout | DONE (reservations) | `test_validation`, `test_cli`; local lock is advisory |
| ASTRA-013 | Causal feature library, train-only scaler | DONE | `test_features` (17) |
| ASTRA-014 | Regime estimates with confidence | PARTIAL | `test_regimes`; transition and event-calendar regimes not implemented |
| ASTRA-015 | Strategy families with structured decisions and abstention | DONE | `test_strategies`, `test_signal_backtest`; none shown profitable |
| ASTRA-016 | Baseline ML with purged walk-forward and calibration | DONE | `test_ml`, `ml-baseline` CLI test; no final holdout inside the ML run |
| ASTRA-012 | Experiment registry, champion/candidate, promotion gates, rollback | DONE | `test_registry` (9) |
| ASTRA-017 | Instrument specs, size rounding, margin | PARTIAL | `instruments.py`; specs are illustrative until a broker supplies them |
| ASTRA-009a | Broker contract and simulated adapter | DONE | `brokers/`, `test_execution` |
| ASTRA-009b | cTrader Open API demo adapter (JSON/WebSocket) | PARTIAL | Built and tested offline (`test_ctrader` 18, `test_websocket` 5); read-only `ctrader-check` ready. Waiting for the user to run it on a demo account |
| ASTRA-009c | Enable DEMO order execution | BLOCKED | After a passing `ctrader-check`, confirm the assumptions in BROKERS.md, then a reviewed change to `EXECUTABLE_MODES` and RiskEngine mode support, plus a supervised one-minimum-size demo order test |
| ASTRA-010 | Durable order state, idempotency, reconciliation, kill switch, freezes | DONE | `test_execution` (28) |
| ASTRA-018 | Permission guard, secret access, two-person limit change, LLM validation | DONE | `test_governance` (8) |
| ASTRA-019 | End-to-end paper replay through the gateway | DONE | `test_engine` (6), `test_cli_operations` (4) |
| ASTRA-011 | Authentication, remote deployment, backup/restore, supervision, alert delivery | TODO | Next lot; restore test required before any claim |
| ASTRA-020 | Drift detection and automatic strategy disable | TODO | Needs live or replayed observation stream |
| ASTRA-021 | Autonomous improvement loop (candidate generation in sandbox) | TODO | Registry and gates exist; generation loop not built |
| ASTRA-022 | Real-mode readiness dossier | TODO | Only after DEMO evidence; no automatic activation |

No item is marked DONE from an agent report alone: the coordinator ran the integrated suite (see ACCEPTANCE_REPORT.md).
