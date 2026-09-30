"""Read-only research scorecard. JSON reports only: no broker or ledger imports.

This module does not infer exchange calendars, manufacture missing valuations,
combine virtual portfolios, or estimate statistical significance from polls.
"""
import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
CAPITAL = 45000.0
# Health tolerance is twice the longest normal poll; not quote-freshness permission.
HEALTH_SECONDS = 660
DAILY_RE = re.compile(r'^daily-(\d{4}-\d{2}-\d{2})\.json$')


def number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def read_json(path):
    try:
        result = json.loads(path.read_text(), parse_constant=lambda _: None,
                            parse_float=lambda x: float(x) if math.isfinite(float(x)) else None)
        return (result, None) if isinstance(result, dict) else (None, 'invalid_object')
    except FileNotFoundError:
        return None, 'absent'
    except (OSError, UnicodeError):
        return None, 'unreadable'
    except (ValueError, TypeError):
        return None, 'invalid_json'


def freshness(document, as_of, error=None):
    if error:
        return {'state': error, 'reported_at': None, 'age_seconds': None}
    observed = stamp((document or {}).get('time'))
    if observed is None:
        return {'state': 'unknown_timestamp', 'reported_at': None, 'age_seconds': None}
    age = (as_of - observed).total_seconds()
    state = 'future_timestamp' if age < -2 else 'fresh' if age <= HEALTH_SECONDS else 'stale'
    return {'state': state, 'reported_at': observed.isoformat(), 'age_seconds': round(age, 1)}


def daily_reports(directory, as_of):
    rows, errors = [], []
    # Read only explicitly named JSON artifacts, never SQLite or arbitrary files.
    try:
        candidates = sorted(directory.glob('daily-*.json'))
    except OSError:
        return [], [{'report': 'daily_reports', 'error': 'unreadable'}]
    for path in candidates:
        match = DAILY_RE.fullmatch(path.name)
        if not match:
            continue
        day = match[1]
        try:
            report_day = datetime.fromisoformat(day).date()
        except ValueError:
            continue
        if report_day > as_of.date():
            continue
        report, error = read_json(path)
        if error:
            errors.append({'report': path.name, 'error': error})
            continue
        value = report.get('latest', report)
        if not isinstance(value, dict):
            errors.append({'report': path.name, 'error': 'invalid_snapshot'})
            continue
        timestamp = stamp(value.get('time'))
        if timestamp and timestamp > as_of + timedelta(seconds=2):
            errors.append({'report': path.name, 'error': 'future_snapshot'})
            continue
        rows.append((day, value, report))
    return rows, errors


def sample_quality(rows, errors, market):
    dates = [d for d, _, _ in rows]
    gaps = []
    for previous, following in zip(dates, dates[1:]):
        gap = (datetime.fromisoformat(following) - datetime.fromisoformat(previous)).days - 1
        if gap > 0:
            gaps.append({'after': previous, 'before': following, 'calendar_days': gap})
    observations = []
    valued_counts = {}
    for day, snapshot, _ in rows:
        if isinstance(snapshot.get('portfolios'), dict):
            for name, value in snapshot['portfolios'].items():
                if isinstance(value, dict) and number(value.get('equity')):
                    valued_counts[name] = valued_counts.get(name, 0) + 1
        elif number(snapshot.get('equity')) or number(snapshot.get('last_complete_equity')):
            valued_counts['equity_shadow'] = valued_counts.get('equity_shadow', 0) + 1
        time = snapshot.get('last_valued_at') if 'last_complete_equity' in snapshot else snapshot.get('time')
        parsed_time = stamp(time)
        time = parsed_time.isoformat() if parsed_time else None
        observations.append({'report_date': day, 'valuation_time': time,
                             'session_date': snapshot.get('date') if market == 'us_daily' else None})
    return {'daily_report_count': len(rows), 'first_report_date': dates[0] if dates else None,
            'last_report_date': dates[-1] if dates else None, 'calendar_gaps': gaps,
            'gap_interpretation': 'Calendar gaps are not missed exchange sessions; an authoritative calendar is required.'
                if market.startswith('us') else 'Missing report dates are not proof of missed decisions.',
            'observations': observations, 'valued_observation_counts': valued_counts,
            'distinct_valuation_times': len({x['valuation_time'] for x in observations if x['valuation_time']}),
            'file_errors': errors,
            'annualized_sharpe': None,
            'sharpe_note': 'Not estimated. Polls are not independent returns; daily marks need aligned times, '
                           'complete coverage and a sufficiently long frozen-strategy sample.',
            'valuation_note': 'Completed-session closing marks; no intraday valuation.' if market == 'us_daily'
                else 'Latest valid observations within each report; variable timestamps are not standardized daily closes.'}


