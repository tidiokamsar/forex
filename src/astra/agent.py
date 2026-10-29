"""ASTRA agent: works around the clock with the dashboard API, in SIMULATION only, through an agent token.

The agent acts for its owner with a token of its own (``astra auth agent-create``), never with a person's
password or TOTP codes. It reaches ASTRA only through ``AstraClient``, which refuses locally any route not in
``ALLOWED_ROUTES`` (the server refuses them as well, ``webapp.AGENT_ENDPOINTS``). There is no route here, or
on the server for a token, that sends an order, lifts the kill switch, changes the mode or touches DEMO or LIVE.

Each cycle:
1. Guard: ``/api/status`` must report ``SIMULATION`` with ``live_locked``, else the agent halts. With the kill
   switch active, entry freezes, an unknown control state or a read-only token, it only observes.
2. Data: asks for an ECB ingestion when the reference rates are late (bounded frequency).
3. Research: queues backtests over the covered series and a small parameter grid, within a daily budget.
4. Simulation: keeps a few paper sessions on the best finished backtests and stops those that lose too much.
   This ranking is in-sample and indicative: it is not the research protocol (sealed holdout, PBO, DSR) and
   the agent never promotes a model.
5. Report: every decision goes to a JSONL journal, and a Markdown summary of the day is rewritten.

Standard library only, like the trading code.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ECB_SOURCE = "ECB_EXR"
DEFAULT_INTERVAL_SECONDS = 300
# (method, path) pairs the agent may call; anything else raises before a request is sent.
ALLOWED_ROUTES = (
    ("GET", r"/api/agent/whoami"), ("GET", r"/api/status"), ("GET", r"/api/controls"),
    ("GET", r"/api/market/overview"), ("GET", r"/api/lab/catalog"), ("GET", r"/api/lab/backtests"),
    ("GET", r"/api/lab/backtests/\d+"), ("GET", r"/api/sim/sessions"), ("GET", r"/api/sim/sessions/\d+"),
    ("GET", r"/api/learn/overview"), ("GET", r"/api/system/jobs"), ("GET", r"/api/mt5/overview"),
    ("GET", r"/api/market/chart"), ("GET", r"/api/dashboard/overview"),
    ("POST", r"/api/mt5/snapshot"), ("POST", r"/api/mt5/quotes"), ("POST", r"/api/mt5/candles"), ("POST", r"/api/mt5/history"),
    # the three POST routes above are refused by the server unless the token holds the mt5 scope
    ("POST", r"/api/market/ingest"), ("POST", r"/api/lab/backtests"), ("POST", r"/api/sim/sessions"),
    ("POST", r"/api/sim/sessions/\d+/stop"),
)
Transport = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]


class AgentHalt(RuntimeError):
    """Stop the agent: the platform is not in SIMULATION, or the token is no longer accepted."""


class ApiError(RuntimeError):
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.code = payload.get("error") if isinstance(payload, dict) else None
        self.message = payload.get("message") if isinstance(payload, dict) else None
        super().__init__(f"HTTP {status} {self.code or ''} {self.message or ''}".strip())


def http_transport(base_url: str, token: str, timeout: float = 30.0) -> Transport:
    """HTTPS to the dashboard (plain HTTP only to loopback, for local tests)."""

    parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
    if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("ASTRA_AGENT_URL must use https (http only to loopback)")
    origin = f"{parsed.scheme}://{parsed.netloc}"

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(origin + path, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json", "Origin": origin,
            "User-Agent": "astra-agent", **({"Content-Type": "application/json"} if data is not None else {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # scheme checked above
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            with exc:
                try:
                    payload = json.loads(exc.read() or b"null")
                except ValueError:
                    payload = None
            return exc.code, payload

    return send


class AstraClient:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> Any:
        if not any(m == method and re.fullmatch(p, path) for m, p in ALLOWED_ROUTES):
            raise PermissionError(f"route not allowed for the agent: {method} {path}")
        if query:
            path = f"{path}?{urllib.parse.urlencode(query)}"
        status, payload = self.transport(method, path, body)
        if status == 401:
            raise AgentHalt("token refused (expired, revoked or owner changed): issue a new one")
        if status >= 400:
            raise ApiError(status, payload)
        return payload

    def get(self, path: str, **query: Any) -> Any:
        return self._call("GET", path, query=query or None)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._call("POST", path, body if body is not None else {})


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    ingest_every_hours: float = 6.0
    max_backtests_per_day: int = 20
    max_pending_backtests: int = 3
    min_candles: int = 300
    variants: tuple[tuple[int, int], ...] = ((10, 30), (20, 50), (50, 200))
    max_sessions: int = 3
    min_trades: int = 20
    min_profit_factor: float = 1.1
    max_backtest_drawdown_pct: float = 15.0
    stop_loss_pct: float = 5.0  # stop an agent session whose net loss exceeds this share of its capital


@dataclass
class AgentState:
    day: str = ""
    backtests_today: int = 0
    last_ingest_at: str | None = None
    submitted: dict[str, int] = field(default_factory=dict)   # series|fast|slow -> backtest id
    sessions: dict[str, int] = field(default_factory=dict)    # backtest id -> simulation session id
    day_log: dict[str, int] = field(default_factory=dict)     # counts of today's decisions

    @classmethod
    def load(cls, path: Path) -> AgentState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)


class Agent:
    def __init__(self, client: AstraClient, workdir: Path, policy: AgentPolicy | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.client = client
        self.policy = policy or AgentPolicy()
        self.workdir = Path(workdir)
        self.state_path = self.workdir / "agent-state.json"
        self.clock = clock or (lambda: datetime.now(UTC))
        self.state = AgentState.load(self.state_path)
        self.me: dict[str, Any] = {}

    # -- journal ----------------------------------------------------------------------------------

    def note(self, kind: str, **fields: Any) -> None:
        entry = {"ts": self.clock().isoformat(), "kind": kind, **fields}
        self.workdir.mkdir(parents=True, exist_ok=True)
        with (self.workdir / f"journal-{self.clock().date().isoformat()}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.state.day_log[kind] = self.state.day_log.get(kind, 0) + 1

    # -- cycle ------------------------------------------------------------------------------------

    def cycle(self) -> dict[str, Any]:
        today = self.clock().date().isoformat()
        if self.state.day != today:
            self.state.day, self.state.backtests_today, self.state.day_log = today, 0, {}
        try:
            summary = self._cycle()
        finally:
            self.state.save(self.state_path)
        self._write_summary(summary)
        return summary

    def _cycle(self) -> dict[str, Any]:
        self.me = self.client.get("/api/agent/whoami")
        status = self.client.get("/api/status")
        if status.get("mode") != "SIMULATION" or status.get("live_locked") is not True \
                or self.me.get("mode") != "SIMULATION":
            self.note("halt", reason="platform not in SIMULATION", status=status)
            raise AgentHalt("the platform does not report SIMULATION with LIVE locked")
        entries_allowed = None
        try:
            controls = self.client.get("/api/controls")
            entries_allowed = controls.get("entries_allowed") is True
        except ApiError as exc:
            self.note("controls_unreadable", error=str(exc))
        can_act = "lab" in self.me.get("scopes", []) and entries_allowed is True
        summary: dict[str, Any] = {"at": self.clock().isoformat(), "agent": self.me.get("agent"),
                                   "owner": self.me.get("owner"), "can_act": can_act,
                                   "entries_allowed": entries_allowed, "actions": []}
        if not can_act:
            self.note("observe_only", entries_allowed=entries_allowed, scopes=self.me.get("scopes"))
        overview = self.client.get("/api/market/overview")
        summary["worker"] = (overview.get("worker") or {}).get("state")
        if can_act:
            self._data(overview, summary)
        backtests = self.client.get("/api/lab/backtests").get("backtests", [])
        if can_act:
            self._research(overview, backtests, summary)
        ranked = self._ranked(backtests)
        summary["ranked"] = [{"backtest_id": b["id"], "symbol": b["symbol"], "timeframe": b["timeframe"],
                              "profit_factor": b["metrics"].get("profit_factor"), "net_pnl": b["metrics"].get("net_pnl"),
                              "trades": b["metrics"].get("trade_count")} for b in ranked[:5]]
        sessions = self.client.get("/api/sim/sessions").get("sessions", [])
        if can_act:
            self._simulations(overview, ranked, sessions, summary)
        summary["sessions"] = [{"id": s["id"], "name": s["name"], "status": s["status"],
                                "net_pnl": (s.get("account") or {}).get("net_pnl")}
                               for s in sessions if s.get("actor") == self._actor()]
        self.note("cycle", can_act=can_act, actions=len(summary["actions"]))
        return summary

    def _actor(self) -> str:
        return f"agent:{self.me.get('agent')}"

    def _act(self, summary: dict[str, Any], kind: str, call: Callable[[], Any], **fields: Any) -> Any:
        try:
            result = call()
        except ApiError as exc:
            self.note(f"{kind}_refused", error=str(exc), **fields)
            summary["actions"].append({"kind": kind, "result": "refused", "error": str(exc), **fields})
            return None
        self.note(kind, **fields)
        summary["actions"].append({"kind": kind, "result": "ok", **fields})
        return result

    # -- data -------------------------------------------------------------------------------------

    def _data(self, overview: dict[str, Any], summary: dict[str, Any]) -> None:
        ecb = next((s for s in overview.get("sources", []) if s.get("name") == ECB_SOURCE), None)
        state = ((ecb or {}).get("freshness") or {}).get("state", "NO_DATA")
        if state == "UP_TO_DATE":
            return
        last = self.state.last_ingest_at
        if last and self.clock() - datetime.fromisoformat(last) < timedelta(hours=self.policy.ingest_every_hours):
            return
        if self._act(summary, "ingest_requested", lambda: self.client.post("/api/market/ingest"), freshness=state):
            self.state.last_ingest_at = self.clock().isoformat()

    # -- research ---------------------------------------------------------------------------------

    def _research(self, overview: dict[str, Any], backtests: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        pending = sum(1 for b in backtests if b.get("status") in ("QUEUED", "RUNNING"))
        series = [c for c in overview.get("coverage", []) if (c.get("candles") or 0) >= self.policy.min_candles]
        for item in series:
            for fast, slow in self.policy.variants:
                if pending >= self.policy.max_pending_backtests \
                        or self.state.backtests_today >= self.policy.max_backtests_per_day:
                    return
                key = f"{item['symbol']}|{item['timeframe']}|{item['source']}|{fast}|{slow}"
                if key in self.state.submitted:
                    continue
                body = {"symbol": item["symbol"], "timeframe": item["timeframe"], "source": item["source"],
                        "strategy_params": {"fast": fast, "slow": slow}}
                result = self._act(summary, "backtest_requested", lambda b=body: self.client.post("/api/lab/backtests", b),
                                   series=key)
                # A refused request is not retried: the key is kept with id 0.
                self.state.submitted[key] = int((result or {}).get("backtest_id") or 0)
                self.state.backtests_today += 1
                if result:
                    pending += 1

    def _ranked(self, backtests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        p = self.policy
        mine = set(self.state.submitted.values())
        good = []
        for b in backtests:
            m = b.get("metrics") or {}
            if b.get("id") not in mine or b.get("status") != "DONE":
                continue
            if (m.get("trade_count") or 0) >= p.min_trades and (m.get("profit_factor") or 0) >= p.min_profit_factor \
                    and (m.get("max_drawdown_pct") or 100) <= p.max_backtest_drawdown_pct and (m.get("net_pnl") or 0) > 0:
                good.append(b)
        return sorted(good, key=lambda b: (b["metrics"]["profit_factor"], b["metrics"]["net_pnl"]), reverse=True)

    # -- simulations ------------------------------------------------------------------------------

    def _simulations(self, overview: dict[str, Any], ranked: list[dict[str, Any]], sessions: list[dict[str, Any]],
                     summary: dict[str, Any]) -> None:
        mine = [s for s in sessions if s.get("actor") == self._actor()]
        running = 0
        for session in mine:
            if session.get("status") != "RUNNING":
                continue
            account = session.get("account") or {}
            limit = -abs(self.policy.stop_loss_pct) / 100 * (account.get("initial_capital") or 0)
            if account.get("drawdown_halt") or (account.get("net_pnl") or 0) <= limit:
                self._act(summary, "simulation_stopped",
                          lambda sid=session["id"]: self.client.post(f"/api/sim/sessions/{sid}/stop"),
                          session_id=session["id"], net_pnl=account.get("net_pnl"))
            else:
                running += 1
        # Forward paper trading needs data that keeps arriving: only sources the platform reports up to date.
        live_sources = {s.get("name") for s in overview.get("sources", [])
                        if (s.get("freshness") or {}).get("state") == "UP_TO_DATE"}
        for backtest in ranked:
            if running >= self.policy.max_sessions:
                break
            if str(backtest["id"]) in self.state.sessions or backtest["source"] not in live_sources:
                continue
            detail = self.client.get(f"/api/lab/backtests/{backtest['id']}")
            params = detail.get("params") or {}
            body = {"name": f"agent {self.me.get('agent')} backtest {backtest['id']}", "symbols": [backtest["symbol"]],
                    "timeframe": backtest["timeframe"], "source": backtest["source"],
                    "strategy_params": params.get("strategy_params"), "risk": params.get("risk"),
                    "costs": params.get("costs")}
            result = self._act(summary, "simulation_started", lambda b=body: self.client.post("/api/sim/sessions", b),
                               backtest_id=backtest["id"])
            self.state.sessions[str(backtest["id"])] = int((result or {}).get("session_id") or 0)
            if result:
                running += 1

    # -- report -----------------------------------------------------------------------------------

    def _write_summary(self, summary: dict[str, Any]) -> None:
        lines = [f"# Agent ASTRA, {self.state.day}", "",
                 f"Agent `{summary.get('agent')}` pour `{summary.get('owner')}`, mode SIMULATION.", "",
                 f"- Dernier cycle : {summary['at']}",
                 f"- Peut agir : {'oui' if summary['can_act'] else 'non (observation seule)'}",
                 f"- Entrées autorisées (contrôles) : {summary['entries_allowed']}",
                 f"- Worker : {summary.get('worker')}",
                 f"- Backtests demandés aujourd'hui : {self.state.backtests_today}/{self.policy.max_backtests_per_day}",
                 "", "## Décisions du jour", ""]
        lines += [f"- {kind} : {count}" for kind, count in sorted(self.state.day_log.items())] or ["- aucune"]
        lines += ["", "## Meilleurs backtests de l'agent (indicatif, hors protocole de recherche)", ""]
        lines += [f"- #{r['backtest_id']} {r['symbol']} {r['timeframe']} : PF {r['profit_factor']}, "
                  f"P&L {r['net_pnl']}, {r['trades']} trades" for r in summary.get("ranked", [])] or ["- aucun"]
        lines += ["", "## Simulations de l'agent", ""]
        lines += [f"- #{s['id']} {s['name']} : {s['status']}, P&L {s['net_pnl']}"
                  for s in summary.get("sessions", [])] or ["- aucune"]
        lines += ["", "## Actions du dernier cycle", ""]
        lines += [f"- {a['kind']} : {a['result']}" + (f" ({a['error']})" if a.get("error") else "")
                  for a in summary["actions"]] or ["- aucune"]
        (self.workdir / f"summary-{self.state.day}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- loop -------------------------------------------------------------------------------------

    def run(self, once: bool = False, sleep: Callable[[float], None] = time.sleep) -> int:
        while True:
            try:
                self.cycle()
            except AgentHalt as exc:
                print(f"astra-agent: arrêt : {exc}", file=sys.stderr)
                return 2
            except (ApiError, OSError, ValueError) as exc:
                # Transient (network, 5xx, malformed answer): noted, retried next cycle.
                self.note("cycle_failed", error=str(exc)[:300])
                self.state.save(self.state_path)
            if once:
                return 0
            sleep(self.policy.interval_seconds)


def run_from_env(once: bool = False) -> int:
    url = os.environ.get("ASTRA_AGENT_URL", "").strip()
    token_file = os.environ.get("ASTRA_AGENT_TOKEN_FILE", "").strip()
    if not token_file and os.environ.get("CREDENTIALS_DIRECTORY"):
        token_file = str(Path(os.environ["CREDENTIALS_DIRECTORY"]) / "agent-token")  # systemd LoadCredential
    if not url or not token_file:
        print("astra-agent: ASTRA_AGENT_URL et ASTRA_AGENT_TOKEN_FILE sont obligatoires", file=sys.stderr)
        return 1
    path = Path(token_file)
    if os.name == "posix" and path.stat().st_mode & 0o007:
        print(f"astra-agent: {path} ne doit pas être lisible par les autres utilisateurs (chmod 0640)", file=sys.stderr)
        return 1
    token = path.read_text(encoding="ascii").strip()
    workdir = Path(os.environ.get("ASTRA_AGENT_DIR") or "var/agent")
    interval = int(os.environ.get("ASTRA_AGENT_INTERVAL_SECONDS") or DEFAULT_INTERVAL_SECONDS)
    agent = Agent(AstraClient(http_transport(url, token)), workdir, AgentPolicy(interval_seconds=max(60, interval)))
    return agent.run(once=once)
