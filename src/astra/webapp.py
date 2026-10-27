"""Authenticated ASTRA dashboard (Flask). Replaces the former stdlib ``web.py`` server.

Security model (SECURITY.md, "Web authentication"):
- The app listens on loopback only (``AppConfig.host``); remote access goes through a reverse
  proxy that terminates TLS. Every route except ``/login``, ``/api/health`` and the stylesheet
  requires a FULL server-side session; API routes also require a web role (READ_ONLY, OPERATOR,
  ADMIN), checked here on each request, never in the browser.
- The Host header must exactly match an allowed value; X-Forwarded-* headers are read only when
  ``WebConfig.trusted_proxy`` is set, and then from a single proxy hop.
- Every state-changing request needs a CSRF token bound to the session (or to the pre-login
  cookie for the login form) and an Origin header from an allowed origin.
- A failure of the session store answers 503: it never grants anonymous access.
- Web roles are not execution roles: this module reads trading state and never calls anything
  that submits, cancels or releases (no kill switch release, no order, no mode change).
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sqlite3
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, g, jsonify, make_response, redirect, render_template, request, send_file

from .auth import AuthError, AuthStore, AuthUnavailable, WebRole, crypto
from .auth.store import SessionRecord, SessionStage
from .config import AppConfig, WebConfig
from .execution import ExecutionStore, TradingControls
from .registry import ModelRegistry
from .storage import BacktestStore

SESSION_COOKIE = "__Host-astra_session"
PRE_COOKIE = "__Host-astra_pre"
STATIC_DIR = Path(__file__).with_name("static")
PUBLIC_STATIC = {"styles.css": "text/css; charset=utf-8"}
PRIVATE_STATIC = {"app.js": "text/javascript; charset=utf-8"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def load_secret_key(path: Path) -> bytes:
    """The server key for CSRF tokens: 32 random bytes in a 0600 file, created on first use."""

    path = Path(path)
    if path.exists():
        key = path.read_bytes()
        if len(key) != 32:
            raise RuntimeError(f"{path} must contain exactly 32 bytes")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def create_app(config: AppConfig, web: WebConfig, *, auth: AuthStore | None = None, secret_key: bytes | None = None,
               store: BacktestStore | None = None) -> Flask:
    app = Flask(__name__, static_folder=None, template_folder="templates")
    app.config.update(MAX_CONTENT_LENGTH=64 * 1024, MAX_FORM_MEMORY_SIZE=64 * 1024, MAX_FORM_PARTS=50,
                      TEMPLATES_AUTO_RELOAD=False)
    auth = auth or AuthStore(web.auth_database_path)
    key = secret_key or load_secret_key(web.secret_key_path)
    backtests = store or BacktestStore(config.database_path)
    execution = ExecutionStore(backtests.path)
    models = ModelRegistry(backtests.path)

    if web.trusted_proxy:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    local_hosts = {f"{config.host}:{config.port}", f"127.0.0.1:{config.port}", f"localhost:{config.port}"}
    allowed_hosts = local_hosts | set(web.public_hosts)
    local_scheme = "https" if web.https and not web.public_hosts else "http"
    allowed_origins = {f"{local_scheme}://{h}" for h in local_hosts} | {f"https://{h}" for h in web.public_hosts}
    cookie_kwargs = {"secure": True, "httponly": True, "samesite": "Strict", "path": "/"}

    def client_ip() -> str | None:
        return request.remote_addr

    def csrf_for_session(session: SessionRecord) -> str:
        return crypto.keyed_digest(key, "session:" + session.token_digest)

    def csrf_for_pre(pre: str) -> str:
        return crypto.keyed_digest(key, "pre:" + crypto.token_digest(pre))

    # -- request guards ------------------------------------------------------------------------

    @app.before_request
    def guard() -> Response | None:
        host = (request.headers.get("Host") or "").lower()
        if host not in allowed_hosts:
            return _error(400, "invalid_host")
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("Origin")
            if origin is None or origin.lower() not in allowed_origins:
                auth_record("web:anonymous", "request.origin", "refused", detail=f"path={request.path}")
                return _error(403, "origin_refused")
        g.session = None
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            try:
                g.session = auth.resolve(token, client_ip())
            except AuthUnavailable:
                return _error(503, "auth_unavailable")
        return None

    @app.after_request
    def headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if web.https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers.pop("Server", None)
        return response

    @app.errorhandler(404)
    def not_found(_exc: Exception) -> Response:
        return _error(404, "not_found")

    @app.errorhandler(405)
    def not_allowed(_exc: Exception) -> Response:
        return _error(405, "method_not_allowed")

    @app.errorhandler(413)
    def too_large(_exc: Exception) -> Response:
        return _error(413, "request_too_large")

    @app.errorhandler(AuthUnavailable)
    def auth_down(_exc: Exception) -> Response:
        return _error(503, "auth_unavailable")

    @app.errorhandler(500)
    def internal(_exc: Exception) -> Response:
        return _error(500, "internal_error")

    def auth_record(actor: str, action: str, result: str, target: str | None = None, detail: str | None = None) -> None:
        with contextlib.suppress(AuthUnavailable):
            auth.record(actor, action, result, target=target, client_ip=client_ip(), detail=detail)

    def actor() -> str:
        return f"web:{g.session.user.username}" if g.session else "web:anonymous"

    def check_csrf(pre_login: bool = False) -> bool:
        """Session-bound token; the login form always carries the pre-login cookie token instead."""

        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
        if g.session is not None and not pre_login:
            return crypto.constant_time_equal(sent, csrf_for_session(g.session))
        pre = request.cookies.get(PRE_COOKIE) or ""
        return bool(pre) and crypto.constant_time_equal(sent, csrf_for_pre(pre))

    def require(role: WebRole, api: bool = True) -> Callable:
        def decorator(view: Callable) -> Callable:
            @wraps(view)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                session = g.session
                if session is None or session.stage is not SessionStage.FULL:
                    if api:
                        return _error(401, "authentication_required")
                    return redirect(_stage_location(session), code=303)
                if not session.user.role.allows(role):
                    auth_record(actor(), "access", "forbidden", detail=f"path={request.path}")
                    return _error(403, "forbidden")
                if request.method in UNSAFE_METHODS and not check_csrf():
                    auth_record(actor(), "request.csrf", "refused", detail=f"path={request.path}")
                    return _error(403, "csrf_refused")
                return view(*args, **kwargs)

            return wrapper

        return decorator

    def set_session_cookie(response: Response, token: str) -> Response:
        response.set_cookie(SESSION_COOKIE, token, **cookie_kwargs)
        response.delete_cookie(PRE_COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
        return response

    def clear_session_cookie(response: Response) -> Response:
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
        return response

    def page(template: str, status: int = 200, **context: Any) -> Response:
        return make_response(render_template(template, **context), status)

    # -- public routes -------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> Response:
        try:
            ok = backtests.check_health()
        except (OSError, sqlite3.Error):
            ok = False
        return make_response(jsonify({"status": "ok" if ok else "unavailable"}), 200 if ok else 503)

    @app.get("/static/<name>")
    def static_file(name: str) -> Response:
        if name in PUBLIC_STATIC:
            return send_file(STATIC_DIR / name, mimetype=PUBLIC_STATIC[name], max_age=0)
        if name in PRIVATE_STATIC:
            if g.session is None or g.session.stage is not SessionStage.FULL:
                return _error(401, "authentication_required")
            return send_file(STATIC_DIR / name, mimetype=PRIVATE_STATIC[name], max_age=0)
        abort(404)

    def login_page(message: str | None = None, status: int = 200) -> Response:
        pre = request.cookies.get(PRE_COOKIE)
        if not pre or not 20 <= len(pre) <= 128:
            pre = crypto.new_token()
        response = page("login.html", status, csrf_token=csrf_for_pre(pre), message=message)
        response.set_cookie(PRE_COOKIE, pre, max_age=900, **cookie_kwargs)
        return response

    @app.get("/login")
    def login_form() -> Response:
        if g.session is not None and g.session.stage is SessionStage.FULL:
            return redirect("/", code=303)
        return login_page()

    @app.post("/login")
    def login_submit() -> Response:
        if not check_csrf(pre_login=True):
            auth_record("web:anonymous", "login.csrf", "refused")
            return login_page("Session de connexion expirée, réessayez.", 403)
        if g.session is not None:
            auth.logout(g.session, client_ip())
        result = auth.authenticate(request.form.get("username", ""), request.form.get("password", ""), client_ip(),
                                   request.headers.get("User-Agent"))
        if result.status == "throttled":
            response = login_page(f"Trop de tentatives. Réessayez dans {result.retry_after_seconds} s.", 429)
            response.headers["Retry-After"] = str(result.retry_after_seconds)
            return response
        if result.status != "ok":
            return login_page("Identifiant ou mot de passe incorrect.", 401)
        return set_session_cookie(redirect(_stage_location_for(result.stage), code=303), result.token)

    @app.post("/logout")
    def logout() -> Response:
        if g.session is None:
            return clear_session_cookie(redirect("/login", code=303))
        if not check_csrf():
            return _error(403, "csrf_refused")
        auth.logout(g.session, client_ip())
        return clear_session_cookie(redirect("/login", code=303))

    # -- intermediate stages -------------------------------------------------------------------

    def stage_session(stage: SessionStage) -> SessionRecord | None:
        session = g.session
        return session if session is not None and session.stage is stage else None

    @app.route("/login/mfa", methods=["GET", "POST"])
    def mfa_verify() -> Response:
        session = stage_session(SessionStage.MFA_PENDING)
        if session is None:
            return redirect(_stage_location(g.session), code=303)
        if request.method == "GET":
            return page("mfa.html", csrf_token=csrf_for_session(session))
        if not check_csrf():
            return _error(403, "csrf_refused")
        result = auth.verify_mfa(session, request.form.get("code", ""), client_ip(), request.headers.get("User-Agent"))
        if result.status == "throttled":
            return page("mfa.html", 429, csrf_token=csrf_for_session(session),
                        message=f"Trop de tentatives. Réessayez dans {result.retry_after_seconds} s.")
        if result.status != "ok":
            return page("mfa.html", 401, csrf_token=csrf_for_session(session), message="Code incorrect.")
        return set_session_cookie(redirect(_stage_location_for(result.stage), code=303), result.token)

    @app.route("/mfa/setup", methods=["GET", "POST"])
    def mfa_setup() -> Response:
        session = stage_session(SessionStage.MFA_ENROLL)
        if session is None:
            return redirect(_stage_location(g.session), code=303)
        if request.method == "GET":
            secret, uri = auth.begin_mfa_enrollment(session)
            return page("mfa_setup.html", csrf_token=csrf_for_session(session), secret=secret, uri=uri)
        if not check_csrf():
            return _error(403, "csrf_refused")
        result, codes = auth.confirm_mfa_enrollment(session, request.form.get("code", ""), client_ip(),
                                                    request.headers.get("User-Agent"))
        if result.status != "ok":
            secret, uri = auth.begin_mfa_enrollment(session)
            return page("mfa_setup.html", 401, csrf_token=csrf_for_session(session), secret=secret, uri=uri,
                        message="Code incorrect : un nouveau secret a été généré, recommencez l'enrôlement.")
        response = page("recovery_codes.html", codes=codes, next_url=_stage_location_for(result.stage))
        return set_session_cookie(response, result.token)

    @app.route("/account/password", methods=["GET", "POST"])
    def change_password() -> Response:
        session = g.session
        if session is None or session.stage not in (SessionStage.FULL, SessionStage.PASSWORD_CHANGE):
            return redirect(_stage_location(session), code=303)
        forced = session.stage is SessionStage.PASSWORD_CHANGE
        if request.method == "GET":
            return page("password.html", csrf_token=csrf_for_session(session), forced=forced)
        if not check_csrf():
            return _error(403, "csrf_refused")
        new, confirm = request.form.get("new_password", ""), request.form.get("confirm_password", "")
        if new != confirm:
            return page("password.html", 400, csrf_token=csrf_for_session(session), forced=forced,
                        message="Les deux nouveaux mots de passe diffèrent.")
        try:
            result = auth.change_password(session, request.form.get("current_password", ""), new, client_ip(),
                                          request.headers.get("User-Agent"))
        except AuthError as exc:
            return page("password.html", 400, csrf_token=csrf_for_session(session), forced=forced, message=str(exc))
        if result.status != "ok":
            return page("password.html", 401, csrf_token=csrf_for_session(session), forced=forced,
                        message="Mot de passe actuel incorrect.")
        return set_session_cookie(redirect("/", code=303), result.token)

    # -- dashboard -----------------------------------------------------------------------------

    @app.get("/")
    @require(WebRole.READ_ONLY, api=False)
    def index() -> Response:
        user = g.session.user
        return page("index.html", csrf_token=csrf_for_session(g.session), username=user.username, role=user.role.value)

    @app.get("/api/session")
    @require(WebRole.READ_ONLY)
    def session_info() -> Response:
        session = g.session
        return jsonify({"username": session.user.username, "role": session.user.role.value,
                        "expires_at": session.absolute_expires_at.isoformat(), "idle_seconds": auth.policy.idle_seconds,
                        "mode": "SIMULATION", "live_locked": True})

    def storage_json(producer: Callable[[], dict[str, Any]]) -> Response:
        try:
            return jsonify(producer())
        except (OSError, sqlite3.Error):
            return _error(503, "storage_unavailable")

    @app.get("/api/status")
    @require(WebRole.READ_ONLY)
    def status() -> Response:
        return storage_json(lambda: {"status": "ok" if backtests.check_health() else "unavailable",
                                     "mode": "SIMULATION", "live_locked": True})

    @app.get("/api/summary")
    @require(WebRole.READ_ONLY)
    def summary() -> Response:
        return storage_json(lambda: {"mode": "SIMULATION", "live_locked": True, "latest_run": backtests.latest_run()})

    @app.get("/api/runs")
    @require(WebRole.READ_ONLY)
    def runs() -> Response:
        return storage_json(lambda: {"runs": backtests.list_runs()})

    @app.get("/api/models")
    @require(WebRole.READ_ONLY)
    def model_versions() -> Response:
        return storage_json(lambda: {"models": models.list_versions(limit=100)})

    @app.get("/api/experiments")
    @require(WebRole.READ_ONLY)
    def experiments() -> Response:
        return storage_json(lambda: {"experiments": models.experiments(limit=50)})

    @app.get("/api/orders")
    @require(WebRole.OPERATOR)
    def orders() -> Response:
        return storage_json(lambda: {"mode": "SIMULATION",
                                     "orders": [order.to_dict() for order in execution.list_orders(limit=100)]})

    @app.get("/api/controls")
    @require(WebRole.OPERATOR)
    def controls() -> Response:
        def produce() -> dict[str, Any]:
            by_scope = TradingControls(execution).all_freezes()
            freezes = sorted({code for codes in by_scope.values() for code in codes})
            kill = execution.get_control("kill_switch", {"active": False})
            return {"mode": "SIMULATION", "live_locked": True, "kill_switch": kill, "entry_freezes": freezes,
                    "entry_freezes_by_account": by_scope,
                    "entries_allowed": not freezes and not kill.get("active", False)}

        return storage_json(produce)

    @app.get("/api/reconciliations")
    @require(WebRole.OPERATOR)
    def reconciliations() -> Response:
        return storage_json(lambda: {"reconciliations": execution.reconciliations(limit=20)})

    @app.get("/api/audit")
    @require(WebRole.OPERATOR)
    def audit() -> Response:
        return storage_json(lambda: {"events": backtests.audit_events()})

    # -- administration (ADMIN, step-up with the admin's own password) ------------------------

    @app.get("/admin/users")
    @require(WebRole.ADMIN, api=False)
    def admin_users_page() -> Response:
        return page("admin_users.html", csrf_token=csrf_for_session(g.session), users=auth.list_users(),
                    roles=[role.value for role in WebRole], me=g.session.user.username)

    @app.get("/api/admin/users")
    @require(WebRole.ADMIN)
    def admin_users() -> Response:
        return jsonify({"users": [user.to_public() for user in auth.list_users()]})

    @app.get("/api/admin/auth-events")
    @require(WebRole.ADMIN)
    def admin_events() -> Response:
        return jsonify({"events": auth.events(limit=200)})

    def step_up() -> bool:
        return auth.confirm_password(g.session, request.form.get("admin_password", ""), client_ip())

    @app.post("/admin/users")
    @require(WebRole.ADMIN, api=False)
    def admin_create() -> Response:
        if not step_up():
            return admin_message("Mot de passe administrateur incorrect.", 401)
        temporary = crypto.new_token()[:24]
        try:
            auth.create_user(actor(), request.form.get("username", ""), WebRole(request.form.get("role", "READ_ONLY")),
                             temporary, must_change_password=True)
        except (AuthError, ValueError) as exc:
            return admin_message(str(exc), 400)
        return page("one_time_secret.html", label="Mot de passe initial (à changer à la première connexion)",
                    target=request.form.get("username", "").strip().casefold(), secret=temporary)

    @app.post("/admin/users/<username>/<action>")
    @require(WebRole.ADMIN, api=False)
    def admin_action(username: str, action: str) -> Response:
        if action not in {"disable", "enable", "revoke-sessions", "reset-password", "reset-mfa", "role"}:
            abort(404)
        if not step_up():
            return admin_message("Mot de passe administrateur incorrect.", 401)
        try:
            if action == "disable":
                auth.set_active(actor(), username, False)
            elif action == "enable":
                auth.set_active(actor(), username, True)
            elif action == "revoke-sessions":
                auth.revoke_user_sessions(actor(), username)
            elif action == "reset-mfa":
                auth.reset_mfa(actor(), username)
            elif action == "role":
                auth.set_role(actor(), username, WebRole(request.form.get("role", "")))
            else:
                temporary = auth.reset_password(actor(), username)
                return page("one_time_secret.html", label="Mot de passe temporaire (à changer à la connexion)",
                            target=username, secret=temporary)
        except (AuthError, ValueError) as exc:
            return admin_message(str(exc), 400)
        own_account = g.session.user.username == username.strip().casefold()
        if own_account and action in {"disable", "revoke-sessions", "reset-mfa", "role"}:
            return clear_session_cookie(redirect("/login", code=303))
        return redirect("/admin/users", code=303)

    def admin_message(message: str, status: int) -> Response:
        return page("admin_users.html", status, csrf_token=csrf_for_session(g.session), users=auth.list_users(),
                    roles=[role.value for role in WebRole], me=g.session.user.username, message=message)

    return app


def _stage_location_for(stage: SessionStage | None) -> str:
    return {SessionStage.MFA_PENDING: "/login/mfa", SessionStage.MFA_ENROLL: "/mfa/setup",
            SessionStage.PASSWORD_CHANGE: "/account/password", SessionStage.FULL: "/"}.get(stage, "/login")


def _stage_location(session: SessionRecord | None) -> str:
    return _stage_location_for(session.stage if session else None)


def _error(status: int, code: str) -> Response:
    wants_html = "text/html" in (request.headers.get("Accept") or "") and not request.path.startswith("/api/")
    if status == 401 and wants_html:
        return redirect("/login", code=303)
    response = jsonify({"error": code})
    response.status_code = status
    return response


def create_app_from_env() -> Flask:
    """Entry point for the production WSGI server: ``gunicorn 'astra.webapp:create_app_from_env()'``."""

    config, web = AppConfig.from_env(), WebConfig.from_env()
    auth = AuthStore(web.auth_database_path)
    auth.initialize()
    store = BacktestStore(config.database_path)
    store.initialize()
    ExecutionStore(config.database_path).initialize()
    ModelRegistry(config.database_path).initialize()
    return create_app(config, web, auth=auth, store=store)


def serve_local(config: AppConfig, web: WebConfig) -> None:
    """Development server on loopback only (``astra serve``); production uses gunicorn behind TLS."""

    app = create_app_from_env()
    print(f"ASTRA dashboard (authentication required): http://{config.host}:{config.port} (SIMULATION, loopback only)")
    app.run(host=config.host, port=config.port, debug=False, use_reloader=False, threaded=True)
