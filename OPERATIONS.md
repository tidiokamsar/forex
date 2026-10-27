# Operations

All commands run from the project folder with `$env:PYTHONPATH = "src"` (PowerShell). Everything below is SIMULATION only, on synthetic data: DEMO orders are disabled, LIVE is locked, and no command sends an order outside the process. The only command that contacts a provider is the read-only `ctrader-check` ([BROKERS.md](BROKERS.md)), which has not yet been run against the real service.

## Normal local run

1. `python -m astra status`: confirm `SIMULATION`, `live_locked: true`, `external_orders: false`.
2. Research: `python -m astra demo`, `walk-forward ...`, `evaluate-strategies ...` or `ml-baseline` (see README and [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md)).
3. Paper replay through the Order Gateway: `python -m astra paper-replay [--strategy sma|momentum|breakout]`.
4. Inspect: `python -m astra orders`, `python -m astra controls show`, `python -m astra models`, `python -m astra attempts list|count|verify`, or `python -m astra serve` for the read-only dashboard.

## Research integrity checks (lot 4)

- `python -m astra attempts verify [--expected-head ROW_HASH]` checks the hash-chained attempt log (exit code 1 when it does not verify) and prints the current chain head. `list` and `count` print it too. The database is opened read-only.
- Keep the printed chain head outside the database (printed, committed, or copied to other media) after each research session. Without an external copy, a consistent rewrite of the chain by the file owner passes `verify`; with one, `--expected-head` detects it, but only for the rows up to that head.
- Evidence anchor: set `ASTRA_EVIDENCE_ANCHOR` before the first sealed plan of a store (see [DEPLOYMENT.md](DEPLOYMENT.md)). The shipped `LocalFileAnchor` is a local file, **not production grade**. No CLI command shows or verifies the anchor; `anchoring.verify_store_against_anchor(database, anchor)` is the Python entry point, and sealing, holdout scoring and promotion call it themselves.
- Promotion has no CLI command. It is a Python call by a human reviewer (`ModelRegistry.promote(..., holdout_bars=...)`) that re-derives the holdout evidence from the bars; with `LocalFileAnchor` it also requires the explicit, audited `allow_non_production_anchor=True`. A `CHAMPION` status is not used by the service or the paper replay.

## Emergency stop and freezes

- Activate: `python -m astra controls kill --reason "<why>" --operator <name>`. Blocks every new order, including automated exits. A human-submitted exit is still allowed so a position can be flattened.
- Release: `python -m astra controls release --reason "<why>" --operator <name>`. Requires a human role. In this local CLI the role is asserted by the local user, not authenticated.
- Entry freezes block new entries but allow risk-reducing exits. `connection_lost`, `reconciliation_required`, `position_divergence` and `unknown_order_state` are cleared **only** by a consistent reconciliation. `daily_loss_limit` lasts until the next UTC day. Each change is an audit event.
- Scope: the kill switch is global to the store. Freezes and the daily-loss day marker are per account (`TradingControls(scope=account_ref)`, `OrderGateway(account_ref=...)`): an incident on one account does not block another, and reconciling one account does not clear another's freezes. Global freezes (written by a gateway built without `account_ref`) apply to every account.
- `controls show` returns `entry_freezes` (global freezes) and `entry_freezes_by_account` (active freezes per account; the key `"*"` holds global freezes). `/api/controls` returns the same `entry_freezes_by_account`, but its `entry_freezes` is the union of the codes of every scope, and its `entries_allowed` is false as soon as any account (or the global scope) is frozen.

## Resolving an UNKNOWN order

An order left `UNKNOWN` (or stuck in `SUBMITTED`) freezes entries. When the adapter cannot settle it, for example cTrader, which has no lookup by client order id, a human records what the provider's own records show:

```powershell
python -m astra orders --resolve <INTENT_ID> --outcome FILLED|NOT_EXECUTED --evidence "<provider deal/order id or statement reference>" --operator <name> [--filled-units N --price P]
```

1. Check the order in the provider's platform or statement. Never guess.
2. Run the command. Evidence of at least 8 characters is required; `FILLED` also requires `--filled-units` (positive, at most the requested units) and `--price`. Only `HUMAN_ADMIN` and `HUMAN_OPERATOR` roles may resolve (the CLI asserts `HUMAN_OPERATOR`).
3. `FILLED` for the full size ends the order `FILLED`; a smaller size ends it `PARTIALLY_FILLED` then `CANCELLED`; `NOT_EXECUTED` ends it `CANCELLED`. The resolution and its evidence are an audit event (`order.resolved_by_operator`).
4. Resolution never lifts a freeze. Run a reconciliation against the broker afterwards; only a consistent one clears the freezes.

