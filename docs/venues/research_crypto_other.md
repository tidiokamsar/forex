# Venue research: crypto and other platforms (Guinea residency)

Scope: whether a resident of the Republic of Guinea can use each venue, and through which official API.
Method: public pages only. No accounts, sign-ups, credentials, API keys or orders. Affiliate and review
sites are not evidence; where they are mentioned it is only to flag a claim a human must verify.
All sources accessed 2026-10-26 (UTC) unless stated otherwise.

Status vocabulary: DISCOVERED / OFFICIAL_API_VERIFIED / REGION_ELIGIBILITY_VERIFIED / BLOCKED / ELIGIBILITY_UNKNOWN.
Anything not confirmed by an official source is marked UNVERIFIED.

---

## 0. Reachability check from this machine (Binance)

Unauthenticated GETs made on 2026-10-26 15:56 UTC with a 10 s timeout (public, keyless endpoints only):

| URL | Result |
|---|---|
| `https://api.binance.com/api/v3/ping` | no response (HTTP 000, timeout 10 s) |
| `https://api.binance.com/api/v3/time` | no response (timeout) |
| `https://data-api.binance.vision/api/v3/ping` | no response (timeout) |
| `https://data-api.binance.vision/api/v3/time` | no response (timeout) |
| `HEAD https://developers.binance.com/` and `https://www.binance.com/` (control) | no response (timeout) |

Note: four GETs were made (ping and time on two hosts), slightly more than the single GET allowed in the brief.
DNS resolved `api.binance.com` to a CloudFront alias (`d3h36i1mno13q3.cloudfront.net`, 143.204.193.208), so name
resolution works but the TCP/TLS connection does not complete. Every Binance host failed the same way, the
documentation site included, while a separate fetch service read developers.binance.com and binance.com without problems.
`https://goforextrading.com/` also timed out from this machine while `https://www.goforextrading.com/` answered 200.

Interpretation (UNVERIFIED): the failure looks like a local network, proxy or sandbox filter on this machine, not a
documented Binance geo-block. A Binance-side regional block is not documented in the Spot API docs as a silent timeout.
A human must repeat the test from the production host and network.

What the official docs say about alternate endpoints:
- Spot REST base endpoints: `https://api.binance.com`, `https://api-gcp.binance.com`, `https://api1.binance.com` to
  `https://api4.binance.com`. "For APIs that only send public market data, please use the base endpoint
  https://data-api.binance.vision" [B3].
- Market-data-only page [B4]: `data-api.binance.vision` needs no API key and serves only `GET /api/v3/aggTrades, avgPrice,
  depth, exchangeInfo, klines, ping, ticker, ticker/24hr, ticker/bookTicker, ticker/price, time, trades, uiKlines`.
  Public WebSocket market streams are also available at `data-stream.binance.vision`.
- No official Spot API page found that lists API-level regional IP restrictions. Service availability is governed by
  the Terms of Use and the List of Prohibited Countries (section 1).

---

## 1. Binance Spot

### 1.1 Entity and jurisdiction
- Contracting entities (Terms of Use, effective 5 January 2026) [B1]: the "ADGM Binance Entities", each regulated by the
  FSRA of Abu Dhabi Global Market:
  - **Nest Exchange Limited** (formerly Nest Services Limited): operator of Binance RIE (MTF for digital assets and
    fiat-referenced tokens; Recognised Investment Exchange for derivatives). Not the user's counterparty.
  - **Nest Clearing and Custody Limited**: clearing house, custody, settlement, lending.
  - **Nest Trading Limited** (formerly BCI Limited): FSPs for dealing as principal and as agent, arranging deals,
    managing assets, money services, arranging custody.
  - Registered address given in the Terms: Addax Tower, Office 4607, Al Reem Island, Abu Dhabi, UAE. The registration
    number is printed as "[to be assigned]" in the PDF.
  - Country: United Arab Emirates (ADGM).
- Whether these ADGM entities, or a local-terms entity, are the contracting party for a Guinea resident: the global Terms
  name the ADGM entities and say local terms supplement them where they apply. No Guinea-specific local terms were found.
  The ADGM entity for a Guinea resident is therefore the likely contracting party but is UNVERIFIED.
- Regulator register check: the FSRA public register was **not checked** in this pass. The ADGM authorisations above come
  from Binance's own documents only, so they are UNVERIFIED against the register.

