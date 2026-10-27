# Tasks and acceptance status

Statuses: **DONE** = implemented and verified by tests run in this checkout; **PARTIAL** = implemented with stated gaps; **BLOCKED** = needs a human decision or external access; **TODO** = not implemented; **IN REVIEW** = implemented and tested by the authors, independent QA pending.

| ID | Scope | Status | Evidence / next step |
|---|---|---|---|
| ASTRA-001 | Audit workspace, stack, instructions | DONE | Initially not a Git repository; now a Git repository on top of the remote's `Initial commit` (C-02). Python 3.14.6, Node 24.18.1, Docker 29.6.2 available |
| ASTRA-002 | Architecture decision | DONE | ADR-0001, ADR-0002, ADR-0003, ADR-0004 |
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
| ASTRA-012 | Experiment registry, champion/candidate, promotion gates, rollback | DONE | `test_registry` (14) |
| ASTRA-017 | Instrument specs, size rounding, margin | PARTIAL | `instruments.py`; specs are illustrative until a broker supplies them |
| ASTRA-009a | Broker contract and simulated adapter | DONE | `brokers/`, `test_execution` |
| ASTRA-009b | cTrader Open API demo adapter (JSON/WebSocket) | PARTIAL | Built and tested offline (`test_ctrader` 32, `test_websocket` 5); read-only `ctrader-check` ready. Waiting for the user to run it on a demo account |
| ASTRA-009c | Enable DEMO order execution | BLOCKED | After a passing `ctrader-check`, confirm the assumptions in BROKERS.md, then a reviewed change to `EXECUTABLE_MODES` and RiskEngine mode support, plus a supervised one-minimum-size demo order test |
| ASTRA-010 | Durable order state, idempotency, reconciliation, kill switch, freezes | DONE | `test_execution` (29) |
| ASTRA-018 | Permission guard, secret access, two-person limit change, LLM validation | DONE | `test_governance` (8) |
| ASTRA-019 | End-to-end paper replay through the gateway | DONE | `test_engine` (6), `test_cli_operations` (6) |
| ASTRA-011 | Authentication, remote deployment, backup/restore, supervision, alert delivery | PARTIAL | Backup/restore done locally with the simulator (F-04). Authentication, remote deployment, external supervision and alert delivery not implemented |
| ASTRA-020 | Drift detection and automatic strategy disable | TODO | Needs live or replayed observation stream |
| ASTRA-021 | Autonomous improvement loop (candidate generation in sandbox) | TODO | Registry and gates exist; generation loop not built |
| ASTRA-022 | Real-mode readiness dossier | TODO | Only after DEMO evidence; no automatic activation |

## Lot 1 corrections (audit 2026-10-25)

| ID | Scope | Status | Evidence |
|---|---|---|---|
| C-01 | No direct adapter path can send without a gateway permit and a safe environment | DONE | `permits.py`, `BrokerAdapter.submit_order`; `test_ctrader.ExecutionBoundaryTests` (13), `test_execution.test_refusal_at_adapter_boundary_is_recorded_and_nothing_sent` |
| D-1 | Permit also binds order type and stop-loss and is single-use (`permit_already_used`) | DONE | `permits.py`; `test_ctrader.ExecutionBoundaryTests.test_permit_binds_stop_and_order_type_and_is_single_use` |
| D-2 | `submit_order` override check resolves through the MRO; a mixin cannot replace the boundary | DONE | `BrokerAdapter.__init_subclass__`; `test_ctrader.ExecutionBoundaryTests.test_a_mixin_cannot_replace_the_boundary_either`, `test_adapters_cannot_override_the_boundary` |
| C-02 | Git repository on top of the remote history, secret scan, `.gitattributes` | DONE | Branch `fix/lot1-securite`; remote `Initial commit` preserved |
| C-03 | Human resolution of UNKNOWN or SUBMITTED orders with evidence; freezes only lifted by reconciliation | DONE | `execution/resolution.py`, CLI `orders --resolve`; `test_lot1_fixes.ResolutionTests` (6) |
| C-04 | Duplicate events from cTrader `lookup_order` | NOT A DEFECT | The audit claim was wrong; `test_lot1_fixes.LookupEventTests` proves one event |
| C-05 | Freezes and daily-loss state per account; kill switch global | DONE | `TradingControls(scope)`, `OrderGateway(account_ref)`; `test_lot1_fixes.AccountScopedControlTests` (5) |
| C-06 | Bound RiskEngine in-memory idempotency set | TODO | Low priority; durable idempotency is in the gateway |
| C-07 | Provider request errors vs connection loss | DONE | `BrokerRequestError`, `CTraderConnectionError`; `test_lot1_fixes.ErrorClassificationTests` (5) |
| C-08 | Project ruff configuration, relevant findings fixed | DONE | `pyproject.toml [tool.ruff]`; `ruff check src tests` passes |

## Foundations (branch `feat/fondations-exploitation`)

DONE here means implemented and tested locally with the simulator, the scripted fake cTrader server or the synthetic feed only.

