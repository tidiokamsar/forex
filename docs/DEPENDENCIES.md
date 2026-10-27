# Third-party dependencies

Until the authentication gate, ASTRA used the standard library only. The trading, risk, research, broker and venue code still does. Only `astra.webapp` and `astra.auth` import third-party packages, and `astra.cli` loads them only for `serve` and `auth`.

## Why the dashboard needs them

- **The HTTP layer.** Python documents `http.server` as not recommended for production, and an Internet-facing authenticated application should not be built on hand-written request parsing. Flask, with Werkzeug, is a maintained framework. It supplies routing, request and form parsing, cookie handling, a body-size limit and templates with automatic escaping.
- **Password storage.** OWASP recommends Argon2id, which the standard library does not provide. `argon2-cffi` is the reference binding of the reference C implementation.
- **TOTP secret encryption.** The TOTP secret must be recovered to verify a code, so it is encrypted, not hashed. `cryptography` (the Python Cryptographic Authority's library) provides AES-GCM; the standard library has no authenticated cipher.
- **TOTP.** `pyotp` implements RFC 6238 and the provisioning URI that authenticator apps read. It avoids a hand-written implementation of the second factor.
- **TOTP enrolment QR code.** `segno` draws the provisioning URI as an SVG QR code on the server, so the phone scans it instead of typing the key. It is pure Python with no dependency, needs no JavaScript and no third-party request, and the CSP already allows `img-src data:`.
- **The production WSGI server.** gunicorn is a maintained server for Linux. Werkzeug's development server is used only by `astra serve` on loopback.
- **Not used.** No identity provider, paid service, frontend toolchain or JavaScript library.

## Pinned versions (2026-10-27)

Every version is pinned in `requirements/web.txt` and `requirements/server.txt` together with the SHA-256 of every file PyPI publishes for it, and installed with `pip install --require-hashes`.

At pinning time:
- each release was at least two weeks old;
- none had a vulnerability listed by PyPI;
- all have wheels for CPython 3.14 on Linux x86_64 and Windows.

| Package | Version | Released | Why |
|---|---|---|---|
| flask | 3.1.3 | 2026-02-19 | Web framework |
| werkzeug | 3.1.9 | 2026-09-27 | Flask's HTTP toolkit; ProxyFix |
| markupsafe | 3.0.4 | 2026-10-02 | Jinja escaping |
| jinja2 | 3.1.6 | 2025-03-05 | Templates (autoescape) |
| itsdangerous | 2.2.0 | 2024-04-16 | Flask requirement (Flask's signed-cookie sessions are not used) |
| click | 8.5.0 | 2026-08-26 | Flask requirement |
| blinker | 1.9.0 | 2024-11-08 | Flask requirement |
| argon2-cffi | 25.1.0 | 2025-06-03 | Argon2id |
| argon2-cffi-bindings | 26.1.0 | 2026-08-20 | Compiled Argon2 reference code |
| cffi | 2.1.1 | 2026-08-03 | Required by the bindings |
| pycparser | 3.0 | 2026-01-21 | Required by cffi (3.12, released 2026-10-22, was too recent) |
| pyotp | 2.10.0 | 2026-06-14 | TOTP |
| segno | 1.6.6 | 2025-03-12 | QR code of the TOTP enrolment (pinned 2026-10-27; BSD licence, no dependency on Python 3.10+) |
| cryptography | 50.0.2 | 2026-09-30 | AES-256-GCM encryption of the TOTP secrets (AUTH-2) |
| gunicorn | 26.2.0 | 2026-08-24 | Production server (Linux only) |

The CI installs `requirements/web.txt` before the checks. A version change is a reviewed commit that regenerates the hashes from PyPI.