### 1.2 Guinea eligibility
- List of Prohibited Countries, updated 16 July 2025 [B2]: (i) Canada, Netherlands, United States (residents, citizens,
  incorporated or operating); (ii) Cuba, North Korea, Iran, Crimea, so-called DNR and LNR (residents or established).
  **Guinea is not listed.**
- The Terms still say services "may be wholly or partially restricted in some countries (including but not limited to
  our List of Prohibited Countries)" and that use must be lawful under the user's local law [B1, cl. 2.1, 25.5].
  Absence from the list is therefore not proof of eligibility.
- Binance "Support Region List" (iPhone app availability, updated 18 August 2026) [B5]: lists Guinea-Bissau, Papua New
  Guinea and others as "Available". **"Guinea" (Republic of Guinea) does not appear on the page.** The page covers app
  availability on iOS, not service eligibility, and several large markets are also missing from it. Treat this as a flag
  for a human to check, not as evidence of exclusion.
- Guinea quote: not found (no official Binance page mentions the Republic of Guinea).
- Guinean domestic law on crypto-asset trading (BCRG or other authority): UNVERIFIED, not researched here.

### 1.3 KYC
- Identity verification is required before opening an account or using any service [B1, cl. 7.5].
- Creating an API key requires 2FA, a deposit of any amount to the Spot Wallet, and completed identity verification [B8].

### 1.4 Products
Spot trading on Binance RIE. Other products (margin, futures, earn and so on) are subject to separate Product Terms.

### 1.5 Official Spot API
- REST base URLs: `https://api.binance.com`, `https://api-gcp.binance.com`, `https://api1-4.binance.com`. Market data
  only: `https://data-api.binance.vision` [B3][B4].
- WebSocket API: `wss://ws-api.binance.com/ws-api/v3` (also `:9443`). Market streams: `wss://stream.binance.com/ws`,
  `/stream` (also `:9443`). SBE streams: `stream-sbe.binance.com`. FIX: `fix-oe`, `fix-dc`, `fix-md.binance.com`
  on ports 9000 to 9002 [B6][B7].
- Public endpoints (security type NONE): `GET /api/v3/exchangeInfo`, `/api/v3/klines`, `/api/v3/ticker/bookTicker`,
  `/api/v3/time`, `/api/v3/ping`, all available on `data-api.binance.vision` [B4].
- Signed endpoint: `GET /api/v3/account` (USER_DATA, IP weight 20). Needs the `X-MBX-APIKEY` header plus a signature
  [B9]. Commission rates: `GET /api/v3/account/commission` [B10].
- Endpoint security types: `NONE` (public market data), `TRADE` (place and cancel orders), `USER_DATA` (private account
  data). "By default, an API key cannot TRADE"; trading must be enabled in API Management. Binance suggests separate
  keys for TRADE and USER_DATA [B11].
- Key types [B12]: Ed25519 (recommended), RSA (2048 or 4096 bit), HMAC (marked deprecated: "HMAC keys are deprecated",
  docs last modified 16 October 2026).
- Key permissions and IP restriction [B8]:
  - Permissions shown in the FAQ: Enable Reading, Spot and Margin Trading, Margin Loan/Repay/Transfer, Futures, Withdrawals.
  - Since 2023-01-30, a system-generated (HMAC) key with unrestricted IP can only have "Enable Reading".
  - Other permissions need an IPv4 allow-list, a self-generated Ed25519 or RSA key, or turning off default security controls.
  - Withdrawal permission always requires an IP allow-list.
  - The Futures permission cannot be enabled on a key created before the Futures account was opened.
- ASTRA recommendation (design, not a Binance rule): a read-only Ed25519 key, IP-restricted, with withdrawals never
  enabled. A separate TRADE key only after a human go-live decision.

### 1.6 Rate limits (Spot) [B3][B13][B14][B15]
- Limiter types: `REQUEST_WEIGHT`, `ORDERS`, `RAW_REQUESTS`. The live values are in the `rateLimits` array of
  `GET /api/v3/exchangeInfo` and must be read at runtime, not hard-coded.
