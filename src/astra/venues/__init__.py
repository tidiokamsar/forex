"""Multi-venue layer: read-oriented connector contract and the venue registry.

Nothing in this package sends orders. Order placement for any venue goes only
through ``astra.brokers.base.BrokerAdapter.submit_order`` and its permit.
"""
