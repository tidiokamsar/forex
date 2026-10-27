import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ops"))

import check_certificate


class CertificateCheckTests(unittest.TestCase):
    def test_hours_remaining_and_threshold(self):
        now = datetime(2027, 1, 14, 12, 0, tzinfo=UTC)
        self.assertAlmostEqual(check_certificate.hours_remaining("Jan 15 12:00:00 2027 GMT", now), 24.0)
        self.assertLess(check_certificate.hours_remaining("Jan 14 11:00:00 2027 GMT", now), 0)

    def test_unreachable_host_fails_closed(self):
        ok, message = check_certificate.check("127.0.0.1", 9, 1.0, timeout=0.5)
        self.assertFalse(ok)
        self.assertIn("TLS check failed", message)


if __name__ == "__main__":
    unittest.main()
