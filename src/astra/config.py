"""Fail-closed application configuration for the local simulation service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import RunMode

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class ModeUnavailableError(ValueError):
    """Raised when configuration requests a mode that this build cannot run."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    mode: RunMode = RunMode.SIMULATION
    data_path: Path = PROJECT_ROOT / "data"
    database_path: Path = PROJECT_ROOT / "var" / "astra.sqlite3"
    host: str = "127.0.0.1"
    port: int = 8765

    def __post_init__(self) -> None:
        if self.mode is not RunMode.SIMULATION:
            raise ModeUnavailableError("this build supports SIMULATION only")
        if not _is_loopback_host(self.host):
            raise ValueError("ASTRA_HOST must resolve to a loopback address")
        if not 1 <= self.port <= 65_535:
            raise ValueError("ASTRA_PORT must be an integer from 1 to 65535")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> AppConfig:
        values = os.environ if environ is None else environ
        requested = values.get("ASTRA_MODE", "SIMULATION").strip().upper()
        try:
            mode = RunMode(requested)
        except ValueError as exc:
            raise ModeUnavailableError("ASTRA_MODE must be SIMULATION") from exc
        if mode is not RunMode.SIMULATION:
            raise ModeUnavailableError(
                f"{mode.value} is unavailable in this build; execution is simulation-only"
            )
        if values.get("ASTRA_LIVE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise ModeUnavailableError("live execution is locked and cannot be enabled by configuration")

        host = values.get("ASTRA_HOST", "127.0.0.1").strip()
        if not _is_loopback_host(host):
            raise ValueError("ASTRA_HOST must resolve to a loopback address")
        try:
            port = int(values.get("ASTRA_PORT", "8765"))
        except ValueError as exc:
            raise ValueError("ASTRA_PORT must be an integer from 1 to 65535") from exc
        if not 1 <= port <= 65_535:
            raise ValueError("ASTRA_PORT must be an integer from 1 to 65535")

        return cls(
            mode=mode,
            data_path=Path(values.get("ASTRA_DATA_DIR", str(PROJECT_ROOT / "data"))),
            database_path=Path(
                values.get("ASTRA_DATABASE", str(PROJECT_ROOT / "var" / "astra.sqlite3"))
            ),
            host=host,
            port=port,
        )


def _is_loopback_host(host: str) -> bool:
    # Use URL parsing to reject host:port and bracketed IPv6 input; only a bare
    # localhost/loopback host is accepted by the service configuration.
    if host.lower() == "localhost":
        return True
    try:
        from ipaddress import ip_address

        return ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class WebConfig:
    """Settings of the authenticated dashboard (``astra.webapp``); the app still binds to loopback only.

    ``public_hosts`` lists the exact Host values the app answers to (for example the public
    name served by the reverse proxy); the loopback bind host and port are always accepted.
    ``trusted_proxy`` makes the app read X-Forwarded-For and X-Forwarded-Proto from exactly one
    proxy hop; leave it off unless the only client of the loopback port is the local proxy.
    ``https`` enables HSTS and makes https://<public host> the only accepted Origin for them.
    """

    auth_database_path: Path = PROJECT_ROOT / "var" / "auth.sqlite3"
    secret_key_path: Path = PROJECT_ROOT / "var" / "web_secret.key"
    public_hosts: tuple[str, ...] = ()
    trusted_proxy: bool = False
    https: bool = False
    # Public mode: a secret the reverse proxy adds to every request (header X-Astra-Proxy). Another local
    # process that connects to the loopback port directly cannot know it, so it cannot pass for the proxy.
    proxy_secret_path: Path | None = None

    def __post_init__(self) -> None:
        for host in self.public_hosts:
            if not _is_plain_host(host):
                raise ValueError(f"ASTRA_PUBLIC_HOSTS entry is not a plain host name or address: {host!r}")
        if self.public_hosts and not self.https:
            raise ValueError("a public host requires ASTRA_HTTPS=1: the dashboard is never served over plain HTTP remotely")
        if self.public_hosts and not self.trusted_proxy:
            # Behind the proxy every client would share 127.0.0.1: one client could throttle everyone.
            raise ValueError("a public host requires ASTRA_TRUSTED_PROXY=1 (the local TLS proxy is the only client)")
        if self.public_hosts and self.proxy_secret_path is None:
            raise ValueError("a public host requires ASTRA_PROXY_SECRET_FILE (the secret the proxy adds to each request)")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> WebConfig:
        values = os.environ if environ is None else environ
        hosts = tuple(h.strip().lower() for h in values.get("ASTRA_PUBLIC_HOSTS", "").split(",") if h.strip())
        return cls(
            auth_database_path=Path(values.get("ASTRA_AUTH_DATABASE", str(PROJECT_ROOT / "var" / "auth.sqlite3"))),
            secret_key_path=Path(values.get("ASTRA_WEB_SECRET_FILE", str(PROJECT_ROOT / "var" / "web_secret.key"))),
            public_hosts=hosts,
            trusted_proxy=_flag(values.get("ASTRA_TRUSTED_PROXY", "")),
            proxy_secret_path=Path(values["ASTRA_PROXY_SECRET_FILE"]) if values.get("ASTRA_PROXY_SECRET_FILE") else None,
            https=_flag(values.get("ASTRA_HTTPS", "")),
        )


def _flag(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"", "0", "false", "no", "off"}:
        return False
    if lowered in {"1", "true", "yes", "on"}:
        return True
    raise ValueError(f"invalid boolean setting: {value!r}")


def _is_plain_host(host: str) -> bool:
    from ipaddress import ip_address

    try:
        ip_address(host)
        return True
    except ValueError:
        pass
    labels = host.split(".")
    return bool(host) and len(host) <= 253 and all(
        label and len(label) <= 63 and label[0] != "-" and label[-1] != "-"
        and all(ch.isascii() and (ch.isalnum() or ch == "-") for ch in label)
        for label in labels
    )
