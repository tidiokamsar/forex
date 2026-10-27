# Local deployment

This delivery is an offline local application, not a public service. It requires Python 3.11+ and no additional package installation. Execution is `SIMULATION` only: DEMO orders are disabled and LIVE is locked in this build.

## Start

From the project folder:

```powershell
$env:PYTHONPATH = "src"
python -m astra demo
python -m astra serve
```

The service defaults to `127.0.0.1:8765`. `ASTRA_HOST` is restricted to loopback; `ASTRA_PORT`, `ASTRA_DATA_DIR`, and `ASTRA_DATABASE` can be configured. `ASTRA_MODE` can only be `SIMULATION`. `ASTRA_LIVE_ENABLED=true` is rejected. The app does not parse `.env`; the sample file `.env.example` documents part of the supported environment variables (the table below is the complete list read by the code).

| Variable | Read by | Effect |
|---|---|---|
| `ASTRA_MODE` | `config` | Must be `SIMULATION`; anything else is refused |
| `ASTRA_LIVE_ENABLED` | `config` | Any true value is refused |
| `ASTRA_HOST`, `ASTRA_PORT` | `config` | Loopback host and port of the dashboard |
| `ASTRA_DATA_DIR`, `ASTRA_DATABASE` | `config` | Data folder and SQLite file (default `var/astra.sqlite3`) |
| `ASTRA_EVIDENCE_ANCHOR` | `anchoring` | File path of the `LocalFileAnchor` evidence anchor (resolved to an absolute path); unset means no anchor |
| `ASTRA_BACKUP_DIR` | `cli` | Backup folder (default `var/backups`) |
| `ASTRA_LOG_LEVEL`, `ASTRA_LOG_DIR` | `cli` | Log level and optional rotating log file folder |
| `ASTRA_CTRADER_ACCOUNT_ID`, `ASTRA_SECRET_*` | `cli`, `governance.SecretVault` | Read-only `ctrader-check` only; set in your own shell, never in a file |

The SQLite database is created under `var/`. Keep it local and protect file permissions. Stop the foreground server with Ctrl+C. Do not run this process under an internet-facing reverse proxy or expose its port to a LAN: the dashboard has no authentication, authorization, TLS or rate limiting.

## Evidence anchor

Without `ASTRA_EVIDENCE_ANCHOR`, research commands still run, but any holdout scored is permanently non-promotable (`holdout_scored_without_anchor`). If you set it, choose the file before the store holds any sealed plan or holdout: a store used without an anchor can never be anchored afterwards, and a store is bound to the first anchor that checkpoints it (repointing the variable to another file is refused: `anchor_identity_mismatch`, or `anchor_empty_for_used_store` for a fresh file). Keep the anchor file outside `var/` and outside the backup folder, ideally on separate media. It is **not production grade**: its owner can truncate, replace or delete it. No external immutable (WORM / object-lock) anchor is implemented or configured.

## Continuous service

The trading service runs as a long-lived foreground process:

```powershell
$env:PYTHONPATH = "src"
$env:ASTRA_LOG_LEVEL = "INFO"           # optional
$env:ASTRA_LOG_DIR = "var/logs"         # optional rotating astra.jsonl
python -m astra service run --realtime
```

In this build it runs only against the in-process simulated broker with a `SYNTHETIC` feed. It stops gracefully on Ctrl+C, SIGINT, SIGTERM or SIGBREAK. Check it from another shell with `python -m astra service status` (heartbeat age and `alive`). See [OPERATIONS.md](OPERATIONS.md).

Backups: `python -m astra backup create` writes verified copies to `ASTRA_BACKUP_DIR` (default `var/backups`); restore procedure in [OPERATIONS.md](OPERATIONS.md). Both are tested locally with the simulator only. Backups are not encrypted.

## Not provided

- External supervision is **not provided or tested**: nothing restarts the service after a crash or starts it at boot, and no alert is sent when the heartbeat goes stale. Next step: choose and test a supervisor (for example Windows Task Scheduler or NSSM on Windows, systemd on Linux) and scheduled backups, then document the tested configuration here.
- No service-manager unit, container image, hosting, authentication, TLS or encrypted backup exists in this repository. A continuous integration workflow (build and tests only, no deployment) is defined in `.github/workflows/ci.yml` (Gate B, see [docs/CI.md](docs/CI.md)); verification can also be run by hand (see [TESTING.md](TESTING.md)).
- No frontend/backend split: the dashboard and the research and service code share one local process model. V5 section 5.13 (separate persistent service, secured web access) and gate H are not started.
