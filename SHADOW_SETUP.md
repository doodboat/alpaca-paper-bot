# Prospective simulation for the existing observer

This patch adds an opt-in, separate simulated ledger. No broker order is submitted or canceled by it. Existing observe/paper code paths are unchanged when the opt-in is absent. Paper-order mode and shadow opt-in together are rejected.

## What it measures

New recorded entry signals only: existing events are deliberately skipped on first start. The September 10 Apple candidate is not retrospectively filled. Entry signals consumed more than 90 seconds late are skipped and the record is flagged incomplete. Up to three whole-share positions share a $45,000 simulated budget. Each entry budget is one third of the day's starting reference equity, subject to available cash and a $10 cash buffer. The day's reference equity is the last complete observed liquidation valuation, which may precede the opening bell; this is an approximation to the paper engine's daily account valuation.

Hypothetical entry uses a fresh simulation-cycle ask plus 5 basis points, within the limit derived from the original observer quote; it is timestamped at processing time; exit is fresh bid minus 5 basis points. Both also charge $1 per fill. These are assumptions, not actual commission quotes or evidence of full IOC fills. Available size, order queue, market impact, operating subscriptions, interest, taxes and corporate actions are not modeled. Consequently this is a preliminary forward simulation, not an executable performance record or a net-of-all-costs return.

Exits check completed bars against the opening low and the third-session exit schedule, including prior sessions after a restart. Missed prior-session exits are executed at the next available fresh quote, never retroactively at a favorable old price, and are flagged incomplete. Endpoints and sampled drawdown do not capture every intraday move. Quote failures produce null current equity rather than a fabricated fresh valuation. Benchmark values retain the observer's independent start timestamp; they are not directly comparable excess returns for a later-starting simulation.

No strategy parameter optimization, futures trading, live trading or deployment is included. Existing signal-event recording only tells us about qualifying candidates; it does not explain every stock/bar that failed the underlying signal formula.

## Install locally first (Windows PowerShell)

Save shadow-simulation.patch in C:\Users\peter\alpaca-paper-bot. Open PowerShell in that folder. Copy only command text, not terminal prompts.

1. `git apply --check shadow-simulation.patch` — should return no output. If it reports an error, stop; do not force it.
2. `git apply shadow-simulation.patch`
3. `python -m unittest discover -s tests -v` (use `py` instead of `python` if that is your installed launcher).
4. Inspect `git diff` and `git status --short`. Only paperbot/__main__.py should be modified; paperbot/shadow.py, tests/test_shadow.py and SHADOW_SETUP.md are new. The downloaded patch may also be untracked. Do not add credentials or state databases.

Tests run here: 44 passed, using offline fixtures. Network calls and broker orders during those tests: zero.

## Deployment is a later step

The patch has not been pushed or deployed. Keep PAPERBOT_MODE=observe. First review local test results and the intended diff. Commit only the four named implementation/documentation files, push to the branch actually selected by Render, and deploy that reviewed commit. After that deployment, set PAPERBOT_SHADOW=1 while keeping PAPERBOT_MODE=observe to opt in and restart the worker. No change to API keys is required. Do not run init or reset existing state.

The first successful start prints SHADOW SIMULATION and subsequent logs include mode=shadow_simulation. Broker-account balances still do not change; simulated results live under the shadow directory. Disable with PAPERBOT_SHADOW=0 and restart; preserve the simulation state to avoid silently erasing its history.

## Read the latest report (Render Shell, after enabling)

```bash
python - <<'PY'
import os
from pathlib import Path
p=Path(os.environ.get('PAPERBOT_STATE_DIR','/var/data/paperbot'))/'shadow'/'status.json'
print(p.read_text() if p.exists() else 'No shadow report yet.')
PY
```

Each session has a daily-YYYY-MM-DD.json snapshot updated during operation, including entries/exits counts and latest valuation. When market quotes are unavailable after close, current equity can be null; last_complete_equity and last_valued_at show the last valid observation. This is not an official closing NAV. The independent shadow/state.sqlite3 retains positions, assumed fills and decision counters across restarts. Reports are files/log output, not automated messages or emails.
