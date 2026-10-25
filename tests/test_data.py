"""Focused ingestion tests using invented data, not a market-history source.

The EURUSD values in data/synthetic_eurusd_m15.csv are deterministic test
fixtures only. They are not broker quotes, exchange observations, or evidence
of historical or live market behavior.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from astra.data import MAX_CSV_BYTES, MAX_CSV_FIELD_CHARS, load_bars_csv


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "synthetic_eurusd_m15.csv"


class LoadBarsCsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_text = FIXTURE.read_text(encoding="utf-8")

    def _load_text(self, text: str, **kwargs):
        # Keep transient fixtures inside the writable project workspace.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary_directory:
            csv_path = Path(temporary_directory) / "market.csv"
            csv_path.write_text(text, encoding="utf-8", newline="")
            return load_bars_csv(csv_path, **kwargs)

    def _replace_row(self, row_number: int, replacement: str) -> str:
        rows = self.fixture_text.splitlines()
        rows[row_number - 1] = replacement
        return "\n".join(rows) + "\n"

    def test_synthetic_fixture_loads_and_hashes_exact_source_bytes(self) -> None:
        raw = FIXTURE.read_bytes()
        dataset = load_bars_csv(
            FIXTURE,
            source_type="synthetic",
            expected_interval_seconds=15 * 60,
        )

        self.assertTrue(dataset.usable)
        self.assertEqual(len(dataset.bars), 160)
        self.assertEqual(dataset.source_type, "SYNTHETIC")
        self.assertEqual(dataset.source_name, FIXTURE.name)
        self.assertEqual(dataset.sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(dataset.dataset_id, dataset.sha256)
        self.assertEqual({bar.instrument for bar in dataset.bars}, {"EURUSD"})
        self.assertTrue(
            all(bar.timestamp.tzinfo is timezone.utc for bar in dataset.bars)
        )
        self.assertTrue(
            all(
                (later.timestamp - earlier.timestamp).total_seconds() == 15 * 60
                for earlier, later in zip(dataset.bars, dataset.bars[1:])
            )
        )
        closes = [bar.close for bar in dataset.bars]
        sma_differences = [
            sum(closes[index - 9 : index + 1]) / 10
            - sum(closes[index - 29 : index + 1]) / 30
            for index in range(29, len(closes))
        ]
        crossovers = sum(
            earlier * later < 0
            for earlier, later in zip(sma_differences, sma_differences[1:])
        )
        self.assertGreaterEqual(crossovers, 4)
        self.assertEqual(dataset.issues, ())

    def test_timestamp_offsets_are_normalized_to_utc(self) -> None:
        text = self.fixture_text.replace(
            "2026-01-05T00:00:00Z", "2026-01-05T02:00:00+02:00", 1
        )
        dataset = self._load_text(text, source_type="HISTORICAL")

        self.assertEqual(dataset.bars[0].timestamp, datetime(2026, 1, 5, tzinfo=timezone.utc))
        self.assertTrue(dataset.usable)

    def test_malformed_row_is_reported_and_dataset_is_unusable(self) -> None:
        text = self._replace_row(
            2,
            "2026-01-05T00:00:00Z,EURUSD,not-a-price,1.10080,1.09970,1.10040,120,1.10030,1.10050",
        )
        dataset = self._load_text(text, source_type="SYNTHETIC")

        self.assertFalse(dataset.usable)
        self.assertEqual(len(dataset.bars), 159)
        issue = next(issue for issue in dataset.issues if issue.code == "INVALID_NUMBER")
        self.assertEqual(issue.row_number, 2)

    def test_naive_and_malformed_timestamps_are_reported(self) -> None:
        rows = self.fixture_text.splitlines()
        rows[1] = rows[1].replace("2026-01-05T00:00:00Z", "2026-01-05T00:00:00")
        rows[2] = rows[2].replace("2026-01-05T00:15:00Z", "not-a-timestamp")
        dataset = self._load_text("\n".join(rows) + "\n", source_type="HISTORICAL")

        self.assertFalse(dataset.usable)
        timestamp_issues = [issue for issue in dataset.issues if issue.code == "INVALID_TIMESTAMP"]
        self.assertEqual(len(timestamp_issues), 2)
        self.assertEqual({issue.row_number for issue in timestamp_issues}, {2, 3})

    def test_invalid_ohlc_negative_price_and_bid_ask_are_reported(self) -> None:
        rows = self.fixture_text.splitlines()
        rows[1] = "2026-01-05T00:00:00Z,EURUSD,1.10000,1.09900,1.09970,1.10040,120,1.10030,1.10050"
        rows[2] = "2026-01-05T00:15:00Z,EURUSD,-1,1.10100,1.10010,1.10080,95,1.10070,1.10090"
        rows[3] = "2026-01-05T00:30:00Z,EURUSD,1.10080,1.10120,1.10050,1.10060,87,1.10090,1.10070"
        dataset = self._load_text("\n".join(rows) + "\n", source_type="SYNTHETIC")

        self.assertFalse(dataset.usable)
        codes = {issue.code for issue in dataset.issues}
        self.assertIn("INVALID_OHLC", codes)
        self.assertIn("NEGATIVE_PRICE", codes)
        self.assertIn("BID_ASK_INVERTED", codes)

    def test_duplicate_and_out_of_order_timestamps_are_reported(self) -> None:
        rows = self.fixture_text.splitlines()
        rows[2] = rows[2].replace("2026-01-05T00:15:00Z", "2026-01-05T00:00:00Z")
        rows[5] = rows[5].replace("2026-01-05T01:00:00Z", "2026-01-05T00:15:00Z")
        dataset = self._load_text("\n".join(rows) + "\n", source_type="HISTORICAL")

        self.assertFalse(dataset.usable)
        codes = {issue.code for issue in dataset.issues}
        self.assertIn("DUPLICATE_TIMESTAMP", codes)
        self.assertIn("OUT_OF_ORDER_TIMESTAMP", codes)

    def test_expected_interval_gap_is_reported(self) -> None:
        rows = self.fixture_text.splitlines()
        del rows[3]  # Omit the 00:30 row so 00:15 is followed by 00:45.
        dataset = self._load_text(
            "\n".join(rows) + "\n",
            source_type="HISTORICAL",
            expected_interval_seconds=15 * 60,
        )

        self.assertFalse(dataset.usable)
        gap = next(issue for issue in dataset.issues if issue.code == "INTERVAL_GAP")
        self.assertEqual(gap.row_number, 4)
        self.assertIn("1 missing bar", gap.message)

    def test_staleness_applies_only_to_live_or_delayed_sources(self) -> None:
        now = datetime(2026, 1, 6, 17, 0, tzinfo=timezone.utc)
        for source_type in ("LIVE", "DELAYED"):
            with self.subTest(source_type=source_type):
                dataset = self._load_text(
                    self.fixture_text,
                    source_type=source_type,
                    now=now,
                    max_staleness_seconds=120,
                )
                self.assertFalse(dataset.usable)
                self.assertIn("STALE_DATA", {issue.code for issue in dataset.issues})

        historical = self._load_text(
            self.fixture_text,
            source_type="HISTORICAL",
            now=now,
            max_staleness_seconds=120,
        )
        self.assertTrue(historical.usable)
        self.assertNotIn("STALE_DATA", {issue.code for issue in historical.issues})

    def test_future_timestamp_is_rejected_for_live_data(self) -> None:
        now = datetime(2026, 1, 6, 15, 30, tzinfo=timezone.utc)
        dataset = self._load_text(self.fixture_text, source_type="LIVE", now=now)

        self.assertFalse(dataset.usable)
        future = [issue for issue in dataset.issues if issue.code == "FUTURE_TIMESTAMP"]
        self.assertEqual(len(future), 1)
        self.assertEqual(future[0].row_number, 161)

    def test_historical_fixture_is_not_checked_against_wall_clock(self) -> None:
        dataset = self._load_text(self.fixture_text, source_type="SYNTHETIC")

        self.assertTrue(dataset.usable)
        self.assertNotIn(
            "FUTURE_TIMESTAMP", {issue.code for issue in dataset.issues}
        )
        self.assertNotIn("STALE_DATA", {issue.code for issue in dataset.issues})

    def test_simulated_source_is_supported_and_not_checked_against_wall_clock(self) -> None:
        now = datetime(2026, 1, 6, 17, 0, tzinfo=timezone.utc)
        dataset = self._load_text(
            self.fixture_text,
            source_type="simulated",
            now=now,
            max_staleness_seconds=120,
        )

        self.assertTrue(dataset.usable)
        self.assertEqual(dataset.source_type, "SIMULATED")
        self.assertNotIn("STALE_DATA", {issue.code for issue in dataset.issues})

    def test_source_name_is_a_bounded_label_without_paths_or_controls(self) -> None:
        dataset = self._load_text(
            self.fixture_text,
            source_type="SYNTHETIC",
            source_name=r"C:\private\market-feed.csv",
        )

        self.assertEqual(dataset.source_name, "market-feed.csv")
        with self.assertRaisesRegex(ValueError, "control characters"):
            self._load_text(
                self.fixture_text,
                source_type="SYNTHETIC",
                source_name="feed\nsecret.csv",
            )

    def test_csv_resource_limits_reject_oversized_input(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary_directory:
            csv_path = Path(temporary_directory) / "oversized.csv"
            csv_path.write_bytes(b"x" * (MAX_CSV_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "maximum size"):
                load_bars_csv(csv_path, source_type="SYNTHETIC")

    def test_csv_field_row_column_and_issue_limits_are_enforced(self) -> None:
        rows = self.fixture_text.splitlines()
        fields = rows[1].split(",")
        fields[1] = "X" * (MAX_CSV_FIELD_CHARS + 1)
        rows[1] = ",".join(fields)
        oversized_field = self._load_text("\n".join(rows) + "\n", source_type="SYNTHETIC")
        self.assertFalse(oversized_field.usable)
        self.assertIn("FIELD_TOO_LARGE", {issue.code for issue in oversized_field.issues})

        with patch("astra.data.MAX_CSV_ROWS", 2):
            row_limited = self._load_text(self.fixture_text, source_type="SYNTHETIC")
        self.assertFalse(row_limited.usable)
        self.assertIn("ROW_LIMIT_EXCEEDED", {issue.code for issue in row_limited.issues})

        with patch("astra.data.MAX_CSV_COLUMNS", 6):
            column_limited = self._load_text(self.fixture_text, source_type="SYNTHETIC")
        self.assertFalse(column_limited.usable)
        self.assertIn("COLUMN_LIMIT_EXCEEDED", {issue.code for issue in column_limited.issues})

        malformed = self._replace_row(
            2,
            "2026-01-05T00:00:00Z,EURUSD,not-a-price,1.10080,1.09970,1.10040,120,1.10030,1.10050",
        )
        with patch("astra.data.MAX_QUALITY_ISSUES", 1):
            capped = self._load_text(malformed, source_type="UNKNOWN")
        self.assertFalse(capped.usable)
        self.assertEqual(len(capped.issues), 2)
        self.assertEqual(capped.issues[-1].code, "ISSUE_LIMIT_REACHED")

    def test_missing_required_header_and_field_count_mismatch_are_issues(self) -> None:
        no_close_header = self.fixture_text.replace(",close,", ",", 1)
        missing_header = self._load_text(no_close_header, source_type="SYNTHETIC")
        self.assertFalse(missing_header.usable)
        self.assertIn(
            "MISSING_REQUIRED_COLUMN",
            {issue.code for issue in missing_header.issues},
        )

        short_rows = self.fixture_text.splitlines()
        short_rows[1] = ",".join(short_rows[1].split(",")[:-1])
        short_row = "\n".join(short_rows) + "\n"
        malformed_row = self._load_text(short_row, source_type="SYNTHETIC")
        self.assertFalse(malformed_row.usable)
        issue = next(
            issue for issue in malformed_row.issues if issue.code == "COLUMN_COUNT_MISMATCH"
        )
        self.assertEqual(issue.row_number, 2)

    def test_negative_volume_is_rejected(self) -> None:
        text = self._replace_row(
            2,
            "2026-01-05T00:00:00Z,EURUSD,1.10000,1.10080,1.09970,1.10040,-1,1.10030,1.10050",
        )
        dataset = self._load_text(text, source_type="SYNTHETIC")

        self.assertFalse(dataset.usable)
        self.assertIn("NEGATIVE_VOLUME", {issue.code for issue in dataset.issues})

    def test_unknown_source_is_unusable_and_unsupported_source_is_rejected(self) -> None:
        unknown = self._load_text(self.fixture_text, source_type="unknown")

        self.assertFalse(unknown.usable)
        self.assertIn("UNKNOWN_SOURCE_TYPE", {issue.code for issue in unknown.issues})
        self.assertEqual(unknown.source_type, "UNKNOWN")
        with self.assertRaisesRegex(ValueError, "unsupported source_type"):
            self._load_text(self.fixture_text, source_type="BROKER_EXPORT")

    def test_zero_market_prices_are_rejected(self) -> None:
        rows = self.fixture_text.splitlines()
        for row_index, column_index in enumerate((2, 3, 4, 5, 7, 8), start=1):
            fields = rows[row_index].split(",")
            fields[column_index] = "0"
            rows[row_index] = ",".join(fields)
        dataset = self._load_text("\n".join(rows) + "\n", source_type="SYNTHETIC")

        self.assertFalse(dataset.usable)
        non_positive = [
            issue for issue in dataset.issues if issue.code == "NON_POSITIVE_PRICE"
        ]
        self.assertEqual(len(non_positive), 6)
        self.assertEqual(
            {issue.message.split()[0] for issue in non_positive},
            {"open", "high", "low", "close", "bid", "ask"},
        )

    def test_instrument_symbols_are_trimmed_and_uppercased(self) -> None:
        text = self._replace_row(
            2,
            "2026-01-05T00:00:00Z, eurUsd ,1.10000,1.10004,1.09996,1.10000,100,1.09995,1.10005",
        )
        dataset = self._load_text(text, source_type="SYNTHETIC")

        self.assertTrue(dataset.usable)
        self.assertEqual(dataset.bars[0].instrument, "EURUSD")


if __name__ == "__main__":
    unittest.main()