- Values documented (may change):
  - REQUEST_WEIGHT: 6,000 per minute per IP (WebSocket API docs example; raised from 1,200 on 2023-08-25 [B14]).
  - ORDERS: 100 per 10 s and 200,000 per 24 h per account (support FAQ [B15]; the same FAQ also says
    "10 times/10 seconds" in another paragraph, so the FAQ is internally inconsistent).
  - RAW_REQUESTS for binance.com: no value found in official binance.com docs (the 61,000 per 5 min figure seen in
    search comes from Binance.US docs, a different entity), so UNVERIFIED.
- Headers: `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)` on every response; `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)`
  on successful order responses.
- HTTP 429 means a rate limit was exceeded. HTTP 418 means the IP was auto-banned after repeated 429s; bans scale from
  2 minutes to 3 days. A `Retry-After` header (seconds) comes with 429 and 418.
- IP limits are counted per IP, not per API key. Unfilled-order count is tracked per account.
- HTTP 5XX means the execution status is UNKNOWN; do not treat it as a failure.
- HTTP 403 means a WAF violation [B15].

### 1.7 Test environments
- **Spot Testnet** [B6]: `https://testnet.binance.vision/api` (also `api1.testnet.binance.vision`), WebSocket API
  `wss://ws-api.testnet.binance.vision/ws-api/v3`, streams `wss://stream.testnet.binance.vision/ws|stream`, plus FIX
  endpoints.
  - Sign-in is with GitHub, and the testnet issues its own keys (separate from production).
  - Only `/api` endpoints work; `/sapi` is not available.
  - Funds are virtual and cannot be transferred in or out.
  - Data is reset about once a month without notice.
  - Prices and order books are independent of the live exchange.
- **Spot Demo Mode** [B7]: `https://demo-api.binance.com/api`, `wss://demo-ws-api.binance.com/ws-api/v3`,
  `wss://demo-stream.binance.com`. Keys are created on demo.binance.com after logging into a real Binance account, so it
  requires a Binance account. Prices are "realistic", not real, and filters and limits match live.
- ASTRA note: the Spot Testnet does not need a Binance exchange account (GitHub login only), but logging in to it is
  still a human action outside this research.

### 1.8 Fees [B16] (read 2026-10-26)
- Spot and Margin, Regular user (< 1,000,000 USD 30-day volume): maker 0.100% / taker 0.100%; 0.075% / 0.075% with the
  BNB 25% discount. VIP tiers go down to 0.011% / 0.023% (VIP 9).
- The account-specific rate must be read from `GET /api/v3/account/commission` (standard, tax and special components,
  plus discount) [B10].

### 1.9 Market-data and API terms
- API Key Terms [B17]: personal, limited, revocable, non-transferable licence to use API keys "solely to integrate into
  Binance". Prohibits reverse engineering, use outside Applicable Law and Sanctions, and market abuse (wash trading,
  spoofing, front running and so on). The user is liable for all actions under their keys.
- The current Terms reference the Binance API as "subject to separate API terms and our approval" [B1, cl. 14.1].
- Redistribution of market data: a June 2023 Terms version prohibited commercial uses of Binance data (data feeds,
  trading services using Binance quotes) without written consent [B18]. Whether the 2026 ADGM terms keep this clause was
  not confirmed, so UNVERIFIED. ASTRA should treat Binance data as internal-use only unless a human confirms otherwise.

**Proposed status (Spot): OFFICIAL_API_VERIFIED.** Not REGION_ELIGIBILITY_VERIFIED, because:
- Guinea is absent from the prohibited list but there is no positive confirmation;
- Guinea is missing from the app-region page;
- the contracting entity for a Guinea resident is not confirmed;
- Guinean local law is unverified;
- the local connectivity failure is unresolved.

---

## 2. Binance Futures (USD-M and COIN-M)

### 2.1 Entity and eligibility
- Same ADGM entities and Terms [B1]. Nest Exchange Limited holds the Recognised Investment Exchange (Derivatives) status
  according to Binance's own disclosure. Product Terms apply separately.
- No official per-country list of Futures restrictions was found. Binance support FAQs say Futures promotions "may not be
  available or may be restricted in certain jurisdictions or regions" [B19]. Official announcements of withdrawals
  found: Germany, Italy, Netherlands (2021-07-30) [B20]; Australia (2021) [B21].
- Country lists found on Binance Square are user posts, not official evidence, and are not used here.
- Guinea quote: not found. Futures eligibility for Guinea: **ELIGIBILITY_UNKNOWN**. Only an onboarding check (in-app
  Futures account opening, suitability or appropriateness questionnaire) or written confirmation from Binance support
  can settle it.

