# Forex/CFD venue eligibility dossier: residents of Guinea (Conakry)

Status: research only. No account was opened, no sign-up form was submitted, no credential was used, no order was sent. Only public pages were read.
Research date: 2026-10-26. Every source below was accessed on 2026-10-26 unless stated otherwise.

## 0. Method and reading rules

- Evidence order: (1) regulator register, (2) provider legal page, client agreement or help centre, (3) provider developer documentation. Affiliate review sites, comparison sites and ads were seen in search results but are **not** used as evidence. Where only such sources exist, the fact is marked UNVERIFIED.
- "Guinea" means the Republic of Guinea (Conakry). It is distinct from Guinea-Bissau, Equatorial Guinea and Papua New Guinea, which appear next to it in several lists.
- A restricted-country list that is explicitly non-exhaustive ("such as", "including", "and others") does **not** prove eligibility when Guinea is absent. Such cases are ELIGIBILITY_UNKNOWN.
- Registry status vocabulary: DISCOVERED, OFFICIAL_API_VERIFIED (official API documentation read and consistent), REGION_ELIGIBILITY_VERIFIED (an official provider source positively lists Guinea as accepted), BLOCKED (an official source excludes Guinea or the official API is unavailable to the entity that would serve Guinea), ELIGIBILITY_UNKNOWN.
- Regulator registers: several registers are search applications (FCA, SCB, BVI FSC, FINRA BrokerCheck) whose result pages do not always render to a non-interactive reader. When a licence number was read only on the provider's own site, it is marked "register not read".

### Background facts relevant to every venue

