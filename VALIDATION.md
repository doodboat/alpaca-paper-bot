# Validation record

Date: 9 September 2026. Runtime: local Python 3.12. No external orders, credentials, subscriptions or account changes.

30 unit/integration tests passed with `python -m unittest discover -s tests -v`. The integration tests use an in-memory fake broker and a temporary SQLite database. They check:

- Completed-bar timing; exclusion of future/incomplete bars; rejection of missing bars; relative-volume threshold; noon cutoff and stale signals.
- Exchange-time conversion, early-close time conversion, weekend holding-period calculation and whole-share sizing.
- Three-position limit, deterministic ranking, full cash funding, duplicate prevention within a process and across restart.
- Order accepted before timeout; order never accepted before timeout; recovery by client ID without blind retries.
- Partial and zero fills, manual cash/position/order detection, changed-account rejection and explicit state initialization.
- Confirmed cancellation of unfilled entry shares before exit, scheduled exits despite denied SIP access, and rejection of reported buy fills above their limit.
- Closed market, stale quotes/bars, scheduled exits when entry data is stale, range-failure exits and exits while entries are paused.
- Read-only operation, exclusion of a second process using the same disk, rejected live broker URLs and redirects, no automatic POST retry, bar pagination and sanitized authentication errors.

The offline demo executed three fake entries, repeated a cycle, reconstructed the engine from saved state, and exited on the third session with stale entry data. No duplicate entries occurred. No network calls were made.

These checks establish tested software behavior in fixtures, not strategy profitability or production readiness. The Docker image has not been built here. Linux Python behavior was executed; Windows-specific file locking was not executed. Real API response races, SIP entitlement, live data completeness, real paper fills and host restart behavior require supervised validation in the chosen deployment environment.

Intentional execution differences from the backtest: five-second publication allowance, 90-second entry deadline, fresh quotes, capped IOC entries, a $10 cash buffer, no chasing unfilled quantities and no automatic adoption of cash/corporate-action adjustments. Historical returns must not be represented as achieved by this bot.