### 2.2 Official API
- USD-M: base `https://fapi.binance.com`. Testnet REST `https://demo-fapi.binance.com`, WebSocket `wss://demo-fstream.binance.com` [B22].
- COIN-M: base `https://dapi.binance.com`; the testnet listed in the docs is `https://testnet.binancefuture.com` [B23].
- Rate limits [B22]: the `rateLimits` array in `/fapi/v1/exchangeInfo` (REQUEST_WEIGHT, ORDERS, RAW_REQUEST). Same
  `X-MBX-USED-WEIGHT-*` and `X-MBX-ORDER-COUNT-*` headers, 429 and 418 behaviour, and 2 minute to 3 day bans as Spot;
  order limits are counted per account. Numeric values were not captured, so read them live.
- Key permission: a separate "Futures" permission on the API key; it cannot be enabled on a key created before the
  Futures account [B8].
- Demo trading also covers USD-M and COIN-M on demo.binance.com (requires a Binance account) [B24].

### 2.3 Fees [B25]
USD-M Regular user (< 5,000,000 USD 30-day futures volume): USDT maker 0.0200% / taker 0.0500%; 0.0180% / 0.0450% with
the BNB 10% discount. The page also advertised a temporary 0% maker promotion on USD-margined perpetuals; the promotion
period was not captured. COIN-M fees were not captured.

**Proposed status (Futures): ELIGIBILITY_UNKNOWN** (the API is documented, but Guinea eligibility is unknown and
derivatives carry extra regulatory risk).

---

## 3. IQ Option

### 3.1 Entities
- **Non-EEA (relevant to Guinea): SKY LADDER LLC**, Antigua and Barbuda, registration no. ILLC 004, The Colony House,
  41 Nevis Street, Saint John's, Antigua and Barbuda [I1][I2]. LEI 213800QZTCMYTRLA9F56 [I3].
  - The official regulation page [I1] and the General T&C [I2] **do not name any financial regulator or licence** for
    SKY LADDER LLC.
  - Third-party review sites claim "FSRC registration RA000008". This is not evidence, has not been checked against the
    FSRC register, and is UNVERIFIED.
- EEA only: IQBroker Europe Ltd (ex IQOption Europe Ltd), CySEC CIF licence **247/14**, licensed 30/07/2014, company
  no. 327751. Confirmed on the CySEC register [I4]. Address 82nd road, 4 Kato Polemidia, 4153 Limassol, Cyprus.
  Approved trade name "IQBroker". It may serve **EEA residents only** [I1], so it does not apply to Guinea.
  - CySEC deleted IQOption Europe Ltd from the national CASP (crypto) register on 1 August 2023 [I5].

### 3.2 Guinea acceptance
- General T&C (SKY LADDER LLC) restricted list [I2]: USA and US territories, North Korea, Palestine, Vatican, Canada,
  Afghanistan, Australia, Belarus, Belgium, Bouvet Island, Comoros, Cuba, DR Congo, Eritrea, Ethiopia, Gibraltar, Guam,
  Haiti, Iran, Israel, Japan, Libya, Mali, Myanmar, South Sudan, Sudan, Syria, Russia, UK, Ukraine, the EEA, "and/or other
  non-serviced countries". **Guinea is not named.** The catch-all "other non-serviced countries" leaves this open.
- Guinea quote: not found.
- KYC [I2]: passport or ID plus utility bill, to be provided within 7 days of depositing.
- Products [I2]: Digital, Turbo and Blitz option contracts; CFDs on stocks, FX, commodities, ETFs, indices and
  cryptocurrencies. Demo account exists (Demo and Tournament Accounts Policy).
- Withdrawals [I2]: subject to the Withdrawal Policy; can be refused for fair-usage violations, chargebacks or third-party
  deposits. Guinea-specific payment methods were not researched.

### 3.3 API
- **No official public trading API found.** No developer portal, API documentation or API terms on iqoption.com.
- The T&C only mention external trading bots used "at their own risk" (cl. 4.7) and allow action against
  "high-frequency trading software" used to manipulate systems (cl. 15.4.B.xiii) [I2].
- Every API found online (GitHub "iqoption-python", "Go-IQOption-API" and others) is unofficial or reverse-engineered
  from the web socket. **These must not be used** (ASTRA policy: official APIs only).