def portfolio(value):
    equity = value.get('equity')
    equity = float(equity) if number(equity) else None
    result = {'starting_virtual_capital': CAPITAL, 'equity': equity,
              'profit_loss': round(equity-CAPITAL, 2) if equity is not None else None,
              'return': equity/CAPITAL-1 if equity is not None else None}
    for key in ('cash', 'cash_before_operating_allowance', 'positions', 'fees', 'slippage',
                'trading_costs', 'modeled_costs_paid', 'operating_allowance',
                'max_observed_drawdown', 'max_daily_drawdown', 'fills', 'simulated_fills',
                'closed_trades', 'receivables'):
        if key in value:
            result[key] = value[key]
    return result


def latest_valued(rows):
    for day, value, _ in reversed(rows):
        if isinstance(value.get('portfolios'), dict) and any(
                isinstance(v, dict) and number(v.get('equity')) for v in value['portfolios'].values()):
            return day, value
    return None, None


def comparison(portfolios, strategy, reference, same_period):
    left, right = portfolios.get(strategy, {}), portfolios.get(reference, {})
    valid = same_period and number(left.get('equity')) and number(right.get('equity'))
    return {'strategy': strategy, 'reference': reference, 'same_period_confirmed': bool(valid),
            'excess_return_percentage_points': (left['equity']-right['equity'])/CAPITAL*100 if valid else None,
            'note': 'Same-start simulated portfolios at the same mark; cost allowances may differ. '
                    'This is descriptive excess return, not established alpha.' if valid
                    else 'Start or common valuation is unconfirmed; no excess return calculated.'}


