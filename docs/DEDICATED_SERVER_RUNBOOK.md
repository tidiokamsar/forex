# Runbook: ASTRA on a dedicated, healthy server

Ordered procedure for the future production host. **Do not run any step until the architect has approved installation.** The previously inspected server `102.211.199.132` is excluded: see [HOST_QUALIFICATION.md](HOST_QUALIFICATION.md), section A, and do not modify it. Execution stays SIMULATION only. No broker key is installed, and nothing in this runbook opens an order path.

Each step lists what to record in the deployment report. Detailed configuration lives in [DEPLOYMENT.md](../DEPLOYMENT.md), section "Remote dashboard", and in [OPERATIONS.md](../OPERATIONS.md).

## 0. Inputs the operator must provide (never in a conversation or the repository)

| Input | Who | Note |
|---|---|---|
| A new dedicated VM or machine | Operator | No other application on it; no paid infrastructure without approval |
| Authorised SSH access with an admin account (sudo with password) | Operator | Key-based; the key never goes through ASTRA tooling |
| A DNS name pointing to the host (for example `astra.<organisation domain>`) | DNS owner | Required for public HTTPS |
| Alert recipient and a relay MTA (for example msmtp to the organisation's mail relay) | Operator | Test delivery before go-live |
| Backup destination off the host | Operator | Separate locations for the databases and for the MFA key ring |

## 1. Official, verified system image

1. Download the official Ubuntu Server 24.04 LTS image (or the organisation's approved distribution) from the vendor.
2. Verify the image before installing: `sha256sum` against the vendor's `SHA256SUMS`, and `gpg --verify SHA256SUMS.gpg SHA256SUMS` with the vendor key.
3. Install with a minimal profile. Apply updates (`apt update && apt full-upgrade`) and enable `unattended-upgrades` for security updates.
4. **Record:** image name, checksum, signature result, date.

## 2. Access and firewall

1. SSH:
   - in `sshd_config`: `PasswordAuthentication no`, `PermitRootLogin no`, `AllowUsers <named admins>`;
   - test a second session before closing the first.
2. Firewall (ufw or nftables):
   - inbound default deny;
   - allow 22 from the admin network only;
   - allow 80 and 443 only when step 9 starts;
   - outbound: allow updates, NTP, DNS and the mail relay.
3. Time: `timedatectl` shows NTP synchronised.
4. **Record:** `ss -tlnp` as root (every listener identified), the firewall rules, and the sshd settings.

## 3. Python 3.14 (production target)

1. Install a maintained Python 3.14 patch release from a trusted source: the distribution's package if it ships 3.14, otherwise a source build from python.org verified with its release signature.
2. Check: `python3.14 --version` matches `deploy/PYTHON_VERSION` (`3.14`). The dashboard's production entry point refuses any other minor version.
3. **Record:** exact version and source.

## 4. Service account, code and dependencies

1. Create the service account: `useradd --system --home /nonexistent --shell /usr/sbin/nologin astra`.
2. Copy the reviewed commit (its SHA approved by the architect) to `/opt/astra/releases/<sha>`, root-owned:
   - use `git archive <sha>` from a verified clone;
   - check `git log -1` and that the SHA matches the report.
3. Create the venv: `python3.14 -m venv /opt/astra/releases/<sha>/venv`, then `pip install --require-hashes -r requirements/web.txt -r requirements/server.txt`. Every package is pinned with its hashes.
4. Point the links at that release: `ln -sfn /opt/astra/releases/<sha> /opt/astra/app` and `ln -sfn /opt/astra/releases/<sha>/venv /opt/astra/venv`.
5. Run the suite on the host before going further: `cd tests && PYTHONPATH=../src /opt/astra/venv/bin/python -m unittest discover -s . -v`. The five POSIX-only tests (file modes, symlinks), skipped on Windows, run there and must pass; the expected result is the one in reports/AUTH2_HARDENING_REPORT.md (15 expected failures, all in Lot 4 modules).
6. **Record:** SHA, `pip freeze`, the suite result and its exit code.

## 5. Secrets and keys (created on the host, never transmitted)

All of these commands run as root, from `/opt/astra/app`, with `PYTHONPATH=src`:
1. MFA key ring: `env ASTRA_MFA_KEY_FILE=/etc/astra/mfa.key /opt/astra/venv/bin/python -m astra auth mfa-key-init --group astra`. This creates it as root:astra 0640.
2. Proxy secret (public mode only): follow `deploy/apache/astra-proxy-secret.conf.example`. Use umask 077; the app-side file is root:astra 0640, and the proxy include is root 0600.
3. `/etc/astra/web.env` (template `deploy/astra-web.env.example`, root:astra 0640). Start in **private mode** (no public host).
4. `/etc/astra/alert.env` (template `deploy/astra-alert.env.example`).
5. **Record:** file paths and modes (`stat -c '%U:%G %a %n'`), never their contents.

## 6. First administrator

1. Run, as root, in the service's own context:

   ```
   sudo systemd-run --pipe --wait -p User=astra -p EnvironmentFile=/etc/astra/web.env -p StateDirectory=astra -p StateDirectoryMode=0700 -p UMask=0077 -p Environment=PYTHONPATH=/opt/astra/app/src /opt/astra/venv/bin/python -m astra auth init-admin --username <name>
   ```

   The password is typed twice, or with `--generate` shown once and to be changed at first sign-in. The command refuses to run if an account already exists.
2. At first sign-in the administrator enrols TOTP and stores the 10 recovery codes offline.
3. **Record:** the account name only.

## 7. Services and supervision

1. Install the units from `deploy/systemd/`:
   - `astra-web.service`;
   - `astra-nft.service`, required by astra-web;
   - `astra-alert@.service`;
   - `astra-cert-check.service` and `astra-cert-check.timer`.
2. Run `systemctl daemon-reload && systemctl enable --now astra-web`.
3. Check the unit:
   - `systemd-analyze security astra-web`;
   - the journal shows no SIGSYS;
   - outbound traffic from the unit is refused;
   - `sudo -u nobody curl -m 3 http://127.0.0.1:8765/api/health` is refused by nftables.
4. Supervision:
   - `Restart=on-failure`;
   - after repeated failures, `OnFailure=` raises the alert (test it with a deliberate failure);
   - certificate checks every 6 hours.
5. **Record:** unit status, the result of the alert test, and the restart drill.

## 8. Private-mode acceptance (before any public exposure)

1. From an admin workstation: `ssh -L 8765:127.0.0.1:8765 <host>`, then open `http://127.0.0.1:8765`.
2. Check the flow: sign-in, TOTP, the forced password change, the role pages, sign-out, and that an old cookie is refused.
3. **Record:** the results.

## 9. Domain and HTTPS (public mode, after acceptance and approval)

1. DNS: the dedicated name resolves to the host.
2. Reverse proxy: `deploy/nginx/astra.conf.example`, or the Apache variant if Apache is the chosen proxy. It must be the only component listening on 80 and 443.
3. Certificate: `certbot certonly --webroot -w /var/www/acme -d <name> --deploy-hook "systemctl reload nginx"`, then `certbot renew --dry-run`.
4. Switch `web.env` to public mode (`ASTRA_PUBLIC_HOSTS`, `ASTRA_HTTPS=1`, `ASTRA_TRUSTED_PROXY=1`, `ASTRA_PROXY_SECRET_FILE`), then restart astra-web.
5. Run the go-live checklist in DEPLOYMENT.md, every item:
   - from outside, port 8765 is unreachable;
   - forged headers have no effect;
   - a request without the proxy secret gets 403;
   - the certificate is valid in a browser.
6. **Record:** the URL tested from an outside client, the certificate's expiry date, and the exposed ports seen from outside.

## 10. Backups and restore

1. Schedule from a systemd timer:
   - `astra backup create` with `ASTRA_BACKUP_DIR=/var/lib/astra/backups`;
   - the SQLite online backup of `auth.sqlite3` (OPERATIONS.md);
   - check that backup files are 0600 (`astra backup create` creates them owner-only) in a 0700 directory;
   - copy off the host encrypted.
2. Back up the MFA key ring **separately** from the database backups. Each one without the other cannot reveal the TOTP secrets.
3. Restore drill:
   - stop astra-web;
   - restore both databases on a test path;
   - start with the backed-up key ring;
   - sign in with TOTP;
   - restore as the `astra` user, or afterwards reset the restored database's owner and mode to those of the file it replaced (`restore_backup` keeps the backup's own mode and owner).
4. **Record:** backup schedule, locations, and the result of the drill.

## 11. Stop point

Report steps 1 to 10, with their records, to the architect. Trading stays in SIMULATION: no broker key, no DEMO or LIVE activation, no merge into `main` without approval.
