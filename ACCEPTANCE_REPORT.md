# Acceptance report

## Lot 4 research integrity (branch `feat/lot4-integrite-quant`, 2026-10-25/26)

Scope: independent review of the Lot 3 holdout fixes, attempt registry, PBO/CSCV and DSR aligned with the real selection and with serial dependence, re-derivation of holdout evidence at promotion, holdout reuse detection with calibration, evidence anchor interface (local adapter, not production grade), versioned research policy 0.5.0, data provenance gate. Synthetic data only; no order sent, DEMO disabled, LIVE locked, no connection to cTrader.

Verification on e557506: ruff `All checks passed!`; compileall exit 0; `Ran 675 tests in 1185.616s`, `OK (expected failures=15)`. After b3da3cc (CPU-time seal test, phase 5b tests): `test_lot4_verification` and `test_lot4_security_review` `OK (expected failures=14)`. Every expected failure is a documented residual (reports/LOT_04_REPORT.md section 4).

Independent reviews: security agent A and QA agent D, five phases each, neither wrote the code they reviewed; final verdicts favourable for exploratory research tooling, no critical or high finding open; not a promotion-grade guarantee (needs an external production anchor, attested data provenance and authenticated human roles). Secret scan before publication: clean (38 changed files; no secret, database, anchor or key tracked; `.env.example` holds empty values only).


## Lot 1 corrections (branch `fix/lot1-securite`, 2026-10-25)

Scope: C-01, C-03, C-05, C-07, C-08 fixed; C-04 shown not to be a defect; Git initialised on top of the remote's `Initial commit`. No order was sent, DEMO execution was not enabled, LIVE stays locked. No connection to any external broker was made.

Publication checks before push: secret scan of every tracked file (token, key, private-key, credential-assignment patterns, `.env` files, personal data) found nothing; the only credential-like strings are labelled fakes in `tests/ctrader_fixture.py` and `tests/test_ctrader.py`. `.gitignore` excludes `.env*` (except `.env.example`), SQLite files, logs, backups and keys. `.gitattributes` keeps `data/` byte-exact: the fixture SHA-256 in the index is `16402dc1...5ef8`, identical to the pinned value.

Verification (bash, project directory, after all lot 1 code changes):

```bash
python -m ruff check src tests              # All checks passed!
PYTHONPATH=src python -m unittest discover -s tests
python -m compileall -q src tests
node --check src/astra/static/app.js
```

Observed at the first publication (`ab843d1`): ruff `All checks passed!`; `Ran 245 tests in 93.941s`, `OK` (0 failures, 0 skipped); compileall and node exit 0.

Independent verification (protocol of 2026-10-25): a QA agent that did not write the code cloned `ab843d1`, re-ran the suite (`Ran 245 tests in 156.812s`, `OK`), ruff, compileall and node, probed the permit boundary adversarially and confirmed C-01, C-03, C-04 (not a defect), C-05, C-07, the secret scan and that DEMO/LIVE are disabled. It reported two low-severity hardening gaps, now fixed: D-1 (the permit did not bind stop-loss or order type and could be replayed within its TTL: now bound, single-use, with a random nonce so identical requests never share a permit) and D-2 (the override guard could be bypassed by a mixin: now checked through the MRO). A documentation agent audited and then corrected the Markdown files; the coordinator reviewed that diff.

Final verification after D-1, D-2 and the documentation corrections: ruff `All checks passed!`; `Ran 248 tests in 75.552s`, `OK` (0 failures, 0 skipped); compileall and node exit 0. All tests are local: simulator, scripted fake cTrader server and loopback servers. None ran against the real cTrader service.

## Lot 2b: cTrader demo adapter, offline (2026-10-25)

Protocol facts were gathered by a delegated web-reading agent from https://help.ctrader.com/open-api/ (pages listed in BROKERS.md). The coordinator used only facts it reported as read from those pages. Undocumented points are recorded as assumptions, not facts.

