from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from zoneinfo import ZoneInfo
import math

NY = ZoneInfo('America/New_York')
UTC = timezone.utc
SYMBOLS = ('AAPL','MSFT','AMZN','GOOGL','META','NVDA','TSLA','AMD','JPM','XOM','WMT','JNJ')
CAPITAL = 45000.0
INTERVAL = timedelta(minutes=15)


class GuardError(Exception):
    """Public, credential-free reason that the bot must not proceed."""


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:
        raise GuardError('Timestamp has no timezone')
    return result.astimezone(UTC)


def session_time(day, value):
    if 'T' in value:
        result = datetime.fromisoformat(value)
    else:
        result = datetime.fromisoformat(day+'T'+value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=NY)
    return result.astimezone(UTC)


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float

    @classmethod
    def parse(cls, raw):
        b = cls(timestamp(raw['t']), *(float(raw[k]) for k in ('o','h','l','c','v','vw')))
        if (not all(math.isfinite(x) for x in (b.open,b.high,b.low,b.close,b.volume,b.vwap))
                or b.low <= 0 or b.volume <= 0 or b.vwap <= 0
                or b.low > min(b.open,b.close) or b.high < max(b.open,b.close)):
            raise GuardError('Invalid price or volume bar')
        return b


def completed_bars(raw, opening, closing, now):
    # Five seconds are allowed for publication; no incomplete bars are accepted.
    cutoff = min(now-timedelta(seconds=5), closing)
    rows = {}
    for r in raw:
        t = timestamp(r['t'])
        if not opening <= t < closing or t+INTERVAL > cutoff:
            continue
        b = Bar.parse(r)
        if t in rows and rows[t] != b:
            raise GuardError('Conflicting duplicate bar')
        rows[t] = b
    expected = []
    t = opening
    while t+INTERVAL <= cutoff:
        expected.append(t)
        t += INTERVAL
    if set(rows) != set(expected):
        raise GuardError('Missing, stale or misaligned regular-session bars')
    return [rows[t] for t in expected]


def entry_signal(bars, opening_volumes, now, closing):
    if len(opening_volumes) != 20 or any(v <= 0 or not math.isfinite(v) for v in opening_volumes):
        raise GuardError('Need 20 valid prior opening volumes')
    if len(bars) < 2:
        return None
    completed = bars[-1].time+INTERVAL
    # No catch-up entries on old signals after downtime or delayed data.
    if not 5 <= (now-completed).total_seconds() <= 90:
        return None
    if completed.astimezone(NY).hour >= 12 or completed >= closing-INTERVAL:
        return None
    rv = bars[0].volume/mean(opening_volumes)
    vwap = sum(b.volume*b.vwap for b in bars)/sum(b.volume for b in bars)
    if rv >= 1.5 and bars[-1].close > bars[0].high and bars[-1].close > vwap:
        return {'signal_time':completed.isoformat(),'opening_low':bars[0].low,
                'opening_high':bars[0].high,'relative_volume':rv,'signal_close':bars[-1].close}
    return None


def exit_day(calendar, day):
    days = sorted(s['date'] for s in calendar if s['date'] >= day)
    if len(days)<3 or days[0] != day:
        raise GuardError('Missing future calendar for third-session exit')
    return days[2]


def quantity(budget, limit_price):
    if not math.isfinite(budget) or not math.isfinite(limit_price) or limit_price <= 0:
        raise GuardError('Invalid entry sizing')
    from decimal import Decimal, ROUND_FLOOR
    return max(0,int((Decimal(str(budget))/Decimal(str(limit_price))).to_integral_value(rounding=ROUND_FLOOR)))
