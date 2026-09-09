# Alpaca paper bot — first build

Built 9 September 2026. This is a working local paper-only pilot implementation, with a deterministic offline demo and automated tests. It has not submitted any Alpaca orders or been deployed. Your earlier paper account check passed, but real-time SIP returned HTTP 403; that remains an account-side blocker for the trading pilot.

## Your immediate next step

Open **Alpaca_Bot_Start.ipynb** in Google Colab using **File → Upload notebook**. Run the first code cell. It contains the bot's source and tests, so you do not need to install Python locally or upload another ZIP. It makes no network requests, asks for no credentials and uses a fake broker. It should report `OFFLINE TESTS: PASS` and `OFFLINE DEMO: PASS`. Send those status lines back in our chat.

The optional second cell checks your paper account and SIP access using hidden credential prompts. It does not trade. You already know SIP returned 403; there is no need to repeat that check until access changes. Keep your keys private. Colab is only for interactive setup; its expiring runtime is unsuitable for managing unattended overnight positions.

The remaining sequence is: verify this build in your environment; provision one persistent worker and disk; initialize its clean paper account state; enable matching SIP data; run in observation mode during market hours; then start a supervised paper-order session. Do not fund a live account. Do not start an unattended pilot from Colab.

## What is included

- Frozen entry logic: 15-minute opening volume at least 1.5 times the prior 20 session openings; later completed 15-minute close above both the opening high and session VWAP before noon; no gap filter.
- Exit after a completed close below the original opening low, or 15 minutes before the exchange close on the third session. Holidays, weekends and early closes come from Alpaca's calendar.
- A separate $45,000 ledger inside the paper account. The remainder of initial paper cash is excluded. At most three fully funded long positions; no shorting, options, crypto or leveraged sizing.
- Saved order intents, client order IDs, partial-fill accounting, reconciliation, a single-process lock, event logs, pause/flatten controls and a status file.
- An indicative equal-weight basket comparator, with no actual benchmark orders. This is price-only, excludes dividends and requires review around corporate actions.
- Paper broker URL fixed in code. Default observation mode refuses order submissions at both the engine and HTTP adapter.

## Execution differs from the historical backtest

The strategy's signal rules are preserved, but the historical return figures are not claimed for this implementation. It waits at least five seconds for completed bars, rejects signals more than 90 seconds old, and requires fresh SIP quotes for purchases. Entries use whole-share immediate-or-cancel limit orders with a cap 0.10% above the observed ask, rounded up to a cent. A $10 cash buffer is retained. Each symbol gets one entry attempt per day; unfilled shares are not chased. An attempt can fill partly or not at all.

Exits are regular-session market sell orders for reconciled owned quantities. They cannot guarantee a stop price. Pending entry shares are canceled and cancellation/fills reconciled before the remaining position is sold. There is no resting broker-side protective stop because that would implement a different exit rule. If the host or Alpaca is unavailable, timely exits are not assured. Observe-only mode never manages or exits positions; do not switch an active pilot to observation mode while relying on it to manage holdings.

The virtual strategy ledger is derived from actual reported paper fills, with initial idle cash excluded. Unexplained cash changes, including fees, dividends, deposits or resets, stop further order generation for review. The code does not silently treat them as trading profits. It does not automatically process corporate actions on held shares; a quantity mismatch stops trading. The paper account may not simulate dividends or all real-market frictions. Historical backtests and paper outcomes are separate evidence.

## Local setup (optional alternative to the starter notebook)