| ID | Scope | Status | Evidence |
|---|---|---|---|
| F-01 | Structured JSON logs: stderr, optional rotating `astra.jsonl`, correlation id, secret-field redaction, events for orders, reconciliation, freezes, kill switch, cTrader, service, backup | DONE (local) | `logs.py`; `test_logs.JsonLoggingTests` (4), `test_logs.GatewayLoggingTests` (2) |
| F-02 | Continuous trading service: loop, bar builder, deterministic intent ids, persisted state and heartbeat, reconciliation at start/periodic/after reconnect, degraded mode, graceful stop, CLI `service run\|status`; broker event cursor per account | DONE (simulator only) | `service.py`; `test_service.ServiceRunTests` (7), `test_service.BarBuilderTests` (1), `test_cli_operations.test_service_run_and_status_on_simulator`. Not wired to a real connector; no external supervisor |
| F-03 | cTrader reconnect with capped backoff, permanent vs transient errors, rate limiter, read-only `retryAfter` retry, token expiry check, store-gated refresh | DONE (offline fixture only) | `brokers/ctrader.py`; `test_ctrader_resilience.ResilienceTests` (11), `test_ctrader_resilience.RateLimiterTests` (1). No secure `TokenStore` bundled (human decision) |
| F-04 | Backup create/verify/restore with manifest, retention, human-only restore, reconciliation required after restore | DONE (local, simulator) | `backup.py`; `test_backup.BackupRestoreTests` (7). No restore drill against a real broker |

No item is marked DONE from an agent report alone: the coordinator ran the integrated suite (see ACCEPTANCE_REPORT.md).

## Lot 3 quantitative validation (branch `feat/validation-quant`)

Synthetic data only. Not DONE until the independent QA review passes.

| ID | Scope | Status | Evidence |
|---|---|---|---|
| P-02 | Generic sealed walk-forward across SMA, momentum and breakout: causal per-block signals, validation-only selection, flat and SMA reference baselines, 1.5x/2x holdout cost stress, one sealed plan per dataset per store, once-only holdout, `holdout_evidence`, CLI `evaluate-strategies` (never promotes). Mean reversion excluded | DONE (local, synthetic data only) | `strategy_validation.py`; `test_strategy_validation` (15), `test_cli_operations.test_evaluate_strategies_seals_plan_and_records_inactive_candidate` |
| Q-01 | Overfitting gates: CSCV PBO and deflated Sharpe ratio (`overfitting.py`), PBO matrix and DSR inputs from the walk-forward, registry fields `pbo`, `deflated_sharpe_probability`, `variants_tested` and criteria `max_pbo`, `min_deflated_sharpe_probability`, `require_overfitting_check`; evidence provenance, policy floor, holdout reuse refusal | DONE (local, synthetic data only) | `test_overfitting` (18), `test_registry` (14, of which 5 added in lot 3), `test_strategy_validation.OverfittingModuleIntegrationTests`. On the bundled fixture DSR is `INCOMPLETE` in development and PBO uses a sparse matrix (RESEARCH_PROTOCOL.md) |

## Lot 4 review, quantitative integrity and attempt registry (branch `feat/lot4-integrite-quant`)

Synthetic data only. Verified by independent agents (A security review, D adversarial QA), see `reports/LOT_04_REPORT.md`.

| ID | Scope | Status | Evidence |
|---|---|---|---|
| L4-A | Independent review of the holdout fingerprint and promotion floor; holdout reuse detection; report binding; re-derivation of holdout evidence from bars at promotion | DONE (local, synthetic data only); residuals documented | `holdout_reuse.py`, `storage.py`, `registry.py`; `test_lot4_security_review`, `test_lot4_integration` |
| L4-B | PBO/CSCV and DSR checked against the papers: mark-to-market matrix, CSCV with the ASTRA selection rule, conservative ties, `TrialUniverse`; trade-count and distinguishable-variant guards; DSR on fixed-interval equity returns with Lo (2002) effective sample size and Ljung-Box gate; PBO/DSR re-derived at promotion | DONE (local, synthetic data only) | `overfitting.py`, `strategy_validation.py`, `docs/references/QUANT_REFERENCES.md`; `test_overfitting`, `test_strategy_validation` |
| L4-C | Append-only, hash-chained attempt registry; store-wide trial count fed into the holdout DSR and evidence; CLI `attempts list\|count\|verify` | DONE (local) | `attempts.py`; `test_attempts` |
| L4-E | Evidence anchoring interface outside SQLite, store-to-anchor binding, LocalFileAnchor (not production grade); versioned research policy 0.5.0; reuse verdicts CONFLICT/SUSPECT/UNDETERMINED/CLEAR and calibration; data provenance gate (SYNTHETIC/unknown not promotable) | DONE (local); external WORM adapter NOT implemented | `anchoring.py`, `research_policy.py`, `holdout_reuse.py`; `test_anchoring`, `test_holdout_reuse_calibration`, `test_lot4_integration` |
| L4-D | Adversarial and verification suites (five review phases by agents A and D) | DONE | `test_lot4_adversarial`, `test_lot4_verification` |
| L4-R | Residuals: A4-D-3 new store plus new anchor; R-2 declared (not attested) data labels; B-8 restore without anchor; C-4, A5-1, A-5, B-2, B-5, D-11, N-6, N-7, Q-3 | OPEN (documented, expected-failure tests) | `reports/LOT_04_REPORT.md` sections 4 and 11 |

