# Research and promotion plan

Version 1, frozen 2026-09-30. This is a research protocol, not a capital allocation or permission to submit orders. Its purpose is to determine whether a reproducible strategy can earn a useful return after costs and justify the risks relative to simpler alternatives. Profitability is an objective, not a promised outcome.

## Current evidence and decision

Multi-year studies already exist. The immediate task is to consolidate and reproduce them, repair identified data and execution weaknesses, and collect comparable forward evidence. Re-running inspected history does not create a new holdout.

No current strategy has passed the research-to-live gates below. Continue the existing simulations under their existing rules as controls. Do not automatically replace them, reset their ledgers, or promote a candidate because it leads a short leaderboard.

The [strategy registry](strategy_registry.json) records known candidates, historical periods, costs, limitations and dispositions. It contains public historical research only. Private account information, forward portfolio values, credentials and operational identifiers belong outside this repository.

Material findings from the existing archives:

| Study | Historical finding | Consequence |
| --- | --- | --- |
| Current stock breakout | A short development sample returned 18.53% and an earlier retrospective validation returned 9.40% at 5 bps per side, before operating costs. Extended 2016–22 segmented history included three losing intervals out of seven and substantial dependence on a few winners. | Keep the unchanged observer as a control; do not treat the short positive result as sufficient evidence. |
| Stock intraday alternatives | Same-day variants had only one positive interval out of seven at 10 bps per side including operations, and none at 20 bps. | Deprioritize the tested rules. More trading is not the research objective. |
| Seven-asset monthly momentum | January 2025–September 9, 2026 returned 48.55% cumulatively, or 26.45% CAGR, after modeled costs. Sharpe above BIL was 1.14 versus 1.37 for equal-weight; drawdown was 16.68% versus 8.14%. Earlier momentum Sharpe was 0.28 in 2019–22 and 0.17 in 2023–24. | Prioritize a fair replication and comparison, not parameter optimization of the strongest period. |
| Momentum alpha analysis | Newer-period alpha versus equal-weight was -1.72% annualized, with a 95% HAC interval of -15.71% to +12.27%; estimated beta was about 1.73. | A higher return did not demonstrate alpha. Risk exposure must be matched before claiming added value. |
| Current crypto rule replay | Trend returned -9.55% in 2022, +79.47% in 2023–24 and -19.01% in January 2025–September 9, 2026. Matched buy-and-hold returned -66.04%, +320.37% and -21.44%. Latest-period trend daily drawdown was 33.34%. | Retain as an experimental control. Reduced losses in one regime and a positive short forward sample do not establish a deployable edge. |
| Three-ETF monthly trend | At 10 bps per side including operating costs, 2019–22 returned 8.20% cumulatively and 2023–24 returned 13.44%. The rule lost 12.23% in 2022. | A possible defensive comparator, not proof of consistently attractive returns. |
| Futures basis | Existing work is scenario arithmetic and historical spot stress, not a futures backtest. | Block this research until contract-specific executable prices, margin and financing data exist. No leverage inference is justified. |

Historical periods start independently; their returns must not be concatenated. Sharpe definitions differ between older studies. Results above are quoted from archived research, not newly calculated by this document.

## Work sequence

Each stage ends in an evidence artifact and an explicit pass, fail or insufficient-evidence decision. This sequence is not a promise of completion dates or permission to trade.

1. **Establish a trustworthy record.** Archive available daily reports, signal inputs, fills, version identifiers and hashes privately. Reconcile missing decision dates, stale quotes and reporting gaps. Produce a daily quality report and retain the original files.
2. **Reproduce existing results.** Use isolated run directories and exact code/data versions. Reconcile trade cash flows, dividends, fees and terminal valuation conventions. Resolve the known corporate-action and intraday-coverage issues before presenting affected results as clean.
3. **Run one predeclared comparison.** Evaluate the bounded queue below over common dates, using the same capital convention, clocks, costs and comparison methods. Record every variant and failed run. Do not add variants in response to the leaderboard.
4. **Review independent evidence.** Use nested chronological walk-forward tests, a genuinely uninspected final holdout where available, and prospective observations. Results on already inspected history remain development evidence.
5. **Validate execution.** Only a research-qualified candidate proceeds to an explicitly authorized broker paper pilot. Compare intended orders, actual paper acknowledgements/fills and modeled execution. Shadow simulations alone cannot validate broker execution.
6. **Consider a limited real-money pilot.** Produce a concrete review package, proposed risk limits and stop conditions. Require separate explicit approval and secure brokerage setup. Passing a statistical screen never enables live orders automatically.

