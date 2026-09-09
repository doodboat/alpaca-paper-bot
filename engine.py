"""Persistent order intents, fill-derived accounting and a paper-only execution loop."""
import hashlib
import math
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_CEILING
from .api import APIError
from .feed import Feed
from .strategy import (SYMBOLS,CAPITAL,NY,UTC,INTERVAL,GuardError,timestamp,
                       session_time,entry_signal,exit_day,quantity)

TERMINAL={'filled','canceled','expired','rejected','done_for_day'}
RULES='volume-1.5/prior-20/or15-vwap/before12/hold3/long3/capital45000/v1'


def number(value):
    n=float(value)
    if not math.isfinite(n): raise GuardError('Non-finite broker value')
    return n


def identity(account):
    return hashlib.sha256(account['id'].encode()).hexdigest()


def account_ready(account):
    if account.get('status')!='ACTIVE' or account.get('currency')!='USD':
        raise GuardError('Need an active USD paper account')
    if account.get('account_blocked',True) or account.get('trading_blocked',True):
        raise GuardError('Paper account is blocked')


def ledger(state):
    cash=CAPITAL;qty={};reserved=0.;pending=[]
    for cid,item in state['orders'].items():
        order=item.get('broker') or {}
        filled=number(order.get('filled_qty') or 0)
        price=number(order.get('filled_avg_price') or 0)
        requested=int(item['request']['qty'])
        if filled<0 or filled>requested or (filled and price<=0):
            raise GuardError('Inconsistent order fill')
        if (filled and item['request']['side']=='buy'
                and price>number(item['request']['limit_price'])+0.000001):
            raise GuardError('Reported buy fill exceeds its limit price')
        sign=1 if item['request']['side']=='buy' else -1
        s=item['request']['symbol']
        qty[s]=qty.get(s,0)+sign*filled
        cash-=sign*filled*price
        if order.get('status') not in TERMINAL:
            pending.append(cid)
            if sign==1: reserved+=(requested-filled)*number(item['request']['limit_price'])
    if any(q<0 or q!=int(q) for q in qty.values()):
        raise GuardError('Unexpected short or fractional strategy position')
    return cash,{s:int(q) for s,q in qty.items() if q},reserved,pending


