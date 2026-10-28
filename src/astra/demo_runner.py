"""astra-demo: the Binance Spot DEMO runtime (account check, controlled test order, supervised loop).

Every order goes through ``OrderGateway`` (configured for DEMO, crypto spot policy) and the adapter's permit
boundary. The controlled test is a technical order requested by a human, minimum size, never a strategy
decision; it is bought, looked up, reconciled, sold back and reconciled again, and each step is stored.
Automatic strategy orders are not enabled by this module until the venue holds DEMO_VALIDATED.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from pathlib import Path
from typing import Any

from .brokers.base import BrokerRequestError, BrokerUnavailable
from .brokers.binance_spot_demo import VENUE_ID, BinanceSpotDemoAdapter, load_demo_credentials
from .demo_requests import DemoStore
from .execution import ExecutionStore, OrderGateway
from .execution.gateway import CONTROLLED_TEST_STRATEGY, ExecutionCommand
from .execution.spot_demo import SpotDemoPolicy
from .governance import Actor, Role
from .logs import get_logger, log_event
from .models import Direction, RunMode

_LOG = get_logger("demo.runner")
STATUS_INTERVAL = timedelta(seconds=30)
QUOTE_INTERVAL = timedelta(seconds=60)


def registry_allows_demo() -> bool:
    from .venues.registry import can_trade_demo, load_registry

    return can_trade_demo(load_registry().get(VENUE_ID))


def registry_allows_demo_auto() -> bool:
    from .venues.registry import can_trade_demo_auto, load_registry

    return can_trade_demo_auto(load_registry().get(VENUE_ID))


def read_credentials_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="ascii").splitlines():
        if "=" in line and not line.startswith("#"):
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip()
    return values


class DemoRuntime:
    def __init__(self, database: str | Path, adapter: BinanceSpotDemoAdapter, *, clock: Callable[[], datetime] | None = None,
                 policy: SpotDemoPolicy | None = None, gate: Callable[[], bool] = registry_allows_demo,
                 auto_gate: Callable[[], bool] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))
        self.adapter = adapter
        self.executions = ExecutionStore(database)
        self.executions.initialize()
        self.demo = DemoStore(database)
        self.demo.initialize()
        self.policy = policy or SpotDemoPolicy()
        self.gate = gate
        self.auto_gate = auto_gate or registry_allows_demo_auto
        self.instrument = adapter.capabilities().instruments[0]
        self._gateway: OrderGateway | None = None
        self.stop = threading.Event()
        self._last_status: datetime | None = None
        self._last_quote: datetime | None = None

    @property
    def gateway(self) -> OrderGateway:
        if self._gateway is None:
            step = float(self.adapter.spot_specs()[self.instrument].step_size)
            self._gateway = OrderGateway(store=self.executions, broker=self.adapter, configured_mode=RunMode.DEMO,
                                         spot_policy=self.policy, spot_auto_allowed=self.auto_gate,
                                         position_tolerance_units=step,
                                         account_ref=self.adapter.account_snapshot().account_ref_masked, clock=self.clock)
        return self._gateway

    # -- status ---------------------------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        """Server time, account, rules and a quote, read from Binance Demo; stored for the dashboard."""

        result: dict[str, Any] = {"venue": VENUE_ID, "environment": "DEMO", "checked_at": self.clock().isoformat()}
        try:
            offset = self.adapter.sync_time()
            snapshot = self.adapter.account_snapshot()
            balances = self.adapter.balances()
            spec = self.adapter.spot_specs()[self.instrument]
            quote = self.adapter.quote(self.instrument)
        except (BrokerUnavailable, BrokerRequestError) as exc:
            result.update(connected=False, error=str(exc))
            self.demo.set("status", result)
            log_event(_LOG, "demo.check_failed", level=logging.WARNING, error=str(exc))
            return result
        shown = {asset: {"free": str(free), "locked": str(locked)} for asset, (free, locked) in balances.balances.items()
                 if asset in (spec.base_asset, spec.quote_asset)}
        result.update(
            connected=True, account_ref=snapshot.account_ref_masked, environment_reported=snapshot.environment_reported.value,
            environment_source=snapshot.environment_source.value, account_type=balances.account_type,
            can_trade=balances.can_trade, clock_offset_ms=round(offset.total_seconds() * 1000), balances=shown,
            quote={"symbol": quote.instrument, "bid": quote.bid, "ask": quote.ask, "at": quote.timestamp.isoformat(),
                   "source": quote.source},
            rules={"symbol": spec.symbol, "status": spec.status, "step_size": str(spec.step_size),
                   "min_qty": str(spec.min_qty), "min_notional": str(spec.min_notional), "tick_size": str(spec.tick_size)},
            demo_gate_open=self._gate_open(), paused=self.demo.paused(), policy=self.policy.to_dict(),
            positions=[{"symbol": p.instrument, "net_units": p.net_units, "average_price": p.average_price}
                       for p in self.adapter.open_positions()])
        self.demo.set("status", result)
        self.demo.add_quote(quote.timestamp.isoformat(), quote.instrument, quote.bid, quote.ask, quote.source)
        return result

    def _gate_open(self) -> bool:
        try:
            return bool(self.gate())
        except Exception:  # noqa: BLE001 - an unreadable registry closes the gate
            return False

    # -- the controlled test --------------------------------------------------------------------

    def controlled_test(self, operator: str) -> dict[str, Any]:
        """One minimum BUY, lookup, reconciliation, SELL back, lookup, reconciliation. Human-requested only."""

        actor = Actor(operator, Role.HUMAN_OPERATOR)
        run_id = self.demo.start_test(operator)
        details: dict[str, Any] = {"operator": operator, "venue": VENUE_ID, "environment": "DEMO", "steps": []}

        def step(name: str, **values: Any) -> None:
            details["steps"].append({"step": name, "at": self.clock().isoformat(), **values})

        try:
            if self.demo.paused():
                raise PermissionError("l'exécution DEMO est en pause : reprenez-la avant l'ordre de test")
            if not self._gate_open():
                raise PermissionError("le registre ne permet pas encore d'ordre DEMO (éligibilité ou lecture du compte "
                                      "non validées)")
            status = self.check()
            if not status.get("connected"):
                raise BrokerUnavailable(status.get("error", "Binance Demo injoignable"))
            reconcile = self.gateway.reconcile()
            step("reconciliation_before", status=reconcile.status, divergences=list(reconcile.divergences))
            if reconcile.status != "CONSISTENT":
                raise RuntimeError("état incohérent avant le test : aucune entrée")
            spec = self.adapter.spot_specs()[self.instrument]
            quote = self.adapter.quote(self.instrument)
            quantity = self._test_quantity(spec, Decimal(repr(quote.bid)), Decimal(repr(quote.ask)))
            stop = math.floor(quote.ask * 0.98 / float(spec.tick_size)) * float(spec.tick_size)
            buy = self._submit(actor, "ENTRY", Direction.LONG, float(quantity), stop)
            step("buy", **buy)
            if buy["state"] not in ("FILLED", "PARTIALLY_FILLED"):
                raise RuntimeError(f"achat non exécuté : {buy['state']} {buy['reasons']}")
            step("reconciliation_after_buy", status=self.gateway.reconcile().status)
            sell = self._submit(actor, "EXIT", Direction.SHORT, None, None)
            step("sell", **sell)
            final = self.gateway.reconcile()
            step("reconciliation_after_sell", status=final.status, divergences=list(final.divergences))
            ok = sell["state"] == "FILLED" and final.status == "CONSISTENT"
            details["result"] = "RÉUSSI" if ok else "ÉCHEC"
        except Exception as exc:  # noqa: BLE001 - every outcome is recorded, never hidden
            details["result"] = "ÉCHEC"
            details["error"] = f"{type(exc).__name__}: {exc}"
        details["positions_after"] = [{"symbol": p.instrument, "net_units": p.net_units}
                                      for p in self._safe(self.adapter.open_positions, ())]
        self.demo.finish_test(run_id, details["result"], details)
        self.check()
        log_event(_LOG, "demo.controlled_test", level=logging.INFO, result=details["result"], run_id=run_id)
        return {"run_id": run_id, **details}

    @staticmethod
    def _test_quantity(spec, bid: Decimal, ask: Decimal) -> Decimal:
        """The smallest quantity whose sell-back, net of a 0.1 % base fee and floored to the step, keeps 10 %
        above the minimum notional at the bid."""

        target = spec.min_notional * Decimal("1.2")
        quantity = max(spec.min_qty, (target / ask / spec.step_size).to_integral_value(rounding=ROUND_UP) * spec.step_size)
        for _ in range(1000):
            sold = ((quantity * Decimal("0.999")) / spec.step_size).to_integral_value(rounding=ROUND_DOWN) * spec.step_size
            if sold >= spec.min_qty and sold * bid >= spec.min_notional * Decimal("1.1"):
                return quantity
            quantity += spec.step_size
        raise RuntimeError("no test quantity fits the rules")

    def _submit(self, actor: Actor, purpose: str, direction: Direction, units: float | None,
                stop: float | None) -> dict[str, Any]:
        now = self.clock()
        intent = f"demo-test:{now.strftime('%Y%m%dT%H%M%S')}:{purpose}:{uuid.uuid4().hex[:8]}"
        command = ExecutionCommand(
            intent_id=intent, decision_id=intent, correlation_id=intent, mode=RunMode.DEMO,
            account_ref=self.adapter.account_snapshot().account_ref_masked, instrument=self.instrument,
            direction=direction, created_at=now, expires_at=now + timedelta(minutes=2),
            strategy=CONTROLLED_TEST_STRATEGY, strategy_version="1", checks_passed=("controlled_test",),
            purpose=purpose, stop_price=stop, requested_units=units)
        result = self.gateway.submit(command, actor)
        record = self.executions.get_order(intent)
        report = None
        if record is not None and record.state.value not in ("RISK_REJECTED",):
            report = self._safe(lambda: self.adapter.lookup_order(record.client_order_id), None)
        return {"intent_id": intent, "client_order_id": result.client_order_id,
                "state": result.state.value if result.state else None, "reasons": list(result.reason_codes),
                "units": result.approved_units, "broker_order_id": report.broker_order_id if report else None,
                "broker_status": report.status.value if report else None,
                "filled_units": report.filled_units if report else None,
                "average_price": report.average_fill_price if report else None}

    @staticmethod
    def _safe(call: Callable[[], Any], default: Any) -> Any:
        try:
            return call()
        except (BrokerUnavailable, BrokerRequestError):
            return default

    # -- the service loop -----------------------------------------------------------------------

    def tick(self) -> dict[str, Any]:
        now = self.clock()
        summary: dict[str, Any] = {}
        if self._last_status is None or now - self._last_status >= STATUS_INTERVAL:
            summary["status"] = self.check().get("connected")
            self._last_status = now
        request = self.demo.next_request()
        if request:
            try:
                if request["kind"] == "pause":
                    self.demo.set("paused", True)
                    result = {"paused": True}
                elif request["kind"] == "resume":
                    self.demo.set("paused", False)
                    result = {"paused": False}
                else:
                    result = self.controlled_test(request["actor"])
                self.demo.finish_request(request["id"], result.get("result", "RÉUSSI") != "ÉCHEC", result)
            except Exception as exc:  # noqa: BLE001 - a failed request is recorded, the service goes on
                self.demo.finish_request(request["id"], False, {"error": f"{type(exc).__name__}: {exc}"})
            summary["request"] = request["kind"]
        self.demo.set("heartbeat", {"at": now.isoformat()})
        return summary

    def run(self) -> None:
        self.demo.recover()
        if self.demo.get("paused") is None:
            self.demo.set("paused", True)  # DEMO execution starts paused
        log_event(_LOG, "demo.started", level=logging.WARNING, venue=VENUE_ID, instrument=self.instrument)
        status = self.check()
        if status.get("connected"):
            self._safe(lambda: self.gateway.reconcile(), None)
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - systemd restarts the process if it dies
                log_event(_LOG, "demo.tick_failed", level=logging.ERROR, error=str(exc))
            self.stop.wait(5.0)


def runtime_from_environment(database: Path) -> DemoRuntime:
    """Credentials from systemd LoadCredential ($CREDENTIALS_DIRECTORY/binance-demo) or the environment."""

    env = dict(os.environ)
    credentials_dir = env.get("CREDENTIALS_DIRECTORY")
    if credentials_dir and (Path(credentials_dir) / "binance-demo").is_file():
        env.update(read_credentials_file(Path(credentials_dir) / "binance-demo"))
    key, secret = load_demo_credentials(env)
    instruments = tuple(s for s in (env.get("ASTRA_DEMO_INSTRUMENTS") or "BTCUSDT").split(",") if s)
    allocation = float(env.get("ASTRA_DEMO_ALLOCATION_USDT") or 1000)
    demo = DemoStore(database)
    demo.initialize()
    adapter = BinanceSpotDemoAdapter(key, secret, instruments, paused=demo.paused, demo_trading_allowed=registry_allows_demo)
    return DemoRuntime(database, adapter, policy=SpotDemoPolicy(allocation=allocation))