def multi_strategy(root, name, as_of):
    directory = root/name
    status, error = read_json(directory/'status.json')
    status = status or {}
    rows, errors = daily_reports(directory, as_of)
    health = freshness(status, as_of, error)
    blocked = status.get('status') == 'blocked'
    is_crypto = name == 'crypto'
    current = status if is_crypto else status.get('latest_report')
    current = current if isinstance(current, dict) else {}
    fallback_date = None
    fallback_source = None
    if not isinstance(current.get('portfolios'), dict):
        fallback_date, fallback = latest_valued(rows)
        current = fallback or {}
        fallback_source = 'daily_report' if fallback else None
        last_valid = status.get('last_valid_valuation') if is_crypto else None
        if isinstance(last_valid, dict) and isinstance(last_valid.get('portfolios'), dict):
            last_time = stamp(last_valid.get('time'))
            daily_time = stamp(current.get('time'))
            if last_time and last_time <= as_of + timedelta(seconds=2) and (not daily_time or last_time >= daily_time):
                current = last_valid
                fallback_date = None
                fallback_source = 'status.last_valid_valuation'
    is_fallback = fallback_source is not None
    parsed_valuation_time = stamp(current.get('time')) if is_crypto else None
    valuation_time = parsed_valuation_time.isoformat() if parsed_valuation_time else None
    session = None if is_crypto else current.get('date')
    raw_portfolios = current.get('portfolios', {})
    raw_portfolios = raw_portfolios if isinstance(raw_portfolios, dict) else {}
    has_valuation = any(isinstance(v, dict) and number(v.get('equity')) for v in raw_portfolios.values())
    if not has_valuation:
        valuation_state = 'absent'
    elif is_fallback or blocked or health['state'] != 'fresh':
        valuation_state = 'last_recorded_not_current'
    elif is_crypto:
        valuation_state = 'current_at_report_timestamp'
    else:
        valuation_state = 'completed_session_only'
    ports = {k: portfolio(v) for k, v in raw_portfolios.items() if isinstance(v, dict)}
    start_key = 'start_day' if is_crypto else 'start_session'
    decision_health = status.get('decision_health', current.get('decision_health'))
    decision_health = decision_health if isinstance(decision_health, dict) else {}
    start = status.get(start_key) or current.get(start_key) or (decision_health.get('start_day') if is_crypto else None)
    result = {'health': health, 'cycle_status': 'blocked' if blocked else 'reported' if status else 'unavailable',
              'broker_orders_enabled': status.get('broker_orders_enabled'), 'start': start,
              'valuation_state': valuation_state, 'valuation_time': valuation_time,
              'valuation_session': session, 'fallback_report_date': fallback_date, 'fallback_source': fallback_source,
              'portfolios': ports, 'daily_quality': sample_quality(rows, errors, 'crypto' if is_crypto else 'us_daily')}
    # Do not echo arbitrary broker/exception text from files; flags and timestamps suffice.
    if blocked:
        result['block_note'] = 'Worker explicitly reported a blocked cycle; inspect its operational logs.'
    if is_crypto:
        result.update(strategy_version=current.get('strategy_version'),
                      last_decision_day=status.get('last_decision_day', current.get('last_decision_day', decision_health.get('last_decision_day'))),
                      missed_days=status.get('missed_days', current.get('missed_days')),
                      decision_health={k: decision_health[k] for k in ('status', 'start_day', 'last_decision_day', 'latest_due_day', 'missing_utc_dates', 'missed_day_count') if k in decision_health},
                      comparison=comparison(ports, 'trend', 'buy_hold', bool(start)))
        journal = status.get('observation_journal_status')
        result['observation_journal_status'] = journal if journal in ('recorded', 'journal_capacity_exceeded', 'write_failed') else None
        result['observation_journal_alarm'] = journal in ('journal_capacity_exceeded', 'write_failed')
        diagnostic = status.get('quote_diagnostic')
        result['quote_diagnostic'] = {k: diagnostic[k] for k in ('code', 'symbol', 'observed_at', 'quote_time', 'quote_age_seconds') if k in diagnostic} if isinstance(diagnostic, dict) else None
        diagnostics = status.get('quote_diagnostics')
        result['quote_diagnostics'] = {symbol: {k: d[k] for k in ('quote_time', 'quote_age_seconds') if k in d}
            for symbol, d in diagnostics.items() if symbol in ('BTC/USD', 'ETH/USD') and isinstance(d, dict)} if isinstance(diagnostics, dict) else None
        result['valuation_age_seconds'] = freshness({'time': valuation_time}, as_of)['age_seconds']
    else:
        result.update(last_completed_session=status.get('last_completed_session'),
                      next_precommitted_session=status.get('next_precommitted_session'),
                      comparisons=[comparison(ports, 'momentum', x, bool(start)) for x in ('equal_weight', 'BIL')],
                      session_currency='Not inferred from weekdays. Compare with the exchange calendar before calling a session missing.')
    return result


