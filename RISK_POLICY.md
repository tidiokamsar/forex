# Initial simulation risk policy

The initial configured values are **indicative software test parameters only**. They are not trading advice, a universal recommendation, or a guarantee against loss.

| Control | Default | Action |
|---|---:|---|
| Risk budget per accepted simulated position | 0.25% of current simulated equity | Size is calculated from entry-to-stop distance; larger requested sizes are rejected |
| Daily realized loss limit | 1% of event-day starting equity | Reject additional entries at or beyond the limit |
| Simultaneous open positions | 1 | Reject an entry while one position is open |
| Maximum data age | 120 seconds | Reject stale, negative, or unknown age |
| Maximum spread | 3.0 pips | Reject excessive or unknown spread |
| Allowed instrument in first slice | EURUSD | Reject other instruments until specifications are reviewed |

Each intent also requires a verified SIMULATION context, known instrument specifications, available margin, a valid stop geometry, and an inactive kill switch. The backtest supplies those verification/specification/margin flags as explicit simulation assumptions; they do not verify a real account, contract specification, or broker margin. The direct RiskEngine contract separately rejects false or malformed safety flags. The simulator assumes account currency equals quote currency and does not apply contract minimums, leverage, conversion, financing, or broker margin rules. Daily loss is based on realized simulation P&L in this first lot.

## Order Gateway checks (lot 2)

Paper-replay orders, and any future connector order, pass through `execution.OrderGateway`, which adds to the RiskEngine:

- verified mode: configuration, command, adapter and account report must agree; `LIVE` is always blocked;
- account reference match, quote freshness and spread from the adapter's own quote;
- kill switch and entry freezes;
- day start equity per account and UTC day, so the daily loss uses account equity including open-position mark-to-market;
- open positions plus pending entry orders against the position limit;
- contract size rounded **down** to the illustrative step, with minimum and maximum checks;
- required margin versus free margin; an unknown currency conversion blocks.

Exits must reduce an existing position. Hitting the daily loss limit freezes entries for that account until the next UTC day and writes an `alert.risk_daily_loss_limit` audit event. The freeze and its day marker are per account (`OrderGateway(account_ref=...)`); other accounts keep trading, while the kill switch stays global. No martingale or loss-recovery sizing exists: size always comes from the fixed risk fraction and the stop distance.

## Changing limits

`governance.approve_risk_policy_change` implements a two-person rule: a `HUMAN_ADMIN` requester, a distinct human reviewer and a written reason. The proposed policy must still pass the RiskEngine's hard caps, and the approval is audited. Agents, strategies, models and LLMs are refused, and the refusal is audited.

Models and strategies cannot raise these limits or expand the instrument allowlist. Current maxima are code constants; a caller may request stricter limits, and the engine snapshots any supplied allowlist into an immutable tuple. There is no runtime risk-policy editor. Any later critical-limit change requires separate human review, versioning, an audit event, and negative tests. LIVE is not supported or configurable.

The research-integrity floors of `research_policy` (holdout reuse thresholds, minimum DSR returns and trades, PBO information floors) are a separate, provisional set of engineering parameters, also fixed in code; see [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md). They are not risk limits and not calibrated statistical thresholds.

## V5 Portfolio Risk Engine (5.9): status

| Dimension | Status |
|---|---|
| Risk per trade from stop distance | Implemented (0.25% cap) |
| Daily loss limit | Implemented (1% cap, per account, realized in the backtest, mark-to-market in the gateway) |
| Weekly loss, drawdown and time under the last peak | Not implemented |
| Exposure per instrument and per currency, correlation between positions | Not implemented (one position, EURUSD only) |
| Margin and leverage | Partial: required versus free margin with illustrative specs; no leverage schedule, margin call or forced close |
| Spread and data age | Implemented (3 pips, 120 s caps) |
| Limits per account, broker and strategy | Partial: freezes and daily loss per account; no per-broker or per-strategy limits |
| Gap, weekend and event risk | Not implemented |
| Global circuit breaker and per-account freezes | Implemented (kill switch global, freezes per account) |
| Martingale, loss-recovery sizing, stop removal | Absent by design; size always comes from the fixed fraction and the stop |
| Kelly sizing | Not implemented (by design) |

A dimension that cannot be computed is not yet systematically blocking: the engine blocks unknown specs, unknown quote age or spread, unknown margin conversion and an unverified mode, but the unimplemented dimensions above are simply not evaluated. All values were exercised on the simulator with synthetic data only.

