# Broker integrations

## Current status

**Implemented:** the broker contract `astra.brokers.base.BrokerAdapter` (interface version 1), `SimulatedBroker` (in-process test double), and a cTrader Open API demo adapter tested offline only (below). No account, region eligibility, demo login, price feed or execution capability has been tested with any real provider.

The contract covers capabilities, connection state, account snapshot, quotes, market order submission, lookup by client order id, open positions, cancellation and event polling. It deliberately has no withdrawal, transfer, deposit or beneficiary operation (a test checks this). Each adapter publishes a `Capabilities` record; an unsupported operation raises `CapabilityNotSupported` and is never emulated.

Strategies, models and LLM outputs cannot reach an adapter. Only `OrderGateway` submits orders to it, with an execution permit issued after permission, schema, idempotency, emergency-control, mode, quote, RiskEngine and contract checks (see [ARCHITECTURE.md](ARCHITECTURE.md)). The read-only `ctrader-check` command calls the adapter's read methods directly and never submits.

### V5 Execution Gateway & Multi-Broker Layer (5.10): status

| Requirement | Status |
|---|---|
| Single execution boundary for every order | Implemented (`OrderGateway` plus single-use permits checked in `BrokerAdapter.submit_order`) |
| Environment verified from a provider source | Implemented in code (`EnvironmentSource.PROVIDER_API`, cTrader `isLive`); never exercised against the real service |
| Authorization, RiskEngine, freshness, spread, specs and margin checked just before sending | Implemented (gateway checks) |
| Idempotent ids, explicit `UNKNOWN`, no blind resend after timeout | Implemented |
| Reconciliation at start, after reconnect and periodically; foreign orders or positions block entries | Implemented (service and gateway, simulator and fake server only) |
| No deposit, withdrawal or fund movement | Implemented (absent from the contract, tested) |
| First connector validated by a real read-only check | **Not done**: `ctrader-check` has not yet been run by the user |
| DEMO order execution | **Disabled** |
| LIVE | **Locked** |
| Comparison of the in-house JSON/WebSocket client with the official SDKs (for example the Python Protobuf SDK) | Not done; ADR-0004 records the JSON choice and keeps Protobuf as the fallback |
| Second adapter, cross-adapter conformance tests | Not implemented |

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

## Multi-venue comparison (as of 2026-10-26, subject to human verification)

Sources: the research dossiers [docs/venues/research_forex.md](docs/venues/research_forex.md) and [docs/venues/research_crypto_other.md](docs/venues/research_crypto_other.md), public pages only, all accessed 2026-10-26, for a user resident in the Republic of Guinea (Conakry). Registry status per venue, with evidence URLs, is in [VENUE_REGISTRY.md](VENUE_REGISTRY.md) (registry 0.2.0). No account was opened and no live connection was made: **no venue below is integrated**, and the cTrader adapter is the only adapter in this repository (tested offline only). Every point below is subject to human verification.

"Not named" means the provider's restricted list is non-exhaustive and omits Guinea, which does not prove eligibility.

### Forex / CFD

| Venue | Contracting entity (for Guinea) | Regulator checked in register | Guinea eligibility | Official API | Demo / paper | Auth | Rate limits | Published costs | Registry status |
|---|---|---|---|---|---|---|---|---|---|
| cTrader Open API (Spotware) | Vendor; broker-dependent | n/a | Broker-dependent | Open API, Protobuf/TCP or JSON/WebSocket | Demo accounts | OAuth 2.0, scopes `accounts` / `trading` | 50 req/s, 5 req/s historical, per connection | API free | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |
| MetaTrader 5 Python package | MetaQuotes; broker-dependent | n/a | Broker-dependent | IPC to a local Windows MT5 terminal | Broker-dependent | Broker login and password | None documented | Package free | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |
| Interactive Brokers | Expected IB LLC (unconfirmed) | FINRA BrokerCheck CRD 36418: yes; NFA not read | **Listed** on the official country page | TWS API (socket), Web API (REST/WS) | Paper after a funded IBKR Pro account | Gateway login (individuals) | Web API 10 req/s + per endpoint | Spot FX 0.20 bp, min USD 2/order | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN (documents only) |
| IC Markets Global | Raw Trading Ltd (Seychelles) | Entity on FSA list; SD018 number not read | Not named | via cTrader / MT5 | Demo, free | via platform | via platform | cTrader USD 3 per 100k per side; MT Raw USD 3.50/lot/side | UNVERIFIED, ELIGIBILITY_UNKNOWN |
| Pepperstone | Unconfirmed (Kenya / Bahamas / Seychelles) | Seychelles entity on FSA list | Negative signal (Guinea excluded for affiliates) | via cTrader / MT5 | Demo | via platform | via platform | not read | UNVERIFIED, ELIGIBILITY_UNKNOWN |
| FxPro | Unconfirmed (SCB / Invemonde SC) | Invemonde on FSA list | Not named | via cTrader / MT5 | Demo | via platform | via platform | not read | UNVERIFIED, ELIGIBILITY_UNKNOWN |
| Deriv | Unconfirmed (BVI / SVG / V / FX / Mauritius) | No (licence PDF only) | No official list | Deriv API (options), cTrader, MT5 | Default demo | OAuth 2.0 or PAT | not found | not read | UNVERIFIED, ELIGIBILITY_UNKNOWN |
| Exness | Unconfirmed (SC / B.V. / VG / MU) | Exness (SC) on FSA list (address mismatch) | Not named | MT5 only | Demo | MT5 login | n/a | not read | UNVERIFIED, ELIGIBILITY_UNKNOWN |
| FXCM | Stratos Global LLC (SVG, unregulated) | Entity unregulated | Not excluded by name, no positive listing | ForexConnect / Java / FIX; REST deprecated | Trading Station demo | Username and password | not documented | FIX needs USD 5,000 balance | UNVERIFIED, ELIGIBILITY_UNKNOWN |
| OANDA | OANDA Global Markets (BVI), only candidate | No | Not listed by any division | v20 not offered to that entity | Practice (other divisions) | Bearer token | 120 req/s/IP | not read | **BLOCKED** |

