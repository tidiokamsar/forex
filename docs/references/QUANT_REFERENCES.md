# Quantitative references (PBO / CSCV, deflated Sharpe, PSR)

Recorded 2026-10-25 for lot 4 (quantitative integrity). This file lists exactly what was read, from
which copy, and what could not be accessed. Summaries are paraphrased; equation numbers refer to the
copies listed.

## Read

### 1. Bailey, Borwein, Lopez de Prado, Zhu, "The Probability of Backtest Overfitting"

- Copy read: author-hosted PDF, https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
  ("Revised version: February 2015"; the SSRN abstract 2326253 version). Read in full as extracted text.
- Not read: the typeset Journal of Computational Finance (2017) version (paywalled). Differences
  between the two were not checked.
- Sections used:
  - 2.1, Definitions 2.1 and 2.2: overfitting means the IS-optimal strategy has an expected OOS rank
    below the median; PBO is the probability of that event.
  - 2.2, Algorithm 2.3 (CSCV), steps:
    - First: matrix M of order T x N, one column of P&L per trial. Rows must be synchronous across
      trials, and "if different model configurations trade with different frequencies, observations
      should be aggregated to match a common index". The selection metric must be computable on
      sub-samples of each column.
    - Second: partition rows into an even number S of disjoint submatrices of equal size T/S.
    - Third: all C(S, S/2) combinations (eq. 2.3).
    - Fourth (a-g): training set J = the S/2 blocks joined in original order, testing set = the
      complement in original order (the paper notes order matters for metrics such as a
      return / maximum-drawdown ratio); performance vectors IS and OOS with the same statistic;
      n* = best IS; relative rank w = rank_OOS(n*) / (N + 1); logit lambda = ln(w / (1 - w)).
    - Fifth: distribution of the logits over all combinations (eq. 2.4).
  - 3.1: PBO estimate phi = integral of f(lambda) from -inf to 0.
  - 3.2: performance degradation and "probability of loss" Prob[OOS performance of n* < 0].
- Ties: the paper does not specify how to treat ties in ranks.

### 2. Bailey and Lopez de Prado, "The Deflated Sharpe Ratio"

- Copy read: author-hosted PDF, https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
  ("This version: July 31, 2014", Journal of Portfolio Management, forthcoming; SSRN 2460551).
  Read in full as extracted text; the equation glyphs did not survive extraction.
- Sections used: "Expected Sharpe ratios under multiple trials" (eq. 1, expected maximum
  SR0 = sqrt(V[SR_n]) * ((1 - gamma) Z^-1[1 - 1/N] + gamma Z^-1[1 - 1/(N e)])), "The deflated Sharpe
  ratio" (eq. 2, DSR = Z[(SR - SR0) sqrt(T - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2)], with V the
  variance across the trials' estimated SR, N the number of independent trials, g3 skewness and g4
  kurtosis of the selected strategy's returns), "A numerical example", Appendix A.1 (derivation of
  eq. 1, including the Python snippet `getExpMaxSR`), Appendix A.3 (number of independent trials).
- Numerical example: the prose survived extraction (N = 100, T = 1250 daily observations, 250 per
  year, annualized SR 2.5, "only a 90% chance", N = 46 gives 0.9505, N = 88 with normal returns), the
  inputs V = 1/2 (annualized), skewness -3, kurtosis 10 and the outputs SR0 = 0.1132 and DSR = 0.9004
  did not. They were taken from a secondary source that quotes them (holdoutlabs-io/holdout-audit,
  docs/METHODS.md on GitHub, seen in search excerpts only) and then reproduced independently by
  `astra.overfitting.deflated_sharpe_from_moments` (SR0 0.113172, DSR 0.900397; N = 46 gives 0.950502
  and N = 47 gives 0.949404; with normal returns N = 88 gives 0.950491 and N = 89 gives 0.949839),
  which agrees with every number stated in the paper's text.
  This is tested in `tests/test_overfitting.py::DeflatedSharpePaperExampleTest`.

### 3. Bailey and Lopez de Prado, "The Sharpe Ratio Efficient Frontier" (Journal of Risk, 2012)

- Copy read: author-hosted PDF, https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf
  (SSRN 1821643), through search-style excerpts only (not the full text).
- Used: the PSR implementation snippet
  `norm.cdf((sr - sr_ref) * (obs - 1) ** 0.5 / (1 - sr * skew + sr ** 2 * (kurt - 1) / 4.) ** 0.5)`
  and `get_Stats`, which computes kurtosis as `m4 / m2 ** 2` (non-excess, normal = 3). This confirms
  the `(kurt - 1) / 4` term uses non-excess kurtosis and that the sample length enters as `T - 1`.
  The hedge-fund example (non-annualized SR 0.458, skewness -2.448, kurtosis 10.164) uses the same
  convention.

## Not accessed

- The typeset journal versions (J. Computational Finance 2017; J. Portfolio Management 2014;
  J. Risk 2012).
- Mertens (2002) and Lo (2002), cited by the DSR paper for the Sharpe-ratio standard error; the
  formula was checked against the PSR snippet above instead.
- Harvey and Liu (2014), the alternative threshold the DSR paper mentions.

## How ASTRA maps to these definitions

See `RESEARCH_PROTOCOL.md`, "Overfitting statistics", and the module docstring of
`src/astra/overfitting.py`. Deviations from the papers, kept on purpose:

- Blocks may differ by one row (floor bounds) instead of exactly T/S; within one half every variant
  is scored on the same rows, so the ranks are unaffected.
- Ties: OOS ties take the average rank and `lambda <= 0` counts as overfit (a selection tied with the
  OOS median counts as overfit). IS ties take the tied variant with the worst OOS metric. Both choices
  can only raise the PBO.
- Trials are counted as independent (Appendix A.3's correlation-implied reduction of N is not
  applied), which is conservative.
