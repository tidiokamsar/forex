"""Dependency-free CSV ingestion and quality checks for market bars.

The loader validates structure and internal consistency; it cannot prove that
CSV values came from a broker, exchange, or an independently verified market
feed. Callers should treat synthetic and user-supplied files as unverified
inputs and retain the returned digest with any downstream result.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import MarketBar


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    """A row-addressable ingestion or market-data quality problem."""

    code: str
    message: str
    row_number: int | None = None


@dataclass(frozen=True, slots=True)
class Dataset:
    """Loaded data plus provenance metadata and all detected quality issues."""

    dataset_id: str
    source_type: str
    source_name: str
    sha256: str
    bars: tuple[MarketBar, ...]
    issues: tuple[DataQualityIssue, ...]
    usable: bool


class _BoundedIssueList(list[DataQualityIssue]):
    """Keep pathological inputs from creating unbounded issue objects."""

    def append(self, issue: DataQualityIssue) -> None:
        if len(self) < MAX_QUALITY_ISSUES:
            super().append(issue)
        elif len(self) == MAX_QUALITY_ISSUES:
            super().append(
                DataQualityIssue(
                    "ISSUE_LIMIT_REACHED",
                    f"Additional issues omitted after {MAX_QUALITY_ISSUES} findings",
                )
            )


_REQUIRED_COLUMNS = ("timestamp", "instrument", "open", "high", "low", "close")
_OPTIONAL_COLUMNS = ("volume", "bid", "ask")
MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_CSV_ROWS = 100_000
MAX_CSV_COLUMNS = 16
MAX_CSV_FIELD_CHARS = 16_384
MAX_QUALITY_ISSUES = 4_096
_SUPPORTED_SOURCE_TYPES = {
    "UNKNOWN",
    "SIMULATED",
    "SYNTHETIC",
    "HISTORICAL",
    "DELAYED",
    "LIVE",
}


def load_bars_csv(
    path: str | Path,
    *,
    source_type: str = "UNKNOWN",
    source_name: str | None = None,
    expected_interval_seconds: float | None = None,
    now: datetime | None = None,
    max_staleness_seconds: float = 120,
) -> Dataset:
    """Load ISO-8601 OHLCV CSV rows without silently accepting bad records.

    Required headers are ``timestamp,instrument,open,high,low,close``;
    ``volume,bid,ask`` are optional. Invalid rows are omitted from ``bars``
    only when a corresponding issue identifies that row; any issue makes the
    returned dataset unusable for backtesting.

    Freshness is meaningful only for ``LIVE`` and ``DELAYED`` sources. For
    those sources, an omitted ``now`` uses the current UTC time. Historical,
    simulated, synthetic, and unknown sources are never compared with the wall
    clock.
    """

    normalized_source_type = _normalize_source_type(source_type)
    normalized_source_name = _normalize_source_name(
        Path(path).name if source_name is None else source_name
    )

    if expected_interval_seconds is not None:
        expected_interval_seconds = _finite_number(
            expected_interval_seconds, "expected_interval_seconds"
        )
        if expected_interval_seconds <= 0:
            raise ValueError("expected_interval_seconds must be greater than zero")

    max_staleness_seconds = _finite_number(
        max_staleness_seconds, "max_staleness_seconds"
    )
    if max_staleness_seconds < 0:
        raise ValueError("max_staleness_seconds must not be negative")

    freshness_checked = normalized_source_type in {"LIVE", "DELAYED"}
    reference_time: datetime | None = None
    if freshness_checked:
        reference_time = now if now is not None else datetime.now(UTC)
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime")
        reference_time = reference_time.astimezone(UTC)

    source_path = Path(path)
    with source_path.open("rb") as source_file:
        raw = source_file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise ValueError(f"CSV exceeds maximum size of {MAX_CSV_BYTES} bytes")
    digest = hashlib.sha256(raw).hexdigest()
    dataset_id = digest
    issues: _BoundedIssueList = _BoundedIssueList()
    bars: list[MarketBar] = []
    if normalized_source_type == "UNKNOWN":
        issues.append(
            DataQualityIssue(
                "UNKNOWN_SOURCE_TYPE",
                "Source type is UNKNOWN; explicit provenance is required",
            )
        )

    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        issues.append(
            DataQualityIssue(
                "INVALID_ENCODING", f"CSV must be UTF-8 encoded: {exc.reason}"
            )
        )
        return _dataset(
            dataset_id,
            normalized_source_type,
            normalized_source_name,
            digest,
            bars,
            issues,
        )

    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    try:
        raw_header = next(reader, None)
    except csv.Error as exc:
        issues.append(DataQualityIssue("CSV_PARSE_ERROR", str(exc), 1))
        return _dataset(
            dataset_id,
            normalized_source_type,
            normalized_source_name,
            digest,
            bars,
            issues,
        )

    if raw_header is None:
        issues.append(DataQualityIssue("EMPTY_FILE", "CSV has no header row", 1))
        return _dataset(
            dataset_id,
            normalized_source_type,
            normalized_source_name,
            digest,
            bars,
            issues,
        )

    if len(raw_header) > MAX_CSV_COLUMNS:
        issues.append(
            DataQualityIssue(
                "COLUMN_LIMIT_EXCEEDED",
                f"CSV has {len(raw_header)} columns; maximum is {MAX_CSV_COLUMNS}",
                1,
            )
        )
        return _dataset(
            dataset_id,
            normalized_source_type,
            normalized_source_name,
            digest,
            bars,
            issues,
        )

    header = [column.strip().lower() for column in raw_header]
    duplicates = sorted({column for column in header if header.count(column) > 1})
    if duplicates:
        issues.append(
            DataQualityIssue(
                "DUPLICATE_HEADER",
                f"CSV contains duplicate column names: {', '.join(duplicates)}",
                1,
            )
        )
        return _dataset(
            dataset_id,
            normalized_source_type,
            normalized_source_name,
            digest,
            bars,
            issues,
        )

    missing = [column for column in _REQUIRED_COLUMNS if column not in header]
    if missing:
        issues.append(
            DataQualityIssue(
                "MISSING_REQUIRED_COLUMN",
                f"CSV is missing required columns: {', '.join(missing)}",
                1,
            )
        )
        return _dataset(
            dataset_id,
            normalized_source_type,
            normalized_source_name,
            digest,
            bars,
            issues,
        )

    column_index = {column: index for index, column in enumerate(header)}
    expected_column_count = len(header)
    previous_timestamp: datetime | None = None
    observed_timestamps: list[tuple[int, datetime]] = []
    row_number = 1

    while True:
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            row_number += 1
            issues.append(DataQualityIssue("CSV_PARSE_ERROR", str(exc), row_number))
            break

        row_number += 1
        if row_number > MAX_CSV_ROWS + 1:
            issues.append(
                DataQualityIssue(
                    "ROW_LIMIT_EXCEEDED",
                    f"CSV exceeds maximum of {MAX_CSV_ROWS} data rows",
                    row_number,
                )
            )
            break
        if any(len(field) > MAX_CSV_FIELD_CHARS for field in row):
            issues.append(
                DataQualityIssue(
                    "FIELD_TOO_LARGE",
                    f"CSV field exceeds maximum length of {MAX_CSV_FIELD_CHARS} characters",
                    row_number,
                )
            )
            continue
        column_count_mismatch = len(row) != expected_column_count
        if column_count_mismatch:
            issues.append(
                DataQualityIssue(
                    "COLUMN_COUNT_MISMATCH",
                    f"Expected {expected_column_count} fields but found {len(row)}",
                    row_number,
                )
            )

        timestamp_column = column_index["timestamp"]
        if timestamp_column >= len(row):
            continue

        timestamp_value = row[timestamp_column].strip()
        try:
            timestamp = _parse_timestamp(timestamp_value)
        except ValueError as exc:
            issues.append(DataQualityIssue("INVALID_TIMESTAMP", str(exc), row_number))
            continue

        observed_timestamps.append((row_number, timestamp))
        if previous_timestamp is not None:
            delta_seconds = (timestamp - previous_timestamp).total_seconds()
            if delta_seconds == 0:
                issues.append(
                    DataQualityIssue(
                        "DUPLICATE_TIMESTAMP",
                        f"Timestamp {timestamp.isoformat()} duplicates the prior row",
                        row_number,
                    )
                )
            elif delta_seconds < 0:
                issues.append(
                    DataQualityIssue(
                        "OUT_OF_ORDER_TIMESTAMP",
                        f"Timestamp {timestamp.isoformat()} precedes the prior row",
                        row_number,
                    )
                )
            elif expected_interval_seconds is not None:
                if delta_seconds > expected_interval_seconds:
                    missing_intervals = max(
                        1,
                        math.ceil(delta_seconds / expected_interval_seconds) - 1,
                    )
                    issues.append(
                        DataQualityIssue(
                            "INTERVAL_GAP",
                            (
                                f"Timestamp gap is {delta_seconds:g}s; expected "
                                f"{expected_interval_seconds:g}s cadence "
                                f"(approximately {missing_intervals} missing bar(s))"
                            ),
                            row_number,
                        )
                    )
                elif delta_seconds < expected_interval_seconds:
                    issues.append(
                        DataQualityIssue(
                            "INTERVAL_MISMATCH",
                            f"Timestamp spacing is {delta_seconds:g}s; expected "
                            f"{expected_interval_seconds:g}s",
                            row_number,
                        )
                    )
        previous_timestamp = timestamp

        # Even a short or overlong record contributes its parseable timestamp
        # to ordering checks above, but its other fields are not trusted.
        if column_count_mismatch:
            continue

        instrument = row[column_index["instrument"]].strip().upper()
        if not instrument:
            issues.append(DataQualityIssue("EMPTY_INSTRUMENT", "Instrument is empty", row_number))
            continue

        row_values: dict[str, float] = {}
        row_invalid = False
        for field_name in ("open", "high", "low", "close", *_OPTIONAL_COLUMNS):
            if field_name not in column_index:
                if field_name == "volume":
                    row_values[field_name] = 0.0
                continue

            raw_value = row[column_index[field_name]].strip()
            if field_name in _OPTIONAL_COLUMNS and not raw_value:
                if field_name == "volume":
                    row_values[field_name] = 0.0
                continue

            try:
                numeric_value = float(raw_value)
            except ValueError:
                issues.append(
                    DataQualityIssue(
                        "INVALID_NUMBER",
                        f"{field_name} must be a finite number",
                        row_number,
                    )
                )
                row_invalid = True
                continue
            if not math.isfinite(numeric_value):
                issues.append(
                    DataQualityIssue(
                        "INVALID_NUMBER",
                        f"{field_name} must be a finite number",
                        row_number,
                    )
                )
                row_invalid = True
                continue
            row_values[field_name] = numeric_value
            if field_name in {"open", "high", "low", "close", "bid", "ask"} and numeric_value <= 0:
                issue_code = "NEGATIVE_PRICE" if numeric_value < 0 else "NON_POSITIVE_PRICE"
                issues.append(
                    DataQualityIssue(
                        issue_code,
                        f"{field_name} must be greater than zero",
                        row_number,
                    )
                )
                row_invalid = True
            elif field_name == "volume" and numeric_value < 0:
                issues.append(
                    DataQualityIssue(
                        "NEGATIVE_VOLUME",
                        "volume must not be negative",
                        row_number,
                    )
                )
                row_invalid = True

        # Required fields always exist in the schema; malformed values above
        # are reported and prevent construction of an incomplete MarketBar.
        if not all(field in row_values for field in ("open", "high", "low", "close")):
            row_invalid = True
        else:
            open_price = row_values["open"]
            high_price = row_values["high"]
            low_price = row_values["low"]
            close_price = row_values["close"]
            if high_price < max(open_price, close_price, low_price) or low_price > min(
                open_price, close_price, high_price
            ):
                issues.append(
                    DataQualityIssue(
                        "INVALID_OHLC",
                        "OHLC values must satisfy low <= open/close <= high",
                        row_number,
                    )
                )
                row_invalid = True

        bid = row_values.get("bid")
        ask = row_values.get("ask")
        if bid is not None and ask is not None and bid > ask:
            issues.append(
                DataQualityIssue("BID_ASK_INVERTED", "bid must not exceed ask", row_number)
            )
            row_invalid = True

        if not row_invalid:
            bars.append(
                MarketBar(
                    instrument=instrument,
                    timestamp=timestamp,
                    open=row_values["open"],
                    high=row_values["high"],
                    low=row_values["low"],
                    close=row_values["close"],
                    volume=row_values.get("volume", 0.0),
                    bid=bid,
                    ask=ask,
                )
            )

    if not observed_timestamps:
        issues.append(DataQualityIssue("EMPTY_DATASET", "CSV contains no parseable data rows"))

    if freshness_checked and reference_time is not None and observed_timestamps:
        for observed_row, timestamp in observed_timestamps:
            if timestamp > reference_time:
                issues.append(
                    DataQualityIssue(
                        "FUTURE_TIMESTAMP",
                        f"Timestamp {timestamp.isoformat()} is later than reference time",
                        observed_row,
                    )
                )
        latest_timestamp = max(timestamp for _, timestamp in observed_timestamps)
        age_seconds = (reference_time - latest_timestamp).total_seconds()
        if age_seconds > max_staleness_seconds:
            issues.append(
                DataQualityIssue(
                    "STALE_DATA",
                    f"Latest data is {age_seconds:g}s old; maximum is "
                    f"{max_staleness_seconds:g}s",
                )
            )

    return _dataset(
        dataset_id,
        normalized_source_type,
        normalized_source_name,
        digest,
        bars,
        issues,
    )


def _normalize_source_type(source_type: str) -> str:
    if not isinstance(source_type, str) or not source_type.strip():
        raise ValueError("source_type must be a non-empty string")
    normalized = source_type.strip().upper()
    if normalized not in _SUPPORTED_SOURCE_TYPES:
        allowed = ", ".join(sorted(_SUPPORTED_SOURCE_TYPES - {"UNKNOWN"}))
        raise ValueError(f"unsupported source_type {source_type!r}; use one of {allowed}, UNKNOWN")
    return normalized


def _normalize_source_name(source_name: str) -> str:
    if not isinstance(source_name, str):
        raise ValueError("source_name must be a string")
    normalized = source_name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not normalized:
        raise ValueError("source_name must not be empty")
    if len(normalized) > 120:
        raise ValueError("source_name must be at most 120 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("source_name must not contain control characters")
    return normalized


def _finite_number(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _parse_timestamp(value: str) -> datetime:
    if not value:
        raise ValueError("timestamp is empty")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"timestamp {value!r} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def _dataset(
    dataset_id: str,
    source_type: str,
    source_name: str,
    digest: str,
    bars: list[MarketBar],
    issues: list[DataQualityIssue],
) -> Dataset:
    return Dataset(
        dataset_id=dataset_id,
        source_type=source_type,
        source_name=source_name,
        sha256=digest,
        bars=tuple(bars),
        issues=tuple(issues),
        usable=not issues and bool(bars),
    )
