# Roadmap

Mapped to the specification's phases (section 14). Gates are not skipped.

- [x] Phase 0: audit and technical framing (ADR-0001).
- [x] Phase 1: software base and data (config, CSV quality, provenance).
- [x] Phase 2: research and backtesting (SMA baseline, walk-forward, holdout, cost stress, strategy families, extended metrics).
- [x] Phase 3: ML and experiment registry (purged walk-forward baseline, registry, pre-registered gates, rollback). Reservation: no trading-evidence pipeline from ML probabilities to backtests yet.
- [x] Phase 4: risk engine and simulated execution (order gateway, state machine, reconciliation, controls, paper replay).
- [~] Phase 5: first demo connector. cTrader Open API selected; adapter built and tested offline, read-only `ctrader-check` ready. **Waiting for the user to run the check on a demo account**; then confirm assumptions, enable DEMO by reviewed change, run a supervised minimum-size demo order.
- [~] Phase 6: dashboard and observability. Dashboard shows orders, controls, reconciliations, registry. Missing: metrics endpoint, structured log output, alert delivery.
- [ ] Phase 7: controlled autonomous improvement (candidate generation in a sandbox, shadow comparison, drift-triggered rollback).
- [ ] Phase 8: additional connectors.
- [ ] Phase 9: real-mode preparation dossier (no automatic activation).
- [ ] Phase 10: supervised operation.

Lot 1 corrections (audit 2026-10-25) are done: execution permits at the adapter boundary, human resolution of undetermined orders, per-account freezes, request-error classification, ruff. C-06 (bound the RiskEngine in-memory idempotency set) remains open.

## Next concrete tasks

1. Backup and verified restore of the SQLite store; structured JSON logs; supervised service runner.
2. Turn strategy decisions and ML probabilities into walk-forward trading evidence that can feed `record_evaluation`.
3. Drift monitor (data, costs, rejection rate, calibration) that places a strategy in observation and triggers `rollback`.
4. Broker already chosen (cTrader Open API) and its adapter built and tested offline. After the user runs `ctrader-check` on a demo account: confirm the BROKERS.md assumptions, enable DEMO by reviewed change, then a supervised minimum-size demo test.
5. C-06: bound the RiskEngine in-memory idempotency set.
