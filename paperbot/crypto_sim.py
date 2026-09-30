"""GET-only prospective crypto ledger. No brokerage order interface exists here."""
import argparse
import calendar
import copy
import hashlib
import json
import math
import os
import signal
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from .api import NoRedirect
from .state import Store, process_lock
from .strategy import GuardError, UTC, timestamp

SYMBOLS = ('BTC/USD', 'ETH/USD')
VERSION = 'crypto-v1-sma50-200-weekly-dailyexit-fee25-slip10'
CAPITAL = 45000.
FEE = .0025
SLIP = .001
BASE = '/v1beta3/crypto/us/'
OBSERVATION_MAX_BYTES = 64 * 1024 * 1024
OBSERVATION_SEGMENT_BYTES = 1024 * 1024


def atomic_text(path, body):
    """Publish a complete file; never expose a partially written JSON record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.'+path.name+'-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def decision_health(state, now):
    """Derive missed dates without modifying trading state or backfilling decisions."""
    if not state:
        return dict(status='not_initialized', missing_utc_dates=[], missed_day_count=0)
    start = datetime.fromisoformat(state['start_day']).date()
    today = now.date()
    window_ended = now.hour > 0 or now.minute >= 30
    latest_due = today if window_ended else today-timedelta(days=1)
    completed = {d['day'] for d in state.get('decisions', [])}
    if state.get('last_day'):
        completed.add(state['last_day'])
    missing = []
    day = start
    while day <= latest_due:
        if day.isoformat() not in completed:
            missing.append(day.isoformat())
        day += timedelta(days=1)
    if today < start:
        status = 'not_started'
    elif today.isoformat() in completed:
        status = 'completed'
    elif window_ended:
        status = 'missed'
    elif now.minute < 10:
        status = 'awaiting_window'
    else:
        status = 'pending_in_window'
    return dict(status=status, start_day=state['start_day'],
                last_decision_day=state.get('last_day'),
                latest_due_day=latest_due.isoformat() if latest_due >= start else None,
                missing_utc_dates=missing, missed_day_count=len(missing))


class QuoteGuardError(GuardError):
    def __init__(self, message, code, symbol, now, quote_time=None, age=None):
        super().__init__(message)
        self.diagnostic = dict(code=code, symbol=symbol, observed_at=now.isoformat(),
                               quote_time=quote_time, quote_age_seconds=age)


def safe_error(exc):
    """Never publish arbitrary exception text, HTTP bodies, headers or credentials."""
    if isinstance(exc, QuoteGuardError):
        return str(exc)
    allowed = {
        'Crypto data request failed', 'Missing crypto bars',
        'Repeated crypto pagination token', 'Incomplete crypto pagination',
        'Duplicate or misaligned crypto daily bar', 'Invalid crypto OHLC',
        'Missing crypto daily bar; decision blocked', 'Need 200 completed daily closes',
        'Nonpositive or nonfinite market value', 'Timestamp has no timezone',
        'UTC day changed during data retrieval; retry',
        'Crypto cash/position constraint breached', 'Crypto input evidence hash mismatch',
    }
    message = str(exc) if isinstance(exc, GuardError) else ''
    if message in allowed:
        return message
    prefix = 'Crypto data HTTP '
    if message.startswith(prefix) and len(message) == len(prefix)+3 and message[-3:].isdigit():
        return message
    return 'Internal error; inspect code and saved state'


def number(value):
    x = float(value)
    if not math.isfinite(x) or x <= 0:
        raise GuardError('Nonpositive or nonfinite market value')
    return x


def eligible(closes):
    if len(closes) < 200:
        raise GuardError('Need 200 completed daily closes')
    values = [number(v) for v in closes[-200:]]
    slow = mean(values)
    return values[-1] > slow and mean(values[-50:]) > slow


class Data:
    def __init__(self):
        self.opener = build_opener(NoRedirect())
        self.headers = {k: os.environ.get(v, '').strip() for k, v in (
            ('APCA-API-KEY-ID', 'ALPACA_API_KEY'), ('APCA-API-SECRET-KEY', 'ALPACA_SECRET_KEY'))}
        if not all(self.headers.values()):
            raise GuardError('Existing Alpaca environment credentials are required')

    def get(self, endpoint, params):
        if endpoint not in ('bars', 'latest/quotes'):
            raise GuardError('Crypto adapter permits market data GET requests only')
        request = Request('https://data.alpaca.markets' + BASE + endpoint + '?' + urlencode(params),
                          headers=self.headers, method='GET')
        try:
            with self.opener.open(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise GuardError('Crypto data HTTP ' + str(code)) from None
        except Exception:
            raise GuardError('Crypto data request failed') from None

    def history(self, now):
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=200)
        params = dict(symbols=','.join(SYMBOLS), timeframe='1Day', start=start.isoformat(),
                      end=(end-timedelta(microseconds=1)).isoformat(), limit=10000, sort='asc')
        rows = {s: [] for s in SYMBOLS}
        tokens = set()
        for _ in range(20):
            response = self.get('bars', params)
            if not isinstance(response.get('bars'), dict):
                raise GuardError('Missing crypto bars')
            for s in SYMBOLS:
                rows[s].extend(response['bars'].get(s, []))
            token = response.get('next_page_token')
            if not token:
                break
            if token in tokens:
                raise GuardError('Repeated crypto pagination token')
            tokens.add(token)
            params['page_token'] = token
        else:
            raise GuardError('Incomplete crypto pagination')
        expected = {start+timedelta(days=i) for i in range(200)}
        closes = {}
        for s in SYMBOLS:
            by_time = {}
            for row in rows[s]:
                t = timestamp(row['t'])
                if t in by_time or t not in expected:
                    raise GuardError('Duplicate or misaligned crypto daily bar')
                o, h, l, c = (number(row[k]) for k in ('o', 'h', 'l', 'c'))
                if not l <= min(o, c) <= max(o, c) <= h:
                    raise GuardError('Invalid crypto OHLC')
                by_time[t] = c
            if set(by_time) != expected:
                raise GuardError('Missing crypto daily bar; decision blocked')
            closes[s] = [by_time[t] for t in sorted(by_time)]
        return closes

    def quotes(self):
        return self.get('latest/quotes', dict(symbols=','.join(SYMBOLS))).get('quotes', {})


def validate_quotes(raw, now):
    output = {}
    for s in SYMBOLS:
        q = raw.get(s, {}) if isinstance(raw, dict) else {}
        if not isinstance(q, dict) or not q.get('t'):
            raise QuoteGuardError('Missing or stale crypto quote', 'missing_quote', s, now)
        try:
            quote_time = timestamp(q['t'])
            age = (now-quote_time).total_seconds()
        except (TypeError, ValueError, AttributeError, OverflowError, GuardError):
            raise QuoteGuardError('Invalid crypto quote timestamp', 'invalid_timestamp', s, now) from None
        if not -2 <= age <= 30:
            code = 'future_quote' if age < -2 else 'stale_quote'
            raise QuoteGuardError('Missing or stale crypto quote', code, s, now,
                                  quote_time.isoformat(), age)
        try:
            bid, ask = number(q['bp']), number(q['ap'])
        except (KeyError, TypeError, ValueError, OverflowError, GuardError):
            raise QuoteGuardError('Invalid crypto quote prices', 'invalid_prices', s, now,
                                  quote_time.isoformat(), age) from None
        if ask < bid or ask/bid-1 > .01:
            code = 'crossed_quote' if ask < bid else 'wide_quote'
            raise QuoteGuardError('Crossed or excessively wide crypto quote', code, s, now,
                                  quote_time.isoformat(), age)
        output[s] = dict(bid=bid, ask=ask, time=q['t'])
    return output


def portfolio():
    return dict(cash=CAPITAL, positions={s: 0. for s in SYMBOLS}, fees=0., slippage=0.,
                peak=CAPITAL, drawdown=0., fills=[], last_decision=None)


def nav(p, quotes):
    return p['cash'] + sum(q*quotes[s]['bid'] for s, q in p['positions'].items())


def rebalance(p, weights, quotes, now):
    """Assumed full quote fills. Buy fee withheld in coin; sell fee withheld in USD."""
    equity = nav(p, quotes)
    targets = {s: equity*weights.get(s, 0)/quotes[s]['ask'] for s in SYMBOLS}
    for side in ('sell', 'buy'):
        for s in SYMBOLS:
            difference = targets[s]-p['positions'][s]
            if side == 'sell' and difference < -1e-10:
                qty = -difference
                price = quotes[s]['bid']*(1-SLIP)
                amount = qty*price
                fee = amount*FEE
                p['cash'] += amount-fee
                p['positions'][s] -= qty
                slip = qty*quotes[s]['bid']*SLIP
            elif side == 'buy' and difference > 1e-10:
                price = quotes[s]['ask']*(1+SLIP)
                qty = min(difference/(1-FEE), max(0, p['cash'])/price)
                if qty*price < 1:
                    continue
                amount = qty*price
                fee = amount*FEE
                p['cash'] -= amount
                p['positions'][s] += qty*(1-FEE)
                slip = qty*quotes[s]['ask']*SLIP
            else:
                continue
            p['fees'] += fee
            p['slippage'] += slip
            p['fills'].append(dict(time=now.isoformat(), symbol=s, side=side, gross_qty=qty,
                                   assumed_price=price, fee_usd_equivalent=fee, quote=quotes[s]))
    if p['cash'] < -1e-6 or any(q < -1e-9 for q in p['positions'].values()):
        raise GuardError('Crypto cash/position constraint breached')


def initial(now):
    return dict(version=VERSION, started=now.isoformat(), start_day=(now.date()+timedelta(days=1)).isoformat(),
                last_day=None, missed_days=[], decisions=[], portfolios={k: portfolio() for k in ('trend', 'buy_hold')})


def decide(state, closes, quotes, now):
    """One prospective decision per UTC date, only 00:10–00:30. Never backfill trades."""
    day = now.date().isoformat()
    if day < state['start_day'] or state['last_day'] == day:
        return
    if now.hour != 0 or not 10 <= now.minute < 30:
        return
    old = state['last_day'] or (datetime.fromisoformat(state['start_day'])-timedelta(days=1)).date().isoformat()
    gap = (now.date()-datetime.fromisoformat(old).date()).days-1
    if gap:
        state['missed_days'].append(dict(before=day, count=gap))
    flags = {s: eligible(closes[s]) for s in SYMBOLS}
    p = state['portfolios']['trend']
    first = p['last_decision'] is None
    weights = {s: .5 if flags[s] else 0. for s in SYMBOLS}
    if first or now.weekday() == 0:
        rebalance(p, weights, quotes, now)
    else:
        # Exit failed trends daily without rebalancing surviving holdings.
        eq = nav(p, quotes)
        exit_weights = {s: p['positions'][s]*quotes[s]['ask']/eq if flags[s] else 0. for s in SYMBOLS}
        rebalance(p, exit_weights, quotes, now)
    p['last_decision'] = day
    benchmark = state['portfolios']['buy_hold']
    if benchmark['last_decision'] is None:
        rebalance(benchmark, {s: .5 for s in SYMBOLS}, quotes, now)
        benchmark['last_decision'] = day
    state['last_day'] = day
    state['decisions'].append(dict(day=day, observed_at=now.isoformat(), eligible=flags,
                                  weekly_or_initial=first or now.weekday()==0,
                                  input_sha256=hashlib.sha256(json.dumps(closes, sort_keys=True).encode()).hexdigest()))


def report(state, quotes, now):
    days = max(0, (now.date()-datetime.fromisoformat(state['start_day']).date()).days+1)
    # Standalone monthly allowance, deducted from NAV only, not spendable cash.
    operations = 0.
    d = datetime.fromisoformat(state['start_day']).date()
    for _ in range(days):
        operations += 106.25/calendar.monthrange(d.year, d.month)[1]
        d += timedelta(days=1)
    results = {}
    for kind, p in state['portfolios'].items():
        gross = nav(p, quotes)
        ops = operations if kind=='trend' else 0.
        net = gross-ops
        p['peak'] = max(p['peak'], net)
        p['drawdown'] = max(p['drawdown'], 1-net/p['peak'])
        results[kind] = dict(equity=round(net, 2), net_return=net/CAPITAL-1,
            equity_before_operating_allowance=round(gross, 2), cash=round(p['cash'], 2),
            positions=p['positions'], fees=round(p['fees'], 2), slippage=round(p['slippage'], 2),
            operating_allowance=round(ops, 2), max_observed_drawdown=p['drawdown'], fills=len(p['fills']))
    return dict(time=now.isoformat(), mode='crypto_simulation', broker_orders_enabled=False,
        strategy_version=VERSION, start_day=state['start_day'], last_decision_day=state['last_day'],
        missed_days=state['missed_days'], portfolios=results, cash_benchmark=CAPITAL,
        assumptions='Independent $45k portfolios. GET-only shadow simulation, not Alpaca paper orders. '
        'Full fills at fresh ask+10bps/bid-10bps, 25bps fee; bid marks. '
        'Trend standalone operations $106.25/month; cash earns zero. No tax, depth or guaranteed fills.')


class Runner:
    def __init__(self, data, directory):
        self.data = data
        self.store = Store(directory)
        self.s = self.store.load()
        if self.s and self.s.get('version') != VERSION:
            raise GuardError('Crypto state version mismatch; do not reset')
        self.history_day = None
        self.closes = None
        self.stage = 'startup'
        self.cycle_id = None
        self.quote_request_started = None
        self.quote_received = None
        self.last_valid = None
        # Older ledgers have only daily/status snapshots. Read a valid snapshot as
        # historical evidence; never interpret it as a current mark.
        candidates = [self.store.directory/'last-valid-valuation.json',
                      self.store.directory/'status.json']
        candidates.extend(sorted(self.store.directory.glob('daily-*.json'), reverse=True))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text())
                if (payload.get('mode') == 'crypto_simulation'
                        and payload.get('portfolios') and payload.get('status') != 'blocked'):
                    timestamp(payload['time'])
                    self.last_valid = payload
                    break
            except (OSError, ValueError, TypeError, KeyError, AttributeError, GuardError):
                continue

    def append_observation(self, result, now):
        """Append complete records; never delete evidence. Backup is needed at the cap."""
        root = self.store.directory/'observations'
        root.mkdir(parents=True, exist_ok=True)
        record = copy.deepcopy(result)
        record['observation_schema'] = 1
        # The last good result is already retained as its own successful record.
        if record.get('last_valid_valuation'):
            record['last_valid_valuation'] = {'time': record['last_valid_valuation']['time'],
                                             'is_current': False}
        line = json.dumps(record, sort_keys=True, allow_nan=False, separators=(',', ':'))+'\n'
        total = sum(p.stat().st_size for p in root.glob('????-??-??-*.jsonl'))
        if total+len(line.encode()) > OBSERVATION_MAX_BYTES:
            raise GuardError('Crypto observation journal capacity exceeded')
        prefix = now.date().isoformat()
        segments = sorted(root.glob(prefix+'-*.jsonl'))
        path = segments[-1] if segments else root/(prefix+'-0000.jsonl')
        previous = path.read_text() if path.exists() else ''
        if len(previous.encode())+len(line.encode()) > OBSERVATION_SEGMENT_BYTES and previous:
            index = int(path.stem.rsplit('-', 1)[1])+1
            path = root/(prefix+f'-{index:04d}.jsonl')
            previous = ''
        atomic_text(path, previous+line)

    def journal_observation(self, result, now):
        result['observation_journal_status'] = 'recorded'
        try:
            self.append_observation(result, now)
        except Exception as exc:
            capacity = (isinstance(exc, GuardError)
                        and str(exc) == 'Crypto observation journal capacity exceeded')
            result['observation_journal_status'] = 'journal_capacity_exceeded' if capacity else 'write_failed'
            result['observation_journal_error'] = (
                'Observation journal cap reached; preserve and back up records before increasing capacity. '
                'New observations are not retained; current status and daily reports continue.'
                if capacity else 'Observation journal write failed; current status and daily reports continue.')

    def blocked(self, exc, now):
        result = dict(time=now.isoformat(), mode='crypto_simulation', status='blocked',
                      message=safe_error(exc), broker_orders_enabled=False,
                      cycle_id=self.cycle_id or uuid.uuid4().hex,
                      quote_request_started_at=self.quote_request_started,
                      quote_received_at=self.quote_received,
                      stage=self.stage, equity=None, portfolios=None,
                      decision_health=decision_health(self.s, now),
                      last_valid_valuation=None)
        if isinstance(exc, QuoteGuardError):
            result['quote_diagnostic'] = exc.diagnostic
        if self.last_valid:
            result['last_valid_valuation'] = dict(time=self.last_valid['time'], is_current=False,
                                                 portfolios=self.last_valid['portfolios'])
        self.journal_observation(result, now)
        self.store.status(result)
        return result

    def tick(self):
        now = datetime.now(UTC)
        self.cycle_id = uuid.uuid4().hex
        self.quote_request_started = None
        self.quote_received = None
        self.stage = 'history'
        if self.history_day != now.date():
            self.closes = self.data.history(now)
            self.history_day = now.date()
        self.stage = 'quotes'
        self.quote_request_started = datetime.now(UTC).isoformat()
        raw = self.data.quotes()
        now = datetime.now(UTC)
        self.quote_received = now.isoformat()
        if self.history_day != now.date():
            raise GuardError('UTC day changed during data retrieval; retry')
        self.stage = 'quote_validation'
        quotes = validate_quotes(raw, now)
        self.stage = 'decision'
        state = copy.deepcopy(self.s) if self.s else initial(now)
        decide(state, self.closes, quotes, now)
        result = report(state, quotes, now)
        result['status'] = 'ok'
        result['cycle_id'] = self.cycle_id
        result['quote_request_started_at'] = self.quote_request_started
        result['quote_received_at'] = self.quote_received
        result['decision_health'] = decision_health(state, now)
        result['quote_diagnostics'] = {s: dict(bid=q['bid'], ask=q['ask'],
            quote_time=timestamp(q['time']).isoformat(),
            quote_age_seconds=(now-timestamp(q['time'])).total_seconds()) for s, q in quotes.items()}
        # Input evidence is written before committing ledger; repeated ticks are idempotent.
        self.stage = 'input_evidence'
        body = json.dumps(self.closes, sort_keys=True, allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        immutable = self.store.directory/'inputs'/('sha256-'+digest+'.json')
        if immutable.exists():
            if hashlib.sha256(immutable.read_bytes()).hexdigest() != digest:
                raise GuardError('Crypto input evidence hash mismatch')
        else:
            atomic_text(immutable, body)
        evidence = self.store.directory/('inputs-'+now.date().isoformat()+'.json')
        if not evidence.exists():
            atomic_text(evidence, json.dumps(self.closes, allow_nan=False))
        result['input_sha256'] = digest
        result['input_evidence'] = str(immutable.relative_to(self.store.directory))
        self.stage = 'ledger_commit'
        self.store.save(state)
        self.s = state
        self.stage = 'reporting'
        self.journal_observation(result, now)
        self.last_valid = copy.deepcopy(result)
        atomic_text(self.store.directory/'last-valid-valuation.json',
                    json.dumps(result, indent=2, allow_nan=False)+'\n')
        self.store.status(result)
        daily = self.store.directory/('daily-'+now.date().isoformat()+'.json')
        atomic_text(daily, json.dumps(result, indent=2, allow_nan=False)+'\n')
        self.stage = 'idle'
        return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['preflight', 'run'])
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--state-dir', default=str(Path(os.environ.get('PAPERBOT_STATE_DIR', '/var/data/paperbot'))/'crypto'))
    args = parser.parse_args(argv)
    data = Data()
    if args.command == 'preflight':
        closes = data.history(datetime.now(UTC))
        raw = data.quotes()
        quotes = validate_quotes(raw, datetime.now(UTC))
        print(json.dumps(dict(mode='crypto_preflight', broker_orders_enabled=False,
            days=len(closes[SYMBOLS[0]]), eligible={s: eligible(closes[s]) for s in SYMBOLS},
            quotes=quotes, note='No ledger initialized; no orders.')), flush=True)
        return 0
    stopped = False
    def stop(*unused):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with process_lock(args.state_dir):
        runner = Runner(data, args.state_dir)
        try:
            while not stopped:
                try:
                    print(json.dumps(runner.tick()), flush=True)
                except Exception as exc:
                    result = runner.blocked(exc, datetime.now(UTC))
                    print(json.dumps(result), flush=True)
                    if args.once:
                        return 2
                if args.once:
                    return 0
                for _ in range(300):
                    if stopped:
                        break
                    time.sleep(1)
        finally:
            runner.store.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except GuardError as exc:
        print('CRYPTO BLOCKED: '+str(exc), flush=True)
        raise SystemExit(2)