The CLI has no adapter attached, so it records the operator's evidence without a broker lookup (`broker_lookup: not_performed_cli_has_no_adapter`). `OrderGateway.resolve_unknown_order` looks the order up first and refuses the resolution if the adapter still reports it; reconcile instead in that case.

## Incident rules implemented in the gateway

| Situation | Behaviour |
|---|---|
| Stale or missing quote | Entry rejected (`data_stale_or_unknown`) |
| Spread above policy | Entry rejected (`spread_excessive_or_unknown`) |
| Account/adapter mode disagrees or is unverified | Rejected and `mode_mismatch` freeze |
| Submit timeout | Order `UNKNOWN`, `unknown_order_state` freeze; a retry of the same intent only looks the order up. Adapters without lookup by client order id (cTrader) leave the order `UNKNOWN` until human resolution, then reconciliation |
| Provider refuses the order request | Order `BROKER_REJECTED` with `broker_request_error:<code>`; no connection freeze |
| Provider refuses a request during reconciliation | Reconciliation `FAILED`; `reconciliation_required` freeze kept, link not declared lost |
| Adapter refuses at its execution boundary (permit or environment guard) | Order `BROKER_REJECTED` with `execution_refused:<reason>`; nothing leaves the process |
| Adapter unreachable, or cTrader authentication/session error | `connection_lost` + `reconciliation_required` freezes |
| Reconnect | Nothing unfreezes until `reconcile()` finds local and broker orders/positions consistent |
| 5 consecutive service tick errors | `service_degraded` freeze; cleared by a human, not by reconciliation |
| Position divergence or foreign order | `position_divergence` freeze; requires investigation |
| Daily loss limit | Entry rejected, freeze and `alert.risk_daily_loss_limit` audit event |
| Command created in the future | Rejected and `clock_inconsistent` freeze |

## Restart and recovery

Orders, transitions, controls, reconciliations, experiments and model versions persist in SQLite. After a restart, run reconciliation before new entries; orders found in `CREATED`/`VALIDATING`/`AUTHORIZED` (never sent) are closed as abandoned, and `SUBMITTED`/`UNKNOWN` orders are looked up at the adapter (an adapter without lookup by client order id, such as cTrader, leaves them for human resolution, above). Local state is never assumed correct without that check.

The `SimulatedBroker` is in-memory, so cross-process reconciliation against it is not meaningful; the paper replay creates a new simulated account per run and reconciles at start and end.

## Continuous service (F-02)

`python -m astra service run` runs `service.TradingService`, a loop that drives one account without a Claude session. In this build the CLI wires it **only** to the in-process `SimulatedBroker` (account `SIM-SERVICE-CLI`) and a `SYNTHETIC` random-walk quote feed; no external connector is wired into the service. The output states `"mode": "SIMULATION"`, `"feed": "SYNTHETIC"`, `"external_orders": false`.

| Option | Default | Effect |
|---|---|---|
| `--strategy sma\|momentum\|breakout` | `sma` | Decision function |
| `--timeframe N` | `60` | Bar length in seconds |
| `--tick-seconds S` | `5.0` | Synthetic feed step |
| `--realtime` | off | Wait `--tick-seconds` between ticks; without it the loop runs as fast as possible on the simulated clock |
| `--max-ticks N` | none | Stop after N ticks; otherwise run until stopped |
| `--seed N` | `7` | Synthetic feed seed |