class Engine:
    def __init__(self,api,store,enable_orders=False):
        self.api=api;self.store=store;self.enable_orders=enable_orders;self.feed=Feed(api)
        self.s=store.load()
        if self.s and self.s.get('rules')!=RULES: raise GuardError('State belongs to a different rule version')

    def initialize(self,account,now,positions,open_orders):
        account_ready(account)
        cash=number(account['cash'])
        if positions or open_orders: raise GuardError('Initialize only with no positions and no open paper orders')
        if cash<CAPITAL: raise GuardError('Paper cash is below USD 45,000')
        if abs(number(account['equity'])-cash)>0.02: raise GuardError('Initial account equity does not equal cash')
        self.s={'schema':1,'rules':RULES,'account_hash':identity(account),'started':now.isoformat(),
                'initial_cash':cash,'excluded_cash':cash-CAPITAL,'orders':{},'positions':{},
                'days':{},'warmup':{},'last_heartbeat':None}
        self.store.save(self.s)
        self.store.event(now,'initialized',capital=CAPITAL,mode='paper' if self.enable_orders else 'observe')

    def sync(self,account,positions,open_orders,now):
        if identity(account)!=self.s['account_hash']: raise GuardError('Credentials point to a different paper account')
        # Detect manual orders, including already-closed ones, from pilot start.
        orders=self.api.audit_orders(self.s['started'])
        for o in orders+open_orders:
            cid=o.get('client_order_id')
            if cid not in self.s['orders']: raise GuardError('Unowned paper order detected; inspect account')
        for cid,item in self.s['orders'].items():
            if (item.get('broker') or {}).get('status') in TERMINAL: continue
            broker=self.api.order(cid)
            if broker is not None:
                req=item['request']
                if (broker.get('client_order_id')!=cid or broker.get('symbol')!=req['symbol']
                        or broker.get('side')!=req['side'] or number(broker['qty'])!=int(req['qty'])):
                    raise GuardError('Broker order identity does not match saved intent')
                prior=item.get('broker') or {}
                if number(broker.get('filled_qty') or 0)<number(prior.get('filled_qty') or 0):
                    raise GuardError('Broker fill quantity moved backwards')
                item['broker']=broker
                if (broker.get('status'),broker.get('filled_qty'),broker.get('filled_avg_price')) != (
                        prior.get('status'),prior.get('filled_qty'),prior.get('filled_avg_price')):
                    self.store.event(now,'order_update',client_order_id=cid,status=broker.get('status'),
                                     filled_qty=broker.get('filled_qty'),filled_avg_price=broker.get('filled_avg_price'))
        self.store.save(self.s)
        cash,qty,reserved,pending=ledger(self.s)
        actual={p['symbol']:number(p['qty']) for p in positions}
        if qty != actual: raise GuardError('Position reconciliation mismatch; new orders blocked')
        expected_account_cash=cash+self.s['excluded_cash']
        if abs(number(account['cash'])-expected_account_cash)>0.02:
            raise GuardError('Cash reconciliation mismatch; inspect fills, fees or manual activity')
        if cash < -0.02 or reserved>cash+0.02: raise GuardError('Strategy cash budget breached')
        for symbol in list(self.s['positions']):
            related=[self.s['orders'][cid] for cid in pending if self.s['orders'][cid]['request']['symbol']==symbol]
            if symbol not in qty and not related: del self.s['positions'][symbol]
        self.store.save(self.s)
        return cash,qty,reserved,pending

    def record_order(self,request,now,metadata=None):
        cid=request['client_order_id']
        if cid in self.s['orders']: return False
        self.store.event(now,'order_intent' if self.enable_orders else 'observe_order',**request)
        if not self.enable_orders: return False
        # Commit intent BEFORE network call. An unknown outcome is queried, never blindly resubmitted.
        self.s['orders'][cid]={'request':request,'created':now.isoformat(),'broker':None}
        if metadata: self.s['positions'][request['symbol']]=metadata
        self.store.save(self.s)
        try:
            response=self.api.submit(request)
            if (not isinstance(response,dict) or response.get('client_order_id')!=cid
                    or response.get('symbol')!=request['symbol'] or response.get('side')!=request['side']
                    or number(response.get('qty',-1))!=int(request['qty'])):
                raise GuardError('Unexpected order response; intent remains unresolved')
            self.s['orders'][cid]['broker']=response
            self.store.save(self.s)
            self.store.event(now,'order_update',client_order_id=cid,status=response.get('status'),
                             filled_qty=response.get('filled_qty'),filled_avg_price=response.get('filled_avg_price'))
        except GuardError:
            self.store.event(now,'order_outcome_unknown',client_order_id=cid)
            raise
        return True

    def benchmark(self,quotes,now):
        # Informational paper comparator only; never submits broker orders.
        for s in SYMBOLS:
            q=quotes.get(s) or {}
            if (not q.get('t') or not -5<=(now-timestamp(q['t'])).total_seconds()<=10
                    or number(q.get('ap') or 0)<=0 or number(q.get('bp') or 0)<=0):return None
        if 'benchmark' not in self.s:
            shares={s:quantity(CAPITAL/len(SYMBOLS),number(quotes[s]['ap'])*1.0005) for s in SYMBOLS}
            spent=sum(shares[s]*number(quotes[s]['ap'])*1.0005 for s in SYMBOLS)
            self.s['benchmark']={'started':now.isoformat(),'cash':CAPITAL-spent,'shares':shares,
                                 'note':'Indicative equal-weight price-only basket; no dividends/corporate-action adjustments. Review if an action occurs.'}
        b=self.s['benchmark']
        equity=b['cash']+sum(qty*number(quotes[s]['bp'])*.9995 for s,qty in b['shares'].items())
        self.store.save(self.s)
        return {'equity':round(equity,2),'net_return':equity/CAPITAL-1,'started':b['started'],
                'label':'Indicative price-only basket; corporate actions require review'}

    def exits(self,qty,pending,bars,now,session,is_open):
        day=now.astimezone(NY).date().isoformat()
        closing=session_time(session['date'],session['close']) if session else None
        for symbol,q in qty.items():
            meta=self.s['positions'].get(symbol)
            if not meta: raise GuardError('Missing exit metadata for strategy position')
            # A failed completed bar remains actionable after downtime or overnight.
            relevant=[b for b in bars.get(symbol,[]) if b.time+INTERVAL>timestamp(meta['signal_time'])]
            if any(b.close<meta['opening_low'] for b in relevant): meta['exit_required']=True
            scheduled=day>meta['exit_date'] or (day==meta['exit_date'] and closing and now>=closing-INTERVAL)
            forced=(self.store.directory/'FLATTEN').exists()
            due=meta.get('exit_required') or scheduled or forced
            self.store.save(self.s)
            if not due or not is_open: continue
            related=[self.s['orders'][cid] for cid in pending if self.s['orders'][cid]['request']['symbol']==symbol]
            if related:
                # Cancel remaining entry shares before submitting an exit; wait for confirmed cancellation.
                for item in related:
                    broker=item.get('broker') or {}
                    if item['request']['side']=='buy' and broker.get('id') and self.enable_orders:
                        self.api.cancel(broker['id'])
                continue
            previous=[o for o in self.s['orders'].values() if o['request']['side']=='sell'
                      and o['request']['symbol']==symbol]
            if previous and (now-timestamp(previous[-1]['created'])).total_seconds()<30: continue
            cid='pb1-'+day.replace('-','')+'-'+symbol+'-s-'+str(len(previous)+1)
            reason='flatten' if forced else 'scheduled' if scheduled else 'range_failed'
            self.store.event(now,'exit_signal',symbol=symbol,reason=reason)
            self.record_order({'symbol':symbol,'qty':str(q),'side':'sell','type':'market',
                               'time_in_force':'day','extended_hours':False,'client_order_id':cid},now)

    def tick(self):
        clock=self.api.clock();now=timestamp(clock['timestamp'])
        if abs((datetime.now(UTC)-now).total_seconds())>15: raise GuardError('Host clock differs from broker clock')
        day=now.astimezone(NY).date().isoformat()
        account=self.api.account();positions=self.api.positions();opened=self.api.orders(status='open')
        if len(opened)>=500: raise GuardError('Open order response may be truncated')
        if self.s is None: raise GuardError('State not initialized. Run the init command with a clean paper account.')
        cash,qty,reserved,pending=self.sync(account,positions,opened,now)
        equity=number(account['equity'])-self.s['excluded_cash']
        status={'time':now.isoformat(),'mode':'paper' if self.enable_orders else 'observe',
                'strategy_equity':round(equity,2),'strategy_cash':round(cash,2),
                'net_return':equity/CAPITAL-1,'positions':qty,'pending_orders':len(pending),
                'entries':'blocked','message':'Checks in progress'}
        self.store.status(status)
        calendar=self.feed.calendar(now)
        session=next((s for s in calendar if s['date']==day),None)
        if day not in self.s['days']:
            start=CAPITAL if self.s['started'][:10]==now.date().isoformat() else number(account['last_equity'])-self.s['excluded_cash']
            if start<=0: raise GuardError('Invalid daily strategy equity')
            self.s['days'][day]={'start_equity':start,'entered':list(qty),'decisions':[]}
        daily=self.s['days'][day]
        self.store.save(self.s)
        bars={};data_errors={}
        market_open=bool(clock['is_open']) and session is not None
        if session and now>=session_time(day,session['open'])+INTERVAL:
            try: bars,data_errors=self.feed.today(session,now)
            except GuardError as exc: data_errors={'all':str(exc)}
        # If offline at a prior close, inspect prior sessions for a missed range failure.
        if qty:
            earliest=min(self.s['positions'][s]['entry_date'] for s in qty)
            checked=self.s.setdefault('checked_prior_sessions',[])
            for prior in calendar:
                if earliest<=prior['date']<day and prior['date'] not in checked:
                    try:
                        past,errors=self.feed.today(prior,now)
                        if errors: raise GuardError('Incomplete prior session for held positions')
                        for s in qty:
                            meta=self.s['positions'][s]
                            if prior['date']>=meta['entry_date']:
                                relevant=[b for b in past[s] if b.time+INTERVAL>timestamp(meta['signal_time'])]
                                if any(b.close<meta['opening_low'] for b in relevant):meta['exit_required']=True
                        checked.append(prior['date']);self.store.save(self.s)
                    except GuardError as exc:data_errors['prior']=str(exc)
        self.exits(qty,pending,bars,now,session,market_open)
        account_ready(account)
        all_quotes={}
        if market_open:
            try:
                all_quotes=self.api.quotes(SYMBOLS)
                comparator=self.benchmark(all_quotes,now)
                if comparator:status['benchmark']=comparator
            except GuardError as exc:data_errors['quotes']=str(exc)
        reason=None
        if not market_open: reason='Market closed; no orders outside regular hours'
        elif data_errors: reason='Incomplete/stale market data: '+','.join(sorted(data_errors))
        elif (self.store.directory/'PAUSE').exists() or (self.store.directory/'FLATTEN').exists(): reason='Entries paused by operator'
        elif any(self.s['orders'][cid].get('broker') is None for cid in pending): reason='Unresolved order intent; waiting for broker confirmation'
        elif now>=session_time(day,session['close'])-INTERVAL or now.astimezone(NY).hour>=12: reason='Outside entry window'
        if reason:
            status.update(message=reason);self.store.status(status);self.store.save(self.s);return status
        if day not in self.s['warmup']:
            # Check real-time SIP entitlement before requesting the historical warm-up.
            history=self.feed.warmup(calendar,day)
            for s in SYMBOLS:
                asset=self.api.asset(s)
                if not asset.get('tradable') or asset.get('status')!='active' or asset.get('class')!='us_equity':
                    raise GuardError('Watchlist contains an unavailable equity: '+s)
            self.s['warmup']={day:history};self.store.save(self.s)
        signals=[]
        for symbol in SYMBOLS:
            if symbol in daily['entered'] or symbol in qty: continue
            signal=entry_signal(bars.get(symbol,[]),self.s['warmup'][day][symbol],now,session_time(day,session['close']))
            if signal and symbol+'|'+signal['signal_time'] not in daily['decisions']:
                signals.append((symbol,signal))
        quotes=self.api.quotes([s for s,_ in signals]) if signals else {}
        for symbol,signal in sorted(signals,key=lambda pair:(-pair[1]['relative_volume'],pair[0])):
            fresh_clock=self.api.clock()
            now=timestamp(fresh_clock['timestamp'])
            if (not fresh_clock['is_open'] or now.astimezone(NY).date().isoformat()!=day
                    or now>=session_time(day,session['close'])-INTERVAL
                    or not 5<=(now-timestamp(signal['signal_time'])).total_seconds()<=90):
                self.store.event(now,'entry_skipped',symbol=symbol,reason='signal_expired_during_checks')
                continue
            decision=symbol+'|'+signal['signal_time']
            daily['decisions'].append(decision);self.store.save(self.s)
            cash,qty,reserved,pending=ledger(self.s)
            occupied=set(qty)|{self.s['orders'][cid]['request']['symbol'] for cid in pending
                              if self.s['orders'][cid]['request']['side']=='buy'}
            if len(occupied)>=3:
                self.store.event(now,'entry_skipped',symbol=symbol,reason='position_limit');continue
            quote=quotes.get(symbol) or {}
            age=(now-timestamp(quote['t'])).total_seconds() if quote.get('t') else 999
            ask=number(quote.get('ap') or 0);bid=number(quote.get('bp') or 0)
            if not -5<=age<=10 or ask<=0 or bid<=0 or ask<bid:
                self.store.event(now,'entry_skipped',symbol=symbol,reason='stale_or_invalid_quote');continue
            limit=float((Decimal(str(ask))*Decimal('1.001')).quantize(Decimal('.01'),rounding=ROUND_CEILING))
            budget=min(daily['start_equity']/3,max(0,cash-reserved-10))
            shares=quantity(budget,limit)
            if shares<=0:continue
            # Consume this symbol's entry attempt for the day, including zero-fill IOC outcomes.
            daily['entered'].append(symbol);self.store.save(self.s)
            cid='pb1-'+day.replace('-','')+'-'+symbol+'-b'
            meta=dict(signal,entry_date=day,exit_date=exit_day(calendar,day),exit_required=False,
                      quote_ask=ask,quote_bid=bid,request_time=now.isoformat())
            self.store.event(now,'entry_signal',symbol=symbol,**meta)
            self.record_order({'symbol':symbol,'qty':str(shares),'side':'buy','type':'limit',
                               'limit_price':format(limit,'.2f'),'time_in_force':'ioc',
                               'extended_hours':False,'client_order_id':cid},now,meta)
        self.s['last_heartbeat']=now.isoformat();self.store.save(self.s)
        status.update(entries='enabled' if self.enable_orders else 'observe_only',message='Cycle complete')
        self.store.status(status)
        self.store.event(now,'equity',strategy_equity=equity,strategy_cash=cash)
        return status
