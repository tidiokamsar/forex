"""Independent risk gateway for MT5 DEMO entries.

The strategy never authorises its own execution: before every opening order the bridge builds an
``EntryContext`` from what the terminal and the server really report and asks ``evaluate_entry``. Every check
runs (no early exit) so the audit shows the complete picture. A missing or unreadable figure fails its check,
and an invalid limits file refuses every entry: the default answer is "no".

Exits (closing a position) never go through this gateway: reducing risk must always be possible.
Pure functions and dataclasses: this module imports no broker, no execution code and no network.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .marketdata import PAIRS, pip_size

LIMITS_VERSION = "1"


class RiskConfigError(ValueError):
    """The limits profile is invalid: every entry is refused until it is fixed."""


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Conservative DEMO profile, isolated from any LIVE configuration (LIVE does not exist in ASTRA)."""

    risk_per_trade_pct: float = 0.5        # loss at the stop, % of equity
    risk_tolerance: float = 1.05           # volume rounding may exceed the target by this factor
    max_lots_per_position: float = 2.0
    max_positions: int = 7
    max_positions_per_symbol: int = 1
    max_total_lots: float = 8.0
    max_spread_pips: float = 3.0
    max_quote_age_seconds: float = 30.0
    daily_loss_limit_pct: float = 3.0
    max_drawdown_pct: float = 10.0
    max_margin_used_pct: float = 30.0      # of equity, including the new position
    allowed_symbols: tuple[str, ...] = field(default_factory=lambda: tuple(PAIRS))

    def validate(self) -> None:
        checks = (
            (0 < self.risk_per_trade_pct <= 2.0, "risk_per_trade_pct entre 0 et 2"),
            (1.0 <= self.risk_tolerance <= 1.5, "risk_tolerance entre 1 et 1,5"),
            (0 < self.max_lots_per_position <= 10, "max_lots_per_position entre 0 et 10"),
            (1 <= self.max_positions <= 50, "max_positions entre 1 et 50"),
            (1 <= self.max_positions_per_symbol <= 5, "max_positions_per_symbol entre 1 et 5"),
            (0 < self.max_total_lots <= 50, "max_total_lots entre 0 et 50"),
            (0 < self.max_spread_pips <= 20, "max_spread_pips entre 0 et 20"),
            (1 <= self.max_quote_age_seconds <= 600, "max_quote_age_seconds entre 1 et 600"),
            (0 < self.daily_loss_limit_pct <= 10, "daily_loss_limit_pct entre 0 et 10"),
            (0 < self.max_drawdown_pct <= 30, "max_drawdown_pct entre 0 et 30"),
            (0 < self.max_margin_used_pct <= 60, "max_margin_used_pct entre 0 et 60"),
            (bool(self.allowed_symbols) and all(s in PAIRS for s in self.allowed_symbols), "allowed_symbols inconnus"),
        )
        for ok, message in checks:
            if not ok:
                raise RiskConfigError(message)

    def public(self) -> dict[str, Any]:
        return {**asdict(self), "allowed_symbols": list(self.allowed_symbols), "version": LIMITS_VERSION}


def load_limits(path: str | Path | None) -> RiskLimits:
    """Defaults, or the profile in a JSON file (only known keys). Raises RiskConfigError when it is invalid."""

    values: dict[str, Any] = {}
    if path:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RiskConfigError(f"profil de risque illisible : {exc}") from exc
        if not isinstance(raw, dict):
            raise RiskConfigError("profil de risque : objet JSON attendu")
        known = {f.name for f in fields(RiskLimits)}
        unknown = set(raw) - known
        if unknown:
            raise RiskConfigError(f"paramètres inconnus : {', '.join(sorted(unknown))}")
        values = dict(raw)
        if "allowed_symbols" in values:
            values["allowed_symbols"] = tuple(values["allowed_symbols"])
    try:
        limits = RiskLimits(**values)
    except TypeError as exc:
        raise RiskConfigError(str(exc)) from exc
    for f in fields(limits):
        value = getattr(limits, f.name)
        if isinstance(value, float) and not math.isfinite(value):
            raise RiskConfigError(f"{f.name} doit être fini")
    limits.validate()
    return limits


