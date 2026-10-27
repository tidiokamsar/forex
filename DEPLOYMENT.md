# Local deployment

The research and simulation code requires Python 3.11+ and no additional package; the authenticated dashboard needs the pinned packages of `requirements/web.txt` (section "Remote dashboard"). Execution is `SIMULATION` only: DEMO orders are disabled and LIVE is locked in this build.

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

The SQLite database is created under `var/`. Keep it local and protect file permissions. `python -m astra serve` runs the authenticated dashboard on loopback with the development server (stop with Ctrl+C); production uses gunicorn and systemd (section "Remote dashboard"). Never expose port 8765 directly: remote access goes only through the TLS reverse proxy, after the go-live checklist.

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

## Remote dashboard (authenticated)

The dashboard is the Flask application `astra.webapp` served by gunicorn on `127.0.0.1:8765`. It needs the pinned packages of `requirements/web.txt` (and `requirements/server.txt` for gunicorn on Linux); see [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md). Starting it never starts a trading engine: `create_app_from_env()` only opens the stores, and the configuration still refuses any mode other than SIMULATION and any non-loopback bind address.

**Two access modes, chosen by configuration (`/etc/astra/web.env`, template `deploy/astra-web.env.example`):**

| Mode | Settings | Reachable at | Use |
|---|---|---|---|
| Private (default) | `ASTRA_PUBLIC_HOSTS` empty, `ASTRA_HTTPS=0`, `ASTRA_TRUSTED_PROXY=0` | `http://127.0.0.1:8765` on the host, or through `ssh -L 8765:127.0.0.1:8765 <host>` | Until a qualified host and a valid certificate exist |
| Public | `ASTRA_PUBLIC_HOSTS=<dedicated DNS name>`, `ASTRA_HTTPS=1`, `ASTRA_TRUSTED_PROXY=1` | `https://<name>` through the TLS reverse proxy only | After the go-live checklist below |

**Installation on a Linux host (requires an administrator with sudo; not yet performed anywhere):**
1. Create the `astra` system user; copy the reviewed commit to `/opt/astra/app` (root-owned).
2. Create `/opt/astra/venv` with Python 3.14 (the version the suite is validated on), then install with `pip install --require-hashes -r requirements/web.txt -r requirements/server.txt`.
3. Install `/etc/astra/web.env` (root:astra 0640), `deploy/systemd/astra-web.service`, `astra-alert@.service` and `astra-cert-check.{service,timer}`.
4. Initialise the first administrator as the `astra` user: `sudo -u astra env $(cat /etc/astra/web.env | xargs) PYTHONPATH=/opt/astra/app/src /opt/astra/venv/bin/python -m astra auth init-admin --username <name>`.
5. Enable `astra-web`. Check with `systemd-analyze security astra-web`, and verify that outbound traffic from the unit is refused and the journal shows no SIGSYS.
6. Public mode only:
   - load `deploy/nftables/astra-loopback.nft`, so only the proxy user can reach port 8765;
   - configure the proxy: `deploy/apache/astra.conf.example` on a host whose Apache already owns ports 80 and 443, otherwise `deploy/nginx/astra.conf.example`;
   - obtain the certificate for the dedicated name, with a renewal deploy hook that reloads the proxy;
   - enable the certificate check timer.

**Go-live checklist (all must be verified on the host and recorded in the report):**
- `ss -tlnp`: only the proxy listens on 80/443, and 8765 listens on 127.0.0.1 only.
- From outside: 8765 is unreachable, 80 only redirects and serves ACME challenges, and 443 serves the valid certificate.
- Forged Host, `X-Forwarded-For` and `Forwarded` headers have no effect.
- File modes:
  - `/opt/astra` root-owned;
  - `/etc/astra/web.env` 0640;
  - `/var/lib/astra` 0700, with the databases and `web_secret.key` 0600;
  - the TLS key 0600.
- Renewal dry run passes, the reload hook fires and a test alert reaches the operator.
- Restart drills: kill gunicorn, then reboot the host.
- Restore drill of `auth.sqlite3` and `astra.sqlite3`.

**Behaviour on incidents:**

| Event | Behaviour |
|---|---|
| Host reboot | systemd starts `astra-web` (enabled unit); sessions survive (SQLite); nothing trading-related starts |
| Dashboard crash | `Restart=on-failure` after 5 s. After 5 failures in 5 minutes the unit stays failed and `OnFailure` raises an alert. A single instance is guaranteed: one unit, and the port is already bound. |
| Database failure | Data routes answer 503 `storage_unavailable`; a failure of the session store answers 503 `auth_unavailable`, never anonymous access |
| Certificate expired or renewal failed | The check timer fails below the threshold and alerts. Browsers refuse the site; nothing falls back to HTTP. |
| Storage full | SQLite writes fail, so logins and session refresh answer 503. Free space, then restart. |
| Failed migration (schema change) | The stores create missing tables only; restore the previous backup and the previous code (rollback below) |
| Backup restore | Restore `auth.sqlite3` with the service stopped. Sessions in the restored copy are only valid if they have not expired. Rotate `web_secret.key` to invalidate every CSRF token and device cookie if the backup leaked. |

**Update, backup, rollback.**
- **Update:** deploy a new reviewed commit to `/opt/astra/app` next to the previous one, switch, then restart `astra-web`.
- **Rollback:** switch back to the previous directory and restart.
- **Backup:** copy `auth.sqlite3` with `sqlite3 .backup` (it holds Argon2id hashes and TOTP secrets: keep the copy encrypted and access-restricted), and `astra.sqlite3` with `astra backup create`.

## Not provided

- External supervision is **not provided or tested**: nothing restarts the service after a crash or starts it at boot, and no alert is sent when the heartbeat goes stale. Next step: choose and test a supervisor (for example Windows Task Scheduler or NSSM on Windows, systemd on Linux) and scheduled backups, then document the tested configuration here.
- A systemd unit, a proxy configuration and alert units for the dashboard exist in `deploy/`, written and reviewed but not yet installed on any host. No container image, hosting, encrypted backup or supervisor for the trading service exists. A continuous integration workflow (build and tests only, no deployment) is defined in `.github/workflows/ci.yml` (Gate B, see [docs/CI.md](docs/CI.md)) but has not executed yet (GitHub account locked for a billing issue); verification can also be run by hand (see [TESTING.md](TESTING.md)).
- No frontend/backend split: the dashboard and the research and service code share one local process model. V5 section 5.13 (separate persistent service, secured web access) and gate H are not started.
