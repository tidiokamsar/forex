"""Read-only, loopback-bound dashboard HTTP service."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from .config import AppConfig
from .execution import ExecutionStore, TradingControls
from .registry import ModelRegistry
from .storage import BacktestStore


STATIC_DIR = Path(__file__).with_name("static")
OPERATION_ROUTES = frozenset(
    {"/api/orders", "/api/controls", "/api/reconciliations", "/api/models", "/api/experiments"}
)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(
    store: BacktestStore,
    allowed_hosts: tuple[str, ...] | None = None,
    execution_store: ExecutionStore | None = None,
    registry: ModelRegistry | None = None,
):
    execution = execution_store or ExecutionStore(store.path)
    models = registry or ModelRegistry(store.path)

    def operations_payload(route: str) -> dict[str, Any] | None:
        if route == "/api/orders":
            return {"mode": "SIMULATION", "orders": [order.to_dict() for order in execution.list_orders(limit=100)]}
        if route == "/api/controls":
            by_scope = TradingControls(execution).all_freezes()
            freezes = sorted({code for codes in by_scope.values() for code in codes})
            kill = execution.get_control("kill_switch", {"active": False})
            return {
                "mode": "SIMULATION",
                "live_locked": True,
                "kill_switch": kill,
                "entry_freezes": freezes,
                "entry_freezes_by_account": by_scope,
                "entries_allowed": not freezes and not kill.get("active", False),
            }
        if route == "/api/reconciliations":
            return {"reconciliations": execution.reconciliations(limit=20)}
        if route == "/api/models":
            return {"models": models.list_versions(limit=100)}
        if route == "/api/experiments":
            return {"experiments": models.experiments(limit=50)}
        return None

    class Handler(BaseHTTPRequestHandler):
        server_version = "ASTRA"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler protocol
            if not self._guard_host():
                return
            route = urlsplit(self.path).path
            if route == "/":
                self._static("index.html", "text/html; charset=utf-8")
            elif route == "/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
            elif route == "/styles.css":
                self._static("styles.css", "text/css; charset=utf-8")
            elif route == "/api/health":
                try:
                    status = "ok" if store.check_health() else "unavailable"
                except (OSError, sqlite3.Error):
                    status = "unavailable"
                self._json(
                    {
                        "status": status,
                        "mode": "SIMULATION",
                        "live_locked": True,
                    },
                    status=200 if status == "ok" else 503,
                )
            elif route == "/api/summary":
                try:
                    latest_run = store.latest_run()
                except (OSError, sqlite3.Error):
                    self._json({"error": "storage_unavailable"}, status=503)
                    return
                self._json(
                    {
                        "mode": "SIMULATION",
                        "live_locked": True,
                        "latest_run": latest_run,
                    }
                )
            elif route == "/api/runs":
                try:
                    runs = store.list_runs()
                except (OSError, sqlite3.Error):
                    self._json({"error": "storage_unavailable"}, status=503)
                    return
                self._json({"runs": runs})
            elif route == "/api/audit":
                try:
                    events = store.audit_events()
                except (OSError, sqlite3.Error):
                    self._json({"error": "storage_unavailable"}, status=503)
                    return
                self._json({"events": events})
            elif route in OPERATION_ROUTES:
                try:
                    payload = operations_payload(route)
                except (OSError, sqlite3.Error):
                    self._json({"error": "storage_unavailable"}, status=503)
                    return
                self._json(payload)
            else:
                self._json({"error": "not_found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802 - every mutation is disabled
            if not self._guard_host():
                return
            self._json({"error": "read_only"}, status=405)

        def do_PUT(self) -> None:  # noqa: N802
            if not self._guard_host():
                return
            self._json({"error": "read_only"}, status=405)

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._guard_host():
                return
            self._json({"error": "read_only"}, status=405)

        def _guard_host(self) -> bool:
            host_header = self.headers.get("Host", "")
            try:
                parsed = urlsplit(f"//{host_header}")
                hostname = parsed.hostname
                port = parsed.port
            except ValueError:
                hostname = None
                port = None
                parsed = None
            expected_hosts = allowed_hosts or (self.server.server_address[0],)
            valid = (
                parsed is not None
                and parsed.path == ""
                and parsed.query == ""
                and parsed.fragment == ""
                and parsed.username is None
                and parsed.password is None
                and hostname is not None
                and hostname.casefold()
                in {host.casefold() for host in expected_hosts}
                and port == self.server.server_address[1]
            )
            if valid:
                return True
            self._json({"error": "invalid_host"}, status=400)
            return False

        def _static(self, name: str, content_type: str) -> None:
            path = STATIC_DIR / name
            try:
                body = path.read_bytes()
            except OSError:
                self._json({"error": "unavailable"}, status=503)
                return
            self._send(body, content_type, 200)

        def _json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(body, "application/json; charset=utf-8", status)

        def _send(self, body: bytes, content_type: str, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # Keep routine local dashboard reads out of stdout. No user values
            # or credentials are accepted by this service.
            return

    return Handler


def serve(config: AppConfig, store: BacktestStore) -> None:
    server = DashboardServer(
        (config.host, config.port), make_handler(store, allowed_hosts=(config.host,))
    )
    print(f"ASTRA dashboard: http://{config.host}:{config.port} (SIMULATION, read-only)")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("ASTRA dashboard stopping")
    finally:
        server.server_close()
