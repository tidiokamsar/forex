# Broker integrations

## Current status

**Implemented:** the broker contract `astra.brokers.base.BrokerAdapter` (interface version 1), `SimulatedBroker` (in-process test double), and a cTrader Open API demo adapter tested offline only (below). No account, region eligibility, demo login, price feed or execution capability has been tested with any real provider.

The contract covers capabilities, connection state, account snapshot, quotes, market order submission, lookup by client order id, open positions, cancellation and event polling. It deliberately has no withdrawal, transfer, deposit or beneficiary operation (a test checks this). Each adapter publishes a `Capabilities` record; an unsupported operation raises `CapabilityNotSupported` and is never emulated.

Strategies, models and LLM outputs cannot reach an adapter. Only `OrderGateway` submits orders to it, with an execution permit issued after permission, schema, idempotency, emergency-control, mode, quote, RiskEngine and contract checks (see [ARCHITECTURE.md](ARCHITECTURE.md)). The read-only `ctrader-check` command calls the adapter's read methods directly and never submits.

## cTrader Open API adapter (selected 2026-10-25)

`astra.brokers.ctrader.CTraderAdapter` speaks the documented JSON protocol over WebSocket to `wss://demo.ctraderapi.com:5036`, using a standard-library client (`brokers/websocket.py`). Status: **implemented and tested offline only.** It has not yet connected to the real service.

What it does, per https://help.ctrader.com/open-api/ (read 2026-10-25):
- Auth sequence `ProtoOAApplicationAuthReq` (2100), `ProtoOAGetAccountListByAccessTokenReq` (2149), `ProtoOAAccountAuthReq` (2102); heartbeat (51) every 10 s.
- Environment comes only from `ProtoOACtidTraderAccount.isLive`. `true` is refused before account auth (`LiveAccountRefused`); a missing flag means `UNKNOWN`, which blocks orders. The module never builds a live-host transport.
- Account: `ProtoOATrader.balance / 10^moneyDigits`, currency from `ProtoOAAssetListReq` (2112), unrealized P&L from `ProtoOAGetPositionUnrealizedPnLReq` (2187), used margin from `ProtoOAReconcileRes` positions.
- Specs from `ProtoOASymbol` (volumes in 0.01 unit, `pipPosition`); prices from `ProtoOASpotEvent` (1/100000).
- Market orders via `ProtoOANewOrderReq` (2106) with `clientOrderId`, `label` and `relativeStopLoss`; exits via `ProtoOAClosePositionReq` (2111) on the single open position. Execution events (2126) are mapped to order states; activity not created by ASTRA is reported as `FOREIGN-<orderId>` and freezes entries through the gateway.