@dataclass(frozen=True, slots=True)
class AccountState:
    """What the terminal reports about the account and the connector, with the history the bridge keeps."""

    environment: str | None = None
    login: int | None = None
    expected_login: int | None = None
    equity: float | None = None
    margin: float | None = None
    day_start_equity: float | None = None
    peak_equity: float | None = None
    positions: tuple[dict[str, Any], ...] = ()          # ASTRA's positions: symbol, side, volume
    connector_ok: bool = False
    algo_trading_enabled: bool | None = None
    entries_allowed_by_server: bool | None = None        # /api/controls: kill switch and entry freezes
    kill_switch_active: bool | None = None


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    side: str
    lots: float
    loss_at_stop_usd: float | None
    required_margin: float | None
    bid: float | None
    ask: float | None
    quote_age_seconds: float | None
    volume_min: float | None
    volume_max: float | None
    volume_step: float | None
    has_stop: bool = True


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    checks: tuple[Check, ...]

    @property
    def reasons(self) -> list[str]:
        return [f"{c.name} : {c.detail}" for c in self.checks if not c.passed]

    def public(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "checks": [c.public() for c in self.checks]}


def _ok(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def account_checks(account: AccountState, limits: RiskLimits | RiskConfigError) -> list[Check]:
    """Checks that do not depend on a particular order: they say whether new entries are allowed at all."""

    if isinstance(limits, RiskConfigError):
        return [Check("profil_de_risque", False, f"profil invalide, entrées refusées : {limits}")]
    out = [Check("profil_de_risque", True, f"profil v{LIMITS_VERSION} valide")]
    out.append(Check("environnement_demo", account.environment == "DEMO",
                     "compte DEMO confirmé" if account.environment == "DEMO" else f"environnement {account.environment!r}"))
    pinned = account.expected_login is not None and account.login == account.expected_login
    out.append(Check("compte_autorise", pinned, "compte attendu" if pinned else f"compte {account.login} non autorisé"))
    out.append(Check("connecteur", account.connector_ok and account.algo_trading_enabled is True,
                     "terminal connecté, Trading Algo actif" if account.connector_ok and account.algo_trading_enabled
                     else "terminal indisponible ou Trading Algo désactivé"))
    if account.entries_allowed_by_server is True and account.kill_switch_active is False:
        out.append(Check("arret_d_urgence", True, "aucun arrêt d'urgence ni gel d'entrée"))
    else:
        out.append(Check("arret_d_urgence", False, "arrêt d'urgence ou gel actif, ou état illisible"))
    if _ok(account.equity) and _ok(account.day_start_equity) and account.day_start_equity > 0:
        loss = (account.day_start_equity - account.equity) / account.day_start_equity * 100
        out.append(Check("perte_journaliere", loss < limits.daily_loss_limit_pct,
                         f"perte du jour {max(loss, 0):.2f} % (limite {limits.daily_loss_limit_pct} %)"))
    else:
        out.append(Check("perte_journaliere", False, "capital de départ du jour inconnu"))
    if _ok(account.equity) and _ok(account.peak_equity) and account.peak_equity > 0:
        dd = (account.peak_equity - account.equity) / account.peak_equity * 100
        out.append(Check("drawdown", dd < limits.max_drawdown_pct,
                         f"drawdown {max(dd, 0):.2f} % (limite {limits.max_drawdown_pct} %)"))
    else:
        out.append(Check("drawdown", False, "sommet des fonds propres inconnu"))
    count = len(account.positions)
    out.append(Check("nombre_de_positions", count < limits.max_positions,
                     f"{count} positions ouvertes (maximum {limits.max_positions})"))
    return out


def order_checks(account: AccountState, order: OrderRequest, limits: RiskLimits) -> list[Check]:
    out = [Check("instrument", order.symbol in limits.allowed_symbols and order.side in ("LONG", "SHORT"),
                 f"{order.symbol} {order.side}")]
    steps_ok = (_ok(order.volume_min) and _ok(order.volume_max) and _ok(order.volume_step) and order.volume_step > 0
                and order.volume_min <= order.lots <= order.volume_max
                and abs(order.lots / order.volume_step - round(order.lots / order.volume_step)) < 1e-6)
    out.append(Check("volume_conforme", bool(steps_ok and 0 < order.lots <= limits.max_lots_per_position),
                     f"{order.lots} lot (spécifications du courtier et maximum {limits.max_lots_per_position})"))
    out.append(Check("stop_present", order.has_stop, "stop défini" if order.has_stop else "pas de stop"))
    if _ok(order.loss_at_stop_usd) and _ok(account.equity) and account.equity > 0:
        allowed = account.equity * limits.risk_per_trade_pct / 100 * limits.risk_tolerance
        out.append(Check("taille_de_position", order.loss_at_stop_usd <= allowed,
                         f"perte au stop {order.loss_at_stop_usd:.2f} USD (autorisé {allowed:.2f})"))
    else:
        out.append(Check("taille_de_position", False, "perte au stop incalculable"))
    if _ok(order.bid) and _ok(order.ask) and 0 < order.bid <= order.ask and order.symbol in PAIRS:
        spread = (order.ask - order.bid) / pip_size(order.symbol)
        out.append(Check("spread", spread <= limits.max_spread_pips,
                         f"spread {spread:.1f} pips (maximum {limits.max_spread_pips})"))
    else:
        out.append(Check("spread", False, "cotation absente ou incohérente"))
    if _ok(order.quote_age_seconds):
        out.append(Check("fraicheur_des_donnees", 0 <= order.quote_age_seconds <= limits.max_quote_age_seconds,
                         f"cotation vieille de {order.quote_age_seconds:.0f} s (maximum {limits.max_quote_age_seconds:.0f})"))
    else:
        out.append(Check("fraicheur_des_donnees", False, "heure de cotation inconnue"))
    if _ok(order.required_margin) and _ok(account.equity) and _ok(account.margin) and account.equity > 0:
        used = (account.margin + order.required_margin) / account.equity * 100
        out.append(Check("marge", used <= limits.max_margin_used_pct,
                         f"marge utilisée après l'ordre {used:.1f} % des fonds propres (maximum {limits.max_margin_used_pct} %)"))
    else:
        out.append(Check("marge", False, "marge requise inconnue"))
    same = sum(1 for p in account.positions if p.get("symbol") == order.symbol)
    out.append(Check("exposition_par_instrument", same < limits.max_positions_per_symbol,
                     f"{same} position(s) déjà ouverte(s) sur {order.symbol} (maximum {limits.max_positions_per_symbol})"))
    total = sum(float(p.get("volume", 0) or 0) for p in account.positions) + order.lots
    out.append(Check("exposition_totale", total <= limits.max_total_lots,
                     f"{total:.2f} lots au total (maximum {limits.max_total_lots})"))
    return out


def evaluate_entry(account: AccountState, order: OrderRequest, limits: RiskLimits | RiskConfigError) -> RiskDecision:
    """The decision for one opening order: allowed only if every check passes."""

    checks = account_checks(account, limits)
    if isinstance(limits, RiskConfigError):
        return RiskDecision(False, tuple(checks))
    checks += order_checks(account, order, limits)
    return RiskDecision(all(c.passed for c in checks), tuple(checks))


def entries_state(account: AccountState, limits: RiskLimits | RiskConfigError) -> RiskDecision:
    """Whether new entries are allowed right now, independent of any order (for the dashboard)."""

    checks = account_checks(account, limits)
    return RiskDecision(all(c.passed for c in checks), tuple(checks))


def summarize(refusals: Iterable[str]) -> str:
    return "; ".join(sorted(set(refusals)))[:400]