Verification (bash, project directory): `PYTHONPATH=src python -m unittest discover -s tests` gave `Ran 215 tests in 53.916s`, `OK`; `compileall` and `node --check` exit 0. The new tests use a scripted fake server (`tests/ctrader_fixture.py`, labelled as not recorded provider traffic) and a loopback WebSocket server. One test expectation (relative stop scale) was corrected to match the documented 1/100000 unit; the code was unchanged.

Not verified: any connection to `demo.ctraderapi.com`, real message shapes, TLS behaviour, enum/int64 JSON encoding, frame type, equity/margin derivation, rate-limit handling. Human step: run `ctrader-check` with your demo credentials (BROKERS.md).

## Lot 2 (version 0.2.0, 2026-10-25)

### Scope delivered

Execution path (instruments, broker contract, simulated broker, order store and gateway, reconciliation, kill switch and freezes), governance (permissions, secrets, two-person limit change, LLM validation), registry (experiments, model versions, promotion gates, rollback), research modules (features, regimes, strategies, generic backtester and metrics, ML baseline), paper replay, CLI commands and dashboard panels. Research modules were written by a delegated agent restricted to new files; the coordinator read their APIs and key logic (purge rule, Donchian exclusion, no-lookahead tests), corrected one reported test count, and integrated them into the CLI and replay.

### Verification record

Command (bash, from the project directory), run by the coordinator after all code changes:

```bash
PYTHONPATH=src python -m unittest discover -s tests
python -m compileall -q src tests
node --check src/astra/static/app.js
```

Observed: `Ran 192 tests in 53.962s`, `OK` (exit 0); compileall exit 0; node exit 0. 192 = 74 lot-1 tests + 61 research-module tests + 57 execution/governance/registry/engine/CLI/web tests. Final re-run after the docs and footer edits: `Ran 192 tests in 73.993s`, `OK`; compileall and node exit 0.

Manual checks: `paper-replay` (sma, momentum, breakout), `ml-baseline`, `orders`, `controls kill/release` run against throwaway databases under `.test-tmp/`; all replays ended with reconciliation `CONSISTENT` and no open position. The dashboard was rendered in the desktop browser pane with populated data; panels displayed orders, controls and reconciliation. At a 375px emulated width the page had no horizontal overflow (scrollWidth 375); tables scroll inside their container. No physical device was tested.

### Limits of this lot

- Only the in-process simulated broker exists; no provider was contacted. DEMO is not executable. (superseded: lot 2b added a cTrader demo adapter tested offline only; still no provider contacted and DEMO still not executable)
- Instrument specs and costs are illustrative; spreads in the replay are a fixed parameter.
- Controls are global per SQLite store; roles are asserted, not authenticated. (superseded: since C-05 freezes and daily-loss state are per account, the kill switch stays global; roles are still asserted)
- ML edge on the synthetic fixture reflects an artificially regular series, not a market property.
- No backup/restore test, service supervisor, metrics endpoint, alert delivery, drift monitor or autonomous candidate generation.

### Human intervention needed

Choose a broker for the first DEMO connector and confirm country/account eligibility and a demo account; see BROKERS.md. (superseded: cTrader Open API was chosen; the open human step is running `ctrader-check` on a demo account) No credentials should be shared in chat or committed.

## Lot 1 (version 0.1.0)

## Scope and starting state

The source workspace at `F:\FOREX` contained the V2 cahier des charges, the V3 prompt, and an empty `ASTRA_Forex_Intelligence_Pack_Complet_V3` folder. It was not a Git repository. Python 3.14.6 and Node.js 24.18.1 were available. There was no existing implementation, test suite, broker adapter, or deployment to preserve.

The user request is the pasted Prompt Maître V3. The Word cahier des charges is the functional/technical reference adopted by that request. The attached specification does not authorize live execution; both documents explicitly keep real trading locked pending a separate human authorization and formal validation.

## Implemented slice

