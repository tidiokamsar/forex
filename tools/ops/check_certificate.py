"""Check the remaining validity of a host's public TLS certificate (standard library only).

Usage: python tools/ops/check_certificate.py --host NAME [--port 443] [--min-hours 336]

Exit 0 when the certificate verifies against the system trust store, matches the name and is valid for
at least ``--min-hours`` more hours; exit 1 otherwise (expired, expiring, untrusted, wrong name, or the
host is unreachable). A systemd timer runs it and alerts on failure (deploy/systemd/astra-cert-check.*).
Suggested thresholds: 336 h (14 days) for a 90-day certificate, 72 h for a short-lived IP certificate.
"""

from __future__ import annotations

import argparse
import socket
import ssl
import sys
from datetime import UTC, datetime


def hours_remaining(not_after: str, now: datetime) -> float:
    """Hours until ``notAfter`` (OpenSSL text format, for example 'Jan 15 12:00:00 2027 GMT')."""

    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC)
    return (expires - now).total_seconds() / 3600


def check(host: str, port: int, min_hours: float, timeout: float = 10.0) -> tuple[bool, str]:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw, \
                context.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
    except (OSError, ssl.SSLError) as exc:
        return False, f"{host}:{port}: TLS check failed: {exc}"
    remaining = hours_remaining(cert["notAfter"], datetime.now(UTC))
    status = f"{host}:{port}: certificate valid until {cert['notAfter']} ({remaining:.1f} h left, threshold {min_hours} h)"
    return remaining >= min_hours, status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--min-hours", type=float, default=336.0)
    args = parser.parse_args()
    ok, message = check(args.host, args.port, args.min_hours)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