The next actionable work is stages 1–2: close the monitoring gaps, build immutable run manifests, reconcile daily return calculations, and reproduce the existing crypto and cross-asset results. Historical data is already available in research archives; it is not included in this public repository.

## Bounded research queue

There are at most three active research tracks. Benchmarks and already-running controls are not additional optimization candidates.

1. **Seven-asset monthly momentum, unchanged.** SPY/EFA/EEM/IEF/GLD/DBC/UUP; rank prior 12-to-1-month total return, select the top two, allocate up to 50% each only when their trailing 12-month total return beats BIL, and otherwise hold that sleeve in BIL. See [cross-asset setup](../CROSS_ASSET_SETUP.md) and [implementation](../paperbot/cross_asset.py). First resolve data/accounting comparability and determine whether returns exceed an exposure-matched passive alternative.
2. **Defensive ETF challenger, fixed before new evaluation.** Monthly inverse-60-session-volatility allocation to SPY/IEF/GLD, maximum 50% per asset, residual BIL. This exact allocation was previously examined; it is not a newly discovered edge. Reproduce it against equal-dollar SPY/IEF/GLD and BIL. Do not add volatility targets, new assets or optimized lookbacks during this round. The earlier three-ETF 200-day trend rule remains an archived reference, not a fourth active track.
3. **Current crypto trend as a control.** BTC/ETH qualify only when prior completed daily close and the 50-day mean both exceed the 200-day mean. Eligible sleeves target 50% each; initial/Monday allocation and daily exits, with other capital in cash. Keep [the deployed rules](../CRYPTO_SETUP.md) fixed and audit the [diagnostic replay](crypto_replay.py). Resolve actual quote availability and execution costs before proposing any successor.

The unchanged stock observer remains an operational control. New stock filters, weekly reversal, SPY pullback and memecoin research are not priority work in this round. Cash-and-carry remains data-blocked; daily spot bars cannot substitute for futures premiums or collateral paths.

## Data and measurement contract

Every run needs a manifest: strategy ID and parent version, commit, parameters, signal/execution/mark clocks, universe construction, data provider and request parameters, retrieval time, hashes, action revisions, costs, benchmark definitions, train/test dates and prior-use classification. A run must never overwrite an earlier result.

Record signal time, input availability, intended order, fill assumption, price/quote time, fees, positions, cash, receivables and mark quality. Store reason codes for no signal, rejected signal, missing/stale data, missed decision, no fill, reconciliation block and closed market. One thousand quote polls are not one thousand independent trading observations.

### Comparable daily observations

- **Equities/ETFs:** report the exchange session's official regular close, including early closes. Separate the economic mark time from when a complete report became available. Pair strategy and benchmark on the same session dates and initial valuation.
- **Crypto:** use a declared UTC daily boundary. A reproducible historical UTC close is a bar-based mark; a prospective executable quote must satisfy the existing freshness limits and a narrow predeclared boundary window. Store those mark types separately. If an eligible boundary mark is missing, report missing data and the last valid timestamp, never label an earlier price as current.
- Existing last-observation daily snapshots are retained as provisional legacy marks; do not silently rewrite them into exact closing valuations. Build any reconstructed historical close series separately and label it retrospective, not a new forward fill record.
- Compare only paired eligible observations; publish coverage and excluded-date reasons. Do not erase bad days or carry old quotes forward as fresh observations. A data gap blocks a performance claim when the omitted exposure is material.
- Separate a heartbeat from valuation freshness and from decision completion. A running process can still miss a decision or fail to mark a portfolio.

### Costs and baselines