The local simulation slice includes simulation-only configuration and risk checks; CSV provenance, source classification and data-quality gates; an event-time SMA crossover backtest with estimated costs; SQLite experiment and audit history; CLI status/demo/backtest/walk-forward commands; and a French read-only loopback dashboard. The 160-row EURUSD file is invented test data, not market history. Walk-forward folds now record training, validation, and test boundaries; candidate selection uses validation metrics; and the final holdout can be scored once per canonical bar identity within the configured local SQLite store. Requirements and limits are in `SPEC.md`, `RESEARCH_PROTOCOL.md`, and the policy documents.

## Verification record

Final coordinator-run integrated command (PowerShell, from this project directory):

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests -v; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; python -m compileall -q src tests; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; node --check src/astra/static/app.js; exit $LASTEXITCODE
```

Observed final result: **74 tests passed** (`Ran 74 tests in 5.186s`, `OK`); command exit code **0**. This includes local loopback HTTP tests. Python `compileall` and `node --check` both completed with exit code **0**. The integrated run used local loopback permission because an earlier sandbox-only run had failed those HTTP tests with Windows `WinError 10013`.

Focused ingestion command, also coordinator-run:

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests -p 'test_data.py' -v
```

Observed focused results during follow-up: data **19/19**, risk **15/15**, research **8/8**, and dashboard **4/4** passed; each process exited with code **0**. The dashboard suite used local loopback permission.

## Review findings and disposition

The final read-only delivery audit returned **VALIDATED WITH RESERVATIONS**. It confirmed the train/validation/test partitions, additive row preservation, evaluation-window metrics, evidence alignment, and local scope limits; it found no remaining actionable delivery items. The security follow-up found three local-workflow gaps and no remaining actionable MAJOR or MINOR findings after coordinator fixes. Its suggested end-to-end test of equivalent CSV representations was also added: the CLI regression changes line endings, instrument case, and timestamp offset while asserting that a repeated plan is rejected without report output. The reviewers did not run tests. The coordinator's 74-test integrated verification passed.

