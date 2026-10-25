# Data policy

## Source classes

Every imported dataset must declare a source class: `HISTORICAL`, `DELAYED`, `LIVE`, `SIMULATED`, `SYNTHETIC`, or `UNKNOWN`. This is a caller-declared label; the loader cannot verify provider origin, and the dashboard marks the label as unverified. The source name and SHA-256 of the exact CSV bytes are recorded with each run. A generated fixture is always labelled `SYNTHETIC`.

## CSV contract

Required columns: `timestamp,instrument,open,high,low,close`. Optional columns: `volume,bid,ask`. Timestamps must be ISO 8601 and include an explicit UTC offset; the loader normalizes accepted timestamps to UTC. Prices must be finite and positive. OHLC values must satisfy `low <= open/close <= high`; bid must not exceed ask. Duplicate or out-of-order observations make the dataset unusable. Expected interval gaps are reported when an interval is supplied. Invalid rows cannot be silently treated as clean input.

Wall-clock staleness applies only to declared live or delayed input. Historical, simulated, and synthetic bars run on event time. That convention is for backtest reproducibility and does not certify the freshness of a real-time feed.

The importer accepts at most 10 MiB, 100,000 data rows, 16 columns, and 16,384 characters per field. It stops recording detailed issues after 4,096 findings and adds an `ISSUE_LIMIT_REACHED` marker. Exceeding the file-size bound raises a validation error; row, column, and field bounds make the dataset unusable.

## Storage, retention, and disclosure

The first implementation stores run metrics, assumptions, source label/name, file hash, and a local audit event. It does not copy raw historical data into SQLite. The source file remains under the user's control. The SQLite audit data is local and mutable; it is not an immutable or externally backed-up record. Do not import account identifiers, secrets, or personal information in CSV metadata.

Dataset quality, provenance, and limitations must appear alongside any reported result. Missing costs or market fields remain unknown or documented assumptions; ASTRA does not invent a real feed.
