# ADR 0001 Local Python simulation foundation

- **Status:** Accepted for the first delivery
- **Date:** 25 October 2026
- **Context:** The supplied project folder was empty, contained no Git repository or application code, and the available local environment has Python 3.14.6. The target brief prioritizes a reproducible offline research-to-simulation path and makes real execution a later, human-authorized gate.
- **Decision:** Implement a standard-library Python modular monolith, SQLite persistence, and a built-in HTTP server bound to loopback. Add no third-party runtime dependency or broker adapter in this lot.

## Options

| Option | Fit | Trade-off |
|---|---|---|
| Python standard library + SQLite + local HTTP | Best fit for an empty repository, offline operation, fast review, and reversible vertical slice | Not suitable for multi-user/network exposure; limited ecosystem features |
| FastAPI + PostgreSQL + separate modern frontend | Strong typed API, concurrency, and later multi-user support | Introduces service/dependency/deployment overhead before it is needed; current environment has no application or database to preserve |
| Node.js full-stack service + database | Broad web tooling and unified client/server ecosystem | Splits the current Python quant core from its service layer and adds package supply-chain surface without demonstrated need |

## Consequences

- Synthetic data, CSV validation, a baseline strategy, costs, risk checks, SQLite run records, and a local dashboard can be verified without external accounts or network access.
- A later network or multi-user service requires a new architecture/security review, authentication and authorization, TLS, secret handling, durable job/order state, migrations, and operational controls.
- Results must remain explicitly classified as simulation and identify incomplete market/contract assumptions.

## Revisit when

Authenticated multi-user access, remote deployment, concurrent workloads, measured SQLite bottlenecks, or a verified demo adapter becomes an approved milestone. Any real-mode design still requires a separate formal decision and human authorization.