### Crypto spot

| Venue | Contracting entity | Regulator checked in register | Guinea eligibility | Official API | Test environment | Permissions | Rate limits | Fees | Registry status |
|---|---|---|---|---|---|---|---|---|---|
| Binance Spot | ADGM entities (Nest Exchange / Clearing and Custody / Trading), unconfirmed for Guinea | No (FSRA register not checked) | Not on the prohibited list; absent from the iOS app-region page; no positive confirmation | REST, WebSocket API, streams, FIX; keyless `data-api.binance.vision` | Spot Testnet (GitHub login); Demo Mode (needs an account) | Read-only key, IP-restricted; withdrawals never (Binance recommends Ed25519; ASTRA's connector signs with HMAC-SHA256 only, stdlib constraint). The connector shares one 418/429/weight state for all production and public-data hosts (Binance bans per IP), so a data-api limit can also pause account reads | REQUEST_WEIGHT 6,000/min/IP; 429 then 418 ban | 0.100% / 0.100% regular | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |

Binance hosts timed out from the research machine on 2026-10-26; the cause is unknown and must be retested from the production network.

### Futures

| Venue | Guinea eligibility | Official API | Test environment | Fees | Registry status |
|---|---|---|---|---|---|
| Binance Futures USD-M / COIN-M | Unknown; no official country list | `fapi.binance.com`, `dapi.binance.com` | `demo-fapi.binance.com`; COIN-M testnet listed | USD-M 0.0200% / 0.0500% regular | **BLOCKED** pending derivatives risk review; ELIGIBILITY_UNKNOWN |

### Options and other

| Venue | Entity | Guinea eligibility | Official API | Registry status |
|---|---|---|---|---|
| IQ Option | SKY LADDER LLC (Antigua), no licence named | Not named; catch-all exclusion | **None** (only unofficial APIs, forbidden) | **BLOCKED** |
| Deriv options (Deriv API) | See Deriv above | No official list | Deriv API documented | UNVERIFIED (options disabled in ASTRA) |
| GoForexFx (goforexfx.com) | Not disclosed | Not stated | None documented | **BLOCKED** |
| GoForex (goforextrading.com) | Not disclosed | Not stated | None | **BLOCKED** |

### Second Forex broker: recommendation

Recommendation from the Forex dossier: **Interactive Brokers** (expected entity Interactive Brokers LLC), via the TWS API through IB Gateway, with the Web API as an alternative. Criteria were access from Guinea, costs, API quality, demo or paper, and ease of validation; minimum deposit and leverage were not used.

Reasoning:
1. It is the only venue where an official provider page names Guinea as an accepted country of residence. Every cTrader/MT5 broker checked uses non-exhaustive exclusion lists.
2. IB LLC can be checked in a primary register (FINRA BrokerCheck CRD 36418), unlike the offshore licence numbers that could not be read in registers.
3. Two official, maintained APIs with published rate limits; a different integration family from cTrader, which tests the multi-venue contract on a genuinely different adapter.
4. Published spot FX commission (0.20 bp, minimum USD 2) and free API use.

Caveats: no free demo, paper only after a funded IBKR Pro account; a Guinean bank statement may not qualify as proof of address (FATF rule); TWS or Gateway must run locally with periodic re-authentication; FX at IB LLC is spot, not a CFD. The registry keeps it at ELIGIBILITY_UNKNOWN until a human confirms the entity, account type and API/paper access in writing.

Fallback, only after written confirmation of Guinea acceptance: a cTrader or MT5 broker with an African-facing entity, IC Markets Global first, Deriv second. Pepperstone is deprioritised (it excludes Guinea for partners). OANDA should not be pursued.

### Blocked venues

| Venue | Reason |
|---|---|
| OANDA | Guinea not listed by any OANDA division; v20 is not offered to OANDA Global Markets, the only candidate entity. |
| Binance Futures | Pending a derivatives risk review (leverage, funding, liquidation); Guinea eligibility unknown. |
| IQ Option | No official API (decisive); no licence named for the non-EEA entity; binary or digital options. |
| goforexfx.com | Claimed FCA number 487321 belongs to another firm per the FCA register; operator unidentifiable. |
| goforextrading.com | No regulator named and no identifiable operator. |

### Still to verify by a human

See section 9 of the Forex dossier and section 6 of the crypto dossier. In short: written Guinea acceptance (entity, account type, API access) from any candidate; licence numbers read directly in the registers; Guinean foreign-exchange and crypto law (BCRG), with local legal advice; Guinea's FATF/GIABA status; Binance connectivity from the production network.

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
