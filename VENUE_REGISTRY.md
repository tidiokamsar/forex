# Venue registry

As of 2026-10-26. Everything below is documentary evidence from two research dossiers and is subject to human verification:

- [docs/venues/research_forex.md](docs/venues/research_forex.md) (Forex/CFD, cTrader, MT5, Interactive Brokers, OANDA, FXCM)
- [docs/venues/research_crypto_other.md](docs/venues/research_crypto_other.md) (Binance Spot and Futures, IQ Option, GoForex domains)

The machine-readable source of truth is [src/astra/venues/venue_registry.json](src/astra/venues/venue_registry.json), loaded and validated by `astra.venues.registry`.

- Registry version: **0.2.0** (was 0.1.0), `updated_on` 2026-10-26.
- Content hash (SHA-256 of the canonical content): `4bdefaeac0e5eb5c6c81852dca4bb81f9d85f5501f8010e7aa63635b6a7eecad`.
- Check it: `PYTHONPATH=src python -m astra.venues.registry check`. After any edit: bump `registry_version`, then `PYTHONPATH=src python -m astra.venues.registry rehash`.

**No live connection was made to any venue.** No account was opened, no credential or API key was used, no order was sent. No venue holds READ_ONLY_CONNECTED or any later status, and `can_trade` is False for every venue in this lot (`TRADING_ENABLED_IN_THIS_LOT = False`).

## Status ladder

Statuses are cumulative evidence, each held since a date. They are not a single label.

| Status | Meaning | Prerequisites enforced by the loader |
|---|---|---|
| DISCOVERED | The venue is known and recorded | Required on every record |
| OFFICIAL_API_VERIFIED | The provider's official API documentation was read and recorded with URL and date | Needs `api_url` and at least one evidence entry; contradicts UNVERIFIED |
| REGION_ELIGIBILITY_VERIFIED | An official source confirms the user's country and account type are accepted | Needs `eligibility_conditions`; contradicts ELIGIBILITY_UNKNOWN |
| READ_ONLY_CONNECTED | A supervised, human-run read-only check passed against the real service | Needs OFFICIAL_API_VERIFIED and REGION_ELIGIBILITY_VERIFIED |
| PAPER_TESTED | Paper trading validated | Needs READ_ONLY_CONNECTED |
| DEMO_VALIDATED | Demo account validated | Needs READ_ONLY_CONNECTED |
| LIVE_REVIEW_REQUIRED | Ready for a human live review | Needs DEMO_VALIDATED |
| UNVERIFIED | Official API not verified (or none exists) | Negative |
| ELIGIBILITY_UNKNOWN | Eligibility not established | Negative |
| BLOCKED | Must not be used | Negative |

Every record must state eligibility explicitly: ELIGIBILITY_UNKNOWN or REGION_ELIGIBILITY_VERIFIED.

Gates (all fail closed; any negative status closes every gate):

- `can_read_public_data`: OFFICIAL_API_VERIFIED and REGION_ELIGIBILITY_VERIFIED.
- `can_read_account`: the above plus READ_ONLY_CONNECTED.
- `can_trade`: always False in this lot.

Because every venue holds ELIGIBILITY_UNKNOWN today, **every gate is closed for every venue**, including public market data.

**An adapter, class or model existing in the code does not mean a venue is integrated.** Integration means the registry holds READ_ONLY_CONNECTED (or more) for that venue after a supervised check. Today the only broker adapter in this repository is the cTrader adapter, and it has been tested offline only, against a scripted fake server. Offline tests prove the adapter matches our reading of the documentation; they prove nothing about the real service, the account or eligibility.

## Summary

| venue_id | Market types | Statuses held | Gates |
|---|---|---|---|
| ctrader | FOREX_CFD | DISCOVERED, OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| iq_option | FIXED_EXPIRY_OPTIONS, FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, **BLOCKED** | all closed |
| binance_spot | CRYPTO_SPOT | DISCOVERED, OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| binance_futures | CRYPTO_FUTURES | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, **BLOCKED** | all closed |
| mt5 | FOREX_CFD | DISCOVERED, OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| interactive_brokers | FOREX_CFD (spot FX in fact) | DISCOVERED, OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| ic_markets_global | FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| pepperstone | FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| fxpro | FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| deriv | FOREX_CFD, FIXED_EXPIRY_OPTIONS | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| exness | FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| oanda | FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, **BLOCKED** | all closed |
| fxcm | FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN | all closed |
| goforexfx_com | FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, **BLOCKED** | all closed |
| goforextrading_com | FOREX_CFD | DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, **BLOCKED** | all closed |

