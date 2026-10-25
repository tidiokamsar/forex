# ADR 0002 Walk-forward evaluation and final holdout control

- **Status:** Accepted
- **Date:** 25 October 2026
- **Approval:** The user approved additive local SQLite protocol and holdout tables during this task.
- **Context:** The V2 cahier and V3 implementation request require chronological validation, a final independent holdout, cost sensitivity, baseline comparison, and a record of attempted variants. The application already uses a standard-library SQLite store. The local database is editable by its owner, so this control is an operational workflow guard rather than tamper-proof evidence.

## Options considered

| Option | Fit | Trade-off |
|---|---|---|
| In-memory protocol and lock | Smallest code change | Restart loses the lock and report; repeated holdout evaluation is possible |
| JSON sidecar file | Human-readable and easy to inspect | Requires custom atomic writes, locking, concurrency handling, and consistency across multiple files |
| Additive SQLite protocol and holdout tables | Fits the existing store and can atomically seal a plan, burn an attempt, and save its report | Local SQLite remains mutable by its owner and is not a tamper-proof audit system |

## Decision

Use `research_protocols` and `holdout_evaluations` tables created with `CREATE TABLE IF NOT EXISTS`, preserving the existing backtest and audit tables. The canonical plan SHA-256 identifies the sealed plan. The holdout key is a canonical digest of normalized market bars; the exact source-file SHA-256 remains separate provenance in the plan and report. The current SQLite holdout tables retain the legacy `dataset_sha256` column name for compatibility, but store the canonical bar identity there. A transaction with `BEGIN IMMEDIATE` freezes the first protocol per normalized dataset within the configured SQLite store, preventing supported CLI plan iteration over the same test windows. A unique identity permits at most one holdout attempt per dataset in that store. Record `STARTED` before holdout scoring; retain `STARTED` after interruption and `FAILED` after a caught error. A `COMPLETED` attempt returns its stored report on retry.

Each fold records an expanding training prefix, fixed validation block, and disjoint test block. Fold test choice uses only that fold's validation score; final holdout choice uses aggregate mean validation score, validation P&L tie-break, and stable candidate identity. The fixed reference strategy and no-position baseline are evaluated on test and holdout windows. The final block remains unscored unless `--evaluate-holdout` is supplied. Raw full-history CLI backtests require `--exploratory` and remain explicitly non-promotable. The demo command is pinned to the bundled synthetic CSV by SHA-256 so a configurable data directory cannot cause arbitrary history to be scored under the synthetic label.

## Consequences

- No external dependency or broker access is introduced.
- The report preserves source hash, implementation fingerprint, protocol and candidate definitions, cost assumptions, fold boundaries, variant outcomes, and aggregate metrics.
- The local owner can modify or replace the database, use another `ASTRA_DATABASE`, or bypass the CLI seal through direct evaluator API calls. This is exploratory evidence and an advisory workflow control scoped to one configured local store; confirmation requires a fresh independent dataset. An external append-only evidence service or signed exports would be needed for stronger audit guarantees.
- Model registry, champion/candidate promotion, statistical inference, and production authorization remain separate roadmap work.