Behaviour (from `service.py`):
- Each tick: connection check (a lost link freezes `connection_lost` and `reconciliation_required`; the adapter's `reconnect` is called if it has one), broker events, quote, bar builder. A bar is emitted only after it closes; the decision sees closed bars only.
- Reconciliation runs at start, every 15 bars, and while a reconciliation-owned freeze is active (retried at most every 30 s of service clock, and immediately after a successful reconnect). Reconnecting lifts nothing; only a consistent reconciliation does.
- Intent ids are deterministic (`svc:<account>:<strategy>:<bar time>:<purpose>:<side>:<tag>`), and the last processed bar and the active stop price are persisted. A restart that replays the last bar gets the stored intent back from the gateway instead of a second order.
- Every order goes through `OrderGateway` with all its checks; the kill switch and freezes apply.
- Degraded mode: 5 consecutive tick errors, or one permanent reconnect failure (live account refused, configuration error, invalid or expired token, account not authorized), freeze entries for the account with `service_degraded`. After a permanent failure the service stops reconnecting until a human clears the freeze. Provider refusals are logged (`service.request_refused`); rate limits (`transient`) are not counted as tick errors, any other refusal is. Once a human clears the freeze, errors are counted again from zero (`service.rearmed`). The freeze is raised (event `service.degraded`, level CRITICAL) and the heartbeat status becomes `degraded`. This freeze is not reconciliation-owned: a human clears it with `python -m astra controls clear-freeze --freeze service_degraded --account <account_ref> --operator <name>` (role asserted as `HUMAN_OPERATOR`). The same command lifts `operator` freezes; reconciliation-owned freezes (`connection_lost`, `reconciliation_required`, `position_divergence`, `unknown_order_state`) are refused and exit with code 3.
- Graceful stop: Ctrl+C, SIGINT, SIGTERM or SIGBREAK (where the platform has it) ends the loop after the current tick, writes a final `stopped` heartbeat and logs `service.stopped`.

`python -m astra service status` prints, per account, the last heartbeat (`status`, `heartbeat_at`, simulated time, ticks, bars, errors, mode, last bar, stop price), `heartbeat_age_seconds`, and `alive` (status `running` or `degraded` and a heartbeat at most 120 s old). Heartbeats are written on every status change and otherwise at most every 5 s.

Service heartbeats, last bar, stop price and the broker event cursor live in the `service_state` table: operational state, not controls, so they are not audit events. The event cursor is stored per account (`broker_event_cursor:<account_ref>`).

The `SimulatedBroker` is in memory: a new `service run` starts a new, empty simulated account, so state left in the store by an earlier run will not match it. Use a fresh `ASTRA_DATABASE` for independent runs.

## Structured logs (F-01)

The CLI writes JSON lines to **stderr** (stdout stays the command's JSON result):
- `ASTRA_LOG_LEVEL` sets the level (default `WARNING` in the CLI; use `INFO` to see order results and reconciliations).
- `ASTRA_LOG_DIR`, if set, adds a rotating file `astra.jsonl` in that folder (5 MB per file, 5 rotated files).

Each line has `ts` (UTC), `level`, `logger`, `event`, `message`, `correlation_id` when one is set (order commands use the command's `correlation_id`), `fields`, and `exception` when relevant. Field names matching `secret`, `token`, `password`, `passwd`, `api_key`, `authorization` or `credential` (case-insensitive, nested included) are replaced by `****`. Any value of at least 8 characters revealed through `SecretValue.reveal()` (credentials read from `SecretVault`) is also masked inside every text value of the line (message, fields, exception text), before JSON encoding, so lines stay valid JSON. Field names that collide with Python `LogRecord` attributes are written as `field_<name>`.

Events emitted in this build: `order.result`, `order.permission_denied`, `execution.reconciliation`, `execution.connection_lost`, `control.entries_frozen`, `control.kill_switch_activated`, `ctrader.connected`, `ctrader.connect_failed`, `ctrader.session_failed`, `ctrader.reconnect_wait`, `ctrader.reconnected`, `ctrader.rate_limited`, `ctrader.token_expiring`, `ctrader.token_refreshed`, `service.started`, `service.stopped`, `service.tick_failed`, `service.degraded`, `service.reconnect_failed`, `service.request_refused`, `service.rearmed`, `service.quote_unavailable`, `service.reconcile_error`, `backup.created`, `backup.restored`.

Logs are local only: no shipping, no alert delivery.

## Backup and restore (F-04)

```powershell
python -m astra backup create [--dir <folder>] [--keep 14]
python -m astra backup verify <backup file>
python -m astra backup restore <backup file> --operator <name> --reason "<why>"
```

- `create` uses SQLite's online backup API, runs `PRAGMA integrity_check`, and writes `astra-<UTC timestamp>.sqlite3` plus `<file>.manifest.json` (path, SHA-256, creation time, source, size, integrity result, row count per table). The folder is `--dir`, else `ASTRA_BACKUP_DIR`, else `var/backups`. Only the newest `--keep` backups (default 14) are kept.
- `verify` recomputes the SHA-256 and the integrity check and compares row counts with the manifest; any mismatch fails.
- `restore` requires a human role (`HUMAN_ADMIN` or `HUMAN_OPERATOR`; the CLI asserts `HUMAN_OPERATOR`, not authenticated) and a non-empty reason. Stop the service first: the restore is refused while any service heartbeat in the target store is alive (status `running` or `degraded`, heartbeat at most 120 s old). Order of operations, so that no failure leaves the target missing or unfrozen: the backup file actually given is verified (hash, integrity, table counts); it is copied to `<name>.restoring` and the copy is hash-checked; every account that has orders in that copy is frozen with `reconciliation_required` inside the copy; only then is the current database moved to `<name>.pre-restore-<UTC timestamp>` and the copy swapped in. Any failure before the move leaves the live database untouched; a failure of the swap puts the previous database back. A corrupted live database can be restored over: the audit of the restore falls back to the logs, and the corrupted file is kept as `<name>.pre-restore-<UTC timestamp>`. Heartbeats copied from the backup are marked `stopped_by_restore`. Restoring a store that has no orders freezes nothing; the service's startup reconciliation still compares it with the broker. A `backup.restored` audit event records actor, reason, backup, hash and frozen accounts.
- After a restore, entries stay blocked until a reconciliation against the broker is consistent. A backup older than the broker's state is detected at that reconciliation (`DIVERGENT`, for example `position_mismatch`) and the freeze stays.
- Research evidence after a restore: backups contain the attempt log, sealed plans and holdout evaluations, but not the evidence anchor file. With an anchor configured, a restored database older than the anchor is reported as behind it (`store_behind_anchor`, `consumed_holdout_missing`, `sealed_protocol_missing`), and sealing a new plan, scoring a holdout and promotion are refused. There is no command to reconcile a store with its anchor: restore a backup that is not behind the anchor, or start a new store (a new store cannot reuse the old anchor, and a spent holdout is only known to the old anchor and store). Without an anchor, restoring an older backup brings a spent holdout back into the store; such a holdout was scored without an anchor and is never promotable.

Evidence: tested locally against the simulator only (`tests/test_backup.py`; anchor and restore interplay in `tests/test_anchoring.py`). No restore drill has been run against a real broker account.

## Dashboard

The dashboard requires an account. Pages after sign-in: summary, runs, controls and freezes (OPERATOR), orders (OPERATOR), reconciliations (OPERATOR), registry; every data block is labelled SIMULATION or SYNTHETIC because no real market feed is integrated. Account procedures (run locally on the server, as the `astra` user, with the web environment loaded):

| Task | Command |
|---|---|
| First administrator | `python -m astra auth init-admin --username <name>` (typed twice) or `--generate` (shown once, change at first login) |
| New account | Web: Comptes, Créer (admin password required); or `python -m astra auth add-user --username <name> --role READ_ONLY` |
| Compromised account | `astra auth disable --username <name>` (revokes every session), then `reset-password` and `reset-mfa` before re-enabling |
| Lost authenticator | `astra auth reset-mfa --username <name>`; the user re-enrols at the next login |
| Lost password | `astra auth reset-password --username <name>` (temporary password shown once, expires after 72 h) |
| End every session of an account | `astra auth revoke-sessions --username <name>` |
| Housekeeping | `astra auth cleanup` (daily), `astra auth events --limit 200` (audit) |
| MFA key ring status | `astra auth mfa-status`: key ids, keys still used, plaintext secrets left |
| First AUTH-2 start on an existing database | Back up `auth.sqlite3`, run (as root) `astra auth mfa-key-init --group astra` if there is no key ring, then `astra auth mfa-reencrypt`. Only genuine base32 secrets are migrated; anything else is reported as `unreadable` and left untouched. Check `mfa-status` shows `plaintext_secrets: 0`. Until then, TOTP sign-in for the unmigrated accounts answers 503; recovery codes still work. |
| Rotate the MFA key | Run as root: `astra auth mfa-key-rotate`, which adds a new active key and re-encrypts every secret. Writers are serialised by a lock, and the file keeps its owner, group and mode. Running dashboards reload the ring as soon as the file changes, so no restart is needed. Back up the new key ring, then run `astra auth mfa-key-retire --kid <old>` once `mfa-status` no longer lists the old key; retirement is refused while a secret still uses it. |
| Key ring created before AUTH-2 fixes (owned by `astra`) | Once, as root: `chown root:astra /etc/astra/mfa.key && chmod 0640 /etc/astra/mfa.key`; later rewrites keep that owner. |
| Lost or damaged key ring | Restore it from its separate backup. If it is lost for good, nothing can decrypt the TOTP secrets: run `astra auth reset-mfa` for every account (users re-enrol) after a fresh `mfa-key-init`. Recovery codes still let users in meanwhile. |
| Renew the proxy secret | Write the new value as the first line of `/etc/astra/proxy.secret`, keep the old one as the second, restart `astra-web`, then update the proxy include and reload the proxy. Finally remove the old line and restart again. |

## Not implemented

External service supervision (restart on crash, start at boot), alert delivery (e-mail/messaging), metrics endpoint beyond `/api/health`, log shipping, scheduled or encrypted backups, drift monitoring, shadow-mode signal comparison, and a continuous service wired to a real connector or a real feed. In V5 terms (section 5.13 and gates F and H), operations and observability are partial: local logs, heartbeat, backup and a read-only local dashboard exist; supervision, alerting, secured remote access and a shadow service on a real feed do not.
