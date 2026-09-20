# Crypto forward simulation

Frozen 19 September 2026. This is a separate GET-only shadow simulation using Alpaca data, not orders in the Alpaca paper dashboard. No real money, margin, shorting, account resets or wallet access. Existing equity and cross-asset ledgers are independent.

## Rules

BTC/USD and ETH/USD, independently: eligible when the last completed UTC daily close is above its 200-day simple moving average AND its 50-day average is above the same 200-day average. Each eligible coin receives a 50% target; otherwise its allocation stays in USD. Initial allocation occurs on the first future UTC date, then rebalancing every Monday. Ineligible holdings exit on intervening days; new entries wait for Monday. No tuning or automatic rule changes.

Decisions run once per date between 00:10 and 00:30 UTC using 200 complete prior daily bars. The worker polls every five minutes, including weekends. Outside the decision window it only marks holdings. Downtime never causes retroactive fills: the next valid decision uses fresh quotes, and missed days are disclosed. A newly initialized ledger starts the next UTC day. State and fills commit in one SQLite transaction under a process lock; restarts do not repeat a decision. Corrupt state must be investigated, never reset.

Two separate hypothetical $45,000 portfolios: trend and initial 50/50 BTC/ETH buy-and-hold, plus a $45,000 zero-yield cash reference. These do not require $90,000 of funding. All accounting remains local to the worker's persistent disk.

## Execution and reporting assumptions

Simulated full fills at fresh ask plus 10bps / bid minus 10bps, with 25bps fee per side. Buy fees withheld from coin received, sell fees from USD received. Sizes may be fractional; this model does not validate exchange minimum quantities or model depth/partial fills. Quotes older than 30 seconds, future-dated more than 2 seconds, crossed or wider than 1% block the cycle. Values marked at bid, without hypothetical terminal liquidation fees. Fees, slippage, cash, positions and observed drawdowns are reported explicitly. Spread is embedded in execution/mark prices rather than the explicit fee/slippage counters.

Trend additionally deducts a standalone $106.25/month calendar-prorated operating allowance from reported NAV, not cash sizing. Buy-and-hold excludes that allowance. This is a comparable standalone research expense, not another subscription purchase. Cash earns no yield, taxes are excluded, and fills are assumptions. No promise of a loss ceiling or profitability.

Status: PAPERBOT_STATE_DIR/crypto/status.json. Daily snapshots: crypto/daily-YYYY-MM-DD.json (last observation of that UTC day). Decisions/fills: crypto/state.sqlite3. Signal inputs: crypto/inputs-YYYY-MM-DD.json. All files are private on Render; no credentials, holdings or reports are published to GitHub. A blocked status contains a reason, not invented current equity. Last valid ledger is retained. Logs print status every five minutes. No email or chat delivery is enabled by this code.

## Historical diagnostic, not fresh validation

`research/crypto_replay.py` replays the frozen rules against the previously saved `cross-asset/data/crypto.json` file from earlier research. Dataset is not duplicated into this repository and is not independently verified. It uses next-day open as a proxy for a later 00:10–00:30 fill, a 20bps full spread plus 10bps slippage per side and 25bps fee. Daily marks and operating allowances as above. No parameter search was performed; all these dates were already available to prior research. Results are in research/crypto_replay_results.json. They do NOT establish alpha or predict forward profitability.

Total returns: 2022 trend -9.55%, buy-hold -66.04%; 2023–24 trend +79.47%, buy-hold +320.37%; January 2025–9 September 2026 trend -19.01%, buy-hold -21.44%. Latest-period trend daily max drawdown 33.34%. Thus this is an experimental candidate, not a profitable strategy selected for live deployment.

## Deployment

Existing credentials ALPACA_API_KEY and ALPACA_SECRET_KEY stay in Render; do not paste them into chat or source control. Existing state directory must be on the persistent disk. No new dependencies or service required.

1. Deploy this code to the current worker with PAPERBOT_MODE=observe. Keep existing PAPERBOT_SHADOW and PAPERBOT_CROSS_ASSET settings.
2. Run `python -m paperbot.crypto_sim preflight` once. This fetches history and fresh quotes only. Do not enable on data/schema errors.
3. Set PAPERBOT_CRYPTO=1 and redeploy. The existing host launches a separate crypto child. Verify status shows crypto_simulation, broker_orders_enabled false and a future start_day.
4. Confirm first daily decision and fresh status after the first 00:10–00:30 UTC window. Routine cycles continue while the laptop is off.
5. Set PAPERBOT_CRYPTO=0 and redeploy to stop; preserve state.

The connector available in chat supplies market data, not brokerage orders. Render sign-in or an approved Render integration is required for the assistant to deploy and read private reports. Until that connection is verified, neither deployment nor autonomous monitoring is confirmed. Browser sessions can expire, so zero future interaction cannot be guaranteed.

## Verification

65 offline tests pass: original 54 plus 11 crypto tests covering fee accounting, cash constraints, future-start/no-backfill rules, restart idempotency, missed days, stale/crossed/wide quotes, trend rules, missing/paginated history and order-enabled configuration rejection. Tests use fake data and make no network calls or broker orders. A connector live-quote attempt returned an internal error; deployment preflight remains required.

Sources: https://docs.alpaca.markets/us/docs/crypto-trading (fee schedule and fee denomination); https://docs.alpaca.markets/us/reference/cryptobars-1 (daily bars).
