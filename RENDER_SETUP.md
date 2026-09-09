# Render setup

Use a single paid background worker with a 1 GB persistent disk. `render.yaml` specifies Python runtime, the smallest 512 MB compute tier, Virginia region, and manual deployments. It starts in `PAPERBOT_MODE=setup`, which makes no Alpaca requests and sends no orders. No credentials are required for this first deployment.

Before deploying, review the price shown by Render. Published reference prices checked 9 September 2026 were $7/month for the 512 MB tier plus $0.25/month for 1 GB disk, before taxes or usage extras. The Alpaca SIP subscription is separate. No service has been created or charged by preparing these files.

1. In Render, choose New → Blueprint and connect this repository, granting access only to it. Use the branch containing this `render.yaml`.
2. Review the service and disk settings and the actual cost. Deploy only when you accept that cost.
3. Expect `SETUP MODE: trading off` in the logs. The worker now remains available for a Shell session without holding the bot database lock.
4. Add `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` as private environment variables in Render, using your paper keys. Keep `PAPERBOT_MODE=setup`.
5. In the service's Shell, run `python -m paperbot preflight`. SIP 403 remains a known blocker until data entitlement is resolved. Do not send credentials in chat.
6. On a clean dedicated paper account with no positions or open orders, run `python -m paperbot init`. This initializes only the saved $45,000 ledger; it does not change the $100,000 account balance or submit orders. Never delete/reinitialize existing state to bypass a mismatch.
7. Once SIP access is working, change `PAPERBOT_MODE` to `observe` and redeploy for read-only observation during regular US market hours. Confirm history, quotes, reconciliation and logs.
8. Only after supervised review, change the mode to `paper` to enable simulated orders. Use the same disk, account and single worker. There is no live mode.

Switching modes stops/restarts the process; it does not flatten holdings. Do not put an active order-enabled pilot into observe/setup while relying on it to manage positions. Follow START_HERE.md for pause, flatten and controlled shutdown.

The application saves its database at `/var/data/paperbot/state.sqlite3`. Never substitute ephemeral storage. A new or missing disk never triggers automatic account initialization. Do not scale to multiple workers or enable automatic deployments during an active pilot. Set up host heartbeat alerts before unattended operation.

The native Python runtime is intentional here; the existing Dockerfile remains available for other hosts. The earlier starter notebook embeds the earlier 30-test source snapshot; this repository now adds four host startup tests. Signals and trading rules are unchanged.

Sources: https://render.com/docs/blueprint-spec , https://render.com/docs/disks , https://render.com/docs/ssh , https://render.com/articles/render-vs-railway , https://render.com/pricing .