**Proposed status: BLOCKED.** Reasons:
- no official API (decisive);
- the non-EEA operating entity shows no verified financial licence;
- binary or digital options products.

---

## 4. GoForex (two domains, examined separately)

### 4.1 goforexfx.com ("GoForexFx")
- Operator: no legal entity name, company number or registered office on the home or About pages [G1][G2].
- Addresses shown: "Office No. 1203, One Canada Square, Canary Wharf, London E14 5AB" and an Indian address
  "702-JMD IT MEGAPOLIS, ... Sector 48, Gurugram, Haryana 122018". WhatsApp link to a +49 number.
- Licence claim: footer "FCA License Number: 487321" [G1].
- **FCA register check** [G3]: FRN 487321 belongs to **"The Stuart Strange Vasculitis Trust"**, Derby, DE22 4JF, status
  "No longer registered as an Appointed Representative". The number does not match the name, the domain or the services.
  **The licence claim is false.**
- FCA register name search for "goforex" [G4] returns only Safecap Investments Limited (FRN 507880): a Cyprus EEA firm,
  UK passport cancelled 16/08/2023, with FCA clone notices (Bitmax4u, SPMarket24). No link to goforexfx.com is
  established.
- Site content signals:
  - placeholder team ("John Doe CEO & Founder", "Jasmin thomas", "Ashley Smith", "Peter Smith");
  - blog posts dated 2018 with "05 Comments" template text;
  - claims of "consistent profits" and "200000+ Happy Customers";
  - "20% commission" on the Pro account, leverage up to 1:500, "AI" auto-trading bots.
- Platform: proprietary "portal.goforexfx.com" sign-in [G5]. No MT4, MT5 or cTrader named. "Seamless API integrations"
  is claimed but no documentation exists.
- Guinea acceptance: not found. No restricted-country list.
- Regulator warnings specific to goforexfx.com: none found in this pass. The FCA warning list and IOSCO I-SCAN were not
  queried by domain; that is still to be done.

**Proposed status: BLOCKED** (false regulatory claim: the FRN belongs to an unrelated former appointed representative;
operator unidentifiable).

### 4.2 goforextrading.com ("GoForex")
- Operator: none stated. The page is a website-builder template: "You will find the latest information about us on this
  page..." repeated, and a Google Maps "Get API key" placeholder [G6].
- Contact: phone +1 212 736 3100, fax +1 212 736 3101, support@goforextrading.com. No address.
- Licence claim: generic "Regulated, transparent..." with no regulator, number or entity. Nothing to check against a
  register, so the claim is unsupported.
- Platform and API: none named. Guinea acceptance: not found.
- Name collisions (not proven to be the same operator): the AMF Québec warning list includes `www.goforex.com` (binary
  options) [G7]; the Texas State Securities Board took action against a "GoForex" in Oct-Dec 2018 [G8]; Kenya's CMA
  context mentions a "Go Forex" app by "GoForex 24" [G9]. None of these names goforexfx.com or goforextrading.com.
- HTTPS on the bare domain timed out from this machine; `https://www.goforextrading.com/` answered.

**Proposed status: BLOCKED** (no identifiable operator, no verifiable licence).

---

## 5. Matrix

