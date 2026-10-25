# ASTRA implementation scope

**Source of truth:** `../ASTRA_Forex_Intelligence_Cahier_des_charges_V2.md` (version 2.0, 24 October 2026), also supplied as a Word document. The pasted Prompt Maître V3 is the user's implementation request. This file records the accepted first delivery and does not replace the full source specification.

## Initial acceptance slice

1. Start in `SIMULATION`; reject `DEMO` and `LIVE` and ignore no explicit live-enable request.
2. Import timestamped OHLC CSV data, label source type and provenance, hash input bytes, and reject unusable quality states.
3. Generate an explainable SMA crossover signal using only data available at signal time.
4. Backtest at the next bar open with explicit spread, slippage, commission, stop, and end-of-data assumptions.
5. Route each simulated entry through an independent risk engine. Its direct contract rejects unverified mode, stale or unknown data, excessive or unknown spread, invalid size/stops, unknown specifications, missing margin, duplicate intents, kill switch, position cap, and daily-loss breach. The offline backtest explicitly assumes its simulation account, illustrative instrument specifications, and simulated margin are available; those assumptions do not verify real account or broker facts.
6. Persist reproducible run metadata and an audit event in local SQLite.
7. Expose a French read-only dashboard on loopback with the active mode and live lock visible.
8. Provide tests and current limitations. No broker connection or external order is in scope for this lot.

## Later acceptance gates

Versioned walk-forward evaluation, baseline comparisons, cost stress, and a once-only final-holdout workflow are implemented for local simulation. Lot 2 adds causal features, regimes, strategy families with abstention, a purged walk-forward ML baseline, an experiment and model registry with pre-registered promotion gates and rollback, a broker contract with a simulated adapter, a durable idempotent order gateway with reconciliation and emergency controls, a permission guard, LLM output validation, and a paper replay that runs the whole chain. Verified demo adapters, alert delivery, authenticated operator controls, backup/restore proof, service supervision, and a formal real-mode authorization checklist remain future work. A module or screen alone does not satisfy those gates.