Report gross market P&L, spread, slippage, commissions/fees, financing/borrow/funding where relevant, operational costs and net P&L separately. Avoid double-charging ETF expenses already embedded in fund prices. Clearly disclose unmodeled taxes. Fixed operating expenses need both standalone and incremental/shared-cost views; do not charge the same real subscription to several portfolios when assessing a combined account.

Current historical allowances are assumptions, not measured fills. Crypto replay assumes 20 bps full spread, 10 bps extra slippage per side and 25 bps fee per side; it uses a daily-open proxy rather than the actual decision-window quote. Existing ETF tests generally use 10 bps per side with 5/20 bps sensitivities. Calibrate a new cost version using prospective executable quotes and later paper fills; retain the old version for comparison.

For each candidate include (a) an accessible cash/Treasury alternative with its own costs and accrual conventions, (b) matched-asset passive buy-and-hold or fixed allocation, and (c) a passive alternative matched to the candidate's ex-ante market exposure or volatility. Fit any matching weights using training data only. Show both cash assumptions for crypto when interest is not actually accessible. A BIL price return is not a literally risk-free 24/7 accrual rate.

Use total-return signals where specified, raw executable prices for orders, split-adjusted holdings and pre-ex-date dividend entitlement. Unknown payment dates remain unavailable cash. Verify action revisions and values against issuer/second-source samples. Reconstruct a historical stock universe including delistings before claiming general stock-selection skill; a fixed modern watchlist must retain its selection/survivorship warning.

Report cumulative and annualized return over adequately long periods, cash-excess Sharpe with uncertainty, benchmark-relative return, factor/exposure attribution, maximum drawdown and duration, tail losses, turnover, market exposure, trade count, independent decision count, cost sensitivity and missing-data coverage. Do not annualize a few days into an income expectation or call raw positive P&L alpha.

## Selection discipline

Before new outcomes are inspected, freeze the economic hypothesis, candidate set, costs, primary metric, split dates and promotion rules in a dated experiment record. Log every parameter trial and data repair; cost scenarios and correlated strategies are not independent discoveries. The registry currently summarizes known work and is not proof that every historical trial has been recovered.

Use expanding or rolling chronological outer evaluation windows. Any parameter choice occurs only in an inner training/validation split, with a gap at least as long as the maximum holding/label overlap where necessary. Carry only information available at each decision; obtain indicator warm-up before the evaluation start. Freeze the calendar and data-gap policy before viewing comparative returns.

All previously reported 2019–2026 daily ETF/crypto periods are already seen. Stock 2023–24 intraday was reserved but remained incompletely acquired in the archive; related daily data for those years was inspected elsewhere. Completing it supplies useful candidate-specific evidence, not a pristine global research holdout. Future changes need an honestly unused sample or forward observations.

Use dependence-aware confidence intervals (for example block bootstrap and HAC) and disclose multiple testing. For the bounded comparison, predeclare one primary benchmark-relative statistic and apply a family-wise correction such as Holm across the eligible active candidates; retain all adjusted and unadjusted results. With too few independent observations, the result is insufficient evidence, not a pass. Parameter-neighborhood and doubled-cost checks test fragility; they do not become extra winner-selection sweeps.

## Proposed gates

These are initial research-screen thresholds, subject to review before the next experiment. They do not state the owner's loss tolerance or authorize an account allocation. Any threshold change must be logged before inspecting the next evaluation sample.

