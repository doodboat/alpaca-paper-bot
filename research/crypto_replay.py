"""Auditable daily-open diagnostic, NOT an unseen holdout or exact quote replay."""
import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paperbot import crypto_sim
from paperbot.crypto_sim import SYMBOLS, CAPITAL, FEE, SLIP, VERSION, initial, decide, report

UTC = timezone.utc
REPLAY_VERSION = 'crypto-daily-open-diagnostic-v2'
HALF_SPREAD = .001
DEFAULT_PERIODS = (('2022-01-01', '2022-12-31'), ('2023-01-01', '2024-12-31'),
                   ('2025-01-01', '2026-09-09'))
NOTE = (
    'Previously inspected cached Alpaca data, not independently verified. '
    'These periods and prior results were already seen; this is a reproducibility '
    'diagnostic, NOT out-of-sample validation. Frozen rules, no parameter search. '
    'Daily 00:00 open is a proxy for the actual 00:15 fill, not an observed quote. '
    'Synthetic bid/ask = open*(1-/+0.001); production 10bps slippage and 25bps fee '
    'are applied without changes. Daily-close bid marks belong to end-of-day '
    'accounting and are only available after the next UTC midnight. '
    'Trend includes standalone $106.25/month allowance; benchmark excludes it. '
    'No tax or cash yield. Mark-to-bid NAV, no terminal liquidation. Daily-only '
    'drawdown omits intraday losses. Each period restarts each portfolio at $45,000.'
)