## Multi-venue lot (branch `feat/multi-venue`)

Offline and research only; no venue contacted by code, no account, no key, no order. See `reports/MULTI_VENUE_REPORT.md`.

| ID | Scope | Status | Evidence |
|---|---|---|---|
| MV-R | Guinea eligibility research on official sources (Forex/CFD, Binance, IQ Option, GoForex) | DONE (public sources, 2026-10-26); human confirmations open | `docs/venues/research_forex.md`, `docs/venues/research_crypto_other.md` |
| MV-C | Read-only common contract with explicit capability gaps; per-market data models (futures and options disabled) | DONE (local) | `venues/contract.py`, `markets/*`; `test_venues_contract`, `test_markets` |
| MV-REG | Versioned venue registry (0.3.0, 15 venues), cumulative statuses, strict loader, all gates closed | DONE (local) | `venues/registry.py`, `venue_registry.json`, VENUE_REGISTRY.md; `test_venue_registry` |
| MV-BIN | Binance Spot public-data connector and read-only account reader (GET only, provider-verified read-only key) | DONE (offline only); NOT READ_ONLY_CONNECTED | `venues/binance_spot.py`; `test_binance_spot` |
| MV-RISK | Per-market risk policies and cost models (spot simulation only, derivatives refused) | DONE (local) | `markets/risk_policies.py`, `markets/costs.py`; `test_market_risk` |
| MV-QA | Independent security (A) and QA (D) reviews, fixes MV-A-1..16, A-17, A-18, MV-D-1..19 | DONE: final verdicts on 9584f46 favourable, re-review of the fixes on 0d6f084 favourable; no critical/high/medium open; low residuals documented; no expected failure left in the multi-venue tests | `test_multi_venue_security_review`, `test_multi_venue_verification`, `test_market_risk`, `test_binance_spot` |
| MV-CLOSE | Closing checks on 0d6f084: Ruff, compileall, secret scan, 260 targeted tests, full suite 937 tests OK (15 expected failures, all Lot 4) | DONE; awaiting the architect's checkpoint evaluation (lot NOT validated yet) | `reports/MULTI_VENUE_REPORT.md` section 7 |
| MV-OPEN | Live read-only checks (cTrader, Binance), written eligibility confirmations, IBKR adapter, derivatives risk review, Ed25519 signing | TODO (human actions first) | `reports/MULTI_VENUE_REPORT.md` section 8 |

## Gate B, repository protection and CI (branch `feat/multi-venue`)

| ID | Scope | Status | Evidence |
|---|---|---|---|
| GB-AUDIT | Initial Git and GitHub audit; secrets audit of the tree and every commit | DONE: clean (env-var names, fake fixtures and placeholders only) | `reports/GATE_B_REPORT.md` |
| GB-PROTECT | `main` branch protection, secret scanning, push protection, Actions restrictions | DONE (verified by API read-back) | `reports/GATE_B_REPORT.md` |
| GB-CI | Workflow `ci.yml` (quality, plan, full-suite), `tools/ci/secret_scan.py`, `tools/ci/check_unittest_log.py`, `docs/CI.md` | Written and reviewed; NOT operational: no CI run has executed (GitHub account locked for a billing issue) | `.github/workflows/ci.yml`, `docs/CI.md` |
| GB-RUN | A real, successful CI run on the final commit | BLOCKED (owner must clear the billing lock, then re-run) | GitHub Actions |

## Gate Auth and deployment (branch `feat/auth-deploy`)

| ID | Scope | Status | Evidence |
|---|---|---|---|
| AUTH-CORE | Accounts, Argon2id, server-side sessions, TOTP MFA, recovery codes, device-cookie throttling, proxy secret, append-only audit | DONE (offline tests, 61 web tests, 994 in the full suite on d738460); four independent review rounds, no critical/high/medium open | `src/astra/auth/`, `tests/test_webapp.py` |
| AUTH-WEB | Flask app replacing the stdlib server: Host/Origin/CSRF, roles per route, headers, no trading mutation | DONE (offline tests) | `src/astra/webapp.py` |
| AUTH-DEPLOY | gunicorn, hardened systemd, alert and certificate-check units, nginx and Apache examples, nftables loopback rule | Written and reviewed; NOT installed anywhere | `deploy/`, DEPLOYMENT.md |
| AUTH-HOST | Deployment on 102.211.199.132 | BLOCKED: read-only inspection only (user decision); no sudo without password; Python 3.12 only; 80/443 and the IP belong to GLPI; partially remediated intrusion | `reports/AUTH_AND_DEPLOYMENT_REPORT.md` |
| AUTH-TLS | Valid public certificate and renewal for a dedicated DNS name | BLOCKED: no DNS name exists for ASTRA | same |

