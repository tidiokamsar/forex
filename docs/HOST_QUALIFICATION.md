# Host qualification

ASTRA is installed only on a host that has passed this qualification. A host is not considered clean because its visible services work.

## A. The previously inspected server (102.211.199.132): not used

Findings from a read-only inspection on 2026-10-27; nothing was modified:
- an intrusion on 2026-10-19 whose remediation is incomplete;
- an unidentified listener on `0.0.0.0:62354`, whose owner an unprivileged account cannot see;
- GLPI already owns ports 80 and 443 and the bare IP address;
- many other exposed services (SMTP, IMAPS, RustDesk, SSH jump host);
- no passwordless administration;
- Python 3.12 only.

Procedure to qualify it, run by its administrator. ASTRA does not run it.

1. **Isolate and preserve before touching anything.** Snapshot or image the disks and memory if possible. Keep logs (`/var/log`, journal, web server and SSH logs) on separate media. Do **not** kill the unknown process blindly: identify it first, because killing it destroys evidence and may trigger destructive behaviour.
2. **Identify port 62354**, as root and read-only:
   - `ss -tlnp 'sport = :62354'` gives the process and PID;
   - `ls -l /proc/<pid>/exe`, `cat /proc/<pid>/cmdline` and `ls -l /proc/<pid>/cwd` show its binary, arguments and working directory;
   - `systemctl status <pid>` shows its owning unit;
   - `dpkg -S <binary>` (or `rpm -qf`) says whether a package owns the binary;
   - compare its hash with the package manifest (`debsums -c` or `dpkg --verify`).

   An unowned binary, a deleted executable (`(deleted)` in `/proc/<pid>/exe`), or a binary in `/tmp`, `/dev/shm` or a home directory is a compromise indicator.
3. **Review the scope of the intrusion:**
   - accounts and keys: `/etc/passwd` and `/etc/shadow` changes, `authorized_keys` of every user, sudoers;
   - persistence: cron jobs, systemd units and timers, `rc.local`, shell profiles, `LD_PRELOAD`, kernel modules;
   - package integrity: `debsums`, `dpkg --verify`;
   - web roots: GLPI files modified since the intrusion;
   - outbound connections.
4. **Decide.** The only acceptable outcome of an incomplete remediation is a **rebuild**: reinstall from a trusted image, restore the data only from backups taken before the intrusion or after verification, and rotate every credential and key that existed on the host. Then run section B on the rebuilt host.

## B. Qualifying a dedicated host for ASTRA (required before any installation)

1. **Trusted origin.**
   - Install from an official image (for example an Ubuntu Server LTS ISO or cloud image) whose checksum and signature you have verified against the vendor's published values.
   - Apply all updates.
   - Record the image name, checksum and date.
2. **Minimal surface.**
   - Only SSH, plus the reverse proxy once the go-live checklist allows it.
   - No other application on the host: GLPI, mail, remote-desktop and jump-host roles live elsewhere.
   - Record `ss -tlnp` (as root), with every listener identified.
3. **Access.**
   - SSH with keys only (`PasswordAuthentication no`, `PermitRootLogin no`), restricted to named administrators, ideally from a VPN or jump host.
   - Admin users need sudo with a password.
4. **Firewall:** inbound default deny, then allow SSH (from the admin network only) and, for public mode, 80 and 443. Record the policy.
5. **Hygiene:**
   - NTP synchronised;
   - unattended security updates;
   - logs kept and shipped off the host if possible;
   - disk with at least 20 GB free for ASTRA data, logs and backups.
6. **Python 3.14.** It must come from a trusted source (the distribution, if it ships 3.14, or a source build verified against python.org signatures), at the patch version recorded in the deployment report. `deploy/PYTHON_VERSION` holds the required minor version, and the production entry point refuses any other.
7. **Record** steps 1 to 6 in the deployment report before running DEPLOYMENT.md, "Remote dashboard".

What ASTRA still needs from the operator: authorised SSH access, administrator privileges on the qualified host, a DNS name (or another valid HTTPS solution), an alert channel, and a backup and restore policy that includes the MFA key ring kept separately from the database backups.