Use Python 3.10 or newer. From the extracted folder:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m paperbot demo
```

On a system where the command is named `python3`, substitute that for `python`. The optional dependency supplies timezone data on systems that lack it. The core program otherwise uses Python's standard library.

Read-only connection check; prompts are hidden:

```bash
python -m paperbot preflight
```

Expected: `PAPER CONNECTION: OK`, `CLEAN ACCOUNT: OK`, `SIP ACCESS: OK`. A 403 means data authentication or permissions need attention. No IEX fallback is used. The runtime checks all 12 symbols and 20 prior opening bars. Quotes may be old while the market is closed; freshness must be checked during regular hours.

## Persistent deployment

Use one Linux worker/container that remains running, has reliable time synchronization, outbound HTTPS, and a persistent writable disk. Mount the disk at `/state` for the Docker image. A serverless request handler, ephemeral scheduled task, multiple replicas, or a sleeping laptop does not meet these requirements. The supplied Docker setup has not been built or deployed in this environment; only the Python code and notebook were executed locally.

Enter `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` using the host's private secret settings. Use paper credentials. Never put keys in source, image builds, screenshots or this chat. The bot ignores no endpoint override: a non-paper value in recognized broker-URL environment variables is rejected. Retain the state disk across restarts, redeployments and image upgrades.

The default `compose.yaml` runs in observation mode and mounts a named volume. If using it, configure secrets privately in your shell/environment, then:

```bash
docker compose build
docker compose run --rm paperbot preflight
docker compose run --rm paperbot init
docker compose up -d
docker compose logs -f
```

`init` writes only local state and requires no existing positions or open paper orders. It must be run once. It does not change the paper balance. Never reinitialize or delete state to bypass a mismatch. The startup account must be dedicated to this pilot; do not trade manually or reset its balance during the run.

After observation and a supervised readiness review, the explicit paper-order command is:

```bash
python -m paperbot run --enable-paper-orders --state-dir state
```

For Docker, change the service command to `["run", "--enable-paper-orders"]` and recreate that single service using the same volume. This enables simulated orders. Do not run a second worker with different state against the same account. This package has no live-trading mode.

## Controls and monitoring

For local execution, use the same state directory as the running process:

```bash
python -m paperbot status --state-dir state
python -m paperbot pause --state-dir state
python -m paperbot flatten --state-dir state
python -m paperbot export --state-dir state
```

For Docker, use `docker compose exec paperbot python -m paperbot status` and the analogous `pause`, `flatten` or `export` commands. The container's state path is set automatically.

- `status` reads the last heartbeat. Check its timestamp: a file can remain after the process stops. It is not proof the bot is alive.
- `pause` blocks entries while an order-enabled process continues normal exits.
- `flatten` requests exits for owned paper positions at the next eligible regular-session cycle and keeps entries blocked. It does not immediately submit orders by itself. It cannot act while the process is stopped, reconciliation is unsafe or the market is closed.
- `resume` clears both operator flags. Use it only after inspecting positions and resolving the reason for pausing.
- `export` writes the event log to CSV. The SQLite state contains cumulative order/fill information. Files are local and no notifications are sent automatically.

Stopping the program does not liquidate positions. Before a planned shutdown, request flatten, wait for fills, and verify the account is flat with no pending orders. Unexpected outages require checking the paper dashboard. Set up host-level heartbeat alerts before unattended operation; this build does not send external alerts or provide a phone dashboard.

## When the bot blocks

| Status | Required next action |
| --- | --- |
| SIP access / HTTP 403 | Check entitlement and credentials; don't replace the strategy's feed without retesting. |
| Missing/stale bars or quotes | Inspect feed timing. No old signals are backfilled into new entries. Scheduled exits can still run if account state is reliable. |
| Unresolved order intent | Inspect the saved client order ID in Alpaca. The bot repeatedly queries it and never blindly resubmits. If no order exists, keep paused and resolve the saved intent with assistance; don't delete the database. |
| Position or cash mismatch | Pause and compare saved fills with the paper dashboard. Allow a transient broker update to settle; persistent differences need review. The program does not guess which position or cash adjustment to adopt. |
| Account blocked / different account | Verify paper credentials and account status. Do not bypass the check. |
| Missing state | Restore the original persistent disk/database. Initialize only a genuinely new, clean pilot. |

## Validation and limits

See `VALIDATION.md` for checks performed. No user credentials were available during development; mock tests do not verify your broker's real order responses, data entitlement or fills. Your previous SIP 403 is still unresolved. Start with supervised paper operation only, after the data and host gates pass. Subscription and hosting charges must be deducted from performance reporting separately.

Official references: [Alpaca orders](https://docs.alpaca.markets/us/reference/postorder), [client order IDs](https://docs.alpaca.markets/us/reference/getorderbyclientorderid), [historical bars](https://docs.alpaca.markets/us/reference/stockbars), [paper limitations](https://docs.alpaca.markets/us/docs/paper-trading), [Colab runtime limits](https://research.google.com/colaboratory/faq.html).
