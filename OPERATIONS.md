# Operations

All commands run from the project folder with `$env:PYTHONPATH = "src"` (PowerShell). Everything below is SIMULATION only.

## Normal local run

1. `python -m astra status`: confirm `SIMULATION`, `live_locked: true`, `external_orders: false`.
2. Research: `python -m astra demo`, `walk-forward ...`, or `ml-baseline` (see README).
3. Paper replay through the Order Gateway: `python -m astra paper-replay [--strategy sma|momentum|breakout]`.
4. Inspect: `python -m astra orders`, `python -m astra controls show`, `python -m astra models`, or `python -m astra serve` for the read-only dashboard.

## Emergency stop and freezes

- Activate: `python -m astra controls kill --reason "<why>" --operator <name>`. Blocks every new order, including automated exits. A human-submitted exit is still allowed so a position can be flattened.
- Release: `python -m astra controls release --reason "<why>" --operator <name>`. Requires a human role. In this local CLI the role is asserted by the local user, not authenticated.
- Entry freezes block new entries but allow risk-reducing exits. `connection_lost`, `reconciliation_required`, `position_divergence` and `unknown_order_state` are cleared **only** by a consistent reconciliation. `daily_loss_limit` lasts until the next UTC day. Each change is an audit event.

## Incident rules implemented in the gateway

| Situation | Behaviour |
|---|---|
| Stale or missing quote | Entry rejected (`data_stale_or_unknown`) |
| Spread above policy | Entry rejected (`spread_excessive_or_unknown`) |
| Account/adapter mode disagrees or is unverified | Rejected and `mode_mismatch` freeze |
| Submit timeout | Order `UNKNOWN`, `unknown_order_state` freeze; a retry of the same intent only looks the order up |
| Adapter unreachable | `connection_lost` + `reconciliation_required` freezes |
| Reconnect | Nothing unfreezes until `reconcile()` finds local and broker orders/positions consistent |
| Position divergence or foreign order | `position_divergence` freeze; requires investigation |
| Daily loss limit | Entry rejected, freeze and `alert.risk_daily_loss_limit` audit event |
| Command created in the future | Rejected and `clock_inconsistent` freeze |

## Restart and recovery

Orders, transitions, controls, reconciliations, experiments and model versions persist in SQLite. After a restart, run reconciliation before new entries; orders found in `CREATED`/`VALIDATING`/`AUTHORIZED` (never sent) are closed as abandoned, and `SUBMITTED`/`UNKNOWN` orders are looked up at the adapter. Local state is never assumed correct without that check.

The `SimulatedBroker` is in-memory, so cross-process reconciliation against it is not meaningful; the paper replay creates a new simulated account per run and reconciles at start and end.

## Backup

Stop the service, then copy the SQLite file (default `var/astra.sqlite3`). A restore procedure and restore test are **not yet implemented or verified**.

## Not implemented

Service supervisor, alert delivery (e-mail/messaging), metrics endpoint beyond `/api/health`, log shipping, backup automation and restore test.
