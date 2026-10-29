"""Dashboard routes for market data, backtests and paper simulations (SIMULATION only).

Reading needs READ_ONLY. Every state change needs OPERATOR and the session CSRF token, and only records a
request: an import or a backtest becomes a job that the worker runs; starting or stopping a simulation sets
its status, and the worker advances it. Nothing here imports the execution gateway, a broker or the risk
policy, and nothing can send an order, lift the kill switch or change the mode.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Flask, Response, g, jsonify, request

from . import indicators, learning, marketdata, mt5view
from .auth import WebRole
from .jobs import JobStore, worker_health
from .marketdata import CandleStore, MarketDataError
from .papertrade import LabStore, SimulationError, backtest_request, simulation_request, strategy_catalog

UPLOAD_LIMIT = marketdata.MAX_IMPORT_BYTES + 64 * 1024


def register_lab_routes(app: Flask, *, database: Path, import_dir: Path, require: Callable, actor: Callable[[], str],
                        error: Callable[[int, str], Response]) -> None:
    candles, lab, jobs = CandleStore(database), LabStore(database), JobStore(database)
    learn_store = learning.LearningStore(database)
    mt5_view = mt5view.Mt5View(database)
    for store in (candles, lab, jobs, learn_store, mt5_view):
        store.initialize()

    def answer(producer: Callable[[], Any], status: int = 200) -> Response:
        try:
            response = jsonify(producer())
            response.status_code = status
            return response
        except (MarketDataError, SimulationError) as exc:
            response = jsonify({"error": "invalid_request", "message": str(exc)})
            response.status_code = 400
            return response
        except (OSError, sqlite3.Error):
            return error(503, "storage_unavailable")

    def body() -> dict[str, Any]:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise SimulationError("corps JSON attendu")
        return payload

    def series_args() -> tuple[str, str, str]:
        symbol = marketdata.canonical_symbol(request.args.get("symbol", ""))
        timeframe = marketdata.canonical_timeframe(request.args.get("timeframe", ""))
        source = (request.args.get("source") or "").strip()
        if not source:
            raise MarketDataError("source obligatoire")
        return symbol, timeframe, source

    # -- reading (READ_ONLY) ----------------------------------------------------------------------

    @app.get("/api/market/overview")
    @require(WebRole.READ_ONLY)
    def market_overview() -> Response:
        def produce() -> dict[str, Any]:
            sources = []
            for source in candles.sources():
                sources.append({**source, "freshness": marketdata.freshness(source)})
            coverage = candles.coverage()
            return {"mode": "SIMULATION", "pairs": list(marketdata.PAIRS), "timeframes": list(marketdata.TIMEFRAMES),
                    "sources": sources, "coverage": coverage, "imports": candles.imports(limit=20),
                    "total_candles": sum(item["candles"] for item in coverage),
                    "live_feed": {"connected": False,
                                  "reason": "aucun fournisseur temps réel avec identifiants n'est configuré ; "
                                            "les cotations affichées ne sont jamais présentées comme du temps réel"},
                    "worker": worker_health(jobs)}

        return answer(produce)

    @app.get("/api/market/quality")
    @require(WebRole.READ_ONLY)
    def market_quality() -> Response:
        return answer(lambda: candles.quality(*series_args()))

    @app.get("/api/market/candles")
    @require(WebRole.READ_ONLY)
    def market_candles() -> Response:
        def produce() -> dict[str, Any]:
            symbol, timeframe, source = series_args()
            try:
                limit = max(10, min(1000, int(request.args.get("limit", "300") or 300)))
            except ValueError as exc:
                raise MarketDataError("limit doit être un nombre") from exc
            rows = candles.candles(symbol, timeframe, source, limit=limit, newest=True)
            return {"symbol": symbol, "timeframe": timeframe, "source": source,
                    "candles": [{"ts": c.timestamp.isoformat(), "open": c.open, "high": c.high, "low": c.low,
                                 "close": c.close, "volume": c.volume, "kind": c.kind} for c in rows]}

        return answer(produce)

    @app.get("/api/lab/catalog")
    @require(WebRole.READ_ONLY)
    def lab_catalog() -> Response:
        return answer(strategy_catalog)

    @app.get("/api/lab/backtests")
    @require(WebRole.READ_ONLY)
    def lab_backtests() -> Response:
        return answer(lambda: {"backtests": lab.backtests(limit=50)})

    @app.get("/api/lab/backtests/<int:backtest_id>")
    @require(WebRole.READ_ONLY)
    def lab_backtest(backtest_id: int) -> Response:
        item = lab.backtest(backtest_id)
        return jsonify(item) if item else error(404, "not_found")

    @app.get("/api/sim/sessions")
    @require(WebRole.READ_ONLY)
    def sim_sessions() -> Response:
        return answer(lambda: {"sessions": lab.sessions(), "worker": worker_health(jobs)})

    @app.get("/api/sim/sessions/<int:session_id>")
    @require(WebRole.READ_ONLY)
    def sim_session(session_id: int) -> Response:
        item = lab.session(session_id)
        return jsonify(item) if item else error(404, "not_found")

    @app.get("/api/learn/overview")
    @require(WebRole.READ_ONLY)
    def learn_overview() -> Response:
        return answer(lambda: learning.overview(learn_store, lab))

    @app.get("/api/mt5/overview")
    @require(WebRole.READ_ONLY)
    def mt5_overview() -> Response:
        return answer(mt5_view.overview)

    @app.get("/api/market/chart")
    @require(WebRole.READ_ONLY)
    def market_chart() -> Response:
        """Candles with SMA 20 and 50, RSI 14 and MACD, aligned; the indicators warm up on 120 earlier candles."""

        def produce() -> dict[str, Any]:
            symbol, timeframe, source = series_args()
            try:
                limit = max(10, min(600, int(request.args.get("limit", "200") or 200)))
            except ValueError as exc:
                raise MarketDataError("limit doit être un nombre") from exc
            rows = list(reversed(candles.candles(symbol, timeframe, source, limit=limit + 120, newest=True)))
            series = indicators.bundle([c.close for c in rows])
            keep = slice(max(0, len(rows) - limit), None)
            return {"symbol": symbol, "timeframe": timeframe, "source": source,
                    "candles": [{"ts": c.timestamp.isoformat(), "open": c.open, "high": c.high, "low": c.low,
                                 "close": c.close, "volume": c.volume} for c in rows[keep]],
                    "indicators": {name: [None if v is None else round(v, 6) for v in values[keep]]
                                   for name, values in series.items()}}

        return answer(produce)

    @app.get("/api/dashboard/overview")
    @require(WebRole.READ_ONLY)
    def dashboard_overview() -> Response:
        def produce() -> dict[str, Any]:
            account = mt5_view.overview(event_limit=40)
            perf = mt5_view.performance()
            quotes = mt5_view.quotes()
            source = mt5view.source_name("MetaQuotes-Demo")
            for symbol, quote in quotes.items():
                quote["change_24h_pct"] = None
                rows = candles.candles(symbol, "H1", source, limit=25, newest=True)
                if len(rows) >= 25 and rows[-1].close:
                    quote["change_24h_pct"] = round((quote["bid"] / rows[-1].close - 1) * 100, 3)
            signals = []
            for pair in learning.overview(learn_store, lab)["pairs"]:
                session = pair.get("session")
                detail = lab.session(session["id"], item_limit=1) if session else None
                last = detail["signals"][-1] if detail and detail.get("signals") else None
                family = pair["champion"]["params"]["strategy"].get("family", "sma_trend")
                signals.append({"symbol": pair["symbol"], "family": family, "version": pair["champion"]["version"],
                                "decision": last["decision"] if last else None, "reason": last["reason"] if last else None,
                                "since": last["ts"] if last else None})
            return {"mode": "SIMULATION", "live_locked": True, "mt5": account, "performance": perf, "quotes": quotes,
                    "signals": signals}

        return answer(produce)

    @app.get("/api/system/jobs")
    @require(WebRole.READ_ONLY)
    def system_jobs() -> Response:
        return answer(lambda: {"jobs": jobs.jobs(limit=60), "worker": worker_health(jobs),
                               "sources": [{**s, "freshness": marketdata.freshness(s)} for s in candles.sources()]})

    # -- requests (OPERATOR, CSRF) ----------------------------------------------------------------

    @app.post("/api/market/import")
    @require(WebRole.OPERATOR)
    def market_import() -> Response:
        request.max_content_length = UPLOAD_LIMIT  # this route only: CSV history files are larger than forms

        def produce() -> dict[str, Any]:
            upload = request.files.get("file")
            if upload is None or not upload.filename:
                raise MarketDataError("fichier CSV manquant")
            form = request.form
            symbol = marketdata.canonical_symbol(form.get("symbol", ""))
            timeframe = marketdata.canonical_timeframe(form.get("timeframe", ""))
            source = (form.get("source") or "").strip()
            nature = form.get("nature") or "HISTORICAL"
            timezone = (form.get("timezone") or "UTC").strip()
            marketdata.check_import_source(candles, source, nature)
            raw = upload.read(UPLOAD_LIMIT + 1)
            # Validate before queueing, so an obviously wrong file is refused at once with the reason.
            _, report = marketdata.parse_csv(raw, symbol, timeframe, source, timezone=timezone, nature=nature)
            if not report.accepted:
                raise MarketDataError("aucune bougie valide dans ce fichier : " +
                                      "; ".join(f"{i['code']} ({i['detail']})" for i in report.issues[:5]))
            dedupe = f"import:{report.file_sha256}:{symbol}:{timeframe}:{source}:{timezone}:{nature}"
            job = jobs.active(dedupe)
            if job is None:
                import_dir.mkdir(parents=True, exist_ok=True)
                path = import_dir / f"import-{secrets.token_hex(12)}.csv"
                path.write_bytes(raw)
                job = jobs.enqueue("import_csv", {"path": str(path), "symbol": symbol, "timeframe": timeframe,
                                                  "source": source, "timezone": timezone, "nature": nature}, actor(),
                                   dedupe_key=dedupe)
                if job["duplicate"]:  # lost a race with an identical request: the file is not needed
                    path.unlink(missing_ok=True)
            return {"job": job, "preview": {"rows_read": report.rows_read, "accepted": report.accepted,
                                            "rejected": report.rejected, "gaps": report.gaps, "first": report.first,
                                            "last": report.last, "issues": report.issues[:10]}}

        return answer(produce, 202)

    @app.post("/api/market/ingest")
    @require(WebRole.OPERATOR)
    def market_ingest() -> Response:
        return answer(lambda: {"job": jobs.enqueue("ingest_ecb", {"force": True}, actor(), dedupe_key="ingest_ecb")}, 202)

    @app.post("/api/lab/backtests")
    @require(WebRole.OPERATOR)
    def lab_backtest_create() -> Response:
        def produce() -> dict[str, Any]:
            validated = backtest_request(body())
            backtest_id = lab.create_backtest(validated, actor())
            job = jobs.enqueue("backtest", {"backtest_id": backtest_id}, actor(), dedupe_key=f"backtest:{backtest_id}")
            return {"backtest_id": backtest_id, "job": job}

        return answer(produce, 202)

    @app.post("/api/mt5/snapshot")
    @require(WebRole.OPERATOR)
    def mt5_snapshot() -> Response:
        if g.get("agent") is None:
            return error(403, "forbidden")  # only the MT5 bridge, with an agent token, reports the account

        def produce() -> dict[str, Any]:
            try:
                return mt5_view.record(request.get_json(silent=True), actor())
            except mt5view.Mt5ViewError as exc:
                raise MarketDataError(str(exc)) from exc

        return answer(produce)

    @app.post("/api/mt5/quotes")
    @require(WebRole.OPERATOR)
    def mt5_quotes() -> Response:
        if g.get("agent") is None:
            return error(403, "forbidden")

        def produce() -> dict[str, Any]:
            try:
                return mt5_view.record_quotes(request.get_json(silent=True))
            except mt5view.Mt5ViewError as exc:
                raise MarketDataError(str(exc)) from exc

        return answer(produce)

    @app.post("/api/mt5/candles")
    @require(WebRole.OPERATOR)
    def mt5_candles() -> Response:
        if g.get("agent") is None:
            return error(403, "forbidden")

        def produce() -> dict[str, Any]:
            try:
                source, symbol, timeframe, rows = mt5view.parse_candles(request.get_json(silent=True))
            except mt5view.Mt5ViewError as exc:
                raise MarketDataError(str(exc)) from exc
            symbol = marketdata.canonical_symbol(symbol)
            batch = [marketdata.Candle(symbol, timeframe, ts, o, h, low, c, v, source, marketdata.KIND_OHLC)
                     for ts, o, h, low, c, v in rows]
            candles.register_source(source, "MT5_DEMO_FEED", "HISTORICAL", "Bougies closes du terminal MT5 (compte démo)")
            inserted, same, conflicts = candles.store(batch, "HISTORICAL", None)
            candles.source_success(source, max(r[0] for r in rows).isoformat())
            return {"inserted": inserted, "already_stored": same, "conflicts": conflicts}

        return answer(produce)

    @app.post("/api/sim/sessions")
    @require(WebRole.OPERATOR)
    def sim_create() -> Response:
        return answer(lambda: {"session_id": lab.create_session(simulation_request(body(), candles), actor())}, 201)

    @app.post("/api/sim/sessions/<int:session_id>/<action>")
    @require(WebRole.OPERATOR)
    def sim_action(session_id: int, action: str) -> Response:
        if action not in ("stop", "start"):
            return error(404, "not_found")
        return answer(lambda: lab.set_session_status(session_id, "STOPPED" if action == "stop" else "RUNNING", actor()))