Changes from 0.1.0: the placeholder `goforex` record was replaced by two records, `goforexfx_com` and `goforextrading_com`, because the dossier found two unrelated-looking domains; `ic_markets_global`, `pepperstone`, `fxpro`, `deriv` and `exness` were added; `ctrader`, `mt5`, `interactive_brokers` and `binance_spot` moved from UNVERIFIED to OFFICIAL_API_VERIFIED; `iq_option`, `oanda` and both GoForex records became BLOCKED.

Notes on choices made conservatively:
- The broker records (IC Markets, Pepperstone, FxPro, Deriv, Exness) stay UNVERIFIED for API even though the cTrader and MT5 platform APIs are verified: whether each broker's entity permits algorithmic access is an open question.
- `binance_futures` and `oanda` keep UNVERIFIED although their API pages were read: the futures dossier did not propose verification (rate-limit values not captured), and OANDA's own docs say v20 is not offered to the only candidate entity.
- `interactive_brokers` is the only venue with a positive official country listing for Guinea, but the dossier calls it documentary only, with open human checks. It stays ELIGIBILITY_UNKNOWN; its `eligibility_conditions` are recorded for when those checks pass.

## Per venue

All sources accessed on 2026-10-26 unless a different date is shown. Quotes are kept short; see the dossiers for detail.

### ctrader: cTrader Open API (Spotware)

- Status: DISCOVERED (2026-10-25), OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN.
- Evidence:
  - https://help.ctrader.com/open-api/ : access with a cTID; Protobuf/TCP or JSON/WebSocket.
  - https://help.ctrader.com/open-api/account-authentication/ : OAuth 2.0 code flow, scopes `accounts` (view only) and `trading`.
  - https://help.ctrader.com/open-api/api-application/ : application must be approved by Spotware.
  - https://help.ctrader.com/open-api/faq/ : 50 req/s per connection, 5 req/s historical, heartbeat 10 s.
  - https://help.ctrader.com/open-api/terms-of-use/ : API offered free; pricing may change.
  - https://help.ctrader.com/open-api/python-SDK/python-sdk-index/ : official Python SDK.
  - Carried from 2026-10-25, not re-read: https://help.ctrader.com/open-api/proxies-endpoints/ (demo and live hosts, ports 5035/5036).
- Verified: official API documentation (auth, scopes, limits, terms).
- Not verified: the live host (not re-read); which cTrader broker accepts a Guinea resident; whether the adapter behaves correctly against the real service.
- Tests passed (offline only): `tests/test_ctrader.py` (scripted fake server), `tests/test_ctrader_resilience.py` (resilience fixture).
- Validations missing: REGION_ELIGIBILITY_VERIFIED via a cTrader broker; READ_ONLY_CONNECTED (`ctrader-check` not run); re-read the endpoints page; comparison with the official Protobuf SDK; per-broker specs, commissions and swaps.
- Human actions: obtain written Guinea acceptance from a cTrader broker; register an Open API application; run `python -m astra ctrader-check` on a demo account (see [BROKERS.md](BROKERS.md)).

### mt5: MetaTrader 5 Python package (MetaQuotes)

- Status: DISCOVERED (2026-10-25), OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN.
- Evidence: https://www.mql5.com/en/docs/python_metatrader5 ; https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py ; https://pypi.org/project/MetaTrader5/ (5.0.5735, 2026-04-04, win_amd64 only); carried from 2026-10-25: https://www.mql5.com/en/book/advanced/python/python_funcs_overview.
- Verified: package documentation; IPC to a local Windows MT5 terminal; login with broker password (no OAuth, no scopes).
- Not verified: demo versus live detection field; investor (read-only) password per broker; broker-side rate limits.
- Tests passed: none. No MT5 adapter exists in this repository.
- Validations missing: MT5 broker eligibility for Guinea; demo detection field; READ_ONLY_CONNECTED.
- Human actions: choose an MT5 broker with written Guinea acceptance; confirm an investor password is available.

### interactive_brokers: Interactive Brokers (TWS API, Web API)