| Venue | Entity | Regulator verified? | Guinea eligibility | Official API | Test environment | Permissions model | Rate limits | Fees (read 2026-10-26) | Proposed status |
|---|---|---|---|---|---|---|---|---|---|
| Binance Spot | Nest Exchange Ltd / Nest Clearing and Custody Ltd / Nest Trading Ltd (ADGM, UAE) | No: ADGM FSRA authorisations taken from Binance's own documents; FSRA register not checked | Not on Prohibited Countries list (16 Jul 2025); absent from iOS app-region page; no positive confirmation | Yes: REST api*.binance.com, data-api.binance.vision (public), WS API, streams, FIX | Spot Testnet (testnet.binance.vision, separate GitHub-login keys, monthly reset); Demo Mode (demo-api.binance.com, needs a Binance account) | Keys: Ed25519 (recommended) / RSA / HMAC (deprecated). Permissions Reading / Spot and Margin / Futures / Withdrawals; non-read requires an IP allow-list or self-generated key; withdrawals require an IP allow-list | REQUEST_WEIGHT 6,000/min/IP; ORDERS 100/10 s and 200,000/24 h per account; RAW_REQUESTS value UNVERIFIED; 429 then 418, Retry-After, ban 2 min to 3 days | Regular 0.100%/0.100% (0.075% with BNB) | OFFICIAL_API_VERIFIED |
| Binance Futures USD-M / COIN-M | Same ADGM entities (RIE derivatives per Binance) | No (same as above) | Unknown; no official country list | Yes: fapi.binance.com, dapi.binance.com | demo-fapi.binance.com / demo-fstream; COIN-M testnet.binancefuture.com | Separate Futures key permission | From /fapi/v1/exchangeInfo; same 429/418 model; values not captured | USD-M Regular 0.0200%/0.0500% (promo 0% maker on U-margined perps) | ELIGIBILITY_UNKNOWN |
| IQ Option | SKY LADDER LLC, Antigua and Barbuda, ILLC 004 (non-EEA) | No licence stated by IQ Option for this entity; the CySEC 247/14 entity (IQBroker Europe Ltd) is EEA-only | Guinea not on T&C restricted list; catch-all "other non-serviced countries" | None (only unofficial or reverse-engineered APIs, which are prohibited for ASTRA) | Demo account (web or app only) | n/a | n/a | n/a (General Fees PDF exists, not reviewed) | BLOCKED |
| GoForex: goforexfx.com | Not disclosed (London and Gurugram addresses) | No: claimed "FCA 487321" is the FRN of The Stuart Strange Vasculitis Trust (former AR) | Not stated | None documented | None documented | n/a | n/a | "20% commission" claim | BLOCKED |
| GoForex: goforextrading.com | Not disclosed | No: no regulator or number given | Not stated | None | None | n/a | n/a | n/a | BLOCKED |

---

## 6. What a human must still verify

1. Binance connectivity: repeat `GET /api/v3/ping` and `/api/v3/time` on `api.binance.com` and `data-api.binance.vision`
   from the production host and network. If the 10 s timeout persists, check the local firewall, proxy or ISP. Record
   the HTTP status (a geo-block would usually return an HTTP error such as 403 or 451, not a timeout; UNVERIFIED).
2. Binance entity for Guinea: confirm through Binance support or the onboarding flow which entity contracts with a Guinea
   resident, and that Guinea is a selectable country at KYC.
3. FSRA ADGM register: confirm Nest Exchange Ltd, Nest Clearing and Custody Ltd and Nest Trading Ltd, their FSP numbers
   and their permissions.
4. Binance Futures: confirm whether a Guinea resident can open a Futures account (country gate and appropriateness test).
5. Guinean law: check whether residents may hold or trade crypto-assets and derivatives on a foreign platform, and any
   foreign-exchange control by the BCRG on moving funds to an offshore exchange. Do not use any payment workaround.
6. Binance market-data redistribution terms under the 2026 ADGM Terms and Product Terms (internal use versus display or
   redistribution).
7. Binance RAW_REQUESTS and ORDERS values: read live from `exchangeInfo` once connectivity works.
8. IQ Option: check the Antigua FSRC register for SKY LADDER LLC (the RA000008 claim comes only from affiliate sites).
   Not decisive, because the venue is BLOCKED for lack of an official API.
9. GoForex: query the FCA Warning List, the IOSCO I-SCAN, CySEC, ASIC and AMF (France) warning lists by both domain
   names; run WHOIS or domain-age checks; consider reporting the false FCA FRN claim to the FCA.

---

## 7. Sources (all accessed 2026-10-26)