| Classification | Evidence and impact | Disposition and next step |
|---|---|---|
| MAJOR — simulator risk assumptions | `src/astra/research.py` passes verified-account, known-specification, and margin-available flags to the RiskEngine. Without qualification, acceptance could be read as broker/account proof. | The backtest records these as simulation assumptions; `SPEC.md` and `RISK_POLICY.md` narrow the claim. Direct RiskEngine tests verify false and malformed flags reject. Keep these limits visible until a separately accepted account/specification/margin integration exists. |
| MAJOR — malformed risk flags | `src/astra/risk.py` previously used truthiness for account, specification, and margin booleans; a string such as `"false"` could pass. | Exact boolean validation now fails closed for all required flags and malformed kill-switch state. Regression tests cover string, `None`, and arbitrary-object values. |
| MINOR — dashboard Host header | `src/astra/web.py` previously served local JSON for arbitrary Host headers, creating a possible DNS-rebinding disclosure path. | Requests must now match the configured loopback host and bound port. A test verifies hostile Host rejection and normal loopback requests. Browser exploitability remains untested. |
| MINOR — CSV resource bounds | `src/astra/data.py` previously loaded and accumulated an unbounded CSV. | Imports now cap bytes, rows, columns, field length, and issue count. Tests cover each limit. |
| IMPROVEMENT — caller-created risk policy | `src/astra/risk.py` previously accepted policy values up to 100% risk/daily loss and retained mutable instrument lists. | Policy maxima now match documented bounds, EURUSD remains the only supported instrument, and the engine snapshots a tuple. Regression tests verify caps and caller-list mutation isolation. |
| MAJOR — train/validation/test partition | The earlier evaluator selected candidates on training P&L and had no separate validation period, short of the V2 BT-02 chronological partition. | `WalkForwardProtocol.validation_bars` now creates fixed validation blocks between the expanding training prefix and disjoint test block. Fold and plan manifests record boundaries. Candidate selection and final holdout selection use validation-only metrics; the final candidate requires the minimum trade count summed across validation windows. Boundary and holdout-invariance tests cover this behavior. |
| MAJOR — holdout lock identity and raw backtest path | The first version keyed attempts by CSV byte hash, and ordinary `backtest` scored the full file without an explicit opt-in. | The lock uses canonical normalized-bar identity while retaining the source-byte hash in the report. `backtest` now requires `--exploratory`, emits `EXPLORATORY_FULL_HISTORY_NON_PROMOTABLE`, and warns that the whole CSV may include nominal holdout bars. Tests cover alternate source hashes for identical bars and the CLI opt-in. A regression also demonstrates that the lock is scoped to one configured local SQLite file, which its owner can replace or edit. |
| MAJOR — repeated test-window exposure through alternate plans | The first seal allowed multiple candidate plans for one canonical dataset, so CLI users could inspect test metrics and revise the plan. | `seal_research_protocol` now uses `BEGIN IMMEDIATE` and rejects a second plan for the canonical identity in that local store. The CLI regression verifies an altered candidate set returns an error without report output. Direct evaluator API use, a replaced database, or another store can still bypass the local guard; all results remain exploratory and non-promotable. |
| MINOR — demo data-directory substitution | `demo` previously read a configurable data directory while always labeling the file synthetic. | `demo` now reads the bundled fixture and verifies its SHA-256 before import. The CLI test places invalid content at the override path and confirms that the pinned bundled fixture is used. |
| MINOR — direct API canonical identity variation | Direct `MarketBar` callers could use alternate time-zone offsets or instrument letter case for the same market bars. | Research input normalizes instrument symbols and timestamps to uppercase and UTC before evaluation; the identity digest applies the same normalization. A regression confirms identity and plan hashes remain equal across those representations. |
| IMPROVEMENT — equivalent CSV representation coverage | The first follow-up tests covered canonical identity and store rejection separately, without proving that the full CLI path ties them together. | The CLI regression changes source bytes, timestamp offset, and instrument case, then confirms the second plan is rejected with no result output. |
| MINOR — legacy SQLite compatibility evidence | Additive tables used `CREATE TABLE IF NOT EXISTS`; earlier tests did not show existing run and audit records surviving initialization. | A regression test creates a legacy database with existing run and audit rows, initializes the updated store, and verifies both rows and the new tables remain. |
| MINOR — delivery status alignment | Earlier documentation disagreed about whether the implemented slice had passed verification. | `TASKS.md`, `ROADMAP.md`, `CHANGELOG.md`, and this report now refer to the same 74-test evidence and final review disposition. |

The dashboard marks source labels as declared and provider-unverified. No browser DNS-rebinding exploit, penetration test, or external execution path was run. The local suite and source compilation are the current verification boundary. The added regressions passed targeted validation, storage, and CLI runs (23 tests total), and the final integrated suite passed.

## Open limitations

- No Git history or remote exists in the project directory. (superseded: Git initialised on top of the remote's `Initial commit`, lot 1 corrections)
- No official broker documentation/account eligibility check or broker connection has been performed. (superseded in part: official documentation was checked in lot 2 and a cTrader adapter built offline in lot 2b; account eligibility and any real connection remain unverified)
- No demo/live execution, authentication, remote deployment, backup/restore proof, model registry, or production acceptance is claimed. (superseded in part: a model registry was added in lot 2; the other items still hold) The local same-dataset plan and holdout guards are advisory and do not establish tamper-proof one-time evaluation; direct evaluator use and a fresh or edited store remain outside the control boundary.
- No browser-based visual or real-device review was performed; dashboard verification is limited to local HTTP behavior and source-level checks.
- The dashboard still has no authentication and must remain bound to loopback. The SQLite audit trail is mutable.
- The backtest's account, instrument-specification, and margin flags are illustrative simulation assumptions, and no broker contract or margin rules are modeled.
