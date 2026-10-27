# Continuous integration (Gate B)

The workflow is [.github/workflows/ci.yml](../.github/workflows/ci.yml). It builds and tests only: it never deploys, never contacts a broker or exchange, uses no repository secret, and its token can read the code (plus, for the `plan` job only, the list of pull requests) and write nothing. Every test is offline (simulated or loopback fixtures); a green run is not a validation of any external API.

## When it runs

| Event | Jobs | Notes |
|---|---|---|
| Pull request to `main` (opened, updated) | `quality`, `full-suite` | These two check names are required before a merge into `main`. |
| Push to any branch | `quality (push)`, then `full-suite (push)` unless an open pull request to `main` already runs the suite for that branch (job `plan`) | Avoids running the 23 to 35 minute suite twice for the same commits. |
| Manual (`workflow_dispatch`) | `quality (workflow_dispatch)`, `full-suite (workflow_dispatch)` | GitHub offers it only once the workflow file is on the default branch (`main`). |

Only pull request runs report the required names `quality` and `full-suite`. A push run reports `quality (push)` / `full-suite (push)`, and a job skipped before it starts (a failed `needs`, or `plan` deciding to skip) is listed under the unevaluated name expression, so neither can ever satisfy a required check (GitHub counts a skipped job as passing). In a pull request, a `full-suite` skipped because `quality` failed therefore leaves the required `full-suite` pending: the merge stays blocked.

A newer push cancels the older run of the same branch or pull request (`concurrency`).

## What each job checks

`quality` (timeout 20 min):
1. `python -m ruff check src tests tools` (Ruff 0.16.1, pinned by hash in `tools/ci/requirements-ruff.txt`).
2. `python -m compileall -q src tests tools`.
3. `node --check src/astra/static/app.js` (the dashboard script).
4. `python tools/ci/secret_scan.py`: every tracked file, plus every line added in the history of the analysed commit. It fails on private key blocks (including PGP), known token formats (AWS, GitHub, Slack, Google, JWT, `sk-`/`sk-ant-`, Stripe `sk_live_`/`rk_live_`, Bearer), credential-like literals (quoted, or unquoted alone on a configuration line), credentials in URLs, tracked key/database/log/env files, and a `.env.example` secret with a value. Environment-variable names (upper case with an underscore) and obviously fake fixtures are not hits. The history scan also covers merge resolutions and renames, and stops with exit 2 on a shallow clone. Values are never printed in full.
5. Targeted tests: the 7 multi-venue modules (`test_multi_venue_security_review`, `test_multi_venue_verification`, `test_market_risk`, `test_binance_spot`, `test_venue_registry`, `test_venues_contract`, `test_markets`) and `test_webapp` (authentication, sessions, roles, CSRF).

Both jobs first install `requirements/web.txt` with `--require-hashes` (see [DEPENDENCIES.md](DEPENDENCIES.md)).

`full-suite` (timeout 75 min, `PYTHON_COLORS=0` so the log has no colour codes):
1. `python -m unittest discover -s . -v` in `tests/` with `PYTHONPATH=../src`; the log is copied with `tee`, and the step exits with the suite's own exit code (`PIPESTATUS[0]` under `bash -eo pipefail`), so the pipe cannot hide a failure.
2. `python tools/ci/check_unittest_log.py full_suite.log --expected-failures 15 --expected-modules test_lot4_`: the log must hold exactly one `Ran N tests` line, the summary after it must be `OK` with exactly 15 expected failures, all in the Lot 4 review modules (documented residuals, `reports/LOT_04_REPORT.md`). unittest already fails on a failure, an error or an unexpected success; this check also fails if a new `expectedFailure` appears elsewhere or a residual disappears without a reviewed update of `EXPECTED_FAILURES` in the workflow.

## Evidence kept for every run

The job summary of each run records the analysed commit, the event and ref, the workflow file and its commit (`GITHUB_WORKFLOW_REF`, `GITHUB_WORKFLOW_SHA`), the runner image, the Python, Ruff, Node and Git versions, each command and its exit code, and the end of the full-suite log. The complete logs stay on the run page (GitHub's default retention). Actions are pinned to full commit SHAs, the runner image to `ubuntu-24.04` and Python to 3.14 (the latest 3.14 patch on the runner; the exact version is in the summary).

## Running the same checks locally

PowerShell, from the repository root:

```powershell
python -m pip install --require-hashes --no-deps -r tools/ci/requirements-ruff.txt   # Linux wheel; on Windows: python -m pip install ruff==0.16.1
python -m ruff check src tests tools
python -m compileall -q src tests tools
node --check src/astra/static/app.js
python tools/ci/secret_scan.py
cd tests
$env:PYTHONPATH = "../src"
python -m unittest test_multi_venue_security_review test_multi_venue_verification test_market_risk test_binance_spot test_venue_registry test_venues_contract test_markets
python -m unittest discover -s . -v 2>&1 | Tee-Object ../full_suite.log
cd ..
python tools/ci/check_unittest_log.py full_suite.log --expected-failures 15 --expected-modules test_lot4_
```

Check each command's exit code (`$LASTEXITCODE`); `Tee-Object` does not change the suite's own exit code check, so read the `OK`/`FAILED` summary line as well, or run the suite without the pipe.

## Hardening choices

- Top-level `permissions: contents: read`; only the `plan` job adds `pull-requests: read` to list open pull requests. No job can write to the repository, issues, pull requests or packages.
- `pull_request` (not `pull_request_target`): code from a fork runs without secrets and with a read-only token.
- `actions/checkout` with `persist-credentials: false`, so the token is not left in the Git configuration for the test code.
- Only GitHub's own actions are used (`actions/checkout`, `actions/setup-python`), each pinned to the commit of a release at least three months old; no third-party action, no paid service, no new runtime dependency. Ruff is installed with `--require-hashes`.
- No broker key, account identifier or secret is defined or referenced; no deployment, release or merge step exists.

## Limits

- The workflow runs on Linux only; the lots were validated locally on Windows. A platform-specific failure on Windows would not be caught by CI.
- The secret scan is pattern-based: it can miss a secret with no recognisable shape. GitHub secret scanning and push protection are enabled on the repository as a second layer (see [reports/GATE_B_REPORT.md](../reports/GATE_B_REPORT.md)). A shallow clone makes the history scan stop with exit 2 instead of passing on a partial history.
- `workflow_dispatch` is inert until the workflow reaches `main`.
- `plan` matches open pull requests by branch name only: a pull request from a fork with the same branch name also skips the push run's suite. The pull request's own run still executes the required `full-suite`, so nothing can merge without it.
