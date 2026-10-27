# Roadmap

Mapped to the specification's phases (section 14). Gates are not skipped.

- [x] Phase 0: audit and technical framing (ADR-0001).
- [x] Phase 1: software base and data (config, CSV quality, provenance).
- [x] Phase 2: research and backtesting (SMA baseline, walk-forward, holdout, cost stress, strategy families, extended metrics). Lot 3 (done, branch `feat/validation-quant`): sealed walk-forward shared by the SMA, momentum and breakout families, with flat and SMA reference baselines and holdout cost stress; mean reversion not yet covered.
- [x] Phase 3: ML and experiment registry (purged walk-forward baseline, registry, pre-registered gates, rollback). Reservation: no trading-evidence pipeline from ML probabilities to backtests yet. Lot 3 (done): PBO (CSCV) and deflated Sharpe gates in the registry; strategy holdout evidence can feed `record_evaluation` through `evaluate-strategies --register`.
- [x] Phase 4: risk engine and simulated execution (order gateway, state machine, reconciliation, controls, paper replay).
- [~] Phase 5: first demo connector. cTrader Open API selected; adapter built and tested offline, read-only `ctrader-check` ready. **Waiting for the user to run the check on a demo account**; then confirm assumptions, enable DEMO by reviewed change, run a supervised minimum-size demo order.
- [~] Phase 6: dashboard and observability. Dashboard shows orders, controls, reconciliations, registry. Structured JSON logs exist locally (F-01, branch `feat/fondations-exploitation`). Missing: metrics endpoint, log shipping, alert delivery, external service supervision.
- [ ] Phase 7: controlled autonomous improvement (candidate generation in a sandbox, shadow comparison, drift-triggered rollback).
- [~] Phase 8: additional connectors. Multi-venue lot (branch `feat/multi-venue`): eligibility research, venue registry, read-only contract, per-market models and risk, Binance Spot read-only connector tested offline only. Independent reviews closed with no critical/high/medium finding open and full suite green on 0d6f084 (offline); awaiting the architect's checkpoint, not validated. No venue is integrated or connected; Interactive Brokers recommended as second Forex broker pending written confirmation; IQ Option and GoForex blocked.
- [ ] Phase 9: real-mode preparation dossier (no automatic activation).
- [~] Gate Auth and deployment (branch `feat/auth-deploy`): authenticated dashboard (accounts, sessions, TOTP MFA, roles, CSRF), offline tests and independent reviews done; hosting blocked (no qualified host, no DNS name, no sudo on 102.211.199.132). See `reports/AUTH_AND_DEPLOYMENT_REPORT.md`.
- [ ] Phase 10: supervised operation.
- [~] V5 Gate B (repository protection and CI, branch `feat/multi-venue`): `main` protected (pull request, required checks `quality` and `full-suite`, no force push or deletion, rules enforced for admins), secret scanning and push protection on, Actions limited to SHA-pinned GitHub actions, CI workflow and secret scan added. Not complete until a real CI run succeeds: the first runs were refused by GitHub because the account is locked for a billing issue (owner action). See `reports/GATE_B_REPORT.md`. Production readiness: `reports/PRODUCTION_READINESS_REPORT.md` (matrix, plan P1 to P7, architect decisions).

Lot 1 corrections (audit 2026-10-25) are done: execution permits at the adapter boundary, human resolution of undetermined orders, per-account freezes, request-error classification, ruff. C-06 (bound the RiskEngine in-memory idempotency set) remains open.

## Next concrete tasks

1. [x] Done on branch `feat/fondations-exploitation`, tested locally only (simulator, fake cTrader server, synthetic feed): structured JSON logs (F-01), continuous service on the simulator (F-02), cTrader reconnection, rate limiting and token handling (F-03), verified SQLite backup and restore (F-04). Still open: external supervision (restart, start at boot), alert delivery, scheduled backups, wiring the service to the cTrader adapter once DEMO is enabled, a secure token store (human decision).
2. Lot 3 (P-02, Q-01) done; Lot 4 (branch `feat/lot4-integrite-quant`) adds the attempt registry, mark-to-market PBO, re-derivation of holdout evidence at promotion and holdout reuse detection, all on synthetic data. An evidence-anchor interface with a local, non-production adapter exists; next for research integrity: an external immutable (WORM / Object Lock) adapter, attested real data (V5 gates C and D). Strategy walk-forward evidence now feeds `record_evaluation`; still open: ML probabilities as trading evidence, mean reversion with per-fold fitted regime thresholds, a real (non-synthetic) dataset large enough for meaningful PBO and DSR, and calibrating the starting thresholds.
3. Drift monitor (data, costs, rejection rate, calibration) that places a strategy in observation and triggers `rollback`.
4. Broker already chosen (cTrader Open API) and its adapter built and tested offline. After the user runs `ctrader-check` on a demo account: confirm the BROKERS.md assumptions, enable DEMO by reviewed change, then a supervised minimum-size demo test.
5. C-06: bound the RiskEngine in-memory idempotency set.
