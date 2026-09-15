# Cross-asset forward simulation

Prepared 12 September 2026. This update adds a separate GET-only, daily-bar simulator. It has not been deployed or authenticated against Peter's data account. The existing Apple breakout simulation remains separate.

## Frozen rules and comparisons

Three independent hypothetical portfolios start together at $45,000 each: cross-asset momentum, monthly equal-weight, and buy-and-hold BIL. They do not share or require $135,000 of actual funding. There is no leverage, shorting, broker order submission or account modification.

Universe: SPY/EFA/EEM/IEF/GLD/DBC/UUP plus residual BIL. Momentum selects the top two using total-return index[-22]/index[-253]-1, then assigns 50% to each only if index[-1]/index[-253]-1 exceeds BIL's corresponding return. Rejected portions go to BIL. Initial investment is at the next session with a decision committed before its open; subsequent momentum/equal-weight rebalances are at the first session of each month. BIL is bought initially and held. Initial mid-month investment mirrors the historical simulator's first-session convention; it is not a new signal parameter.

Daily raw prices and cash distributions/split ratios reconstruct the same total-return formula as the research engine. Prices adjusted for dividends are NOT treated as executable prices. Whole-share targets use prior gross closing equity and prior raw closes. Sells precede buys; purchases are capped by available cash at the published next-open price plus 10bps, and BIL buys are last. Sells use the open minus 10bps. No broker orders are sent. Assumed open fills are calculated after the daily bar becomes available, only for plans saved before that session opened. These are prospective decisions with simulated execution, not proof of actual auction access or fills.

Dividends accrue on the ex-date to shares already held, before that day's purchases, and become spendable on the supplied payable date. Unknown payable dates stay receivable. A holding-period split blocks processing pending explicit review, matching the original backtest limitation. Unsupported or incomplete corporate actions also block. A changed or newly reported past action in the holding period halts accounting instead of silently rewriting reported results. Provider omissions can still be undetectable until new information arrives.

Trading costs match the historical baseline: 10bps per side. Momentum and equal-weight each have the historical $106.25/month calendar-prorated operating allowance; BIL has no bot operating charge. The allowance reduces reported NAV without reducing gross sizing, retaining the original approximation. Actual costs, taxes and interest are unverified/unmodeled. Three reporting allowances are not three real subscriptions. ETF expenses already embedded in prices are not charged twice. Daily marked NAV does not deduct hypothetical liquidation of all positions; comparisons to a terminal-liquidation backtest must add that ending execution cost.

## Validation completed

54 offline tests passed (44 existing plus 10 new). Coverage includes exact lookbacks, immutable pre-open plans, opening gaps/cash affordability, costs, dividend entitlement and receivables, revision/missing-plan blocks, pagination, fixed read-only endpoint routing and restart persistence. No network or broker orders were used by these tests.

An additional comparison replayed the actual cached 2025–9 September 2026 input data through the new accounting functions for 422 sessions and all three portfolios. Daily NAV matched the original stored results within half a cent (report rounding). After allowing for terminal liquidation, ending NAV matched: momentum $66,848.94; equal-weight $59,664.98; BIL $47,820.75. This establishes accounting agreement on those supplied inputs, not a new holdout or confirmation that all historical data is correct. The prior report's data-quality and model-selection limitations still apply.

## Install and verify (Windows PowerShell)

Save cross-asset-simulation.patch in C:\Users\peter\alpaca-paper-bot. Run commands one at a time from that folder:

1. `git apply --check cross-asset-simulation.patch`
2. If the check succeeds: `git apply cross-asset-simulation.patch`
3. `py -3.12 -m unittest discover -s tests -v`
4. `git status --short`

Expected changes: paperbot/__main__.py modified; paperbot/cross_asset.py, tests/test_cross_asset.py and CROSS_ASSET_SETUP.md new. No requirements change or new Python package is needed. Downloaded patch files should remain untracked. Review the diff and test output before committing only the four named files and pushing the branch linked to Render.

The patch is incremental to the deployed shadow-simulation code (user commit bd53cd3). A compatibility check must pass on the user's repository; do not force a conflicting patch.

## Data gate on Render, before enabling

After deploying the reviewed code, keep PAPERBOT_MODE=observe, PAPERBOT_SHADOW=1 and PAPERBOT_CROSS_ASSET absent or 0. In Render Shell run:

`python -m paperbot.cross_asset preflight`

This validates daily price coverage, corporate-action response structure and lookback length, and prints diagnostic target weights. It does not initialize a simulated ledger or submit orders. HTTP 403, an unexpected schema or incomplete data blocks this step. We have not verified this account's access; do not buy another subscription merely to clear an error without reviewing it.

Alpaca's daily bars endpoint supports raw prices, 1Day aggregation and pagination: https://docs.alpaca.markets/us/reference/stockbars

The corporate-action endpoint filters process dates and warns that announcements may arrive late: https://docs.alpaca.markets/us/reference/corporateactions-1

The adapter requests known records through one year of future process dates, then applies their ex-dates locally. It cannot recover events the provider has not supplied, nor promise point-in-time completeness. Future processing dates do not authorize using future ex-date distributions in today's signal.

## Enable only after the data gate passes

Set PAPERBOT_CROSS_ASSET=1 on the existing worker and redeploy, leaving PAPERBOT_MODE=observe and PAPERBOT_SHADOW=1. No API-key change, account reset or new paid Render service is required by this implementation. The host launches an independently locked child process, so heavy daily-data requests do not block the breakout loop. A child failure is logged while the observer continues. Successful live operation still needs verification from logs.

The child checks approximately every 30 minutes. Daily bars become eligible 20 minutes after the exchange close. Three portfolio decisions are saved together before their next open and portfolios advance together after the completed session's inputs pass checks. If an intervening session has no precommitted plan, processing blocks for review rather than inventing a past trade. Do not reset state to hide missed sessions.

The first start chooses a future session. If started during a session, it waits to plan from that session's completed close. It cannot retroactively capture earlier September returns. Status may show the proposed weights every day, but only the initial/month-boundary plans have trade targets; daily diagnostics are not daily rebalancing.

## Review results

Files are under PAPERBOT_STATE_DIR/cross_asset; with the current setup this is /var/data/paperbot/cross_asset. state.sqlite3 stores all decisions, fills, portfolios and reports. inputs/ retains data snapshots identified by SHA-256. status.json is the current process status; daily-YYYY-MM-DD.json is the latest processed session report. No live intraday quotes, email or external notification is provided.

Render Shell: `cat /var/data/paperbot/cross_asset/status.json`

First review the preflight and first completed session for correct initialization and accounting. After subsequent month boundaries, verify the rebalances and compare cumulative returns/drawdowns on the same dates. A few days or one rebalance cannot establish reliable alpha or Sharpe. No automated promotion to live or leveraged trading is implemented.

To stop this optional process, set PAPERBOT_CROSS_ASSET=0 and redeploy; retain its state for audit. Existing simulation settings are independent. Actual deployments and environment edits are separate user actions; none has been performed by preparing this patch.
