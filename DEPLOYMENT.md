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
| `ASTRA_AUTH_DATABASE`, `ASTRA_WEB_SECRET_FILE` | `config.WebConfig` | Dashboard accounts and sessions (SQLite) and the CSRF/device-cookie key file |
| `ASTRA_PUBLIC_HOSTS`, `ASTRA_HTTPS`, `ASTRA_TRUSTED_PROXY`, `ASTRA_PROXY_SECRET_FILE` | `config.WebConfig` | Public mode behind the TLS proxy; all four are required together (section "Remote dashboard") |

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
| Public | `ASTRA_PUBLIC_HOSTS=<dedicated DNS name>`, `ASTRA_HTTPS=1`, `ASTRA_TRUSTED_PROXY=1`, `ASTRA_PROXY_SECRET_FILE=/etc/astra/proxy.secret` | `https://<name>` through the TLS reverse proxy only | After the go-live checklist below |

**Prerequisite (blocking): a trustworthy host,** qualified with [docs/HOST_QUALIFICATION.md](docs/HOST_QUALIFICATION.md), section B. The previously inspected server `102.211.199.132` is not used (section A of that document).
- A host with an open or only partially remediated intrusion is not acceptable. Root-level access there exposes the password hashes, the TOTP secrets, the CSRF key, the proxy secret and the TLS key, and captures what users type; no application or proxy setting mitigates that.
- Before installing, record:
  - forensic closure or a rebuild of the host;
  - the list of every listening port with its owner (`ss -tlnp` as root);
  - the host firewall policy.
- A dedicated clean VM is preferred.

**Installation on a Linux host (requires an administrator with sudo; not yet performed anywhere):**
1. **Python 3.14** (production target, decided in AUTH-2). Install a maintained 3.14 patch release from a trusted source; it receives bugfixes until October 2030, whereas 3.12 only gets security fixes. `deploy/PYTHON_VERSION` records it, and the production entry point (`create_app_from_env`) refuses any other minor version. Using 3.12 instead would require running and recording the full suite on it first; that variant is not validated.
2. **Releases.** Create the `astra` system user. Copy the reviewed commit to `/opt/astra/releases/<sha>` (root-owned), with its own venv: `python3.14 -m venv /opt/astra/releases/<sha>/venv`, then `pip install --require-hashes -r requirements/web.txt -r requirements/server.txt`. Point `/opt/astra/app` and `/opt/astra/venv` at that release with `ln -sfn`.
3. **Configuration.**
   - Install `/etc/astra/web.env` (root:astra 0640) and `/etc/astra/alert.env` (templates in `deploy/`).
   - Create the MFA key ring **as root**, before the first administrator: `sudo env ASTRA_MFA_KEY_FILE=/etc/astra/mfa.key PYTHONPATH=/opt/astra/app/src /opt/astra/venv/bin/python -m astra auth mfa-key-init --group astra`. This writes it as root:astra 0640, and later rewrites keep that owner, group and mode.
   - Back the key ring up separately from the database backups.
   - In public mode the dashboard refuses to start without it, or with it in the database directory. Without the key ring, TOTP sign-in fails closed.
   - Public mode only: generate the proxy secret into `/etc/astra/proxy.secret` (root:astra 0640) and into the proxy's root-only include (`deploy/apache/astra-proxy-secret.conf.example`).
   - Install the units: `astra-web`, `astra-nft`, `astra-worker`, `astra-alert@`, `astra-cert-check.{service,timer}`, `astra-backup.{service,timer}`.
   - On a host whose reverse proxy is not `www-data` (Caddy runs as `caddy`), install `/etc/astra/astra-loopback.nft` with that user and a drop-in for `astra-nft` pointing to it.
4. **First administrator.** Create it in the service's own context, so the state directory and permissions are right: `sudo systemd-run --pipe --wait -p User=astra -p EnvironmentFile=/etc/astra/web.env -p StateDirectory=astra -p StateDirectoryMode=0700 -p UMask=0077 -p Environment=PYTHONPATH=/opt/astra/app/src /opt/astra/venv/bin/python -m astra auth init-admin --username <name>`. The password is typed twice, or with `--generate` shown once.
5. **Start and verify the unit.**
   - Enable `astra-web` (it also starts `astra-nft`), `astra-worker`, `astra-cert-check.timer` and `astra-backup.timer`.
   - The worker needs outbound HTTPS to `data-api.ecb.europa.eu`; the dashboard itself keeps `IPAddressDeny=any`.
   - Check `systemd-analyze security astra-web`, that the journal shows no SIGSYS, and that outbound traffic from the unit is refused.
   - Check that `sudo -u nobody curl -m 3 http://127.0.0.1:8765/api/health` is refused by the nftables rule.
6. **Public mode only, with Apache already serving other sites:**
   1. Enable only the `:80` virtual host of `deploy/apache/astra.conf.example`.
   2. Run `certbot certonly --webroot -w /var/www/acme -d <name> --deploy-hook "systemctl reload apache2"`.
   3. Enable the `:443` virtual host.
   4. Before every reload, run `apache2ctl configtest` and `apache2ctl -S`. The ASTRA virtual host must not become the default `*:443` host of the other sites.
   5. Enable `astra-cert-check.timer`.

