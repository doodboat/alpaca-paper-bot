"""Deterministic fake broker. No credentials, network or real order calls."""
from datetime import datetime,timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from .strategy import SYMBOLS,UTC,NY,Bar,GuardError
from .api import APIError
from .engine import Engine,ledger
from .state import Store


class FakeBroker:
    def __init__(self):
        self.now=datetime(2026,9,9,14,0,10,tzinfo=UTC)
        self.cash=100000.;self.holdings={};self.records={};self.submissions=[]
        self.timeout=None;self.partial=None;self.open=True;self.quote_age=0
    def clock(self):return {'timestamp':self.now.isoformat(),'is_open':self.open}
    def account(self):return {'id':'offline-demo-account','status':'ACTIVE','currency':'USD','cash':str(self.cash),
                              'equity':str(self.cash+sum(self.holdings.values())*102),'last_equity':'100000',
                              'account_blocked':False,'trading_blocked':False}
    def positions(self):return [{'symbol':s,'qty':str(q)} for s,q in self.holdings.items() if q]
    def orders(self,**params):return [o for o in self.records.values() if o['status'] not in ('filled','canceled')]
    def audit_orders(self,start):return list(self.records.values())
    def order(self,cid):return self.records.get(cid)
    def asset(self,s):return {'tradable':True,'status':'active','class':'us_equity'}
    def quotes(self,symbols):return {s:{'t':(self.now-timedelta(seconds=self.quote_age)).isoformat(),'ap':102,'bp':101.99} for s in symbols}
    def submit(self,request):
        self.submissions.append(dict(request))
        if self.timeout=='before':raise APIError()
        cid=request['client_order_id'];q=int(request['qty'])
        if cid in self.records:raise GuardError('Duplicate client ID in fake broker')
        filled=min(q,self.partial) if self.partial is not None and request['side']=='buy' else q
        result=dict(request,id='fake-'+cid,status='filled' if filled==q else 'canceled',
                    filled_qty=str(filled),filled_avg_price='102' if filled else None)
        sign=1 if request['side']=='buy' else -1
        self.cash-=sign*filled*102
        self.holdings[request['symbol']]=self.holdings.get(request['symbol'],0)+sign*filled
        self.records[cid]=result
        if self.timeout=='after':raise APIError()
        return result
    def cancel(self,oid):
        for o in self.records.values():
            if o['id']==oid:o['status']='canceled'


class FakeFeed:
    def __init__(self,api):self.api=api;self.stale=False;self.range_fail=False
    def calendar(self,now):
        result=[]
        for i in range(-40,16):
            d=now.astimezone(NY).date()+timedelta(days=i)
            if d.weekday()<5:result.append({'date':d.isoformat(),'open':'09:30','close':'16:00'})
        return result
    def warmup(self,calendar,day):return {s:[100.]*20 for s in SYMBOLS}
    def today(self,session,now):
        if self.stale:return {},{'all':'fixture missing data'}
        opening=datetime.fromisoformat(session['date']+'T09:30:00').replace(tzinfo=NY).astimezone(UTC)
        bars=[Bar(opening,100,101,99,100,200,100),Bar(opening+timedelta(minutes=15),100,103,99,102,100,101)]
        if self.range_fail:bars.append(Bar(opening+timedelta(minutes=30),102,103,97,98,100,99))
        return {s:bars for s in SYMBOLS},{}


def clock_patch(api):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls,tz=None):return api.now.astimezone(tz) if tz else api.now.replace(tzinfo=None)
    return patch('paperbot.engine.datetime',FixedDateTime)


def make_engine(directory,enable=True):
    api=FakeBroker();store=Store(directory);engine=Engine(api,store,enable)
    engine.feed=FakeFeed(api)
    engine.initialize(api.account(),api.now,[],[])
    return api,store,engine


def run_demo():
    with TemporaryDirectory(prefix='paperbot-demo-') as directory:
        api,store,engine=make_engine(directory)
        with clock_patch(api):
            engine.tick()
            assert len(api.submissions)==3
            engine.tick()
            assert len(api.submissions)==3
            restarted=Engine(api,store,True);restarted.feed=engine.feed
            restarted.tick()
            assert len(api.submissions)==3
            cash,qty,_,_=ledger(restarted.s)
            assert cash>=0 and len(qty)==3
            api.now=datetime(2026,9,11,19,45,10,tzinfo=UTC)
            restarted.feed.stale=True
            restarted.tick()
            assert len(api.submissions)==6 and not any(api.holdings.values())
        store.close()
    print('OFFLINE DEMO: PASS')
    print('Three budgeted entries; repeated cycle and restart produced no duplicates; third-session exits worked with stale entry data.')
    print('NETWORK CALLS: 0 | REAL/PAPER BROKER ORDERS: 0 (fake broker only)')
    return 0
