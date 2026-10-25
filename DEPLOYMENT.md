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

The SQLite database is created under `var/`. Keep it local and protect file permissions. Stop the foreground server with Ctrl+C. Do not run this process under an internet-facing reverse proxy or expose its port to a LAN.

## Continuous service

The trading service runs as a long-lived foreground process:

```powershell
$env:PYTHONPATH = "src"
$env:ASTRA_LOG_LEVEL = "INFO"           # optional
$env:ASTRA_LOG_DIR = "var/logs"         # optional rotating astra.jsonl
python -m astra service run --realtime
```

In this build it runs only against the in-process simulated broker with a `SYNTHETIC` feed. It stops gracefully on Ctrl+C, SIGINT, SIGTERM or SIGBREAK. Check it from another shell with `python -m astra service status` (heartbeat age and `alive`). See [OPERATIONS.md](OPERATIONS.md).

Backups: `python -m astra backup create` writes verified copies to `ASTRA_BACKUP_DIR` (default `var/backups`); restore procedure in [OPERATIONS.md](OPERATIONS.md). Both are tested locally with the simulator only.

## Not provided

External supervision is **not provided or tested**: nothing restarts the service after a crash or starts it at boot, and no alert is sent when the heartbeat goes stale. Next step: choose and test a supervisor (for example Windows Task Scheduler or NSSM on Windows, systemd on Linux) and scheduled backups, then document the tested configuration here. No service-manager unit, container image, authentication or TLS has been validated.
