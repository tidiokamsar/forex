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