## Per-market risk policies (multi-venue lot)

`astra.markets.risk_policies` holds one policy per market type and `astra.markets.costs` one cost model per market type. Rules are not shared between markets unless their assumptions match. Every decision is a `MarketRiskDecision` (ALLOW or REFUSE, reason codes, policy name and **policy version**). The modules are pure: they import no gateway, permit or broker adapter, and nothing in this lot can send a crypto order. The only order path stays `OrderGateway` -> `ExecutionPermit` -> `BrokerAdapter.submit_order`.

### Forex / CFD

The existing `RiskEngine` and `OrderGateway` checks above remain the authority and are unchanged. `ForexCfdPreCheck` (version `forex-cfd-precheck-1`) is a contract-data pre-check that is necessary, never sufficient. It refuses:

- an ILLUSTRATIVE `ForexCfdSpec` outside SIMULATION, and `LIVE` in every case;
- outside SIMULATION: unknown swap (long, short or unit), unknown commission or commission currency, unknown account currency, or a missing conversion rate for any currency the position touches (quote, margin, commission, currency-denominated swap) that is not the account currency;
- in every mode: an ambiguous lot conversion (contract size not a multiple of the unit step, or requested lots that do not map to a whole step) and requested lots outside the contract limits.

Costs reuse `CostModel`; `forex_cost_model` derives a per-unit commission only when the commission basis (`PER_SIDE` or `ROUND_TURN`) is explicit and the commission currency is the quote currency.

### Crypto spot

`CryptoSpotRiskPolicy` (version `crypto-spot-1`, frozen). Values are **provisional engineering parameters**, not calibrated and not advice; a policy may be made stricter, never looser than these ceilings:

| Control | Ceiling | Action |
|---|---:|---|
| Notional per BUY order | 2% of free quote balance | Refuse (no resize) |
| BUY notional per UTC day (including this order) | 10% of free quote balance | Refuse; an unknown day total refuses |
| Quote (bookTicker) age | 5 s | Refuse stale, future-dated or unknown |
| Symbol spec and status age | 300 s | Refuse |
| Balance age | 30 s | Refuse |
| Fee schedule age | 24 h | Refuse |
| Slippage assumption | 0.05% per side, `UNCALIBRATED` | Used in net expectancy |
| Leverage | 1 (fixed) | No other value can be configured |
| Shorting | Not allowed (fixed) | SELL limited to the free base balance |

Also refused: any mode other than SIMULATION (ALLOW is reachable only there in this lot; `LIVE` is always refused), kill switch active or unknown, borrowing enabled or unknown, symbol status other than `TRADING`, symbol mismatch, unknown or missing base/quote balance (a missing asset is unknown, not zero), insufficient free quote for the BUY, unknown or mismatched fees, a fee asset that cannot be valued or is not held, a BUY without a stop below the price, a BUY without a strategy edge, and a non-positive net expectancy (edge minus two worst-rate fees, one spread and two slippage legs). Quantity and price are rounded down through `CryptoSpotSpec` and then checked with `order_violations` (step, tick, minimum and maximum quantity, minimum notional).

Notional caps apply to BUY orders, which open or add exposure; a SELL only reduces holdings and is bounded by the free base balance.

Martingale and loss recovery are refused by construction: after a negative realized day P&L, a BUY larger than the previous BUY is refused (`risk_increase_after_loss`); a BUY while the strategy position is below its average entry is refused (`adding_to_losing_position`); `evaluate_stop_change` refuses stop removal and widening and allows only tightening. The policy has no size multiplier.

### Crypto futures / perpetuals and fixed-expiry options

`CryptoFuturesRiskPolicy` and `FixedExpiryOptionsRiskPolicy` always REFUSE with `market type not enabled: requires derivatives risk review`, in every mode, consistent with `ENABLED = False`. The funding model (`FundingCostAssumption`) is a data structure only. A future derivatives review must settle at least:

- futures: initial and maintenance margin (per tier, provider-sourced), mark and index price sources and staleness, funding sign convention and cost, liquidation distance with a buffer (ASTRA never computes a liquidation price), margin mode (isolated or cross), cross-contract and cross-venue exposure, leverage schedule, auto-deleverage, position mode and reduce-only semantics;
- fixed-expiry options: expiry schedule and settlement source, payout and premium terms including the tie rule, premium budget, product restrictions or prohibitions for retail clients in the user's jurisdiction, expected value net of the payout asymmetry, early close and requote rules.