- Status: DISCOVERED (2026-10-25), OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN.
- Evidence:
  - https://www.interactivebrokers.com/en/accounts/open-account-country-list.php : Guinea listed as an available country (Hedge Fund accounts excluded).
  - https://brokercheck.finra.org/firm/summary/36418 : Interactive Brokers LLC, CRD 36418.
  - https://www.interactivebrokers.com/en/general/what-you-need-inv.php : KYC; bank-statement proof of address requires an FATF member country.
  - https://www.interactivebrokers.com/en/accounts/fees/fxcoms.php?path=3 : spot FX 0.20 bp, minimum USD 2 per order.
  - https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/ : Web API, free to clients, 10 req/s per username.
  - https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/limitations/paper-trading : paper only after a funded live account.
  - https://www.interactivebrokers.com/docs : API documentation hub.
- Verified: official API documentation; Guinea on the official country list (documentary).
- Not verified: contracting entity (expected IB LLC); IBKR Pro, paper and API access for a Guinea resident; proof-of-address and funding methods; CFD availability; TWS API pacing; NFA registration.
- Eligibility conditions recorded: country list entry; entity to confirm on the application screen; proof-of-address rule.
- Tests passed: none. No Interactive Brokers adapter exists in this repository.
- Validations missing: see `validations_missing` in the JSON (entity, written confirmation, documents, CFDs, pacing, NFA, FATF status, READ_ONLY_CONNECTED).
- Human actions: start (do not submit) an application with residence Guinea and record the entity shown; obtain written confirmation from IBKR Client Services; check NFA BASIC; confirm Guinea's FATF/GIABA status.

### binance_spot: Binance Spot

- Status: DISCOVERED, OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN.
- Evidence: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information ; https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only ; https://developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints ; https://developers.binance.com/en/docs/products/spot/faqs/api_key_types ; https://www.binance.com/en/support/faq/detail/360002502072 ; https://testnet.binance.vision/ ; https://www.binance.com/en/legal/list-of-prohibited-countries ; https://www.binance.com/en-GB/support-region-list ; https://www.binance.com/en/fee/trading ; https://www.binance.com/en-GB/legal/terms-binance-api.
- Verified: Spot API documentation (endpoints, security types, key types, rate-limit model, testnet); Guinea absent from the prohibited-countries list.
- Not verified: contracting entity (ADGM entities likely); FSRA register; Guinea at KYC; Guinean law; RAW_REQUESTS and ORDERS values; data redistribution terms; connectivity (all Binance hosts timed out from the research machine, cause unknown).
- Tests passed: none recorded in the registry.
- Validations missing: REGION_ELIGIBILITY_VERIFIED; FSRA register; Guinean law and BCRG controls; connectivity from production; live rate limits; redistribution terms; READ_ONLY_CONNECTED.
- Human actions: repeat the ping/time GETs from the production network; confirm the entity and KYC country with Binance; check the FSRA register; obtain local legal advice.

### binance_futures: Binance Futures (USD-M, COIN-M)

- Status: DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, BLOCKED.
- BLOCKED reason: pending a derivatives risk review (leverage, funding, liquidation); crypto futures models are disabled.
- Evidence: https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info ; https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Long-Short-Ratio ; https://www.binance.com/en/support/announcement/detail/f72d0fff2bd24b488213fa30284dd4cd ; https://www.binance.com/en/fee/futureFee.
- Verified: base URLs and fee page read. Not verified: numeric rate limits, COIN-M testnet, Guinea eligibility.
- Tests passed: none.
- Human actions: derivatives risk review; confirm whether a Guinea resident can open a Futures account.

### ic_markets_global, pepperstone, fxpro, deriv, exness (cTrader and/or MT5 brokers)

- Status for each: DISCOVERED, UNVERIFIED (no broker-level API verification), ELIGIBILITY_UNKNOWN.
- Common reason: restricted-country lists are non-exhaustive or not published, so Guinea's absence proves nothing.
- Evidence:
  - IC Markets Global (Raw Trading Ltd, Seychelles, SD018 per the provider): https://www.ic.com/en/company/regulation ; https://fsaseychelles.sc/regulated-entities/capital-markets ; https://www.icmarkets.com/global/en/trading-accounts/ctrader-raw. Company number discrepancy (8419879-1 vs 8419879-2).
  - Pepperstone: https://partnership.pepperstone.com/en/affiliates-program (Guinea excluded for affiliate partners: negative signal) ; https://pepperstone.com/en-af/help-and-support/opening-an-account/is-pepperstone-licensed-and-regulated ; FSA register as above.
  - FxPro: https://www.fxpro.com/about/licences ; FSA register as above.
  - Deriv: https://deriv.com/terms-and-conditions/general-terms-of-use ; https://docs.deriv.com/regulatory/Deriv_(BVI)_Ltd.pdf ; https://developers.deriv.com/docs/intro/api-overview (own API, options product).
  - Exness: https://www.exness.com/regulation ; FSA register (address mismatch) ; https://get.exness.help/hc/en-us/articles/5714316735378-Can-I-verify-an-Exness-account-from-a-foreign-country.
