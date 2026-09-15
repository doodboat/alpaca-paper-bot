"""Prospective daily-bar simulation: precommitted decisions, no broker mutations."""
import argparse
import calendar as month_calendar
import copy
import hashlib
import json
import math
import os
import signal
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from .api import NoRedirect
from .state import Store, process_lock
from .strategy import GuardError, NY, UTC, timestamp, session_time

ASSETS=('SPY','EFA','EEM','IEF','GLD','DBC','UUP')
SYMBOLS=ASSETS+('BIL',)
KINDS=('momentum','equal_weight','BIL')
VERSION='cross-asset-v1-12to1-top2-absoluteBIL-monthly-10bps-106.25ops'
COST=.001
SUPPORTED={'cash_dividends','forward_splits','reverse_splits'}


def num(x,positive=False):
    v=float(x)
    if not math.isfinite(v) or (positive and v<=0):raise GuardError('Invalid cross-asset numeric value')
    return v


def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class Data:
    """Fixed-host GET-only adapter; no account, submit or cancel methods."""
    def __init__(self):
        self.key=os.environ.get('ALPACA_API_KEY','').strip()
        self.secret=os.environ.get('ALPACA_SECRET_KEY','').strip()
        if not self.key or not self.secret:raise GuardError('Cross-asset needs existing Alpaca environment credentials')
        self.opener=build_opener(NoRedirect())

    def get(self,path,params=None):
        hosts={'/v2/clock':'https://paper-api.alpaca.markets',
               '/v2/calendar':'https://paper-api.alpaca.markets',
               '/v2/stocks/bars':'https://data.alpaca.markets',
               '/v1/corporate-actions':'https://data.alpaca.markets'}
        if path not in hosts:raise GuardError('Cross-asset endpoint not allowed')
        url=hosts[path]+path+('?' + urlencode(params) if params else '')
        req=Request(url,method='GET',headers={'APCA-API-KEY-ID':self.key,'APCA-API-SECRET-KEY':self.secret})
        try:
            with self.opener.open(req,timeout=20) as r:return json.load(r)
        except HTTPError as e:
            code=e.code;e.close();raise GuardError('Cross-asset data request HTTP '+str(code)) from None
        except Exception:raise GuardError('Cross-asset data request failed') from None

    def collect(self,path,params,field):
        output={};tokens=set()
        for _ in range(100):
            result=self.get(path,params)
            content=result.get(field)
            if not isinstance(content,dict):raise GuardError('Unexpected cross-asset response structure: '+field)
            for k,rows in content.items():
                if not isinstance(rows,list):raise GuardError('Unexpected cross-asset rows')
                output.setdefault(k,[]).extend(rows)
            token=result.get('next_page_token')
            if not token:return output
            if not isinstance(token,str) or token in tokens:raise GuardError('Cross-asset pagination repeated')
            tokens.add(token);params={**params,'page_token':token}
        raise GuardError('Cross-asset pagination incomplete')

    def snapshot(self,now,start):
        end=now.astimezone(NY).date()
        cal=self.get('/v2/calendar',{'start':start,'end':(end+timedelta(days=40)).isoformat()})
        if not isinstance(cal,list) or len({s['date'] for s in cal})!=len(cal):raise GuardError('Invalid exchange calendar')
        cal=sorted(cal,key=lambda s:s['date'])
        completed=[s for s in cal if session_time(s['date'],s['close'])+timedelta(minutes=20)<=now]
        if len(completed)<253:raise GuardError('Need at least 253 complete historical sessions')
        last=completed[-1]['date']
        raw=self.collect('/v2/stocks/bars',{'symbols':','.join(SYMBOLS),'start':start,
             'end':last+'T23:59:59Z','timeframe':'1Day','adjustment':'raw','feed':'sip','sort':'asc','limit':10000},'bars')
        # Endpoint filters process_date, not ex_date. Include already-known future processing dates.
        actions=self.collect('/v1/corporate-actions',{'symbols':','.join(SYMBOLS),'start':start,
             'end':(end+timedelta(days=366)).isoformat(),'data_quality':'all','sort':'asc','limit':1000},'corporate_actions')
        return validate(cal,completed,raw,actions)