def shadow_strategy(root, as_of):
    directory = root/'shadow'
    status, error = read_json(directory/'status.json')
    status = status or {}
    rows, errors = daily_reports(directory, as_of)
    health = freshness(status, as_of, error)
    snapshot = status
    source = 'status'
    if not snapshot and rows:
        snapshot, source = rows[-1][1], 'last_daily_report'
    valid = number(snapshot.get('equity'))
    value = dict(snapshot)
    if not valid:
        value['equity'] = snapshot.get('last_complete_equity')
    parsed_valued_at = stamp(snapshot.get('last_valued_at'))
    valued_at = parsed_valued_at.isoformat() if parsed_valued_at else None
    mark_health = freshness({'time': valued_at}, as_of)
    state = 'current_at_report_timestamp' if (valid and source == 'status' and health['state'] == 'fresh'
             and mark_health['state'] == 'fresh' and snapshot.get('status') != 'blocked') else 'last_recorded_not_current'
    if not number(value.get('equity')):
        state = 'absent'
    return {'health': health, 'broker_orders_enabled': status.get('broker_orders_enabled'),
            'valuation_state': state, 'valuation_time': valued_at,
            'current_equity_available': valid and state == 'current_at_report_timestamp',
            'portfolio': portfolio(value), 'source': source, 'valuation_missing': snapshot.get('valuation_missing'),
            'exit_data_issues': snapshot.get('exit_data_issues'), 'history_complete': snapshot.get('history_complete'),
            'unvalued_cycles': snapshot.get('unvalued_cycles'), 'decision_counts': snapshot.get('decision_counts'),
            'benchmark': {'equity': snapshot.get('benchmark_equity'), 'started': snapshot.get('benchmark_started'),
                          'comparable': False, 'note': 'Observer basket has a different start and price-only accounting; no alpha comparison.'},
            'daily_quality': sample_quality(rows, errors, 'us_intraday')}


def supervisor(root, as_of):
    document, error = read_json(root/'supervisor-health.json')
    document = document or {}
    health = freshness(document, as_of, error)
    result = {'health': health, 'last_reported_status': document.get('supervisor_status'),
              'current_worker_state': 'unknown', 'children': {},
              'note': 'A stale or missing heartbeat does not establish whether a worker is running or stopped.'}
    if health['state'] == 'fresh':
        result['current_worker_state'] = document.get('supervisor_status', 'unknown')
    children = document.get('children', {})
    children = children if isinstance(children, dict) else {}
    for name, child in children.items():
        if isinstance(child, dict):
            result['children'][name] = {k: child[k] for k in ('status', 'started_at', 'stopped_at', 'exit_code') if k in child}
    shadow = document.get('shadow')
    if isinstance(shadow, dict):
        result['shadow'] = {k: shadow[k] for k in ('status', 'time', 'generation') if k in shadow}
    return result


def build_report(state_dir, as_of=None):
    as_of = as_of or datetime.now(UTC)
    if as_of.tzinfo is None:
        raise ValueError('as_of must include a timezone')
    as_of = as_of.astimezone(UTC)
    root = Path(state_dir)
    observer, error = read_json(root/'status.json')
    return {'schema_version': 1, 'as_of': as_of.isoformat(), 'read_only': True,
            'scope': 'Independent virtual portfolios; never sum equities or interpret these as real returns.',
            'observer': {'health': freshness(observer, as_of, error), 'mode': (observer or {}).get('mode'),
                         'note': 'Observe mode monitors the source strategy; its uninvested ledger is not simulated strategy performance.'},
            'supervisor': supervisor(root, as_of), 'equity_shadow': shadow_strategy(root, as_of),
            'cross_asset': multi_strategy(root, 'cross_asset', as_of),
            'crypto': multi_strategy(root, 'crypto', as_of)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', default='/var/data/paperbot')
    parser.add_argument('--as-of', help='Timezone-aware ISO timestamp; defaults to current UTC time')
    args = parser.parse_args(argv)
    when = stamp(args.as_of) if args.as_of else datetime.now(UTC)
    if when is None:
        parser.error('--as-of must be an ISO timestamp with a timezone')
    print(json.dumps(build_report(args.state_dir, when), separators=(',', ':'), allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
