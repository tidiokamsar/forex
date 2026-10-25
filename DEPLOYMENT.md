# Local deployment

This delivery is an offline local application, not a public service. It requires Python 3.11+ and no additional package installation.

## Start

From the project folder:

```powershell
$env:PYTHONPATH = "src"
python -m astra demo
python -m astra serve
```

The service defaults to `127.0.0.1:8765`. `ASTRA_HOST` is restricted to loopback; `ASTRA_PORT`, `ASTRA_DATA_DIR`, and `ASTRA_DATABASE` can be configured. `ASTRA_MODE` can only be `SIMULATION`. `ASTRA_LIVE_ENABLED=true` is rejected. The app does not parse `.env`; the sample file documents supported environment variables only.

The SQLite database is created under `var/`. Keep it local and protect file permissions. Stop the foreground server with Ctrl+C. Do not run this process under an internet-facing reverse proxy or expose its port to a LAN. No service-manager unit, container image, authentication, TLS, backup, or restore procedure has been validated.