def validate(cal,completed,raw,actions):
    days=[s['date'] for s in completed];bars={}
    for s in SYMBOLS:
        bs={}
        for b in raw.get(s,[]):
            d=timestamp(b['t']).astimezone(NY).date().isoformat()
            values={k:num(b[k],True) for k in ('o','h','l','c','v')}
            if not values['l']<=min(values['o'],values['c'])<=max(values['o'],values['c'])<=values['h']:
                raise GuardError('Invalid daily bar: '+s)
            if d in bs:raise GuardError('Duplicate daily bar: '+s)
            bs[d]=values
        if set(bs)!=set(days):raise GuardError('Daily calendar coverage mismatch: '+s)
        bars[s]=bs
    seen=set();normalized=[]
    for kind,rows in actions.items():
        for a in rows:
            if a.get('symbol') not in SYMBOLS:raise GuardError('Unexpected corporate-action symbol')
            if not a.get('id') or a['id'] in seen:raise GuardError('Duplicate/missing corporate-action ID')
            seen.add(a['id'])
            ex=a.get('ex_date')
            if not ex:raise GuardError('Incomplete corporate action needs review')
            date.fromisoformat(ex)
            if ex<=days[-1] and ex>=days[0] and kind not in SUPPORTED:
                raise GuardError('Unsupported corporate action needs review: '+kind)
            if kind in SUPPORTED:
                row=dict(id=a['id'],symbol=a['symbol'],ex_date=ex,kind=kind)
                if kind=='cash_dividends':
                    rate=num(a['rate'])
                    if rate<0:raise GuardError('Negative distribution')
                    row.update(rate=rate,payable_date=a.get('payable_date'))
                    if row['payable_date']:date.fromisoformat(row['payable_date'])
                else:row['ratio']=num(a['new_rate'],True)/num(a['old_rate'],True)
                normalized.append(row)
    tri={s:[] for s in SYMBOLS}
    for s in SYMBOLS:
        value=1.
        for i,d in enumerate(days):
            if i:
                todays=[a for a in normalized if a['symbol']==s and a['ex_date']==d]
                div=sum(a['rate'] for a in todays if a['kind']=='cash_dividends')
                split=math.prod(a['ratio'] for a in todays if a['kind']!='cash_dividends')
                value*=split*(bars[s][d]['c']+div)/bars[s][days[i-1]]['c']
            tri[s].append(num(value,True))
    return {'calendar':cal,'days':days,'bars':bars,'actions':normalized,'tri':tri}


def weights(tri):
    if any(len(tri[s])<253 for s in SYMBOLS):raise GuardError('Insufficient momentum lookback')
    rank=sorted(ASSETS,key=lambda s:(-(tri[s][-22]/tri[s][-253]-1),s))[:2]
    cash=tri['BIL'][-1]/tri['BIL'][-253]-1
    w={s:.5 for s in rank if tri[s][-1]/tri[s][-253]-1>cash}
    w['BIL']=1-sum(w.values())
    return w


def initial(now,session):
    return {'version':VERSION,'created':now.isoformat(),'start_session':session['date'],
      'last_day':None,'plans':{},'actions_used':{},'reports':[],
      'portfolios':{k:{'cash':45000.,'shares':{s:0 for s in SYMBOLS},'receivables':[],
                      'gross':45000.,'ops':0.,'costs':0.,'trades':[],'peak':45000.,'drawdown':0.} for k in KINDS}}


def plan(state,snapshot,session,now):
    d=session['date']
    if now>=session_time(d,session['open']):raise GuardError('Cannot precommit after session open')
    if d in state['plans']:return
    prior=snapshot['days'][-1]
    if prior>=d:raise GuardError('Allocation input is not prior-session data')
    first=d==state['start_session'];monthly=d[:7]!=prior[:7]
    ws={'momentum':weights(snapshot['tri']),'equal_weight':{s:1/7 for s in ASSETS},'BIL':{'BIL':1.}}
    targets={}
    for kind,p in state['portfolios'].items():
        if first or (monthly and kind!='BIL'):
            targets[kind]={s:math.floor(p['gross']*ws[kind].get(s,0)/snapshot['bars'][s][prior]['c']) for s in SYMBOLS}
    state['plans'][d]={'created':now.isoformat(),'prior_session':prior,'weights':ws,'targets':targets,
                       'input_hash':digest(snapshot)}


def accrue_ops(first,last):
    fees=0.;d=date.fromisoformat(first)
    while d<=date.fromisoformat(last):
        fees+=106.25/month_calendar.monthrange(d.year,d.month)[1];d+=timedelta(days=1)
    return fees


