"""Per-market data models, kept separate so crypto and options are not forced into Forex rules.

Only ``forex_cfd`` wraps a spec the order path already uses (``InstrumentSpec``).
``crypto_spot`` is a data model with validation helpers; ``crypto_futures`` and
``fixed_expiry_options`` are disabled (``ENABLED = False``). No module here
sends orders or feeds the RiskEngine.
"""

from .crypto_futures import ContractType, CryptoFuturesSpec, FuturesMarkState
from .crypto_spot import CryptoSpotSpec, floor_to_step
from .fixed_expiry_options import FixedExpiryOptionSpec, OptionOutcome, TieRule
from .forex_cfd import ForexCfdSpec

MarketSpec = ForexCfdSpec | CryptoSpotSpec | CryptoFuturesSpec | FixedExpiryOptionSpec

__all__ = [
    "ContractType",
    "CryptoFuturesSpec",
    "CryptoSpotSpec",
    "FixedExpiryOptionSpec",
    "ForexCfdSpec",
    "FuturesMarkState",
    "MarketSpec",
    "OptionOutcome",
    "TieRule",
    "floor_to_step",
]