**Go-live checklist (all must be verified on the host and recorded in the report):**
- The trustworthy-host prerequisite above is met and recorded.
- Ports:
  - `ss -tlnp`: only the proxy listens on 80 and 443, and 8765 listens on 127.0.0.1 only.
  - From outside: 8765 is unreachable, 80 only redirects and serves ACME challenges, and 443 serves the valid certificate.
- Proxy protections:
  - a local process other than the proxy user cannot reach 8765 (nftables);
  - a request without the proxy secret gets 403;
  - forged Host, `X-Forwarded-For` and `Forwarded` headers have no effect.
- File modes:
  - `/opt/astra` root-owned;
  - `/etc/astra/web.env` and `/etc/astra/proxy.secret` 0640;
  - `/var/lib/astra` 0700, with the databases and `web_secret.key` 0600;
  - the proxy include and the TLS key 0600.
- Alerts: a renewal dry run passes, the reload hook fires, and a test alert reaches the operator (journal only is not enough). The alert unit is sandboxed (`ProtectSystem=strict`, `NoNewPrivileges`), so a queue-based MTA such as Postfix (setgid `postdrop`) cannot queue mail from it: use a relay-only client (for example msmtp) or adjust the unit, and test.
- Restarting or reloading `nftables.service` re-runs `/etc/nftables.conf`, which usually flushes every rule. `astra-nft` follows both (`PartOf=` for a restart, `ReloadPropagatedFrom=` for a reload) and rebuilds its table. Verify that the rule is present again after a restart and after a reload.
- Drills: kill gunicorn, reboot the host (the nftables rule comes back before the app), and restore `auth.sqlite3` and `astra.sqlite3`.

**Behaviour on incidents:**

| Event | Behaviour |
|---|---|
| Host reboot | systemd starts `astra-web` (enabled unit); sessions survive (SQLite); nothing trading-related starts |
| Dashboard crash | `Restart=on-failure` after 5 s. After 5 failures in 5 minutes the unit stays failed and `OnFailure` raises an alert. A single instance is guaranteed: one unit, and the port is already bound. |
| Database failure | Data routes answer 503 `storage_unavailable`; a failure of the session store answers 503 `auth_unavailable`, never anonymous access |
| Certificate expired or renewal failed | The check timer fails below the threshold and alerts. Browsers refuse the site; nothing falls back to HTTP. |
| Storage full | SQLite writes fail, so logins and session refresh answer 503. Free space, then restart. |
| Failed migration (schema change) | The stores create missing tables only; restore the previous backup and the previous code (rollback below) |
| Backup restore | Restore `auth.sqlite3` with the service stopped. Sessions in the restored copy are only valid if they have not expired. |
| A copy of `auth.sqlite3` leaked | It holds the Argon2id hashes and the TOTP secrets.<br>1. Disable every account.<br>2. Rotate `web_secret.key`, which invalidates CSRF tokens and device cookies.<br>3. Reset the password and the MFA of every account (`astra auth reset-password`, `reset-mfa`), then re-enable them. |

**Update, backup, rollback.**
- **Update:**
  1. Back up first (below).
  2. Install the new reviewed commit as a new release directory with its own venv.
  3. Switch the `/opt/astra/app` and `/opt/astra/venv` symlinks.
  4. Restart `astra-web`: a restart, not a reload, because the app is preloaded.
- **Rollback:** point the symlinks back at the previous release and restart. If the new release changed a schema, restore the backup taken before the update.
- **Backup:**
  - `astra backup create` with `ASTRA_BACKUP_DIR=/var/lib/astra/backups`;
  - `auth.sqlite3` with the SQLite online backup, for example `python -c "import sqlite3; s=sqlite3.connect('/var/lib/astra/auth.sqlite3'); d=sqlite3.connect('/var/lib/astra/backups/auth.sqlite3'); s.backup(d)"`, run as `astra`;
  - schedule both from a systemd timer;
  - copy them off the host encrypted: they are secrets.

## Not provided

- External supervision is **not provided or tested**: nothing restarts the service after a crash or starts it at boot, and no alert is sent when the heartbeat goes stale. Next step: choose and test a supervisor (for example Windows Task Scheduler or NSSM on Windows, systemd on Linux) and scheduled backups, then document the tested configuration here.
- A systemd unit, a proxy configuration and alert units for the dashboard exist in `deploy/`, written and reviewed but not yet installed on any host. No container image, hosting, encrypted backup or supervisor for the trading service exists. A continuous integration workflow (build and tests only, no deployment) is defined in `.github/workflows/ci.yml` (Gate B, see [docs/CI.md](docs/CI.md)) but has not executed yet (GitHub account locked for a billing issue); verification can also be run by hand (see [TESTING.md](TESTING.md)).
- No frontend/backend split: the dashboard and the research and service code share one local process model. V5 section 5.13 (separate persistent service, secured web access) and gate H are not started.
