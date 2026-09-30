# Research operations

This repository contains simulations and research tools. A successful build, a
positive return, and a passing research gate are three different things. None
automatically authorizes brokerage orders or real-money trading.

## Read the system

On the existing worker, the unified read-only diagnostic is:

```sh
python -m paperbot.research_report --state-dir /var/data/paperbot
```

It reads reporting files, prints JSON, and needs no API credentials or network.
It does not initialize a portfolio, place orders, reset ledgers, or change any
settings. Missing or corrupt reports are reported separately for each strategy.
Reports contain private simulation holdings and should not be committed to this
public repository.

Report these items in each daily review:

1. Worker/supervisor heartbeat, each strategy process, and the deployed revision.
2. Current status time, last valid valuation time, and completed session date.
3. Each independent portfolio's starting capital, equity, return, holdings,
   turnover/fills, modelled trading costs and operating allowance.
4. Benchmark differences only when start, end and valuation conventions agree.
5. Missed decision dates, stale/missing quotes, incomplete input data, gaps in
   observations and blocked/stopped processes.
6. The next investigation or milestone; a positive day is not a promotion.

`supervisor-health.json` is a process report, not proof of good market data.
An old heartbeat is unknown/stale, even when its last recorded state is running.
Cross-asset accounting describes completed exchange sessions; it is not an
intraday quote valuation. Weekends and exchange holidays require the actual
calendar, not a weekday-only assumption.

## Preserve evidence

- Leave each strategy ledger and its original start date intact.
- Keep strategies and reference portfolios independent. Never add their virtual
  capital or profits and call the sum one funded account.
- A changed trading rule receives a new strategy ID, new precommitted protocol
  and separate ledger. Keep the old rule as a control.
- Crypto daily files are last successful observations. They are not guaranteed
  midnight closes. The observation journal exposes outages; it does not repair
  old gaps or authorize backfilled decisions.
- Content-addressed crypto input snapshots must match the hash of the input used
  for a decision. Provider revisions create another snapshot; earlier snapshots
  remain evidence.
- Back up source data, reports and ledger snapshots privately before any
  destructive maintenance. SQLite backups must be transactionally consistent;
  copying a database file while ignoring its WAL is not a reliable backup.
- Monitor available disk space and observation-journal capacity. Capacity alarms
  must be investigated before evidence collection stops. Off-host automated
  backup is a separate operational milestone, not implied by a persistent disk.

## Reproduce historical diagnostics

The crypto replay uses cached daily bars and an opening-price execution proxy.
Read `CRYPTO_SETUP.md` and the replay's own metadata before interpreting results.
The old periods have already been inspected and cannot become new holdouts by
rerunning them. Save new runs in separate output locations. Preserve the input
hash, code revision, parameters, all trials, and execution/valuation timestamps.

Earlier ETF and equity studies contain additional source data and limitations
that are not bundled into this public repository. The strategy registry records
their evidence status. A summary of those studies is not a claim that their
entire data pipeline can be reproduced from this repository alone.

## Change and release procedure

1. Work on an isolated branch. Record whether the change affects observation,
   accounting, execution assumptions, or strategy decisions.
2. Run the complete offline suite and the specific regression checks for the
   change. Never test new software by enabling orders.
3. Review the diff for unchanged order guards, credentials, endpoint restrictions,
   sizing, fees, decision schedule and ledger schema.
4. Save the exact release commit and prior deployed commit. Deploy only a tested
   revision. Auto-deploy remains an explicit infrastructure setting.
5. Verify the runtime commit, parent/child heartbeats, order-disabled flags and
   preservation of existing portfolios/decision history. Do not run `init` again.
6. On regression, roll back the software revision while preserving all saved
   evidence and ledgers. Diagnose incompatible state instead of resetting it.

The observation upgrade does not fix all known data problems: the existing
five-minute crypto polling schedule, 30-second quote freshness requirement,
00:10–00:30 UTC decision window and history prerequisites remain unchanged.
Better diagnostics are required before changing how failures are retried.

## Review cadence and next research increment

Daily: read-only health and performance review. Treat the existing independent
shadow portfolios as ongoing experiments, not investment recommendations.

Weekly: reconcile decision coverage and costs; examine newly collected evidence;
then complete one bounded item from `RESEARCH_PLAN.md`. Register a candidate and
its selection criterion before looking at its evaluation results. Report
rejections as clearly as apparent successes. New code can be proposed and tested
in a branch; a weekly research job must not enable orders, reset a control or
silently change a deployed strategy.

First research increment: consolidate the existing cross-asset and defensive
allocation studies on common dates, verify total-return inputs and cash returns,
and measure whether momentum's extra return survives exposure matching and cost
stress. If data integrity is inadequate, the result is **blocked**, not a pass.

Human decision before real capital: a concrete candidate, reproducible evidence,
account/venue eligibility, execution reconciliation, maximum capital and loss
limits, shutdown/recovery procedure, and explicit approval. A small initial
allocation and no borrowing are the proposed starting conditions; no amount is
authorized by this document.