| Gate | Required evidence | Failure action |
| --- | --- | --- |
| Data/accounting | Reproducible manifests; no future-data leakage; correct actions and cash reconciliation; no unresolved material gaps; independent spot-checks of prices/actions; all daily marks labeled. | Block performance promotion, repair/version data and preserve prior outputs. |
| Operational shadow | At least 30 consecutive scheduled decision days with no unexplained missed decisions, duplicate fills, ledger mismatch or secret leakage. At least 99% of scheduled valuation checks eligible, with all failures explicitly recorded. Crypto boundary marks and ETF closing marks separately audited. | Remain in shadow mode; resolve incident and restart the clean operational observation window without resetting P&L history. |
| Historical economics | Aim for at least five years across rising/falling/sideways regimes and at least three chronological outer test windows. Positive net excess return over cash in aggregate and at least two-thirds of outer windows; positive aggregate cash-excess return under doubled variable execution costs. Initial screen: excess Sharpe at least 0.75 and daily closing drawdown no greater than 15%. | Reject or mark insufficient evidence; do not loosen rules after seeing a failure. These screens are not loss guarantees. |
| Added value | The predeclared benchmark-relative measure must improve after realistic costs and exposure matching, with a positive lower 95% dependence-aware bound after the stated multiple-testing adjustment. A defensive candidate may instead use a predeclared downside-improvement objective with a required cash-excess return; the objective cannot be switched after seeing results. | Continue as a benchmark/control or stop further development; do not label alpha. |
| Broker paper execution | Separate approval and order-path review. At least 60 market sessions for equities/ETFs or 90 calendar days for crypto, plus at least 12 scheduled rebalance opportunities for a monthly/weekly allocation rule as applicable. Confirm entry/exit, partial-fill, timeout/restart, rejection and reconciliation behavior; costs and availability remain within the frozen stress budget. Simulated failure tests supplement sparse market events. | Stop new pilot entries on an integrity failure; preserve exits and ledgers under the reviewed procedure. More time may be needed; count alone is not proof. |
| Real-money pilot | Written review of the whole evidence set, an agreed capital cap/loss budget, instrument eligibility, fees/taxes/custody, tested operational stop and recovery procedures, and explicit authorization of the exact live configuration. No leverage assumed. | No live activation until these decisions are complete. |

Monthly strategies can require substantially more than 60 sessions to observe 12 rebalance opportunities. This is intentional: daily prices on one continuing allocation do not constitute many independent strategy decisions. Statistical conviction may require longer than any minimum above.

If a live pilot is later approved, begin at a separately agreed small size, compare realized costs and behavior to the frozen model, and scale only through another evidence review. An observed historical drawdown is not a contractual maximum loss, and a stop cannot guarantee an exit price. No script or scheduled report may promote a strategy to real-money execution.

## Implementation and reporting

Existing components:

- [Equity observer and order guards](../paperbot/engine.py), [stock signal rules](../paperbot/strategy.py), and [shadow accounting](../paperbot/shadow.py).
- [Cross-asset simulation](../paperbot/cross_asset.py) and [crypto simulation](../paperbot/crypto_sim.py).
- [Crypto historical replay](crypto_replay.py) and its [existing results](crypto_replay_results.json).
- [Offline tests](../tests/), [host process](../paperbot/host.py), and [validation notes](../VALIDATION.md).

Do not infer present deployment state from older setup-document prose. Deployment health and versions must be checked using current private diagnostics. This public plan records no private forward returns or account state.

The archive audit found standalone `trading-research-v01`, `long-history`, `cross-asset` and `hedged-futures` datasets and engines outside this repository. They must be imported into a private, immutable research workspace with hashes and any necessary data-use permissions; copying this repository alone cannot reproduce those studies. Do not publish provider data or credentials while making the research reproducible.

Daily reporting should prioritize health, mark/decision timestamps, missing data, cumulative net returns, benchmarks, exposure and exceptions. Weekly review should update the trial registry, cost calibration, paired daily comparisons and unresolved incidents. Monthly review should decide continue/reject/insufficient evidence against the frozen gates. Reporting is not autonomous strategy modification.

The initial deliverables are an evidence manifest, a comparable daily return panel, an incident log, a frozen experiment record and a reproducible scorecard with every candidate outcome. This plan adds no broker orders, data entitlement, new paid service, ledger reset or guaranteed return. Creating this plan does not claim that any new tests, repairs or deployments have already passed.

## Method and execution references

- Bailey and Lopez de Prado, [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) (2014): selection across many trials and non-normal returns can inflate reported performance. Keep all trials and account for dependence and selection when assessing evidence.
- Alpaca, [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading): paper execution has limitations relative to real trading. Our internal shadow fills are an additional modelling layer and have not validated real execution.
- [Operational runbook](OPERATIONS.md): reporting commands, evidence preservation and release checks.