- FATF lists (19 June 2026 plenary, latest found): Guinea is **not** on the "call for action" list (Iran, DPRK, Myanmar) and **not** among the 22 jurisdictions under increased monitoring (Angola, Bolivia, Bosnia and Herzegovina, Bulgaria, Cameroon, Côte d'Ivoire, DRC, Haiti, Iraq, Kenya, Kuwait, Lao PDR, Lebanon, Monaco, Nepal, Papua New Guinea, South Sudan, Syria, Venezuela, Vietnam, Yemen, and British Virgin Islands as the 22nd per one secondary list). Primary: https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html. The member list of the grey list was read from secondary reproductions (IFSCA press release of 17 July 2026, which reproduces the FATF statement); the FATF page itself was not read in full. A later October 2026 plenary may have changed the list: **UNVERIFIED**.
- FATF has 40 members; West African states participate through GIABA (an FATF-style regional body). Guinea is not, to the extent checked, an FATF member: **UNVERIFIED** (members page https://www.fatf-gafi.org/en/countries/fatf.html not read). This matters for Interactive Brokers' proof-of-address rules (section 3).
- Payment rails from Guinea (bank wires in GNF/USD/EUR, cards, e-wallets) were not researched beyond what each provider states, as instructed. No workaround was researched.

---

## 1. cTrader (Spotware) Open API and cTrader brokers

### 1.1 Spotware / cTrader Open API (technology vendor, not a broker)

| Item | Finding |
|---|---|
| Provider | Spotware Systems Ltd (named in the Open API Terms of use). Registered address: **UNVERIFIED** (not read on an official page this session). Spotware is a platform vendor; it does not hold client accounts. Eligibility always depends on the cTrader broker. |
| Access | "the API can be accessed by anyone with a cTID" and is supported "by all trading accounts of any cTrader-affiliated brokers" (help.ctrader.com/open-api). |
| Protocol | Protobuf over TCP or JSON over WebSocket; demo endpoint `wss://demo.ctraderapi.com:5036` for JSON/WebSocket per the existing ASTRA adapter (BROKERS.md, read 2026-10-25); live host not re-read this session (UNVERIFIED). Official SDKs: C# (`spotware/OpenAPI.Net`), Python (`ctrader-open-api` on PyPI, Twisted based). |
| Auth | OAuth 2.0 authorisation code. Application must be registered and approved by Spotware (help.ctrader.com/open-api/api-application/). Grant URL `https://id.ctrader.com/my/settings/openapi/grantingaccess/?client_id=...&redirect_uri=...&scope=...`; token endpoint `https://openapi.ctrader.com/apps/token`. Authorisation code valid 1 minute; access token `expiresIn` 2,628,000 s (about 30 days); refresh token has no expiry; refresh via HTTP or `ProtoOARefreshTokenReq`. Message sequence: `ProtoOAApplicationAuthReq`, `ProtoOAGetAccountListByAccessTokenReq`, `ProtoOAAccountAuthReq`. |
| Scopes | `accounts` (view only, trading impossible) and `trading` (full access including trading). ASTRA's read-only check should request `accounts`. |
| Rate limits | 50 requests/s per connection for non-historical requests; 5 requests/s per connection for historical data requests. Heartbeat at least every 10 s (FAQ). |
| Demo | Yes. Demo and live accounts both usable; Spotware recommends demo for development. |
| Cost | "cTrader Open API is offered for free"; Spotware reserves the right to change pricing without notice (Terms of use). |
| Other terms | No trading on behalf of a trader without explicit prior approval; trader personal data only for the app's stated purpose; developer contact details are shared with brokers via cBroker; GDPR acknowledgement. |
| Registry status | **OFFICIAL_API_VERIFIED** (API). Region eligibility is a broker property, see below. |

Sources: https://help.ctrader.com/open-api/ ; https://help.ctrader.com/open-api/account-authentication/ ; https://help.ctrader.com/open-api/terms-of-use/ ; https://help.ctrader.com/open-api/api-application/ ; https://help.ctrader.com/open-api/faq/ ; https://help.ctrader.com/open-api/python-SDK/python-sdk-index/ ; https://openapi.ctrader.com/

### 1.2 IC Markets Global (Raw Trading Ltd) - cTrader and MT5

| Item | Finding |
|---|---|
| Contracting entity | Raw Trading Ltd, trading as "IC Markets Global" / "IC" / "IC MARKETS (SC)". |
| Country of registration | Seychelles. Company number shown as 8419879-1 on the website footer but 8419879-2 in the Terms and Conditions for transferable securities and the affiliate agreement: **discrepancy, to be clarified**. |
| Address | FSA register: Eden House, 1st Floor, Eden Island, Mahe, Seychelles. |
| Regulator | Seychelles Financial Services Authority, Securities Dealer licence SD018 (website, T&C). Register: the FSA "Capital Markets" regulated-entities page lists **Raw Trading Ltd** with trade names IC Markets Global and IC MARKETS (SC) and accredited Securities Dealer Representatives. The licence number itself was not visible in the extracted register text: number **register not read**, entity presence **verified**. |
| Activities | Securities Dealer (OTC derivatives / CFDs on FX, indices, commodities, crypto; transferable securities per its separate T&C). |
| Guinea acceptance | Restricted-countries clause names only examples: "such as the United States of America, Canada, Iran and North Korea" (some pages add New Zealand and Israel). Guinea is not named, but the list is non-exhaustive. Full country list in the client agreement: **not found**. Status **ELIGIBILITY_UNKNOWN**. |
| KYC | Age 18 or legal age; client registers "without solicitation". Document list not researched in detail: UNVERIFIED. |
| Products | CFDs on forex, metals, indices, commodities, crypto; platforms MT4, MT5, cTrader, TradingView. Demo: free, all account types. |
| Costs (published, read 2026-10-26) | Raw Spread (MetaTrader): USD 3.50 per lot per side. Raw Spread (cTrader/TradingView): USD 3 per USD 100,000 traded (per side). Standard: no commission, spreads from 0.8 pips. Minimum deposit USD 200 (stated, not a decision criterion). |
| APIs | No proprietary API found. Via cTrader Open API (section 1.1) or MetaTrader5 Python package (section 2.1). |
| Deposit/withdrawal | Not researched for Guinea; UNVERIFIED. |
| Registry status | **ELIGIBILITY_UNKNOWN** (API path OFFICIAL_API_VERIFIED through cTrader/MT5). |

Sources: https://www.ic.com/en/company/regulation ; https://www.icmarkets.com/global/en/trading-accounts/ctrader-raw ; https://www.ic.com/en/trading-pricing/spreads ; https://www.icmarkets.sc/en/trading-accounts/raw-spread-account ; https://cdn.icmarkets.com/uploads/terms_and_conditions_of_business_for_trasnsferable_securities.pdf ; https://fsaseychelles.sc/regulated-entities/capital-markets

### 1.3 Pepperstone - cTrader and MT5

| Item | Finding |
|---|---|
| Entities seen | Pepperstone Markets Kenya Limited (Capital Markets Authority, Kenya; address 2nd Floor, The Oval, Ring Road Parklands, Nairobi; serves pepperstone.com/en-af); Pepperstone Markets Limited (Securities Commission of The Bahamas, SIA-F217; #1 Pineapple House, Old Fort Bay, Nassau); Pepperstone International Markets Limited (listed on the FSA Seychelles register, Room 2, Block B, Global Village, Jivan's Complex, Mont Fleuri, Mahe). Which entity would contract with a Guinea resident: **UNVERIFIED**. |
| Register checks | FSA Seychelles: Pepperstone International Markets Limited **present** (licence number not visible). CMA Kenya licence number (secondary sources say 128): **register not read**. SCB SIA-F217: **register not read**. |
| Guinea acceptance | Pepperstone's own partner page (list "relevant as of February, 2025") puts **Guinea** in the countries from which it does not accept *affiliate partners*, and Guinea is absent from the list of about 100 client countries for which affiliates are rewarded. This is an official Pepperstone source but concerns affiliates, not clients. It is a negative signal, not a client ban. Client restricted list: not published (sign-up form decides). Status **ELIGIBILITY_UNKNOWN (negative signal)**. |
| Products / platforms | 1,350+ CFDs; MT4, MT5, cTrader, TradingView. |
| API | Via cTrader Open API or MT5 package. |
| Registry status | **ELIGIBILITY_UNKNOWN** |

Sources: https://partnership.pepperstone.com/en/affiliates-program ; https://pepperstone.com/en-af/help-and-support/opening-an-account/is-pepperstone-licensed-and-regulated ; http://pepperstone.com/en/help-and-support/opening-an-account/is-pepperstone-licensed-and-regulated ; https://pepperstone.com/en-af/go/ctrader ; https://fsaseychelles.sc/regulated-entities/capital-markets

### 1.4 FxPro - cTrader and MT5

| Item | Finding |
|---|---|
| Entities | FxPro UK Limited (FCA 509956, UK clients); FxPro Global Markets Ltd (SCB, SIA-F184); Invemonde Trading Ltd "in partnership with FxPro Global Markets Ltd", Seychelles FSA Investment Dealer licence SD120. FSA register lists Invemonde Trading Ltd, trade name FxPro, Room 2, Attic Suites, 3rd Floor, Oliver Maradan Building, Victoria, Mahe. Contracting entity for Guinea: **UNVERIFIED**. |
| Register checks | FSA: Invemonde Trading Ltd **present** (number not visible). SCB SIA-F184 and FCA 509956: **register not read** this session. |
| Guinea acceptance | "does not offer Contracts for Difference to residents of certain jurisdictions, including the United States, Iran and Canada" (non-exhaustive). Guinea not named. **ELIGIBILITY_UNKNOWN**. |
| API | Via cTrader Open API or MT5 package. |
| Registry status | **ELIGIBILITY_UNKNOWN** |

Source: https://www.fxpro.com/about/licences

### 1.5 Deriv - cTrader (also MT5, own API)

| Item | Finding |
|---|---|
| Entities | Deriv (BVI) Ltd (BVI FSC investment business licence SIBA/L/18/1114, licence certificate PDF published by Deriv; Categories 1A/1B dealing as agent and as principal); Deriv (FX) Ltd (Labuan FSA); Deriv (V) Ltd (Vanuatu FSC); Deriv (Mauritius) Ltd (FSC Mauritius); Deriv (SVG) LLC (St Vincent; registered office First Floor, SVG Teachers Credit Union Uptown Building, Kingstown); Deriv Investments (Cayman) Limited (CIMA); holding company Deriv.com Limited (Guernsey). Which entity serves Guinea: **UNVERIFIED** ("The entity ... depends on your country of residence and the products"). |
| Register checks | BVI FSC register: **not read**; only Deriv's copy of the licence certificate was read. |
| Guinea acceptance | General terms: services only "to residents of certain countries"; list "may change". No official country list found. Secondary sources list restricted countries (US, Canada, Hong Kong, Malaysia, Israel, etc.) without Guinea, but they are not evidence. **ELIGIBILITY_UNKNOWN**. |
| Products | CFDs on Deriv MT5 and Deriv cTrader, options and multipliers on Deriv Trader; forex included; synthetic "derived indices" (not real-market instruments, not suitable as ASTRA FX data). |
| Own API | Deriv API (developers.deriv.com): REST base `https://api.derivws.com` plus WebSocket endpoints public / demo / real (`wss://api.derivws.com/trading/v1/options/ws/...`); OTP issued by `POST /trading/v1/options/accounts/{accountId}/otp` (OAuth2 scope `trade`); auth by OAuth 2.0 (client_id, client_secret, redirect_uri) or Personal Access Token plus `Deriv-App-ID` header. Demo account given by default at sign-up. The API surface is the **options** product; Deriv's FX CFDs are reached through MT5 or cTrader. Rate limits: **not found**. |
| Registry status | **ELIGIBILITY_UNKNOWN** (Deriv API: DISCOVERED, documented but options-oriented). |

Sources: https://deriv.com/terms-and-conditions/general-terms-of-use ; https://docs.deriv.com/tnc/general-terms.pdf ; https://deriv.com/regulatory ; https://docs.deriv.com/regulatory/Deriv_(BVI)_Ltd.pdf ; https://deriv.com/help-centre/deriv-ctrader ; https://developers.deriv.com/docs/intro/api-overview ; https://developers.deriv.com/docs/options/websocket ; https://developers.deriv.com/docs/workflows

---

## 2. MetaTrader 5

### 2.1 Official `MetaTrader5` Python package (MetaQuotes)

| Item | Finding |
|---|---|
| Provider | MetaQuotes Ltd (PyPI author/maintainer `plugins@metaquotes.net`). Address: **UNVERIFIED** (not read). Not a broker. |
| Package | PyPI `MetaTrader5` (normalised `metatrader5`), latest 5.0.5735 released 2026-04-04, MIT licence, `Requires: Python <4, >=3.6`. Wheels only for **win_amd64** (CPython 3.6 to 3.14). No source distribution. |
| Architecture | Interprocess communication "directly from the MetaTrader 5 terminal". The package needs an installed MT5 terminal on the same Windows machine; `initialize()` finds or launches `terminal64.exe` and can log in with `login`, `password`, `server`, `timeout` (default 60,000 ms), `portable`. The terminal stays the network client to the broker. |
| Functions | `initialize`, `shutdown`, `version`, `last_error`, `account_info`, `terminal_info`, `symbols_*`, `symbol_info_tick`, `market_book_*`, `copy_rates_*`, `copy_ticks_*`, `orders_*`, `order_calc_margin`, `order_calc_profit`, `order_check`, `order_send`, `positions_*`, `history_orders_*`, `history_deals_*`. |
| Auth | Broker account login and password (stored in the terminal or passed to `initialize`). No OAuth, no scopes: a password grants full trading rights; an MT5 "investor" (read-only) password is a broker-side feature, availability per broker **UNVERIFIED**. |
| Rate limits | None documented in the Python reference. Broker-side limits possible: UNVERIFIED. |
| Demo | Depends on the broker (MetaQuotes-Demo server used in official examples). |
| Constraints for ASTRA | Windows host required; terminal must be running and logged in; the official example stores a password in code, which ASTRA must not copy. Third-party wrappers note the API is not thread-safe (secondary, UNVERIFIED). |
| Registry status | **OFFICIAL_API_VERIFIED** (package). Eligibility per broker below. |

Sources: https://www.mql5.com/en/docs/python_metatrader5 ; https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py ; https://pypi.org/project/MetaTrader5/ ; https://www.metatrader5.com/en/metaeditor/help/development/python

### 2.2 MT5 brokers

IC Markets Global (1.2), Pepperstone (1.3), FxPro (1.4) and Deriv (1.5) also offer MT5; same eligibility findings apply. Additional MT5-only broker checked:

#### Exness

| Item | Finding |
|---|---|
| Entities | Exness (SC) Ltd, Seychelles, reg. no. 8423606-1, FSA Securities Dealer licence SD025, also FSCA ODP (South Africa); Exness B.V. (Curaçao, CBCS licence 0003LSI); Exness (VG) Ltd (BVI FSC, SIBA/L/20/1133); Exness (MU) Ltd (FSC Mauritius, Investment Dealer GB20025294); Exness ZA (PTY) Ltd (FSCA FSP 51024); Exness (KE) Ltd (CMA Kenya); Forexite Ltd (Belize). Which entity serves Guinea: **UNVERIFIED**. |
| Address | Website: 9A CT House, 2nd Floor, Providence, Mahe. FSA register lists Exness (SC) Ltd at Office 107, 1st Floor, Waterside Property Ltd, Eden Island, Mahe: **address mismatch between register and website**. |
| Register checks | FSA Seychelles: Exness (SC) Ltd **present** (licence number not visible). Other registers: **not read**. |
| Guinea acceptance | "does not offer services to residents of certain jurisdictions including the USA, Iran, North Korea, the European Union, the United Kingdom and others" (contact page adds Canada, Russia, Belarus). Non-exhaustive; Guinea not named. **ELIGIBILITY_UNKNOWN**. |
| KYC | Residency rule: nationals living in a served country are accepted; proof of address must be issued in the same country as the account registration (help centre). |
| API | No public Exness trading API found. MT4/MT5 only for ASTRA purposes; client agreement mentions conditions on "programmatic or automated access" (clause not read in full: UNVERIFIED). |
| Registry status | **ELIGIBILITY_UNKNOWN** |

Sources: https://www.exness.com/regulation ; https://www.exness.com/contact ; https://get.exness.help/hc/en-us ; https://get.exness.help/hc/en-us/articles/5714316735378-Can-I-verify-an-Exness-account-from-a-foreign-country ; https://www.exness.com/cdn/media/exnesssc/exness_sc_client_agreement.pdf ; https://fsaseychelles.sc/regulated-entities/capital-markets

---

## 3. Interactive Brokers

| Item | Finding |
|---|---|
| Provider | Interactive Brokers group (IBG LLC managing member of IB LLC). |
| Contracting entity for Guinea | Not stated on an official page read this session. IBKR assigns the entity by country of residence; for residents of countries without a local IBKR entity, secondary sources and a published IBKR support reply (forum quote) indicate **Interactive Brokers LLC**. Status: **UNVERIFIED**, to be confirmed during application (the application shows the entity). |
| IB LLC identity | INTERACTIVE BROKERS LLC, CRD 36418, SEC 8-47257, main address One Pickwick Plaza, Greenwich, CT 06830, USA; brokerage firm regulated by FINRA (BrokerCheck, report dated 2026-10-21, 120 disclosures). IBKR pages state IB LLC is a member of NFA (futures/forex oversight); NFA ID **register not read** (NFA BASIC not queried). |
| Guinea acceptance | **Positive official listing.** The page "Available Countries and Territories" on interactivebrokers.com lists "Guinea" (separately from Guinea-Bissau and Equatorial Guinea). Footnote: "Excluding Hedge Fund accounts". |
| KYC / residency | Name, residential address, date and country of birth, tax residency and ID number, employer, assets and income, investment objectives and experience. Proof of address: bank statement / bank card statement / broker statement proofs require that customer and bank be "from a country that is a member of the Financial Action Task Force". If Guinea is not an FATF member (see section 0), a Guinean bank statement would **not** qualify; utility bill, lease, government letter or similar would be needed. IB LLC may request additional documents. |
| Products | Stocks, options, futures, bonds, funds, spot FX (IDEALPRO conversion and leveraged FX). IB-issued CFDs: "Legal residents of the US, Canada, Hong Kong, New Zealand and Israel are prohibited" (IBIE CFD page); Guinea not in that list. For IB LLC clients, CFD positions are held in a separate "F" account segment (IBKR CFD FAQ), meaning CFDs are issued by a European IBKR entity. CFD availability for a Guinea-resident IB LLC client: **UNVERIFIED**. |
| Costs (published, read 2026-10-26) | Spot FX: 0.20 basis point x trade value, minimum USD 2.00 per order, for monthly volume up to USD 1 billion (tiered down to 0.08 bp). Commissions may change at IBKR's discretion. Market data subscriptions and inactivity rules: not researched (UNVERIFIED). |
| Official APIs | (a) **TWS API**: TCP socket API to a locally running TWS or IB Gateway; languages Python, Java, C++, C#; protobuf transport now documented; daily/weekly re-authentication of TWS/Gateway. (b) **Web API** (Client Portal Web API merged with Digital Account Management and Flex Web Service): HTTPS REST plus WebSocket. Individuals authenticate through the **Client Portal Gateway** (local Java program) with IBKR username and password; OAuth 1.0a / OAuth 2.0 (private_key_jwt, beta) are for organisations and third parties. (c) FIX for institutions (not researched). |
| Rate limits | Web API: global 10 requests/s per authenticated username; per-endpoint limits (e.g. `/iserver/marketdata/snapshot` 10/s, `/iserver/orders` 1 per 5 s, `/portfolio/accounts` 1 per 5 s, `/tickle` 1/s); HTTP 429, penalty box 10 minutes, repeat violators may be blocked. TWS API pacing (messages per second): **not read** this session, UNVERIFIED. One brokerage session per username across all IB platforms. |
| Paper account | Yes, but only after the live account is **approved and funded**; for the Web API, the live account must be fully open, funded and of type **IBKR Pro**. No standalone demo for API work. |
| Cost of API | "available to all IBKR clients free of cost" (Web API trading). |
| Registry status | **REGION_ELIGIBILITY_VERIFIED** at documentary level (official country list) and **OFFICIAL_API_VERIFIED**. Contracting entity and CFD access remain UNVERIFIED. |

Sources: https://www.interactivebrokers.com/en/accounts/open-account-country-list.php ; https://brokercheck.finra.org/firm/summary/36418 ; https://files.brokercheck.finra.org/firm/firm_36418.pdf ; https://www.interactivebrokers.com/en/general/what-you-need-inv.php ; https://www.interactivebrokers.com/en/accounts/fees/fxcoms.php?path=3 ; https://www.interactivebrokers.ie/en/index.php?ex=eu&f=38596 ; https://www.interactivebrokers.com/lib/cstools/faq/#/content/1169367415 ; https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/ ; https://www.interactivebrokers.com/docs ; https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/limitations/paper-trading ; https://interactivebrokers.github.io/cpwebapi (deprecated docs, points to IBKR Campus)

---

## 4. OANDA

| Item | Finding |
|---|---|
| Candidate entity | OANDA Global Markets Ltd (OGM), BVI company no. 2026433, registered office Kingston Chambers, PO Box 173, Road Town, Tortola, BVI; BVI FSC licence SIBA/L/20/1130. This is the OANDA division for emerging markets (bvi-en site). |
| Register check | BVI FSC register: **not read**; licence number read on OANDA's help centre and group page. |
| Guinea acceptance | OANDA's official region selector lists, under "Africa, Middle East, and India", countries such as Cameroon, Mali, Niger, Senegal, Nigeria, Côte d'Ivoire (routed to OANDA UK), but **not Guinea**. OGM's "Who can apply" help page lists Chile, Colombia, Hong Kong, Indonesia, Macao, Malaysia, Mexico, Philippines, Taiwan, Thailand, UAE, Vietnam (as extracted; page may be longer). OANDA partner country lists (2022, 2023 PDFs, partner FAQ 2023) do not include Guinea. No positive listing found. |
| API | v20 REST API: "available to all divisions except OANDA Global Markets and OANDA TMS BROKERS S.A." The API comparison page says v20 is "Not yet available in Global Markets". So even if OGM accepted a Guinea resident, the official API would not be available. |
| v20 facts (for reference) | REST `https://api-fxtrade.oanda.com` / practice `https://api-fxpractice.oanda.com`; streaming `stream-fxtrade` / `stream-fxpractice`; personal access token (Bearer) generated in the Account Management Portal after accepting the API licence; 120 requests/s per IP (HTTP 429), 20 active streams per IP, 2 new connections/s; free practice account. |
| Registry status | **BLOCKED** (Guinea not listed by any OANDA division; the only plausible division, OGM, has no v20 API). |

Sources: https://help.oanda.com/bvi/en/faqs/eligible-ogm-countries-bvi.htm ; https://www.oanda.com/region-selector ; https://www.oanda.com/group/where-we-are ; https://developer.oanda.com/rest-live-v20/introduction ; https://developer.oanda.com/rest-live-v20/development-guide/ ; https://developer.oanda.com/mt4-live/api-comparison ; https://partners.oanda.com/assets/documents/1407/ogm_affiliate_country_list_12.04.2023.pdf

---

## 5. FXCM

| Item | Finding |
|---|---|
| Entity for non-UK/EU/AU/ZA clients | **Stratos Global LLC** ("FXCM"), incorporated in St Vincent and the Grenadines, company registration No. 1776 LLC 2022; registered address First Floor, SVG Teachers Co-operative Credit Union Limited Uptown Building, Corner of James and Middle Street, Kingstown, SVG. Group HQ 110 Bishopsgate, London. |
| Regulation | FXCM states Stratos Global LLC "is not required to hold any financial services license" in SVG and "is not subject to regulatory oversight" of the FCA, ASIC or FSCA. **Unregulated entity.** Regulated sister entities: Stratos Markets Limited (FCA 217689), Stratos Europe Limited (CySEC 392/20), Stratos Trading Pty Ltd (AFSL 309763), Stratos South Africa (Pty) Ltd (FSCA FSP 46534, intermediary for South African residents only), Stratos Light Limited (Israel ISA). Registers: **not read** (FCA 217689 not queried). |
| Guinea acceptance | Stratos Global LLC "is not intended to be used by residents of" the US, Canada, EU, Japan, Hong Kong, Australia; site not directed at UK, Israel, India either. Guinea not named, list is specific but no positive acceptance statement found. **ELIGIBILITY_UNKNOWN**. |
| APIs | Official FXCM page lists three free APIs: FIX (requires USD 5,000 minimum balance, live only), Java API, ForexConnect (C++, C#, Java, Python via `forexconnect` on PyPI; demo or live; connection `www.fxcorporate.com/Hosts.jsp`, login with account username/password, no OAuth). The **REST/socket.io API is being deprecated** per FXCM's own GitHub notice (support "not expected to extend beyond Q1 ... 2023"). A third-party vendor (eSeGeCe, 2026-05-21) states FXCM has deprecated its public trading APIs including ForexConnect; not confirmed by FXCM: **UNVERIFIED**, a human must ask api@fxcm.com. Rate limits: not documented in the pages read. |
| Demo | Trading Station demo with USD 20,000 virtual funds (FXCM Markets page). |
| Costs | Not read: UNVERIFIED. |
| Registry status | **ELIGIBILITY_UNKNOWN** (also flagged: unregulated contracting entity, API continuity in doubt). |

Sources: https://www.fxcm.com/markets/help/new-accounts-is-fxcm-markets-regulated ; https://www.fxcm.com/markets/algorithmic-trading/api-trading ; https://www.fxcm.com/markets/algorithmic-trading/market-data ; https://www.fxcm.com/markets/platforms/trading-station/free-demo ; https://github.com/fxcm/ForexConnectAPI ; https://github.com/fxcm/fxcmrest-php ; https://www.esegece.com/community/blog/fxcm-api-removed-from-sgcwebsockets (third-party, context only)

---

## 6. Other providers with a documented API

- **Deriv**: covered in 1.5 (own API documented; Guinea eligibility unknown).
- **Tradier** and others: seen only on comparison sites claiming Guinea acceptance; not forex/CFD providers in scope and not verified. Not retained.
- No other Forex/CFD provider with a documented API was found that **officially** lists Guinea as accepted. Interactive Brokers is the only venue in this dossier with a positive, official, country-specific listing.

---

## 7. Comparison matrix

| Venue | Contracting entity (for Guinea) | Regulator verified in register? | Guinea eligibility | Official API | Demo / paper | Auth | Rate limits | Key published costs | Proposed registry status |
|---|---|---|---|---|---|---|---|---|---|
| cTrader Open API (Spotware) | n/a (vendor); broker-dependent | n/a | Broker-dependent | Open API, Protobuf/JSON, TCP/WebSocket | Yes (demo accounts) | OAuth 2.0, scopes `accounts` / `trading` | 50 req/s, 5 req/s historical, per connection | API free | OFFICIAL_API_VERIFIED |
| IC Markets Global | Raw Trading Ltd (SC) | Entity on FSA list: yes; SD018 number: not read | Not named in non-exhaustive list | via cTrader Open API / MT5 | Yes, free | via platform | via platform | cTrader USD 3 per 100k per side; MT Raw USD 3.50/lot/side | ELIGIBILITY_UNKNOWN |
| Pepperstone | UNVERIFIED (Kenya CMA / Bahamas SCB / Seychelles) | Seychelles entity on FSA list: yes; others not read | Negative signal (Guinea excluded for affiliates) | via cTrader / MT5 | Yes | via platform | via platform | not read | ELIGIBILITY_UNKNOWN |
| FxPro | UNVERIFIED (SCB / Invemonde SC) | Invemonde on FSA list: yes; others not read | Not named in non-exhaustive list | via cTrader / MT5 | Yes | via platform | via platform | not read | ELIGIBILITY_UNKNOWN |
| Deriv | UNVERIFIED (BVI / SVG / V / FX / Mauritius) | No (licence PDF only) | No official list found | Deriv API (options), cTrader, MT5 | Yes, default demo | OAuth 2.0 or PAT + OTP for WS | not found | not read | ELIGIBILITY_UNKNOWN |
| MetaTrader5 Python package | n/a (MetaQuotes); broker-dependent | n/a | Broker-dependent | IPC to local MT5 terminal, Windows only | Broker-dependent | Broker login/password | none documented | free package | OFFICIAL_API_VERIFIED |
| Exness (MT5) | UNVERIFIED (SC / B.V. / VG / MU) | Exness (SC) on FSA list: yes (address mismatch); others not read | Not named in non-exhaustive list | MT5 only | Yes | MT5 login | n/a | not read | ELIGIBILITY_UNKNOWN |
| Interactive Brokers | Likely IB LLC (UNVERIFIED) | IB LLC on FINRA BrokerCheck: yes (CRD 36418); NFA not read | **Listed as available country** | TWS API (socket), Web API (REST/WS) | Paper after funded live IBKR Pro account | CP Gateway + username/password (individual); OAuth for orgs | Web API 10 req/s global + per-endpoint | FX 0.20 bp, min USD 2/order | REGION_ELIGIBILITY_VERIFIED (documentary) |
| OANDA | OANDA Global Markets Ltd (BVI) would be the only candidate | No (BVI FSC not read) | Not listed by any division | v20 REST excluded for OGM | Practice (other divisions) | Bearer token | 120 req/s/IP, 20 streams | not read | BLOCKED |
| FXCM | Stratos Global LLC (SVG, unregulated) | Entity unregulated; FCA sister not read | Not excluded by name, no positive listing | ForexConnect / Java / FIX; REST deprecated; continuity doubtful | Yes (Trading Station demo) | Username/password | not documented | FIX needs USD 5,000 balance | ELIGIBILITY_UNKNOWN |

---

## 8. Recommendation: the "second Forex broker"

Criteria used (as instructed): access from Guinea, costs, API quality, demo/paper, ease of validation. Minimum deposit and leverage were **not** used.

**Recommendation: Interactive Brokers (target entity to be confirmed, expected Interactive Brokers LLC), via the TWS API through IB Gateway, with the Client Portal Web API as an alternative.**

Reasons:
1. **Access from Guinea**: it is the only venue in this dossier where an official provider page names Guinea as an accepted country of residence. Every cTrader/MT5 broker checked uses non-exhaustive exclusion lists, so their eligibility cannot be stated from public evidence; OANDA is blocked; FXCM's international entity is unregulated and its API future is uncertain.
2. **Regulation**: IB LLC is verifiable in a primary US register (FINRA BrokerCheck CRD 36418), which is stronger than offshore entities whose licence numbers could not be read in the registers.
3. **API quality**: two official, documented, actively maintained APIs (TWS API, Web API) with published rate limits, official SDKs, and a clear session model. This is a different integration family from cTrader, which tests ASTRA's multi-broker contract on a genuinely different adapter.
4. **Costs**: published spot FX commission (0.20 bp, min USD 2) with direct interbank-style quotes; API use is free.
5. **Paper account**: full paper trading environment, though only after a funded live account (see caveats).

Caveats that make it harder to validate than cTrader:
- The paper account and the Web API require a **fully open and funded IBKR Pro** account: there is no free standalone demo. Validation therefore requires a real, funded account first.
- Proof of address: if Guinea is not an FATF member, Guinean bank statements will not be accepted as proof of address; another document type will be needed.
- The TWS API needs a long-running TWS or IB Gateway process with periodic re-authentication; the Web API for individuals needs the local Client Portal Gateway and an interactive login. Unattended operation must be designed around that.
- Spot FX at IB LLC is not a CFD; IB-issued CFDs for a Guinea resident are unconfirmed.

Fallback if IB cannot be used: a cTrader or MT5 broker with an African entity (IC Markets Global first, given published low costs and an entity listed by the FSA Seychelles; Deriv second), but **only** after written confirmation of Guinea acceptance. Pepperstone should be deprioritised given its own exclusion of Guinea for partners. OANDA should not be pursued.

## 9. What a human must still verify

1. **Interactive Brokers**: start (do not submit) the application with country of residence "Guinea" and record which IBKR entity is shown; obtain written confirmation from IBKR Client Services that a Guinea resident can open an IBKR Pro individual account, trade spot FX via API, and open a paper account after funding; confirm accepted proof-of-address documents for Guinea; confirm accepted funding currencies/methods from Guinea; confirm whether CFDs are available; read TWS API pacing limits; check NFA BASIC for IB LLC.
2. **cTrader/MT5 brokers (IC Markets Global, Deriv, FxPro, Exness, Pepperstone)**: written confirmation from each broker's compliance/support that residents of the Republic of Guinea are accepted, by which legal entity, and that cTrader Open API / MT5 algorithmic access is permitted for that entity; full restricted-country list from the client agreement; licence numbers checked directly in the FSA Seychelles, SCB, BVI FSC, CMA Kenya and FSC Mauritius registers; clarify IC Markets' company number discrepancy (8419879-1 vs 8419879-2) and Exness' register address mismatch.
3. **FXCM**: written answer from api@fxcm.com on whether ForexConnect remains supported for Stratos Global LLC clients, and whether Guinea residents are accepted; note that the entity is unregulated.
4. **OANDA**: only if desired, ask OANDA whether any division accepts Guinea residents with v20 API access (current evidence says no).
5. **Local law**: whether Guinean foreign-exchange regulation (BCRG) restricts residents from holding offshore brokerage accounts or transferring funds abroad was **not researched** and must be checked by a human, ideally with local legal advice.
6. **FATF**: confirm the October 2026 plenary outcome and Guinea's FATF/GIABA status on fatf-gafi.org.

## 10. Sources (all accessed 2026-10-26)

Regulators and registers
- https://fsaseychelles.sc/regulated-entities/capital-markets
- https://brokercheck.finra.org/firm/summary/36418
- https://files.brokercheck.finra.org/firm/firm_36418.pdf
- https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html
- https://www.fatf-gafi.org/en/countries.html
- https://ifsca.gov.in/CommonDirect/ViewFile?fileName=PRESS_RELEASE___FATF_Public_Statement___19_June_2026__1__20260717_0238.pdf&id=29709e474fe6b066fc0cb19f7d6ad0b1 (reproduces FATF 19 June 2026 statement)

Spotware / cTrader
- https://help.ctrader.com/open-api/
- https://help.ctrader.com/open-api/account-authentication/
- https://help.ctrader.com/open-api/terms-of-use/
- https://help.ctrader.com/open-api/api-application/
- https://help.ctrader.com/open-api/faq/
- https://help.ctrader.com/open-api/python-SDK/python-sdk-index/
- https://openapi.ctrader.com/

MetaQuotes / MT5
- https://www.mql5.com/en/docs/python_metatrader5
- https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py
- https://pypi.org/project/MetaTrader5/
- https://www.metatrader5.com/en/metaeditor/help/development/python

IC Markets Global
- https://www.ic.com/en/company/regulation
- https://www.icmarkets.com/global/en/trading-accounts/ctrader-raw
- https://www.ic.com/en/trading-pricing/spreads
- https://www.icmarkets.sc/en/trading-accounts/raw-spread-account
- https://cdn.icmarkets.com/uploads/terms_and_conditions_of_business_for_trasnsferable_securities.pdf

Pepperstone
- https://partnership.pepperstone.com/en/affiliates-program
- https://pepperstone.com/en-af/help-and-support/opening-an-account/is-pepperstone-licensed-and-regulated
- http://pepperstone.com/en/help-and-support/opening-an-account/is-pepperstone-licensed-and-regulated
- https://pepperstone.com/en-af/go/ctrader

FxPro
- https://www.fxpro.com/about/licences

Deriv
- https://deriv.com/terms-and-conditions/general-terms-of-use
- https://docs.deriv.com/tnc/general-terms.pdf
- https://deriv.com/regulatory
- https://docs.deriv.com/regulatory/Deriv_(BVI)_Ltd.pdf
- https://deriv.com/help-centre/deriv-ctrader
- https://developers.deriv.com/docs/intro/api-overview
- https://developers.deriv.com/docs/options/websocket
- https://developers.deriv.com/docs/workflows

Exness
- https://www.exness.com/regulation
- https://www.exness.com/contact
- https://get.exness.help/hc/en-us
- https://get.exness.help/hc/en-us/articles/5714316735378-Can-I-verify-an-Exness-account-from-a-foreign-country
- https://www.exness.com/cdn/media/exnesssc/exness_sc_client_agreement.pdf

Interactive Brokers
- https://www.interactivebrokers.com/en/accounts/open-account-country-list.php
- https://www.interactivebrokers.com/en/general/what-you-need-inv.php
- https://www.interactivebrokers.com/en/accounts/fees/fxcoms.php?path=3
- https://www.interactivebrokers.ie/en/index.php?ex=eu&f=38596
- https://www.interactivebrokers.com/lib/cstools/faq/#/content/1169367415
- https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/
- https://www.interactivebrokers.com/docs
- https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/limitations/paper-trading
- https://interactivebrokers.github.io/cpwebapi

OANDA
- https://help.oanda.com/bvi/en/faqs/eligible-ogm-countries-bvi.htm
- https://www.oanda.com/region-selector
- https://www.oanda.com/group/where-we-are
- https://developer.oanda.com/rest-live-v20/introduction
- https://developer.oanda.com/rest-live-v20/development-guide/
- https://developer.oanda.com/mt4-live/api-comparison
- https://partners.oanda.com/assets/documents/1407/ogm_affiliate_country_list_12.04.2023.pdf

FXCM
- https://www.fxcm.com/markets/help/new-accounts-is-fxcm-markets-regulated
- https://www.fxcm.com/markets/algorithmic-trading/api-trading
- https://www.fxcm.com/markets/algorithmic-trading/market-data
- https://www.fxcm.com/markets/platforms/trading-station/free-demo
- https://github.com/fxcm/ForexConnectAPI
- https://github.com/fxcm/fxcmrest-php
- https://www.esegece.com/community/blog/fxcm-api-removed-from-sgcwebsockets (third-party, context only, not evidence)

Internal
- BROKERS.md (this repository, cTrader adapter notes read 2026-10-25)