def utc_datetime(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('Invalid bar timestamp') from exc
    if result.tzinfo is None:
        raise ValueError('Bar timestamp must include a timezone')
    return result.astimezone(UTC)


def checked_date(value):
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Period bounds must be ISO calendar dates') from exc
    if result.isoformat() != value:
        raise ValueError('Period bounds must use YYYY-MM-DD')
    return result


def daily_dates(start, end):
    return [start+timedelta(days=i) for i in range((end-start).days+1)]


def market_number(value, *, positive=True):
    if isinstance(value, bool):
        raise ValueError('Boolean market value')
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Nonnumeric market value') from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError('Invalid nonfinite or nonpositive market value')
    return result


def validate_data(raw, as_of=None):
    """Reject ambiguous bars and gaps instead of overwriting or intersecting them."""
    as_of = as_of or datetime.now(UTC)
    if as_of.tzinfo is None:
        raise ValueError('Validation time must include a timezone')
    as_of = as_of.astimezone(UTC)
    if not isinstance(raw, dict) or not isinstance(raw.get('bars'), dict):
        raise ValueError('Expected a bars mapping')
    if raw.get('request', {}).get('timeframe', '1Day') != '1Day':
        raise ValueError('Replay requires 1Day bars')
    by_symbol = {}
    for symbol in SYMBOLS:
        source = raw['bars'].get(symbol)
        if not isinstance(source, list) or not source:
            raise ValueError('Missing bars for '+symbol)
        rows, previous = {}, None
        for row in source:
            if not isinstance(row, dict):
                raise ValueError('Invalid bar record')
            stamp = utc_datetime(row.get('timestamp'))
            if stamp.time() != time(0):
                raise ValueError('Daily bars must start at UTC midnight')
            if stamp.date() in rows:
                raise ValueError('Duplicate daily bar: '+symbol+' '+stamp.date().isoformat())
            if previous is not None and stamp <= previous:
                raise ValueError('Daily bars must be strictly increasing')
            if stamp+timedelta(days=1) > as_of:
                raise ValueError('Incomplete or future daily bar')
            if row.get('symbol', symbol) != symbol:
                raise ValueError('Bar symbol does not match its series')
            try:
                o, h, l, c = (market_number(row[k]) for k in ('open', 'high', 'low', 'close'))
            except KeyError as exc:
                raise ValueError('Missing OHLC field') from exc
            if not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError('Invalid OHLC range')
            if 'volume' in row:
                market_number(row['volume'], positive=False)
            if 'vwap' in row:
                market_number(row['vwap'])
            rows[stamp.date()] = dict(open=o, high=h, low=l, close=c)
            previous = stamp
        if set(rows) != set(daily_dates(min(rows), max(rows))):
            raise ValueError('Missing calendar date in '+symbol)
        by_symbol[symbol] = rows
    if set(by_symbol[SYMBOLS[0]]) != set(by_symbol[SYMBOLS[1]]):
        raise ValueError('Symbols have different date coverage')
    return by_symbol


def proxy_quotes(bars, day, field, source_time):
    return {s: dict(bid=bars[s][day][field]*(1-HALF_SPREAD),
                    ask=bars[s][day][field]*(1+HALF_SPREAD),
                    time=source_time.isoformat(), replay_proxy=True,
                    source='cached_daily_'+field, source_bar_date=day.isoformat()) for s in SYMBOLS}


def _run_validated(bars, start, end):
    start, end = checked_date(start), checked_date(end)
    if end < start:
        raise ValueError('Period end precedes its start')
    selected, available = daily_dates(start, end), set(bars[SYMBOLS[0]])
    if not set(selected).issubset(available):
        raise ValueError('Requested period is not fully covered by cached data')
    if not set(daily_dates(start-timedelta(days=200), start-timedelta(days=1))).issubset(available):
        raise ValueError('Need 200 consecutive prior daily bars for warmup')
    first = datetime.combine(start, time(0, 15), UTC)
    state = initial(first-timedelta(days=1))
    previous = {k: CAPITAL for k in state['portfolios']}
    returns, daily = {k: [] for k in previous}, []
    for day in selected:
        history = daily_dates(day-timedelta(days=200), day-timedelta(days=1))
        closes = {s: [bars[s][h]['close'] for h in history] for s in SYMBOLS}
        opening = datetime.combine(day, time(0), UTC)
        decision_time = opening+timedelta(minutes=15)
        # Open proxy intentionally bypasses live freshness checks; it is not a
        # claim of an available 00:15 quote or realizable execution at that price.
        decide(state, closes, proxy_quotes(bars, day, 'open', opening), decision_time)
        completed_at = opening+timedelta(days=1)
        accounting_time = completed_at-timedelta(microseconds=1)
        # Attributing the close to its day preserves exact day-based costs.
        # The full close bar is only observable at completed_at, next midnight.
        result = report(state, proxy_quotes(bars, day, 'close', accounting_time), accounting_time)
        changes = {}
        for kind, p in result['portfolios'].items():
            changes[kind] = p['equity']/previous[kind]-1
            returns[kind].append(changes[kind])
            previous[kind] = p['equity']
        daily.append(dict(date=day.isoformat(), decision_time=decision_time.isoformat(),
                          proxy_open_time=opening.isoformat(),
                          signal_latest_close_date=history[-1].isoformat(),
                          accounting_mark_time=accounting_time.isoformat(),
                          bar_completed_at=completed_at.isoformat(),
                          eligibility=state['decisions'][-1]['eligible'], daily_returns=changes,
                          portfolios=json.loads(json.dumps(result['portfolios']))))
    results = {}
    for kind, p in result['portfolios'].items():
        rr = returns[kind]
        dispersion = stdev(rr) if len(rr) > 1 else 0.
        results[kind] = dict(final_equity=p['equity'], total_return=p['net_return'],
                             CAGR=(p['equity']/CAPITAL)**(365/len(rr))-1,
                             daily_Sharpe_zero_cash=mean(rr)/dispersion*math.sqrt(365) if dispersion else None,
                             max_daily_drawdown=p['max_observed_drawdown'], fills=p['fills'],
                             fees=p['fees'], slippage=p['slippage'],
                             operating_allowance=p['operating_allowance'])
    return dict(start=start.isoformat(), end=end.isoformat(), days=len(selected), results=results, daily=daily)


def run(raw, start, end, *, as_of=None):
    return _run_validated(validate_data(raw, as_of), start, end)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_report(data_path, periods=DEFAULT_PERIODS, *, as_of=None):
    as_of = as_of or datetime.now(UTC)
    data_path = Path(data_path)
    source_paths = {'strategy_module': Path(crypto_sim.__file__), 'replay': Path(__file__)}
    source_hashes = {name: sha256(path) for name, path in source_paths.items()}
    payload = data_path.read_bytes()
    raw = json.loads(payload)
    bars = validate_data(raw, as_of)
    days = sorted(bars[SYMBOLS[0]])
    result = dict(note=NOTE, metadata=dict(
        replay_version=REPLAY_VERSION, strategy_version=VERSION,
        created_at=as_of.astimezone(UTC).isoformat(), input_name=data_path.name,
        input_sha256=hashlib.sha256(payload).hexdigest(), code_sha256=source_hashes,
        strategy_module='paperbot/crypto_sim.py', python_version=sys.version.split()[0],
        input_coverage=dict(first_day=days[0].isoformat(), last_day=days[-1].isoformat(),
                            completed_days=len(days), rows_per_symbol={s: len(bars[s]) for s in SYMBOLS}),
        requested_periods=[dict(start=a, end=b) for a, b in periods],
        evidence_class='previously_seen_historical_diagnostic', out_of_sample=False, parameter_search=False,
        config=dict(symbols=list(SYMBOLS), initial_capital_per_portfolio=CAPITAL,
                    fee_rate=FEE, slippage_rate=SLIP, synthetic_half_spread_rate=HALF_SPREAD,
                    trend_monthly_operating_allowance=106.25, cash_yield=0,
                    warmup_daily_closes=200, fast_sma_days=50, slow_sma_days=200,
                    decision_time_utc='00:15', sizing='unchanged production strategy',
                    mark='daily close minus synthetic half spread', terminal_liquidation=False,
                    independent_period_restarts=True)),
        periods=[_run_validated(bars, a, b) for a, b in periods])
    if any(sha256(path) != source_hashes[name] for name, path in source_paths.items()):
        raise RuntimeError('Code changed during replay; rerun from a stable version')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data')
    parser.add_argument('--output', type=Path, help='New JSON path; refuses to overwrite existing results')
    args = parser.parse_args(argv)
    if args.output is not None and args.output.exists():
        parser.error('Output exists; choose a new versioned path')
    body = json.dumps(build_report(args.data), indent=2, allow_nan=False)+'\n'
    if args.output is None:
        print(body, end='')
    else:
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(body)
        print('Wrote diagnostic replay to '+str(args.output))


if __name__ == '__main__':
    main()