Assumptions to confirm on the first supervised connection (also listed in the adapter's `Capabilities.limitations`):
1. JSON travels in WebSocket text frames (docs silent; receive side accepts both).
2. Enums are sent as numbers and int64 values as JSON numbers.
3. Equity = balance + net unrealized P&L, free margin = equity - used margin (no documented formula).
4. Margin rate = 100 / `leverageInCents` (dynamic leverage not modelled).

Deliberate limits: there is no documented lookup by `clientOrderId`, so `supports_order_lookup_by_client_id` is `False` and an order left `UNKNOWN` after a timeout stays frozen instead of being assumed absent, until a human resolves it with evidence (`python -m astra orders --resolve ...`, see [OPERATIONS.md](OPERATIONS.md)) and a reconciliation is consistent. Cancel/modify and hedged accounts with several positions per symbol are not supported.

### Resilience (F-03, offline fixture only)

- `CTraderAdapter.reconnect(max_attempts=5, base_delay=1.0, max_delay=30.0)`: each attempt stops the old session and opens a new transport; the delay doubles per failed attempt, capped at `max_delay`. It returns the number of attempts or raises `BrokerUnavailable` after the last one.
- Permanent failures are raised at once, without retry: `LiveAccountRefused`, `CTraderConfigurationError` (account not granted to the token, symbol not offered, missing specification) and `CTraderConnectionError` with `OA_AUTH_TOKEN_EXPIRED`, `CH_ACCESS_TOKEN_INVALID`, `CH_CLIENT_AUTH_FAILURE`, `ACCOUNT_NOT_AUTHORIZED` or `RET_ACCOUNT_DISABLED`. These exceptions carry `permanent = True`; the continuous service then degrades immediately and stops reconnecting. Other connection errors, `BrokerUnavailable`, `BrokerTimeout` and `OSError` are transient and retried. A rate limit (`BLOCKED_PAYLOAD_TYPE`, `REQUEST_FREQUENCY_EXCEEDED`) during authorization is also transient; the next attempt waits at least `retryAfter`.
- Reconnecting lifts no gateway freeze; a consistent reconciliation is still required.
- Client rate limiter: token bucket of 40 requests/s (burst 40) per connection, below the documented 50 requests/s.
- On `BLOCKED_PAYLOAD_TYPE` or `REQUEST_FREQUENCY_EXCEEDED` with a `retryAfter` of at most 10 s, a read-only request (trader, assets, symbols, symbol by id, reconcile, unrealized P&L, account list) is retried once after the wait. Order and close requests are never retried automatically.
- Token expiry: `CTraderCredentials.access_token_expires_at` is optional. When set, `connect` refuses an expired token before any message is sent and logs `ctrader.token_expiring` under 3 days left; `token_status()` reports it. The CLI does not set it yet, so `ctrader-check` runs with an unknown expiry.
- `refresh_access_token(token_store)` sends `ProtoOARefreshTokenReq` (2172, not yet exercised against the provider) and saves the new pair to the store before using it. It refuses without a `TokenStore`, and **none is bundled**: choosing secure durable storage for rotated tokens is a human decision.

All of the above is tested against the scripted fake server only (`tests/test_ctrader_resilience.py`).

**DEMO execution remains disabled.** `execution.mode.EXECUTABLE_MODES` and the RiskEngine accept SIMULATION only. Even with a valid gateway permit, the adapter refuses to send unless the provider reported `isLive: false`, DEMO is in `EXECUTABLE_MODES`, the adapter was built with `allow_demo_orders=True` and its connection is `CONNECTED`. Enabling DEMO orders is a separate reviewed change after the read-only check below passes on your account.

### Read-only connection check (run by you)

1. Register an Open API application at Spotware and obtain an access token for your **demo** cTID account (OAuth flow in the docs: https://help.ctrader.com/open-api/account-authentication/).
2. In your own PowerShell session (never in a file or chat):
   ```powershell
   $env:PYTHONPATH = "src"
   $env:ASTRA_CTRADER_ACCOUNT_ID = "<ctidTraderAccountId>"
   $env:ASTRA_SECRET_CTRADER_CLIENT_ID = "<client id>"
   $env:ASTRA_SECRET_CTRADER_CLIENT_SECRET = "<client secret>"
   $env:ASTRA_SECRET_CTRADER_ACCESS_TOKEN = "<access token>"
   python -m astra ctrader-check --operator <your name>
   ```
3. The command authenticates, reads account, currency, EURUSD specification and one quote, prints them with `orders_sent: 0`, and logs a `connector.ctrader_check` audit event. It sends no order or close request (enforced and tested). Share the printed JSON (it contains no secret) to continue.

## Evaluation matrix (documentation check only)

Checked on 2026-10-25 by web search of official documentation. "Documented" means the provider's own pages describe it; nothing below was tested, and country/account eligibility for this user is **unknown**.

| Provider / API | Official source | Documented demo/test environment | How the environment could be verified | Open questions before an adapter |
|---|---|---|---|---|
| cTrader Open API (Spotware) | https://help.ctrader.com/open-api/ and https://help.ctrader.com/open-api/proxies-endpoints/ | Separate hosts `demo.ctraderapi.com` and `live.ctraderapi.com` (Protobuf port 5035, JSON 5036); docs state demo and live are separated | Endpoint host plus the account's live/demo attribute returned by the API | Which brokers offer cTrader in the user's country; OAuth app approval; rate limits; contract specs per broker |
| OANDA v20 REST | https://developer.oanda.com/rest-live-v20/development-guide and https://developer.oanda.com/rest-live-v20/introduction/ | Practice host `api-fxpractice.oanda.com` (streaming `stream-fxpractice.oanda.com`); token generated from a practice account | Practice endpoint plus account endpoint responses | Regional entity and eligibility; token scope; financing and cost data |
| Interactive Brokers TWS API | https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/limitations/paper-trading | Paper trading account, opened from Account Management after a funded live account is approved; docs warn paper execution is more simulated | Paper login on the local TWS/Gateway socket (commonly port 7497 per IBKR campus docs) plus account id | Requires a funded live account first; local TWS/Gateway process; FX contract details |
| MetaTrader 5 Python package (MetaQuotes) | https://www.mql5.com/en/book/advanced/python/python_funcs_overview and https://metaquotes.net/en/metatrader5/news/5208 | Depends on the broker's MT5 server; not confirmed from official pages in this check | Not confirmed: the search did not reach the official `ACCOUNT_TRADE_MODE` documentation | Windows terminal process required; demo detection field must be verified in MQL5 docs before relying on it |

## Required before enabling DEMO orders

The cTrader adapter exists (steps 3 and 4 are done offline for cTrader); the remaining steps apply to it and to any later provider.

1. Re-verify the official documentation, record URL and date, and confirm the user's country and account eligibility.
2. Confirm how the provider reports demo versus live, and map it to `EnvironmentSource.PROVIDER_API`. A configuration value alone is never accepted (`verify_mode` rejects `CONFIGURATION_ONLY`).
3. Map instrument contract specifications (minimum size, step, margin, pip value) from the provider, replacing the illustrative `instruments.DEFAULT_SPECS`.
4. Implement the adapter against the existing contract with offline tests using recorded or mocked responses, clearly labelled.
5. A human supplies credentials through `SecretVault` (environment variables prefixed `ASTRA_SECRET_`, read by `HUMAN_ADMIN` only). Never commit, log or paste them into prompts.
6. Enable DEMO in `execution.mode.EXECUTABLE_MODES` and `AppConfig` only through a reviewed change after the above evidence is recorded in `ACCEPTANCE_REPORT.md`.
7. Build the adapter that may send orders with `allow_demo_orders=True` (the read-only check builds it with `False`), and send only while it reports `CONNECTED`; the adapter's guard refuses otherwise.

LIVE stays locked in this build and is out of scope until the real-mode dossier in the specification (section 18) is complete and a human authorizes it.
