# Data policy

## Current data

Every dataset used by tests, the CLI defaults and the reports so far is **synthetic** (the bundled `data/synthetic_eurusd_m15.csv`, SHA-256 pinned, and generated test series). No real historical, delayed or live market data has been imported or validated. No result produced by ASTRA is evidence about real markets.

## Source classes

Every imported dataset must declare a source class: `HISTORICAL`, `DELAYED`, `LIVE`, `SIMULATED`, `SYNTHETIC`, or `UNKNOWN` (the CLI accepts the first five). This is a caller-declared label; the loader cannot verify provider origin, and the dashboard shows the label as declared and the provider origin as unverified. The source name and SHA-256 of the exact CSV bytes are recorded with each run. A generated fixture is always labelled `SYNTHETIC`.

The V5 directive's label set (`SYNTHETIC`, `PUBLIC`, `BROKER_HISTORICAL`, `LIVE_FEED`, `DEMO_EXECUTION`) is **not implemented**; the labels above are the ones the code accepts.

## CSV contract

Required columns: `timestamp,instrument,open,high,low,close`. Optional columns: `volume,bid,ask`. Timestamps must be ISO 8601 and include an explicit UTC offset; the loader normalizes accepted timestamps to UTC. Prices must be finite and positive. OHLC values must satisfy `low <= open/close <= high`; bid must not exceed ask. Duplicate or out-of-order observations make the dataset unusable. Expected interval gaps are reported when an interval is supplied. Invalid rows cannot be silently treated as clean input.

Wall-clock checks (staleness and timestamps later than the reference time) apply only to declared `LIVE` or `DELAYED` input. Historical, simulated, and synthetic bars run on event time. That convention is for backtest reproducibility and does not certify the freshness of a real-time feed.

The importer accepts at most 10 MiB, 100,000 data rows, 16 columns, and 16,384 characters per field. It stops recording detailed issues after 4,096 findings and adds an `ISSUE_LIMIT_REACHED` marker. Exceeding the file-size bound raises a validation error; row, column, and field bounds make the dataset unusable.

## Dataset identity

Two fingerprints are kept: the SHA-256 of the source bytes, and a canonical market-bar identity (SHA-256 over normalized instrument, UTC timestamp, OHLC, volume, bid and ask). The canonical identity keys the sealed plan, the once-only holdout and the store-wide trial count, so reformatting the same bars does not create a new dataset. Both prove which bytes or bars were used, not where they came from.

## Storage, retention, and disclosure

SQLite stores run metrics, assumptions, source label/name, file hashes and local audit events. It does not copy the raw CSV. Since lot 4 it does store values derived from the bars: the log returns of every sealed holdout (`holdout_signatures`, and the `STARTED` event of each holdout attempt) for the holdout reuse check, and per-attempt metrics in the attempt log. The source file remains under the user's control; keep it unchanged, because promotion re-derives the holdout evidence from the bars (`holdout_bars`) and refuses without them.

The SQLite tables are local. Research-integrity tables are append-only through triggers, but the file owner can still edit or replace the file; it is not an immutable record. The only evidence anchor shipped, `LocalFileAnchor`, is a local file and is not production grade; no external immutable (WORM) store is implemented. Do not import account identifiers, secrets, or personal information in CSV metadata.

Dataset quality, provenance, and limitations must appear alongside any reported result. Missing costs or market fields remain unknown or documented assumptions; ASTRA does not invent a real feed and never substitutes synthetic data for missing real data.

## V5 Market Data Gateway (5.1): not implemented yet

- Connectors for history download, bid/ask quotes or ticks (the cTrader adapter reads one spot quote for the read-only check and the service, offline only).
- Receipt timestamps next to event timestamps, clock-drift tracking.
- Outlier, stale-candle and DST-change detection.
- Provenance and licence verification, parser version.
- Immutable raw store, separately versioned normalized data and derived bars; corrections as new versions.
- As-of join helpers for multi-source data.

Before any real dataset is used for research, it needs a verified source, recorded licence terms, quality checks (timezone, gaps, duplicates, bid/ask), and broker costs and specifications documented (V5 gate D).
