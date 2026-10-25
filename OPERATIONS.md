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
- Scope: the kill switch is global to the store. Freezes and the daily-loss day marker are per account (`TradingControls(scope=account_ref)`, `OrderGateway(account_ref=...)`): an incident on one account does not block another, and reconciling one account does not clear another's freezes. Global freezes (written by a gateway built without `account_ref`) apply to every account.
- `controls show` and `/api/controls` return `entry_freezes` (global freezes) and `entry_freezes_by_account` (active freezes per account; the key `"*"` holds global freezes).

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
| Position divergence or foreign order | `position_divergence` freeze; requires investigation |
| Daily loss limit | Entry rejected, freeze and `alert.risk_daily_loss_limit` audit event |
| Command created in the future | Rejected and `clock_inconsistent` freeze |

## Restart and recovery

Orders, transitions, controls, reconciliations, experiments and model versions persist in SQLite. After a restart, run reconciliation before new entries; orders found in `CREATED`/`VALIDATING`/`AUTHORIZED` (never sent) are closed as abandoned, and `SUBMITTED`/`UNKNOWN` orders are looked up at the adapter (an adapter without lookup by client order id, such as cTrader, leaves them for human resolution, above). Local state is never assumed correct without that check.

The `SimulatedBroker` is in-memory, so cross-process reconciliation against it is not meaningful; the paper replay creates a new simulated account per run and reconciles at start and end.

## Backup

Stop the service, then copy the SQLite file (default `var/astra.sqlite3`). A restore procedure and restore test are **not yet implemented or verified**.

## Not implemented

Service supervisor, alert delivery (e-mail/messaging), metrics endpoint beyond `/api/health`, log shipping, backup automation and restore test.