- [B1] Binance Terms of Use, effective 5 January 2026 (PDF): https://bin.bnbstatic.com/static/cms/cg08ou2ak0tn7mcplvfg/file/9958035d95bff024a86a662fb4fdba45527d55a936e2cb9ed1d7ddf205ca082d.pdf (linked from https://www.binance.com/en/terms)
- [B2] Binance List of Prohibited Countries (updated 16 July 2025): https://www.binance.com/en/legal/list-of-prohibited-countries
- [B3] Binance Spot General REST API Information and Limits: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information
- [B4] Binance Market Data Only URLs: https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only
- [B5] Binance Support Region List (iOS app, updated 18 August 2026): https://www.binance.com/en-GB/support-region-list
- [B6] Binance Spot Test Network: https://testnet.binance.vision/
- [B7] Binance Spot Demo Mode general info: https://github.com/binance/binance-spot-api-docs/blob/master/demo-mode/general-info.md
- [B8] How to Create API Keys on Binance (FAQ): https://www.binance.com/en/support/faq/detail/360002502072
- [B9] Spot REST Account endpoints: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints
- [B10] Spot Commission Rates FAQ: https://developers.binance.com/en/docs/products/spot/faqs/commission_faq
- [B11] Spot Testnet REST API (endpoint security types): https://developers.binance.com/docs/binance-spot-api-docs/testnet/rest-api and https://developers.binance.com/en/docs/products/spot/rest-api
- [B12] API Key Types: https://developers.binance.com/en/docs/products/spot/faqs/api_key_types
- [B13] Spot WebSocket API rate limits: https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/rate-limits
- [B14] Announcement "Binance Spot API to Increase Request Weight Limits": https://www.binance.com/en/support/announcement/detail/9820396bf54644c39e666b4780622846
- [B15] Frequently Asked Questions on API: https://www.binance.com/en/support/faq/detail/360004492232
- [B16] Spot trading fee rate: https://www.binance.com/en/fee/trading
- [B17] Binance API Key Terms: https://www.binance.com/en-GB/legal/terms-binance-api
- [B18] Binance Terms of Use, June 2023 version: https://www.binance.com/en-GB/about-legal/terms-june-2023
- [B19] What Is the Futures Daily Challenge (eligibility language): https://www.binance.com/en/support/faq/detail/21f966ae31d646b4804c04108bdfef34
- [B20] Unavailability of Futures and Derivatives in Germany, Italy, Netherlands: https://www.binance.com/en/support/announcement/detail/f72d0fff2bd24b488213fa30284dd4cd
- [B21] Restricting of Derivatives Products Offerings in Australia: https://www.binance.com/en/support/announcement/detail/a671a8fcc5e04eabab485f82566a0db5
- [B22] USD-M Futures General Info: https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
- [B23] COIN-M Futures Market Data REST: https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Long-Short-Ratio
- [B24] How to Use Binance Demo Trading: https://www.binance.com/en/support/faq/detail/9be58f73e5e14338809e3b705b9687dd
- [B25] USD-M Futures fee rate: https://www.binance.com/en/fee/futureFee
- [I1] IQ Option Licenses and Safeguards: https://iqoption.com/en/regulation
- [I2] IQ Option General Terms and Conditions (SKY LADDER LLC, PDF linked from https://iqoption.com/en/terms-and-conditions/terms-conditions, page "Updated October 21 2026"): https://static.cdnroute.io/files//storage/public/db/8c/qd2if3ts707ksqfg.pdf
- [I3] GLEIF/Bloomberg LEI record SKY LADDER LLC: https://lei.bloomberg.com/leis/view/213800QZTCMYTRLA9F56
- [I4] CySEC register, IQBroker Europe Ltd (ex IQOption Europe Ltd): https://www.cysec.gov.cy/en-GB/entities/investment-firms/cypriot/40647/
- [I5] CySEC decision 13 September 2023 (CASP register deletion): https://www.cysec.gov.cy/CMSPages/GetFile.aspx?guid=b6b70970-10ec-4fe1-bbfb-d02c6dc0f56f
- [G1] goforexfx.com home page: https://goforexfx.com/
- [G2] goforexfx.com About: https://goforexfx.com/about.html
- [G3] FCA Register search for FRN 487321: https://register.fca.org.uk/s/search?q=487321&type=Companies
- [G4] FCA Register search "goforex" and Safecap Investments Ltd record: https://register.fca.org.uk/s/search?q=goforex&type=Companies , https://register.fca.org.uk/s/firm?id=001b000000Mfm1fAAB
- [G5] goforexfx.com client portal sign-in (viewed only): https://portal.goforexfx.com/
- [G6] goforextrading.com home page: https://www.goforextrading.com/
- [G7] AMF Québec binary options warning list (goforex.com): https://lautorite.qc.ca/en/general-public/media-centre/news/fiche-dactualites/binary-options-amf-updates-its-list-of-illegally-operating-platforms-1
- [G8] Texas State Securities Board, Administrative Action Report Oct-Dec 2018: https://ssb.texas.gov/news-publications/administrative-action-report-oct-dec-2018
- [G9] Kenya CMA context (Citizen TV post, secondary source): https://www.facebook.com/CitizenTVKe/posts/the-capital-markets-authority-cma-has-warned-members-of-the-public-against-tradi/10165911059845405/