def settle_day(state,snapshot,day):
    """Atomic in caller; fills use opens only for allocations committed before that open."""
    if state['last_day'] and day<=state['last_day']:return
    if day not in state['plans']:raise GuardError('Missing precommitted daily plan: '+day)
    dec=state['plans'][day]
    sess=next(s for s in snapshot['calendar'] if s['date']==day)
    if timestamp(dec['created'])>=session_time(day,sess['open']):raise GuardError('Late allocation decision rejected')
    relevant=[a for a in snapshot['actions'] if a['ex_date']==day]
    for a in relevant:state['actions_used'][a['id']]=a
    report={'date':day,'mode':'cross_asset_simulation','broker_orders_enabled':False,'portfolios':{}}
    fee_start=(date.fromisoformat(state['last_day'])+timedelta(days=1)).isoformat() if state['last_day'] else day
    for kind,p in state['portfolios'].items():
        for a in p['receivables']:
            if not a['paid'] and a['payable_date'] and a['payable_date']<=day:
                p['cash']+=a['amount'];a['paid']=True
        for a in relevant:
            q=p['shares'][a['symbol']]
            if a['kind']!='cash_dividends':
                if q:raise GuardError('Holding-period split requires review; portfolio not advanced')
                continue
            if q:
                amount=q*a['rate'];paid=bool(a['payable_date'] and a['payable_date']<=day)
                if paid:p['cash']+=amount
                p['receivables'].append(dict(id=a['id'],amount=amount,payable_date=a['payable_date'],paid=paid))
        def trade(s,q):
            if not q:return
            ref=snapshot['bars'][s][day]['o'];fill=ref*(1+COST if q>0 else 1-COST)
            p['cash']-=q*fill;p['shares'][s]+=q;p['costs']+=abs(q)*ref*COST
            if p['cash'] < -1e-6 or p['shares'][s]<0:raise GuardError('Cross-asset budget breached')
            p['trades'].append(dict(date=day,symbol=s,shares_change=q,assumed_price=fill,cost=abs(q)*ref*COST,
                                     allocation_created=dec['created']))
        target=dec['targets'].get(kind)
        if target:
            for s in SYMBOLS:
                if target[s]<p['shares'][s]:trade(s,target[s]-p['shares'][s])
            for s in sorted(SYMBOLS,key=lambda s:(s=='BIL',s)):
                affordable=math.floor(max(0,p['cash'])/(snapshot['bars'][s][day]['o']*(1+COST)))
                trade(s,min(max(0,target[s]-p['shares'][s]),affordable))
        receivable=sum(a['amount'] for a in p['receivables'] if not a['paid'])
        p['gross']=p['cash']+receivable+sum(q*snapshot['bars'][s][day]['c'] for s,q in p['shares'].items())
        if kind!='BIL':p['ops']+=accrue_ops(fee_start,day)
        net=p['gross']-p['ops'];p['peak']=max(p['peak'],net);p['drawdown']=max(p['drawdown'],1-net/p['peak'])
        report['portfolios'][kind]={'equity':round(net,2),'gross_equity':round(p['gross'],2),'net_return':net/45000-1,
          'cash_before_operating_allowance':round(p['cash'],2),'receivables':round(receivable,2),
          'positions':{s:q for s,q in p['shares'].items() if q},'trading_costs':round(p['costs'],2),
          'operating_allowance':round(p['ops'],2),'max_daily_drawdown':p['drawdown'],
          'simulated_fills':len(p['trades'])}
    state['last_day']=day;state['reports'].append(report)


def check_revisions(state,snapshot):
    if not state['last_day']:return
    current={a['id']:a for a in snapshot['actions'] if state['start_session']<=a['ex_date']<=state['last_day']}
    if current!=state['actions_used']:raise GuardError('Past corporate actions revised/missing: review before continuing')


