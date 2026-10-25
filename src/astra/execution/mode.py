"""Cross-checks of the requested mode against what the connector reports.

A configuration flag alone never proves the account mode. Configuration, the
adapter's declared environment, the account's reported environment and the
source of that report must all agree; anything else blocks new orders.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..brokers.base import AccountSnapshot, BrokerEnvironment, Capabilities, EnvironmentSource
from ..models import RunMode


# DEMO is defined by the contract but no verified demo connector exists in
# this build, so it is not yet an executable mode. LIVE is always locked here.
EXECUTABLE_MODES = frozenset({RunMode.SIMULATION})


@dataclass(frozen=True, slots=True)
class ModeVerification:
    verified: bool
    mode: RunMode
    reason_codes: tuple[str, ...]


def verify_mode(
    configured_mode: RunMode,
    command_mode: RunMode,
    capabilities: Capabilities,
    account: AccountSnapshot,
) -> ModeVerification:
    reasons: list[str] = []
    if not isinstance(configured_mode, RunMode) or not isinstance(command_mode, RunMode):
        return ModeVerification(False, RunMode.SIMULATION, ("mode_invalid",))
    if command_mode is not configured_mode:
        reasons.append("command_mode_differs_from_configuration")
    if RunMode.LIVE in (configured_mode, command_mode) or BrokerEnvironment.LIVE in (
        capabilities.declared_environment,
        account.environment_reported,
    ):
        reasons.append("live_locked_requires_human_authorization")
    if configured_mode not in EXECUTABLE_MODES:
        reasons.append("mode_not_executable_in_this_build")

    expected = BrokerEnvironment(configured_mode.value)
    if capabilities.declared_environment is not expected:
        reasons.append("adapter_environment_mismatch")
    if account.environment_reported is not expected:
        reasons.append("account_environment_mismatch")
    if account.environment_reported is BrokerEnvironment.UNKNOWN:
        reasons.append("account_environment_unknown")

    trusted_sources = {
        RunMode.SIMULATION: {EnvironmentSource.SIMULATOR},
        RunMode.DEMO: {EnvironmentSource.PROVIDER_API},
        RunMode.LIVE: {EnvironmentSource.PROVIDER_API},
    }[configured_mode]
    if account.environment_source not in trusted_sources:
        reasons.append("account_environment_not_provider_verified")
    if capabilities.environment_source not in trusted_sources:
        reasons.append("adapter_environment_not_verifiable")

    unique = tuple(dict.fromkeys(reasons))
    return ModeVerification(not unique, configured_mode, unique)