- Tests passed: none.
- Human actions: written confirmation from each broker's compliance of Guinea acceptance, the legal entity, and permitted cTrader Open API / MT5 algorithmic access; read licence numbers directly on the FSA Seychelles, SCB, BVI FSC, CMA Kenya and FSC Mauritius registers.

### oanda: OANDA v20

- Status: DISCOVERED (2026-10-25), UNVERIFIED, ELIGIBILITY_UNKNOWN, BLOCKED.
- BLOCKED reason: Guinea is not listed by any OANDA division, and v20 is not offered to OANDA Global Markets, the only candidate division.
- Evidence: https://developer.oanda.com/rest-live-v20/introduction ; https://developer.oanda.com/mt4-live/api-comparison ; https://www.oanda.com/region-selector ; https://help.oanda.com/bvi/en/faqs/eligible-ogm-countries-bvi.htm ; carried from 2026-10-25: https://developer.oanda.com/rest-live-v20/development-guide.
- Tests passed: none. Human action (optional): ask OANDA whether any division accepts Guinea residents with v20 access.

### fxcm: FXCM (Stratos Global LLC)

- Status: DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN.
- Evidence: https://www.fxcm.com/markets/help/new-accounts-is-fxcm-markets-regulated (international entity states it needs no licence) ; https://www.fxcm.com/markets/algorithmic-trading/api-trading ; https://github.com/fxcm/fxcmrest-php (REST deprecation notice).
- Not verified: ForexConnect continuity, Guinea acceptance, costs, rate limits.
- Tests passed: none. Human actions: ask api@fxcm.com about ForexConnect support; decide whether an unregulated entity is acceptable at all.

### iq_option: IQ Option

- Status: DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, BLOCKED.
- BLOCKED reason: no official public trading API (decisive); no licence named for the non-EEA entity SKY LADDER LLC; binary or digital options.
- Evidence: https://iqoption.com/en/regulation ; https://static.cdnroute.io/files//storage/public/db/8c/qd2if3ts707ksqfg.pdf (General T&C) ; https://www.cysec.gov.cy/en-GB/entities/investment-firms/cypriot/40647/ (EEA entity only).
- Tests passed: none. Human action (not decisive): check the Antigua FSRC register.

### goforexfx_com: GoForexFx

- Status: DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, BLOCKED.
- BLOCKED reason: the claimed FCA number 487321 belongs to another firm per the FCA register; operator unidentifiable.
- Evidence: https://goforexfx.com/ ; https://register.fca.org.uk/s/search?q=487321&type=Companies.
- Human actions: query warning lists (FCA, IOSCO I-SCAN, CySEC, ASIC, AMF) by domain; WHOIS; decide whether to report the false FRN claim to the FCA.

### goforextrading_com: GoForex

- Status: DISCOVERED, UNVERIFIED, ELIGIBILITY_UNKNOWN, BLOCKED.
- BLOCKED reason: no regulator named, no identifiable operator.
- Evidence: https://www.goforextrading.com/.
- Human actions: warning-list queries by domain; WHOIS.

## Cross-cutting human checks

1. Guinean law: whether BCRG foreign-exchange rules allow residents to hold offshore brokerage or exchange accounts and move funds abroad. Not researched; needs local legal advice.
2. FATF: confirm the October 2026 plenary outcome and Guinea's FATF/GIABA status.
3. No payment workaround of any kind is to be researched or used.

## Registry tests (offline)

`cd tests && PYTHONPATH=../src python -m unittest test_venue_registry` checks the schema, hash, gates and status rules. In this version two tests fail because they pin the 0.1.0 seed content, not because the registry is invalid:

- `SeedTests.test_seeded_venues` expects exactly the nine seeded venue ids (including `goforex`).
- `SchemaTests.test_reseal_file_round_trip` rewrites the version to `0.2.0` and expects the hash to break; the version is now 0.2.0, so nothing changes.

These need a reviewed test update; the registry itself passes `check`.
