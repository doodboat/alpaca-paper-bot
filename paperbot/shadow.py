"""Prospective, quote-based simulation. Never submits or cancels broker orders."""
import json
import math
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from .state import Store
from .strategy import CAPITAL, SYMBOLS, NY, UTC, INTERVAL, GuardError, timestamp, session_time, quantity
from .feed import Feed

VERSION='shadow-v1-askbid-5bps-fee1-nointerest'
SLIPPAGE=.0005
FEE=1.0  # Illustrative commission/fees per simulated fill, not a broker quote.


def valid_quote(q,now):
    try:
        ask,bid=float(q['ap']),float(q['bp'])
        age=(now-timestamp(q['t'])).total_seconds()
        return all(math.isfinite(v) for v in (ask,bid)) and 0<bid<=ask and -5<=age<=10
    except (KeyError,TypeError,ValueError,GuardError):return False


class Shadow:
    def __init__(self,api,source,directory):
        if getattr(api,'enable_orders',True):raise GuardError('Shadow requires an order-disabled API')
        self.api=api;self.source=source;self.store=Store(directory);self.feed=Feed(api)
        self.s=self.store.load()
        if self.s is None:
            cursor=source.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
            self.s={'version':VERSION,'started':datetime.now(UTC).isoformat(),'cursor':cursor,
                    'cash':CAPITAL,'positions':{},'decisions':{},'daily':{},'trades':[],
                    'costs':0.,'marks':{},'last_equity':CAPITAL,'peak':CAPITAL,'max_drawdown':0.,
                    'unvalued_cycles':0,'complete':True}
            self.store.save(self.s)
        if self.s.get('version')!=VERSION:raise GuardError('Shadow state version mismatch; do not reset')

    def close(self):self.store.close()

    def save(self):self.store.save(self.s)

    def reject(self,reason):
        d=self.s['decisions'];d[reason]=d.get(reason,0)+1

    def ingest(self,now,market_open,quotes=None):
        # Cursor, fills and cash are committed together. Replayed rows cannot duplicate trades.
        rows=self.source.db.execute("SELECT id,time,body FROM events WHERE id>? AND kind='entry_signal' ORDER BY id",
                                    (self.s['cursor'],)).fetchall()
        if quotes is None:quotes=self.api.quotes(SYMBOLS) if market_open else {}
        day=now.astimezone(NY).date().isoformat()
        daily=self.s['daily'].setdefault(day,{'start_equity':self.s['last_equity'],
                'start_mark_note':'Last complete observed liquidation valuation; may precede session open',
                'entries':0,'exits':0})
        for eid,event_time,body in rows:
            meta=json.loads(body);symbol=meta['symbol']
            age=(now-timestamp(event_time)).total_seconds()
            self.s['cursor']=eid
            if not market_open or not 0<=age<=90 or meta['entry_date']!=day:
                self.reject('missed_or_expired_event');self.s['complete']=False;continue
            if symbol in self.s['positions']:
                self.reject('already_held');continue
            if len(self.s['positions'])>=3:
                self.reject('position_limit');continue
            if now.astimezone(NY).hour>=12:
                self.reject('entry_window_closed');continue
            if not self.s.get('valuation_valid',False):
                self.reject('valuation_unavailable');continue
            # Preserve the observer's limit, but never backdate a fill to its signal quote.
            q=quotes.get(symbol,{})
            if not valid_quote(q,now):
                self.reject('entry_quote_unavailable');continue
            ask,bid=float(meta['quote_ask']),float(meta['quote_bid'])
            if not all(math.isfinite(x) for x in (ask,bid)) or not 0<bid<=ask:
                self.reject('invalid_entry_quote');continue
            limit=float((Decimal(str(ask))*Decimal('1.001')).quantize(Decimal('.01'),rounding=ROUND_CEILING))
            fill=float(q['ap'])*(1+SLIPPAGE)
            budget=min(daily['start_equity']/3,self.s['cash']-10)
            shares=quantity(max(0,budget-FEE),limit)
            if not shares or fill>limit:
                self.reject('insufficient_cash_or_limit');continue
            self.s['cash']-=shares*fill+FEE
            self.s['costs']+=shares*float(q['ap'])*SLIPPAGE+FEE
            self.s['positions'][symbol]=dict(meta,qty=shares,entry_price=fill,entry_fee=FEE,
                    simulated_at=now.isoformat(),source_event=eid)
            self.s['trades'].append({'side':'buy','symbol':symbol,'qty':shares,'price':fill,
                    'fee':FEE,'time':now.isoformat(),'source_event':eid,'assumed_fill':True})
            daily['entries']+=1;self.reject('simulated_entry')
        self.save()

    def exits(self,now,calendar,session,quotes):
        day=now.astimezone(NY).date().isoformat()
        issues=[]
        for symbol,meta in list(self.s['positions'].items()):
            if day>meta['exit_date']:
                self.s['complete']=False
            # Check all sessions since entry, including missed sessions after restart.
            for prior in calendar:
                if not meta['entry_date']<=prior['date']<=day:continue
                try:
                    bars,errors=self.feed.today(prior,now)
                    if symbol in errors or symbol not in bars:
                        issues.append(symbol+':incomplete_exit_bars');continue
                    if any(b.time+INTERVAL>timestamp(meta['signal_time']) and b.close<meta['opening_low']
                           for b in bars[symbol]):
                        meta['exit_required']=True
                        if prior['date']<day:self.s['complete']=False
                except GuardError:issues.append(symbol+':exit_data_request_failed')
            if not any(s['date']==meta['entry_date'] for s in calendar):
                issues.append(symbol+':entry_session_outside_calendar');self.s['complete']=False
            scheduled=day>meta['exit_date'] or (day==meta['exit_date'] and session and
                       now>=session_time(day,session['close'])-INTERVAL)
            if not (meta.get('exit_required') or scheduled):continue
            # Historical bar requests may take time; refresh before modeling an exit.
            q=self.api.quotes([symbol]).get(symbol,{})
            fresh_clock=self.api.clock();execution_time=timestamp(fresh_clock['timestamp'])
            if not fresh_clock['is_open'] or execution_time.astimezone(NY).date().isoformat()!=day:
                issues.append(symbol+':market_closed_during_exit_checks');continue
            if not valid_quote(q,execution_time):issues.append(symbol+':exit_quote_unavailable');continue
            fill=float(q['bp'])*(1-SLIPPAGE);shares=meta['qty']
            self.s['cash']+=shares*fill-FEE
            self.s['costs']+=shares*float(q['bp'])*SLIPPAGE+FEE
            pnl=shares*(fill-meta['entry_price'])-FEE-meta['entry_fee']
            self.s['trades'].append({'side':'sell','symbol':symbol,'qty':shares,'price':fill,'fee':FEE,
                'time':execution_time.isoformat(),'pnl':pnl,'reason':'scheduled' if scheduled else 'range_failed',
                'assumed_fill':True})
            del self.s['positions'][symbol]
            self.s['daily'][day]['exits']+=1
        self.save()
        return issues

    def mark(self,quotes,now):
        missing=[s for s in self.s['positions'] if not valid_quote(quotes.get(s,{}),now)]
        self.s['valuation_valid']=not missing
        if missing:
            self.s['unvalued_cycles']+=1
            return None,missing
        equity=self.s['cash']+sum(p['qty']*float(quotes[s]['bp'])*(1-SLIPPAGE)-FEE
                                     for s,p in self.s['positions'].items())
        self.s['last_equity']=equity;self.s['last_valued_at']=now.isoformat()
        self.s['peak']=max(self.s['peak'],equity)
        self.s['max_drawdown']=max(self.s['max_drawdown'],1-equity/self.s['peak'])
        return equity,[]

    def tick(self):
        clock=self.api.clock();now=timestamp(clock['timestamp'])
        if abs((datetime.now(UTC)-now).total_seconds())>15:raise GuardError('Shadow clock mismatch')
        day=now.astimezone(NY).date().isoformat()
        self.s['daily'].setdefault(day,{'start_equity':self.s['last_equity'],
          'start_mark_note':'Last complete observed liquidation valuation; may precede session open',
          'entries':0,'exits':0})
        calendar=self.feed.calendar(now)
        session=next((s for s in calendar if s['date']==day),None)
        opened=bool(clock['is_open']) and session is not None
        quotes=self.api.quotes(SYMBOLS) if opened else {}
        self.s['valuation_valid']=all(valid_quote(quotes.get(s,{}),now) for s in self.s['positions'])
        issues=self.exits(now,calendar,session,quotes) if opened else []
        if issues:self.s['complete']=False
        # Refresh entry/valuation snapshots after any exit-history requests.
        quotes=self.api.quotes(SYMBOLS) if opened else {}
        fresh_clock=self.api.clock();now=timestamp(fresh_clock['timestamp'])
        opened=opened and bool(fresh_clock['is_open']) and now.astimezone(NY).date().isoformat()==day
        self.s['valuation_valid']=all(valid_quote(quotes.get(s,{}),now) for s in self.s['positions'])
        self.ingest(now,opened and not issues,quotes)
        equity,missing=self.mark(quotes,now)
        # Copy the observer's price-only benchmark, keeping its independent start time visible.
        source_state=self.source.load() or {}
        benchmark=source_state.get('benchmark')
        benchmark_value=None
        if benchmark and all(valid_quote(quotes.get(s,{}),now) for s in benchmark['shares']):
            benchmark_value=benchmark['cash']+sum(q*float(quotes[s]['bp'])*.9995 for s,q in benchmark['shares'].items())
        result={'time':now.isoformat(),'mode':'shadow_simulation','broker_orders_enabled':False,
          'equity':round(equity,2) if equity is not None else None,
          'last_complete_equity':round(self.s['last_equity'],2),'last_valued_at':self.s.get('last_valued_at'),
          'cash':round(self.s['cash'],2),'net_return':equity/CAPITAL-1 if equity is not None else None,
          'positions':{s:p['qty'] for s,p in self.s['positions'].items()},
          'closed_trades':sum(t['side']=='sell' for t in self.s['trades']),
          'max_observed_drawdown':self.s['max_drawdown'],'modeled_costs_paid':round(self.s['costs'],2),
          'decision_counts':self.s['decisions'],'valuation_missing':missing,'exit_data_issues':issues,
          'history_complete':self.s['complete'],'unvalued_cycles':self.s['unvalued_cycles'],
          'benchmark_equity':round(benchmark_value,2) if benchmark_value is not None else None,
          'benchmark_started':benchmark.get('started') if benchmark else None,
          'benchmark_missing_quotes':[s for s in benchmark['shares'] if not valid_quote(quotes.get(s,{}),now)] if benchmark and opened else [],
          'benchmark_status':'available' if benchmark_value is not None else 'market_closed' if not opened else 'uninitialized_or_invalid_quotes',
          'benchmark_note':'Observer price-only basket; different start date, not an excess-return calculation',
          'assumptions':'Immediate full fills at fresh ask +5bps within original limit / current bid -5bps; $1 per fill. No operating costs, interest, taxes or corporate actions.',
          'started':self.s['started']}
        self.s['daily'][day]['latest']=result
        self.save();self.store.status(result)
        # One latest daily snapshot, overwritten atomically; ledger retains all simulated fills.
        path=self.store.directory/('daily-'+day+'.json');temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.s['daily'][day],indent=2,allow_nan=False)+'\n');temp.replace(path)
        return result
