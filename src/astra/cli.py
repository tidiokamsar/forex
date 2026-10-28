"""Command line entry point for local research and dashboard operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .attempts import AttemptLog, AttemptStatus, TrialScope
from .brokers.base import BrokerRequestError, BrokerTimeout, BrokerUnavailable
from .config import AppConfig, ModeUnavailableError
from .data import load_bars_csv
from .engine import run_paper_replay, strategy_decisions
from .execution import ExecutionStore, TradingControls
from .governance import Actor, PermissionDenied, Role
from .logs import configure_logging, reset_logging
from .models import BacktestConfig
from .registry import ModelRegistry, PromotionCriteria
from .research import run_sma_backtest
from .risk import RiskEngine
from .storage import BacktestStore
from .validation import (
    WalkForwardProtocol,
    evaluate_holdout_once,
    evaluate_walk_forward,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_TYPES = ("HISTORICAL", "DELAYED", "LIVE", "SIMULATED", "SYNTHETIC")
DEMO_FIXTURE_SHA256 = "16402dc19dc55033e851b1f796aed2a872a19d0a9648ede39f1dfb59ab5d5ef8"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astra",
        description="Recherche Forex locale en simulation uniquement.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Afficher le mode et les capacités locales")
    commands.add_parser("demo", help="Backtester le jeu synthétique inclus")
    commands.add_parser("serve", help="Démarrer le tableau de bord authentifié (boucle locale, développement)")
    auth_cmd = commands.add_parser("auth", help="Comptes du tableau de bord (à exécuter localement sur le serveur)")
    auth_actions = auth_cmd.add_subparsers(dest="auth_action", required=True)
    init_admin = auth_actions.add_parser("init-admin", help="Créer le premier administrateur (refusé si un compte existe)")
    init_admin.add_argument("--username", required=True)
    init_admin.add_argument("--generate", action="store_true",
                            help="Générer le mot de passe (affiché une fois, à changer à la connexion) au lieu de le saisir")
    add_user = auth_actions.add_parser("add-user", help="Créer un compte (mot de passe initial généré, affiché une fois)")
    add_user.add_argument("--username", required=True)
    add_user.add_argument("--role", required=True, choices=("READ_ONLY", "OPERATOR", "ADMIN"))
    for name, text in (("disable", "Désactiver un compte et révoquer ses sessions"), ("enable", "Réactiver un compte"),
                       ("revoke-sessions", "Révoquer toutes les sessions d'un compte"),
                       ("reset-mfa", "Récupération : supprimer la MFA (réenrôlement à la connexion)"),
                       ("reset-password", "Récupération : mot de passe temporaire affiché une fois")):
        sub = auth_actions.add_parser(name, help=text)
        sub.add_argument("--username", required=True)
    set_role = auth_actions.add_parser("set-role", help="Changer le rôle web d'un compte (révoque ses sessions)")
    set_role.add_argument("--username", required=True)
    set_role.add_argument("--role", required=True, choices=("READ_ONLY", "OPERATOR", "ADMIN"))
    auth_actions.add_parser("list", help="Lister les comptes")
    key_init = auth_actions.add_parser("mfa-key-init", help="Créer le trousseau de clés MFA (ASTRA_MFA_KEY_FILE)")
    key_init.add_argument("--group", help="Groupe lecteur du fichier (mode 0640), par exemple astra ; POSIX")
    auth_actions.add_parser("mfa-key-rotate", help="Nouvelle clé MFA active et rechiffrement de tous les secrets TOTP")
    retire = auth_actions.add_parser("mfa-key-retire", help="Retirer une ancienne clé MFA qui ne protège plus aucun secret")
    retire.add_argument("--kid", required=True)
    auth_actions.add_parser("mfa-reencrypt", help="Migration : chiffrer les secrets TOTP en clair, rechiffrer sous la clé active")
    auth_actions.add_parser("mfa-status", help="Clés MFA, clés utilisées et secrets en clair restants")
    auth_actions.add_parser("cleanup", help="Supprimer les sessions expirées ou révoquées depuis plus d'un jour")
    events = auth_actions.add_parser("events", help="Journal d'audit de l'authentification")
    events.add_argument("--limit", type=int, default=100)
    replay = commands.add_parser(
        "paper-replay",
        help="Rejouer des barres via Order Gateway et courtier simulé (aucun ordre externe)",
    )
    replay.add_argument("--csv", type=Path, help="Fichier OHLC CSV; défaut: jeu synthétique inclus")
    replay.add_argument("--source-type", choices=SOURCE_TYPES)
    replay.add_argument("--source-name", help="Libellé de provenance sans chemin local")
    replay.add_argument("--interval-seconds", type=float)
    replay.add_argument("--spread-pips", type=float, default=1.0)
    replay.add_argument("--strategy", choices=("sma", "momentum", "breakout"), default="sma")
    ml = commands.add_parser(
        "ml-baseline",
        help="Régression logistique vs taux de base en walk-forward purgé; enregistre l'expérience",
    )
    ml.add_argument("--csv", type=Path, help="Fichier OHLC CSV; défaut: jeu synthétique inclus")
    ml.add_argument("--source-type", choices=SOURCE_TYPES)
    ml.add_argument("--source-name")
    ml.add_argument("--interval-seconds", type=float)
    ml.add_argument("--horizon", type=int, default=4)
    ml.add_argument("--folds", type=int, default=2)
    ml.add_argument("--validation-rows", type=int, default=20)
    ml.add_argument("--minimum-training-rows", type=int, default=60)
    ml.add_argument("--embargo-bars", type=int, default=2)
    strategies_wf = commands.add_parser(
        "evaluate-strategies",
        help="Walk-forward scellé multi-familles (SMA, momentum, breakout); preuves pour le registre",
    )
    strategies_wf.add_argument("--csv", type=Path, help="Fichier OHLC CSV; défaut: jeu synthétique inclus")
    strategies_wf.add_argument("--source-type", choices=SOURCE_TYPES)
    strategies_wf.add_argument("--source-name", help="Libellé de provenance sans chemin local")
    strategies_wf.add_argument("--interval-seconds", type=float)
    strategies_wf.add_argument("--folds", type=int, default=2)
    strategies_wf.add_argument("--minimum-training-bars", type=int, default=60)
    strategies_wf.add_argument("--validation-bars", type=int, default=20)
    strategies_wf.add_argument("--holdout-bars", type=int, default=20)
    strategies_wf.add_argument("--minimum-candidate-trades", type=int, default=1)
    strategies_wf.add_argument(
        "--evaluate-holdout",
        action="store_true",
        help="Consommer une seule fois le holdout final, après scellement SQLite du plan",
    )
    strategies_wf.add_argument(
        "--register",
        action="store_true",
        help="Enregistrer le candidat sélectionné (CANDIDATE inactif) avec la preuve holdout",
    )
    strategies_wf.add_argument("--model-id", help="Identifiant du modèle au registre (avec --register)")
    strategies_wf.add_argument("--version", help="Version immuable au registre (avec --register)")
    check = commands.add_parser(
        "ctrader-check",
        help="Vérification en lecture seule d'un compte démo cTrader (aucun ordre envoyé)",
    )
    check.add_argument("--operator", required=True, help="Identifiant de l'opérateur humain")
    orders = commands.add_parser("orders", help="Lister les ordres, ou résoudre un ordre à l'état inconnu")
    orders.add_argument("--resolve", metavar="INTENT_ID", help="Résoudre un ordre UNKNOWN (opérateur humain)")
    orders.add_argument("--outcome", choices=("FILLED", "NOT_EXECUTED"))
    orders.add_argument("--evidence", help="Référence chez le fournisseur (deal, ordre, relevé)")
    orders.add_argument("--filled-units", type=float)
    orders.add_argument("--price", type=float, help="Prix moyen d'exécution si FILLED")
    orders.add_argument("--operator", help="Identifiant de l'opérateur humain")
    commands.add_parser("models", help="Lister le registre des modèles et expériences")
    attempts_cmd = commands.add_parser(
        "attempts", help="Registre des tentatives de recherche (lecture seule): lister, compter, vérifier la chaîne"
    )
    attempt_actions = attempts_cmd.add_subparsers(dest="attempts_action", required=True)
    attempts_list = attempt_actions.add_parser("list", help="Lister l'état courant des tentatives")
    attempts_list.add_argument("--protocol", help="Filtrer sur un plan scellé (protocol_id)")
    attempts_list.add_argument("--instrument")
    attempts_list.add_argument("--status", choices=[status.value for status in AttemptStatus])
    attempts_list.add_argument("--limit", type=int, default=100)
    attempts_count = attempt_actions.add_parser(
        "count", help="Nombre d'essais distincts à l'échelle du store (tous plans, tous statuts)"
    )
    attempts_count.add_argument("--protocol", help="Portée: instrument et période du plan scellé")
    attempts_count.add_argument("--instrument", help="Portée explicite (sans --protocol)")
    attempts_count.add_argument("--start", help="Début de période ISO 8601 (sans --protocol)")
    attempts_count.add_argument("--end", help="Fin de période ISO 8601 (sans --protocol)")
    attempts_verify = attempt_actions.add_parser("verify", help="Vérifier la chaîne de hachage des tentatives")
    attempts_verify.add_argument(
        "--expected-head", help="Tête de chaîne conservée hors de la base (row_hash), pour détecter une réécriture"
    )
    service = commands.add_parser("service", help="Service de trading continu (courtier simulé dans cette version)")
    service_actions = service.add_subparsers(dest="service_action", required=True)
    service_run = service_actions.add_parser("run", help="Lancer la boucle continue sur le courtier simulé")
    service_run.add_argument("--strategy", choices=("sma", "momentum", "breakout"), default="sma")
    service_run.add_argument("--timeframe", type=int, default=60, help="Durée des barres en secondes")
    service_run.add_argument("--max-ticks", type=int, help="Arrêt après N ticks (défaut: jusqu'à Ctrl+C)")
    service_run.add_argument("--tick-seconds", type=float, default=5.0, help="Pas du flux synthétique")
    service_run.add_argument("--realtime", action="store_true", help="Attendre réellement entre deux ticks")
    service_run.add_argument("--seed", type=int, default=7)
    service_actions.add_parser("status", help="Afficher le dernier battement de cœur de chaque service")
    backup = commands.add_parser("backup", help="Sauvegarde, vérification et restauration de la base")
    backup_actions = backup.add_subparsers(dest="backup_action", required=True)
    backup_create = backup_actions.add_parser("create", help="Créer une sauvegarde cohérente vérifiée")
    backup_create.add_argument("--dir", type=Path, help="Dossier de sauvegarde (défaut: ASTRA_BACKUP_DIR ou var/backups)")
    backup_create.add_argument("--keep", type=int, default=14)
    backup_verify = backup_actions.add_parser("verify", help="Vérifier hash et intégrité d'une sauvegarde")
    backup_verify.add_argument("path", type=Path)
    backup_restore = backup_actions.add_parser("restore", help="Restaurer (service arrêté, opérateur humain)")
    backup_restore.add_argument("path", type=Path)
    backup_restore.add_argument("--operator", required=True)
    backup_restore.add_argument("--reason", required=True)
    controls = commands.add_parser("controls", help="Arrêt d'urgence et gels des entrées")
    control_actions = controls.add_subparsers(dest="control_action", required=True)
    control_actions.add_parser("show", help="Afficher l'état des contrôles")
    kill = control_actions.add_parser("kill", help="Activer l'arrêt d'urgence")
    kill.add_argument("--reason", required=True)
    kill.add_argument("--operator", required=True, help="Identifiant de l'opérateur")
    release = control_actions.add_parser("release", help="Lever l'arrêt d'urgence (humain)")
    release.add_argument("--reason", required=True)
    release.add_argument("--operator", required=True, help="Identifiant de l'opérateur humain")
    clear = control_actions.add_parser(
        "clear-freeze",
        help="Lever un gel non lié à la réconciliation (operator, service_degraded...) ; humain",
    )
    clear.add_argument("--freeze", required=True, help="Code du gel, par exemple service_degraded")
    clear.add_argument("--account", help="Compte concerné (défaut : gel global)")
    clear.add_argument("--operator", required=True, help="Identifiant de l'opérateur humain")
    backtest = commands.add_parser("backtest", help="Backtester un fichier CSV local")
    backtest.add_argument("--csv", required=True, type=Path, help="Fichier OHLC CSV UTF-8")
    backtest.add_argument("--source-type", required=True, choices=SOURCE_TYPES)
    backtest.add_argument(
        "--exploratory",
        action="store_true",
        required=True,
        help="confirmer explicitement un score exploratoire sur tout l'historique du CSV",
    )
    backtest.add_argument("--source-name", help="Libellé de provenance sans chemin local")
    backtest.add_argument(
        "--interval-seconds", type=float, help="Cadence attendue pour le contrôle des trous"
    )
    walk_forward = commands.add_parser(
        "walk-forward",
        help="Évaluer des variantes sur des fenêtres chronologiques; réserve le holdout final",
    )
    walk_forward.add_argument("--csv", required=True, type=Path, help="Fichier OHLC CSV UTF-8")
    walk_forward.add_argument("--source-type", required=True, choices=SOURCE_TYPES)
    walk_forward.add_argument("--source-name", help="Libellé de provenance sans chemin local")
    walk_forward.add_argument(
        "--interval-seconds", type=float, help="Cadence attendue pour le contrôle des trous"
    )
    walk_forward.add_argument(
        "--hypothesis", required=True, help="Hypothèse falsifiable fixée avant évaluation"
    )
    walk_forward.add_argument(
        "--candidate",
        action="append",
        metavar="FAST:SLOW",
        help="Paire SMA candidate; répétable. Défaut: 5:13, 8:21 et 10:30.",
    )
    walk_forward.add_argument("--folds", type=int, default=3)
    walk_forward.add_argument("--minimum-training-bars", type=int, default=60)
    walk_forward.add_argument("--validation-bars", type=int, default=20)
    walk_forward.add_argument("--holdout-bars", type=int, default=20)
    walk_forward.add_argument("--minimum-candidate-trades", type=int, default=1)
    walk_forward.add_argument(
        "--evaluate-holdout",
        action="store_true",
        help="Consommer une seule fois le holdout final, après scellement SQLite du protocole",
    )
    demo_cmd = commands.add_parser("demo-account", help="Compte Binance Spot DEMO (aucun ordre réel ; LIVE verrouillé)")
    demo_actions = demo_cmd.add_subparsers(dest="demo_action", required=True)
    demo_actions.add_parser("check", help="Lire l'heure, le compte, les règles et un prix de Binance Demo")
    demo_test = demo_actions.add_parser("controlled-test", help="Ordre DEMO de test minimal : achat, contrôle, revente")
    demo_test.add_argument("--operator", required=True, help="Nom de l'opérateur humain qui demande le test")
    demo_actions.add_parser("run", help="Service DEMO supervisé (astra-demo.service)")
    demo_actions.add_parser("pause", help="Mettre l'exécution DEMO en pause")
    demo_actions.add_parser("resume", help="Reprendre l'exécution DEMO")
    worker = commands.add_parser("worker", help="Worker continu : imports, taux BCE, backtests, simulations (SIMULATION)")
    worker.add_argument("--once", action="store_true", help="Une seule itération puis arrêt")
    worker.add_argument("--no-ecb", action="store_true", help="Ne pas planifier l'acquisition des taux BCE")
    data_cmd = commands.add_parser("data", help="Données de marché : import CSV, taux BCE, couverture")
    data_actions = data_cmd.add_subparsers(dest="data_action", required=True)
    data_import = data_actions.add_parser("import", help="Importer un fichier CSV OHLC")
    data_import.add_argument("--csv", required=True, type=Path)
    data_import.add_argument("--symbol", required=True)
    data_import.add_argument("--timeframe", required=True, help="M5, M15, H1, H4 ou D1")
    data_import.add_argument("--source", required=True, help="Nom du fournisseur ou de l'export (sans chemin)")
    data_import.add_argument("--timezone", default="UTC", help="Fuseau des horodatages sans décalage (ex. Europe/Paris)")
    data_import.add_argument("--nature", choices=("HISTORICAL", "SYNTHETIC"), default="HISTORICAL")
    ecb = data_actions.add_parser("ingest-ecb", help="Acquérir les taux de référence BCE (sans identifiant)")
    ecb.add_argument("--force", action="store_true", help="Ignorer la pause après échec")
    data_actions.add_parser("coverage", help="Paires, unités de temps et nombre de bougies stockées")
    lab_cmd = commands.add_parser("lab", help="Backtest sur les bougies stockées (stratégie expérimentale)")
    lab_actions = lab_cmd.add_subparsers(dest="lab_action", required=True)
    lab_backtest = lab_actions.add_parser("backtest", help="Lancer un backtest et l'enregistrer")
    lab_backtest.add_argument("--symbol", required=True)
    lab_backtest.add_argument("--timeframe", required=True)
    lab_backtest.add_argument("--source", required=True)
    lab_backtest.add_argument("--start")
    lab_backtest.add_argument("--end")
    lab_backtest.add_argument("--capital", type=float)
    lab_backtest.add_argument("--risk-pct", type=float)
    lab_backtest.add_argument("--spread-pips", type=float)
    return parser


def _lab_command(args: argparse.Namespace, config: AppConfig) -> int:
    from .jobs import JobStore, Worker
    from .marketdata import CandleStore, ingest_ecb
    from .papertrade import LabStore, backtest_request, run_backtest

    candles = CandleStore(config.database_path)
    candles.initialize()
    lab = LabStore(config.database_path)
    lab.initialize()
    JobStore(config.database_path).initialize()
    if args.command == "worker":
        import_dir = Path(os.environ.get("ASTRA_IMPORT_DIR") or (config.database_path.parent / "imports"))
        worker = Worker(config.database_path, import_dir, ecb_enabled=not args.no_ecb)
        worker.run(once=args.once)
        return 0
    if args.command == "data":
        if args.data_action == "import":
            _write_json(candles.import_csv(args.csv.read_bytes(), args.symbol, args.timeframe, args.source,
                                           timezone=args.timezone, nature=args.nature, actor="cli"))
        elif args.data_action == "ingest-ecb":
            result = ingest_ecb(candles, actor="cli", force=args.force)
            _write_json(result)
            return 0 if result["status"] in ("OK", "SKIPPED") else 3
        else:
            _write_json({"coverage": candles.coverage(), "sources": candles.sources()})
        return 0
    risk = {"initial_capital": args.capital, "risk_per_trade_pct": args.risk_pct}
    request = backtest_request({"symbol": args.symbol, "timeframe": args.timeframe, "source": args.source,
                                "start": args.start, "end": args.end,
                                "risk": {k: v for k, v in risk.items() if v is not None},
                                "costs": {"spread_pips": args.spread_pips} if args.spread_pips is not None else {}})
    backtest_id = lab.create_backtest(request, "cli")
    result = run_backtest(candles, request)
    lab.save_backtest_result(backtest_id, result)
    _write_json({"backtest_id": backtest_id, "data": result["data"], "metrics": result["metrics"],
                 "assumptions": result["assumptions"]})
    return 0


def _demo_command(args: argparse.Namespace, config: AppConfig) -> int:
    from .demo_requests import DemoStore

    if args.demo_action in ("pause", "resume"):
        store = DemoStore(config.database_path)
        store.initialize()
        store.set("paused", args.demo_action == "pause")
        _write_json({"paused": args.demo_action == "pause"})
        return 0
    from .demo_runner import runtime_from_environment

    runtime = runtime_from_environment(config.database_path)
    if args.demo_action == "check":
        result = runtime.check()
        _write_json(result)
        return 0 if result.get("connected") else 3
    if args.demo_action == "controlled-test":
        result = runtime.controlled_test(f"cli:{args.operator}")
        _write_json(result)
        return 0 if result.get("result") == "RÉUSSI" else 3
    runtime.run()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Logs go to stderr (and optionally a rotating file); stdout stays JSON output.
    configure_logging(
        level=os.environ.get("ASTRA_LOG_LEVEL", "WARNING"),
        log_dir=os.environ.get("ASTRA_LOG_DIR") or None,
        stream=sys.stderr,
    )
    try:
        return _run(args)
    finally:
        reset_logging()


def _run(args: argparse.Namespace) -> int:
    try:
        config = AppConfig.from_env()
        if args.command == "status":
            _write_json(
                {
                    "mode": config.mode.value,
                    "live_locked": True,
                    "broker_adapter": None,
                    "external_orders": False,
                    "dashboard_host": config.host,
                    "dashboard_port": config.port,
                    "database_path": str(config.database_path),
                }
            )
            return 0

        if args.command == "serve":
            from .config import WebConfig
            from .webapp import serve_local

            serve_local(config, WebConfig.from_env())
            return 0

        if args.command == "auth":
            from .auth.admin_cli import run as run_auth

            return run_auth(args)

        if args.command == "ctrader-check":
            _write_json(ctrader_check(args.operator, config))
            return 0

        if args.command == "service":
            return _service_command(args, config)

        if args.command in ("worker", "data", "lab"):
            return _lab_command(args, config)

        if args.command == "demo-account":
            return _demo_command(args, config)

        if args.command == "backup":
            from dataclasses import asdict as _asdict

            from .backup import create_backup, restore_backup, verify_backup

            if args.backup_action == "create":
                directory = args.dir or Path(os.environ.get("ASTRA_BACKUP_DIR") or (PROJECT_ROOT / "var" / "backups"))
                _write_json({"backup": _asdict(create_backup(config.database_path, directory, keep=args.keep))})
            elif args.backup_action == "verify":
                _write_json({"verified": _asdict(verify_backup(args.path))})
            else:
                actor = Actor(f"cli:{args.operator}", Role.HUMAN_OPERATOR)
                _write_json({"restore": restore_backup(args.path, config.database_path, actor=actor, reason=args.reason)})
            return 0

        if args.command in ("orders", "controls", "models"):
            return _operations_command(args, config)

        if args.command == "attempts":
            return _attempts_command(args, config)

        defaultable = ("paper-replay", "ml-baseline", "evaluate-strategies")
        if args.command in defaultable and args.csv is not None and args.source_type is None:
            raise ValueError("--source-type is required with --csv")

        if args.command == "demo" or (args.command in defaultable and args.csv is None):
            csv_path = PROJECT_ROOT / "data" / "synthetic_eurusd_m15.csv"
            if hashlib.sha256(csv_path.read_bytes()).hexdigest() != DEMO_FIXTURE_SHA256:
                raise ValueError(
                    "bundled demo fixture failed its integrity check; use backtest --exploratory "
                    "for a separate CSV"
                )
            source_type = "SYNTHETIC"
            source_name = "synthetic_eurusd_m15.csv"
            expected_interval = 900.0
        else:
            csv_path = args.csv
            source_type = args.source_type
            source_name = args.source_name
            expected_interval = args.interval_seconds

        dataset = load_bars_csv(
            csv_path,
            source_type=source_type,
            source_name=source_name,
            expected_interval_seconds=expected_interval,
        )
        if not dataset.usable:
            _write_json(
                {
                    "status": "rejected",
                    "mode": "SIMULATION",
                    "dataset_id": dataset.dataset_id,
                    "source_type": dataset.source_type,
                    "sha256": dataset.sha256,
                    "usable": False,
                    "issues": [asdict(issue) for issue in dataset.issues],
                    "external_orders": False,
                },
                stream=sys.stderr,
            )
            return 2

        if args.command == "paper-replay":
            execution_store = ExecutionStore(config.database_path)
            execution_store.initialize()
            summary = run_paper_replay(
                dataset.bars,
                execution_store,
                spread_pips=args.spread_pips,
                decide=strategy_decisions(args.strategy),
                data_label=f"{dataset.source_type}:{dataset.source_name}",
            )
            _write_json(
                {
                    "status": "completed",
                    "mode": "SIMULATION",
                    "dataset": {"dataset_id": dataset.dataset_id, "sha256": dataset.sha256},
                    "replay": summary.to_dict(),
                    "external_orders": False,
                }
            )
            return 0

        if args.command == "ml-baseline":
            _write_json(_ml_baseline(args, config, dataset))
            return 0

        if args.command == "evaluate-strategies":
            _write_json(_evaluate_strategies(args, config, dataset))
            return 0

        provenance = {
            "dataset_id": dataset.dataset_id,
            "source_type": dataset.source_type,
            "source_name": dataset.source_name,
            "sha256": dataset.sha256,
            "generated_at_utc": datetime.now(UTC).isoformat(),
        }
        if args.command == "backtest":
            provenance["evaluation_classification"] = "EXPLORATORY_FULL_HISTORY_NON_PROMOTABLE"
        if args.command == "walk-forward":
            candidate_pairs = args.candidate or ("5:13", "8:21", "10:30")
            candidates = tuple(
                BacktestConfig(fast_window=fast, slow_window=slow)
                for fast, slow in (_parse_candidate(value) for value in candidate_pairs)
            )
            store = BacktestStore(config.database_path)
            store.initialize()
            # The store seals the plan before scoring and records every attempt.
            report = evaluate_walk_forward(
                dataset.bars,
                candidates,
                hypothesis=args.hypothesis,
                protocol=WalkForwardProtocol(
                    fold_count=args.folds,
                    minimum_training_bars=args.minimum_training_bars,
                    validation_bars=args.validation_bars,
                    holdout_bars=args.holdout_bars,
                    minimum_candidate_trades=args.minimum_candidate_trades,
                ),
                dataset_sha256=dataset.sha256,
                store=store,
            )
            holdout_result = None
            if args.evaluate_holdout:
                holdout_value = evaluate_holdout_once(
                    dataset.bars,
                    candidates,
                    walk_forward_report=report,
                    protocol=WalkForwardProtocol(
                        fold_count=args.folds,
                        minimum_training_bars=args.minimum_training_bars,
                        validation_bars=args.validation_bars,
                        holdout_bars=args.holdout_bars,
                        minimum_candidate_trades=args.minimum_candidate_trades,
                    ),
                    store=store,
                    dataset_sha256=dataset.sha256,
                )
                holdout_result = (
                    holdout_value if isinstance(holdout_value, dict) else asdict(holdout_value)
                )
            _write_json(
                {
                    "status": "completed",
                    "mode": "SIMULATION",
                    "report": asdict(report),
                    "protocol_id": report.plan_sha256,
                    "holdout_persistence": "SQLITE_ONCE_PER_CANONICAL_MARKET_BAR_IDENTITY",
                    "holdout_lock_scope": "CONFIGURED_LOCAL_SQLITE_STORE",
                    "holdout_evaluation": holdout_result,
                    "exploratory_warning": (
                        "Walk-forward results are exploratory and non-promotable. This local "
                        "store freezes one plan per canonical dataset, but direct API calls, "
                        "full-history exploratory backtests, database replacement or edits can "
                        "contaminate evaluation windows. Confirmatory use requires a fresh, "
                        "independent dataset."
                    ),
                    "external_orders": False,
                }
            )
            return 0

        result = run_sma_backtest(
            dataset.bars,
            config=BacktestConfig(),
            risk_engine=RiskEngine(),
            provenance=provenance,
        )
        store = BacktestStore(config.database_path)
        store.initialize()
        run_id = store.save_backtest(result, dataset)
        _write_json(
            {
                "status": "completed",
                "mode": "SIMULATION",
                "run_id": run_id,
                "dataset": {
                    "dataset_id": dataset.dataset_id,
                    "source_type": dataset.source_type,
                    "source_name": dataset.source_name,
                    "sha256": dataset.sha256,
                    "observations": len(dataset.bars),
                    "usable": dataset.usable,
                },
                "result": {
                    "strategy": result.strategy,
                    "strategy_version": result.strategy_version,
                    "evaluation_classification": result.provenance.get(
                        "evaluation_classification"
                    ),
                    "instrument": result.instrument,
                    "start": result.start.isoformat() if result.start else None,
                    "end": result.end.isoformat() if result.end else None,
                    "initial_equity": result.initial_equity,
                    "final_equity": result.final_equity,
                    "trade_count": result.trade_count,
                    "gross_pnl": result.gross_pnl,
                    "total_costs": result.total_costs,
                    "net_pnl": result.net_pnl,
                    "max_drawdown_pct": result.max_drawdown_pct,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "risk_rejections": result.risk_rejections,
                    "assumptions": result.assumptions,
                },
                **(
                    {
                        "exploratory_warning": (
                            "This command scores the complete CSV history, including bars that "
                            "could later be assigned to a holdout. Do not use it to tune a sealed "
                            "protocol, claim out-of-sample performance, or promote a strategy."
                        )
                    }
                    if args.command == "backtest"
                    else {}
                ),
                "external_orders": False,
            }
        )
        return 0
    except PermissionDenied as exc:
        _write_json({"status": "denied", "reason": str(exc)}, sys.stderr)
        return 3
    except ModeUnavailableError as exc:
        _write_json({"status": "rejected", "reason": str(exc), "live_locked": True}, sys.stderr)
        return 2
    except (BrokerUnavailable, BrokerTimeout, BrokerRequestError) as exc:
        _write_json({"status": "connector_error", "reason": str(exc), "external_orders": False}, sys.stderr)
        return 4
    except ModuleNotFoundError as exc:
        _write_json({"status": "error", "reason": f"{exc}; install requirements/web.txt for serve and auth"}, sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        _write_json({"status": "error", "reason": str(exc)}, sys.stderr)
        return 1


def ctrader_check(operator: str, config: AppConfig, environ=None, transport=None) -> dict:
    """Read-only connection check against the cTrader DEMO host.

    Authenticates, reads account, assets, symbol and one quote, then
    disconnects. It never sends an order or position request.
    """

    import os

    from .brokers.ctrader import NEVER_SENT_BY_CHECK, CTraderAdapter, CTraderCredentials
    from .execution.mode import verify_mode
    from .governance import PermissionGuard, SecretVault
    from .models import RunMode

    values = os.environ if environ is None else environ
    store = ExecutionStore(config.database_path)
    store.initialize()
    vault = SecretVault(PermissionGuard(store), environ=values)
    actor = Actor(f"cli:{operator}", Role.HUMAN_ADMIN)
    secrets = {name: vault.get(actor, name) for name in ("CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCESS_TOKEN")}
    missing = [f"ASTRA_SECRET_{name}" for name, value in secrets.items() if value is None]
    account_text = values.get("ASTRA_CTRADER_ACCOUNT_ID", "").strip()
    if not account_text.isdigit():
        missing.append("ASTRA_CTRADER_ACCOUNT_ID")
    if missing:
        raise ValueError(f"missing environment variables: {', '.join(missing)}")
    adapter = CTraderAdapter(
        CTraderCredentials(
            secrets["CTRADER_CLIENT_ID"], secrets["CTRADER_CLIENT_SECRET"], secrets["CTRADER_ACCESS_TOKEN"], int(account_text)
        ),
        transport=transport,
    )
    result: dict = {"status": "failed", "host": "demo.ctraderapi.com", "orders_sent": 0}
    try:
        adapter.connect()
        import time as _time

        deadline = _time.monotonic() + 10
        quote = None
        while quote is None and _time.monotonic() < deadline:
            try:
                quote = adapter.quote("EURUSD")
            except BrokerUnavailable:
                # No complete spot yet; the adapter raises this until bid and ask arrive.
                _time.sleep(0.2)
        account = adapter.account_snapshot()
        capabilities = adapter.capabilities()
        verification = verify_mode(RunMode.DEMO, RunMode.DEMO, capabilities, account)
        spec = adapter.instrument_specs().get("EURUSD")
        result.update(
            {
                "status": "connected",
                "environment_reported": account.environment_reported.value,
                "environment_source": account.environment_source.value,
                "demo_mode_checks": {"verified": verification.verified, "reason_codes": list(verification.reason_codes)},
                "account_ref": account.account_ref_masked,
                "currency": account.currency,
                "balance": account.balance,
                "equity_derived": account.equity,
                "margin_used": account.margin_used,
                "open_positions": [[p.instrument, p.net_units] for p in adapter.open_positions()],
                "eurusd_spec": None if spec is None else {
                    "min_units": spec.min_units, "unit_step": spec.unit_step, "max_units": spec.max_units,
                    "pip_size": spec.pip_size, "margin_rate_derived": spec.margin_rate,
                },
                "eurusd_quote": None if quote is None else {"bid": quote.bid, "ask": quote.ask, "timestamp": quote.timestamp},
                "limitations": list(capabilities.limitations),
            }
        )
    finally:
        adapter.disconnect()
        sent_orders = [t for t in adapter.sent_payload_types if t in NEVER_SENT_BY_CHECK]
        result["orders_sent"] = len(sent_orders)
        store.audit(
            "connector.ctrader_check",
            {"operator": operator, "status": result["status"], "orders_sent": len(sent_orders),
             "environment": result.get("environment_reported")},
        )
    if sent_orders:
        raise RuntimeError("ctrader-check sent an order request; this must never happen")
    result["note"] = (
        "Read-only check. DEMO order execution remains disabled in this build until this check and a "
        "reviewed change enable it."
    )
    result["external_orders"] = False
    return result


def _ml_baseline(args: argparse.Namespace, config: AppConfig, dataset) -> dict:
    from .ml import PurgedWalkForwardConfig, run_purged_walk_forward

    registry = ModelRegistry(config.database_path)
    registry.initialize()
    author = Actor("cli:ml-baseline", Role.SYSTEM)
    walk_config = PurgedWalkForwardConfig(
        horizon=args.horizon,
        fold_count=args.folds,
        validation_rows=args.validation_rows,
        minimum_training_rows=args.minimum_training_rows,
        embargo_bars=args.embargo_bars,
    )
    identity = {"dataset_id": dataset.dataset_id, "sha256": dataset.sha256, "source_type": dataset.source_type}
    experiment_id = registry.open_experiment(
        author=author,
        hypothesis=(
            f"Scale-free causal features predict the sign of the {args.horizon}-bar forward return "
            "better than the training base rate (lower Brier score out of sample)."
        ),
        mechanism="Short-horizon persistence or reversal in returns, volatility and trend efficiency.",
        dataset_id=dataset.dataset_id,
        plan={"metric": "brier_edge_vs_base_rate", "walk_forward": asdict(walk_config)},
        variants_tested=1,
    )
    run = run_purged_walk_forward(dataset.bars, identity, config=walk_config)
    manifest = run.manifest.to_dict()
    edges = [fold["edge_vs_baseline"] for fold in manifest["fold_metrics"]]
    mean_edge = sum(edges) / len(edges)
    beats_everywhere = all(edge > 0 for edge in edges)
    decision = "NEEDS_MORE_RESEARCH" if beats_everywhere else "REJECTED"
    registry.close_experiment(
        experiment_id,
        result={"fold_edges": edges, "mean_edge": mean_edge, "folds": len(edges)},
        decision=decision,
    )
    version = f"{manifest['model_version']}+{run.manifest.code_sha256[:8]}-{experiment_id[:8]}"
    registry.register_candidate(
        actor=author,
        model_id=manifest["model_name"],
        version=version,
        manifest={**manifest, "dataset_identity": identity},
        criteria=PromotionCriteria(),
    )
    return {
        "status": "completed",
        "mode": "SIMULATION",
        "experiment_id": experiment_id,
        "decision": decision,
        "model_id": manifest["model_name"],
        "version": version,
        "registry_status": "CANDIDATE",
        "dataset": {**identity, "source_name": dataset.source_name, "observations": len(dataset.bars)},
        "promotion_blockers": list(registry.check_promotion(manifest["model_name"], version)),
        "fold_edge_vs_base_rate_brier": edges,
        "fold_metrics": manifest["fold_metrics"],
        "warning": (
            "Classification edge is not trading performance. The candidate stays inactive: promotion "
            "requires pre-registered trading evidence after costs, a holdout, and a human reviewer."
        ),
        "external_orders": False,
    }


STRATEGY_HYPOTHESIS = (
    "At least one pre-declared SMA, momentum or breakout configuration, selected on validation "
    "windows only, earns positive net P&L after modelled costs out of sample and beats both the "
    "flat and the SMA reference baselines, including under 1.5x and 2x cost stress."
)
STRATEGY_MECHANISM = (
    "Trend persistence (SMA crossover, time-series momentum) or range expansion (Donchian breakout) "
    "observable at bar close."
)


def _evaluate_strategies(args: argparse.Namespace, config: AppConfig, dataset) -> dict:
    from . import features
    from .strategy_validation import (
        default_candidates,
        evaluate_strategy_holdout_once,
        evaluate_strategy_walk_forward,
        holdout_evidence,
    )

    if args.register and not args.evaluate_holdout:
        raise ValueError("--register requires --evaluate-holdout (only holdout evidence is recorded)")
    if args.register and not (args.model_id and args.version):
        raise ValueError("--register requires --model-id and --version")
    protocol = WalkForwardProtocol(
        fold_count=args.folds,
        minimum_training_bars=args.minimum_training_bars,
        validation_bars=args.validation_bars,
        holdout_bars=args.holdout_bars,
        minimum_candidate_trades=args.minimum_candidate_trades,
    )
    candidates = default_candidates()
    registry = ModelRegistry(config.database_path)
    registry.initialize()
    store = BacktestStore(config.database_path)
    store.initialize()
    author = Actor("cli:evaluate-strategies", Role.SYSTEM)
    identity = {"dataset_id": dataset.dataset_id, "sha256": dataset.sha256, "source_type": dataset.source_type}
    experiment_id = registry.open_experiment(
        author=author,
        hypothesis=STRATEGY_HYPOTHESIS,
        mechanism=STRATEGY_MECHANISM,
        dataset_id=dataset.dataset_id,
        plan={
            "protocol": asdict(protocol),
            "candidates": [candidate.descriptor() for candidate in candidates],
            "selection": "validation windows only",
            "evaluate_holdout": bool(args.evaluate_holdout),
        },
        variants_tested=len(candidates),
    )
    try:
        report = evaluate_strategy_walk_forward(
            dataset.bars,
            candidates,
            hypothesis=STRATEGY_HYPOTHESIS,
            protocol=protocol,
            dataset_sha256=dataset.sha256,
            store=store,
            experiment_id=experiment_id,
            source_type=dataset.source_type,  # sealed in the plan; promotion reads it from there
        )
        selected = next((item for item in report.final_candidate_selection if item.selected), None)
        registered = None
        if args.register:
            if selected is None:
                raise ValueError("no eligible candidate was selected on validation windows; nothing to register")
            prior = store.holdout_for_protocol(report.plan_sha256)
            if prior is not None:
                # Criteria must be frozen before the holdout is seen; a retry cannot register new versions.
                raise ValueError(
                    f"the holdout of this plan is already {prior['status']}; a version can only be registered "
                    "before its holdout is scored"
                )
            registry.register_candidate(
                actor=author,
                model_id=args.model_id,
                version=args.version,
                manifest={
                    "dataset_identity": {**identity, "canonical_sha256": report.dataset_identity_sha256},
                    "feature_set_version": features.FEATURE_SET_VERSION,
                    "code_sha256": report.implementation_sha256,
                    "params": selected.config,
                    "candidate_id": selected.candidate_id,
                    "plan_sha256": report.plan_sha256,
                    "strategy_validation_version": report.strategy_validation_version,
                    "experiment_id": experiment_id,
                },
                criteria=PromotionCriteria(),
            )
            registered = {"model_id": args.model_id, "version": args.version}
        holdout_payload = None
        evidence = None
        if args.evaluate_holdout:
            holdout = evaluate_strategy_holdout_once(
                dataset.bars,
                candidates,
                report=report,
                protocol=protocol,
                store=store,
                dataset_sha256=dataset.sha256,
                experiment_id=experiment_id,
                model_id=registered["model_id"] if registered else None,
                model_version=registered["version"] if registered else None,
            )
            holdout_payload = holdout if isinstance(holdout, dict) else asdict(holdout)
            evidence = holdout_evidence(holdout)
            if registered is not None:
                attempt = store.holdout_for_protocol(report.plan_sha256)
                registry.record_evaluation(
                    actor=author,
                    model_id=args.model_id,
                    version=args.version,
                    kind="holdout",
                    evidence=evidence,
                    holdout_evaluation_id=attempt["evaluation_id"] if attempt else None,
                )
    except Exception as exc:
        # Failed runs are kept as rejected experiments, never deleted.
        registry.close_experiment(
            experiment_id,
            result={"status": "error", "error_type": type(exc).__name__, "reason": str(exc)},
            decision="REJECTED",
        )
        raise

    if selected is None:
        decision = "REJECTED"
    elif evidence is not None:
        stress = evidence["cost_stress_net_pnl"]
        positive = all(
            isinstance(value, (int, float)) and value > 0
            for value in (evidence["net_pnl"], evidence["edge_vs_baseline"])
        )
        decision = "NEEDS_MORE_RESEARCH" if positive and stress is not None and stress >= 0 else "REJECTED"
    else:
        selected_net = report.selected_candidate_test_aggregate.net_pnl
        baseline_net = max(0.0, report.sma_reference_test_aggregate.net_pnl)
        decision = "NEEDS_MORE_RESEARCH" if selected_net > baseline_net else "REJECTED"
    registry.close_experiment(
        experiment_id,
        result={
            "selected_candidate_id": report.selected_candidate_id,
            "selected_family": selected.family if selected else None,
            "variants_tested": report.variants_tested,
            "overfitting_status": report.overfitting.status,
            "holdout_evidence": evidence,
        },
        decision=decision,
    )
    blockers = list(registry.check_promotion(registered["model_id"], registered["version"])) if registered else None
    warnings = [
        "Strategy walk-forward results are simulation evidence, not a promotion decision or a trading "
        "authorization. This command never promotes; a human reviewer must pass every pre-registered gate.",
        "This local store freezes one plan per canonical dataset, but direct API calls, full-history "
        "backtests, database replacement or edits can contaminate evaluation windows.",
    ]
    if dataset.source_type == "SYNTHETIC":
        warnings.append("SYNTHETIC data: results are exploratory only and say nothing about real markets.")
    return {
        "status": "completed",
        "mode": "SIMULATION",
        "experiment_id": experiment_id,
        "decision": decision,
        "dataset": {**identity, "source_name": dataset.source_name, "observations": len(dataset.bars)},
        "protocol_id": report.plan_sha256,
        "report": asdict(report),
        "holdout_persistence": "SQLITE_ONCE_PER_CANONICAL_MARKET_BAR_IDENTITY",
        "holdout_evaluation": holdout_payload,
        "evidence": evidence,
        "registered": registered,
        "registry_status": "CANDIDATE" if registered else None,
        "promotion_blockers": blockers,
        "exploratory_warning": " ".join(warnings),
        "external_orders": False,
    }


def _attempts_command(args: argparse.Namespace, config: AppConfig) -> int:
    """Read-only views of the attempt registry; the database is opened read-only."""

    import sqlite3

    log = AttemptLog(config.database_path)
    try:
        if args.attempts_action == "list":
            items = log.attempts(protocol_id=args.protocol, instrument=args.instrument, status=args.status,
                                 limit=max(1, min(int(args.limit), 1000)))
            _write_json({"attempts": items, "count": len(items), "chain_head": log.chain_head()})
            return 0
        if args.attempts_action == "count":
            if args.protocol:
                stats = log.trial_statistics_for_protocol(args.protocol)
            else:
                stats = log.trial_statistics(
                    TrialScope(instrument=args.instrument, period_start=args.start, period_end=args.end)
                )
            _write_json({"trials": asdict(stats), "chain_head": log.chain_head()})
            return 0
        verification = log.verify_chain(args.expected_head)
        _write_json(
            {
                "verification": asdict(verification),
                "chain_head": log.chain_head(),
                "limit": (
                    "Tamper-evidence only: whoever can write the SQLite file can rewrite the whole chain. "
                    "Keep the printed chain head outside the database and pass it as --expected-head."
                ),
            }
        )
        return 0 if verification.ok else 1
    except (sqlite3.Error, KeyError) as exc:
        raise ValueError(f"attempt registry unavailable: {exc}") from exc


def _service_command(args: argparse.Namespace, config: AppConfig) -> int:
    import signal
    from dataclasses import asdict as _asdict

    from .brokers.simulated import SimulatedBroker
    from .service import SyntheticQuoteFeed, TradingService, service_status

    store = ExecutionStore(config.database_path)
    store.initialize()
    if args.service_action == "status":
        _write_json({"services": service_status(store)})
        return 0
    # This build runs the service only against the in-process simulator with
    # a SYNTHETIC feed; no external connector is wired into the service yet.
    account_ref = "SIM-SERVICE-CLI"
    holder: dict = {}
    broker = SimulatedBroker(account_ref=account_ref, clock=lambda: holder["feed"].clock())
    feed = SyntheticQuoteFeed(broker, step_seconds=args.tick_seconds, seed=args.seed)
    holder["feed"] = feed
    service = TradingService(
        store=store,
        broker=broker,
        decide=strategy_decisions(args.strategy),
        account_ref=account_ref,
        timeframe_seconds=args.timeframe,
        clock=feed.clock,
        reconnect=broker.reconnect,
    )
    previous_handlers = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            number = getattr(signal, name)
            previous_handlers[number] = signal.signal(number, lambda *_: service.stop())
    try:
        stats = service.run(
            advance=feed.advance,
            max_ticks=args.max_ticks,
            tick_interval_seconds=args.tick_seconds if args.realtime else 0.0,
        )
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)
    _write_json(
        {
            "mode": "SIMULATION",
            "broker": "astra-simulated-broker",
            "feed": feed.label,
            "stats": _asdict(stats),
            "positions": [[p.instrument, p.net_units] for p in broker.open_positions()],
            "note": "Simulated broker and synthetic quotes only. The in-memory broker does not survive a restart.",
            "external_orders": False,
        }
    )
    return 0


def _operations_command(args: argparse.Namespace, config: AppConfig) -> int:
    execution_store = ExecutionStore(config.database_path)
    execution_store.initialize()
    if args.command == "orders" and args.resolve:
        from .execution.resolution import resolve_unknown_order
        from .governance import PermissionGuard

        if not (args.outcome and args.evidence and args.operator):
            raise ValueError("--resolve requires --outcome, --evidence and --operator")
        # No adapter is attached to the CLI: the operator's evidence is
        # recorded, and freezes stay until a connected reconciliation is consistent.
        record = resolve_unknown_order(
            store=execution_store,
            guard=PermissionGuard(execution_store),
            actor=Actor(f"cli:{args.operator}", Role.HUMAN_OPERATOR),
            intent_id=args.resolve,
            outcome=args.outcome,
            evidence=args.evidence,
            filled_units=args.filled_units,
            average_fill_price=args.price,
            broker_lookup="not_performed_cli_has_no_adapter",
        )
        _write_json(
            {
                "resolved": record.to_dict(),
                "note": "Entry freezes remain until a reconciliation against the broker is consistent.",
            }
        )
        return 0
    if args.command == "orders":
        _write_json({"mode": "SIMULATION", "orders": [o.to_dict() for o in execution_store.list_orders(limit=100)]})
        return 0
    if args.command == "models":
        registry = ModelRegistry(config.database_path)
        registry.initialize()
        _write_json({"models": registry.list_versions(), "experiments": registry.experiments()})
        return 0
    controls = TradingControls(execution_store)
    if args.control_action == "kill":
        controls.activate_kill_switch(f"cli:{args.operator}", args.reason)
    elif args.control_action == "release":
        # The CLI runs locally under the operator's own account; the role is
        # asserted, not authenticated. Remote use requires real authentication.
        controls.release_kill_switch(Actor(f"cli:{args.operator}", Role.HUMAN_OPERATOR), args.reason)
    elif args.control_action == "clear-freeze":
        # Reconciliation-owned freezes (connection_lost, ...) are refused here:
        # only a consistent reconciliation may lift them.
        controls = TradingControls(execution_store, scope=args.account)
        controls.clear_freeze(args.freeze, Actor(f"cli:{args.operator}", Role.HUMAN_OPERATOR))
    _write_json(
        {
            "kill_switch_active": controls.kill_switch_active(),
            "entry_freezes": list(controls.freezes()),
            "entry_freezes_by_account": controls.all_freezes(),
            "reconciliations": execution_store.reconciliations(limit=5),
        }
    )
    return 0


def _write_json(payload: dict, stream=None) -> None:
    target = sys.stdout if stream is None else stream
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), file=target)


def _json_default(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _parse_candidate(value: str) -> tuple[int, int]:
    try:
        fast_text, slow_text = value.split(":", maxsplit=1)
        fast, slow = int(fast_text), int(slow_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"candidat invalide {value!r}; format attendu FAST:SLOW") from exc
    if fast < 1 or slow < 2 or fast >= slow:
        raise ValueError(f"candidat invalide {value!r}; FAST doit être inférieur à SLOW")
    return fast, slow


if __name__ == "__main__":
    raise SystemExit(main())