class Runner:
    def __init__(self,data,directory):
        self.data=data;self.store=Store(directory);self.s=self.store.load()
        if self.s and self.s.get('version')!=VERSION:raise GuardError('Cross-asset state version mismatch')

    def tick(self):
        now=timestamp(self.data.get('/v2/clock')['timestamp'])
        if abs((datetime.now(UTC)-now).total_seconds())>30:raise GuardError('Cross-asset host/broker clock mismatch')
        start=(date.fromisoformat(self.s['start_session']) if self.s else now.date())-timedelta(days=800)
        snap=self.data.snapshot(now,start.isoformat())
        # Recheck time after potentially lengthy data requests: never precommit using a stale clock.
        now=timestamp(self.data.get('/v2/clock')['timestamp'])
        if self.s is None:
            upcoming=[s for s in snap['calendar'] if session_time(s['date'],s['open'])>now]
            if not upcoming:raise GuardError('No future session found')
            state=initial(now,upcoming[0])
        else:state=copy.deepcopy(self.s)
        check_revisions(state,snap)
        for d in snap['days']:
            if d>=state['start_session'] and (not state['last_day'] or d>state['last_day']):settle_day(state,snap,d)
        # Only plan the immediately following exchange session from the latest completed close.
        nxt=next((s for s in snap['calendar'] if s['date']>snap['days'][-1]),None)
        if nxt and nxt['date']>=state['start_session'] and session_time(nxt['date'],nxt['open'])>now:
            plan(state,snap,nxt,now)
        result={'time':now.isoformat(),'mode':'cross_asset_simulation','broker_orders_enabled':False,
          'start_session':state['start_session'],'last_completed_session':state['last_day'],
          'next_precommitted_session':max(state['plans']) if state['plans'] else None,
          'next_weights':state['plans'][max(state['plans'])]['weights'] if state['plans'] else None,
          'next_plan_rebalances':list(state['plans'][max(state['plans'])]['targets']) if state['plans'] else [],
          'latest_report':state['reports'][-1] if state['reports'] else None,
          'note':'Daily-open assumed fills from precommitted plans; no intraday performance. $45k EACH hypothetical portfolio, no account funding.',
          'limitations':'Corporate-action feed may be delayed; revisions halt accounting. Operating allowance reduces reported NAV, not gross sizing. No tax, interest or guaranteed fills.'}
        # Preserve the observed inputs before committing any decision or portfolio progression.
        inputs=self.store.directory/'inputs';inputs.mkdir(exist_ok=True)
        p=inputs/(digest(snap)+'.json')
        if not p.exists():p.write_text(json.dumps(snap,allow_nan=False))
        self.store.save(state);self.s=state;self.store.status(result)
        for report in state['reports'][-1:]:
            p=self.store.directory/('daily-'+report['date']+'.json');tmp=p.with_suffix('.tmp')
            tmp.write_text(json.dumps(report,indent=2,allow_nan=False));tmp.replace(p)
        return result


def preflight(data):
    now=timestamp(data.get('/v2/clock')['timestamp'])
    snap=data.snapshot(now,(now.date()-timedelta(days=800)).isoformat())
    return {'mode':'cross_asset_preflight','broker_orders_enabled':False,'symbols':list(SYMBOLS),
      'completed_sessions':len(snap['days']),'latest_input_session':snap['days'][-1],
      'momentum_weights_from_latest_close':weights(snap['tri']),
      'corporate_action_records':len(snap['actions']),
      'note':'Data/schema check only. No simulated portfolio initialized. No orders.'}


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['preflight','run'])
    parser.add_argument('--state-dir',default=str(Path(os.environ.get('PAPERBOT_STATE_DIR','/var/data/paperbot'))/'cross_asset'))
    parser.add_argument('--once',action='store_true');args=parser.parse_args(argv)
    data=Data()
    if args.command=='preflight':print(json.dumps(preflight(data)),flush=True);return 0
    with process_lock(args.state_dir):
        runner=Runner(data,args.state_dir);stopped=False
        def stop(*unused):
            nonlocal stopped
            stopped=True
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        try:
            while not stopped:
                try:print(json.dumps(runner.tick()),flush=True)
                except GuardError as e:
                    result={'time':datetime.now(UTC).isoformat(),'mode':'cross_asset_simulation','status':'blocked','message':str(e),'broker_orders_enabled':False}
                    runner.store.status(result);print(json.dumps(result),flush=True)
                    if args.once:return 2
                except Exception:
                    result={'mode':'cross_asset_simulation','status':'blocked','message':'Internal data/schema error; inspect cross-asset inputs','broker_orders_enabled':False}
                    runner.store.status(result);print(json.dumps(result),flush=True);return 2
                if args.once:return 0
                for _ in range(1800):
                    if stopped:break
                    time.sleep(1)
        finally:runner.store.close()
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except GuardError as e:print('CROSS-ASSET BLOCKED: '+str(e),flush=True);raise SystemExit(2)
    except Exception:print('CROSS-ASSET BLOCKED: unexpected data/schema error; no broker orders',flush=True);raise SystemExit(2)
