"""A small HTTP adapter whose broker hostname cannot be configured to live."""
import json
import os
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode, quote
from urllib.error import HTTPError
from .strategy import GuardError

PAPER = 'https://paper-api.alpaca.markets'
DATA = 'https://data.alpaca.markets'


class APIError(GuardError):
    def __init__(self, status=None):
        self.status = status
        super().__init__('Alpaca request failed: HTTP '+str(status) if status else 'Alpaca network/response error')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise GuardError('HTTP redirect blocked')


class Alpaca:
    def __init__(self, key, secret, enable_orders=False):
        if not key or not secret:
            raise GuardError('Paper API credentials are missing')
        for name in ('APCA_API_BASE_URL','ALPACA_BASE_URL'):
            if os.environ.get(name, PAPER).rstrip('/') != PAPER:
                raise GuardError('Non-paper broker URL rejected')
        self.key, self.secret = key, secret
        self.enable_orders = enable_orders
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, params=None, body=None, data=False):
        # Paths are internal; never accept arbitrary URLs or forward redirects.
        allowed_get = ('/v2/account','/v2/clock','/v2/calendar','/v2/positions',
                       '/v2/orders','/v2/orders:by_client_order_id')
        if data:
            allowed = method == 'GET' and path in ('/v2/stocks/bars','/v2/stocks/quotes/latest')
        else:
            allowed = ((method == 'GET' and (path in allowed_get or path.startswith('/v2/assets/')))
                       or (method == 'POST' and path == '/v2/orders')
                       or (method == 'DELETE' and path.startswith('/v2/orders/')))
        if not allowed or '..' in path or '?' in path or '#' in path or '://' in path:
            raise GuardError('Endpoint not permitted')
        if method != 'GET' and not self.enable_orders:
            raise GuardError('Order submission is disabled')
        url = (DATA if data else PAPER)+path
        if params:
            url += '?'+urlencode(params)
        req = Request(url, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'APCA-API-KEY-ID':self.key,'APCA-API-SECRET-KEY':self.secret,
                               'Accept':'application/json','Content-Type':'application/json'})
        # Never automatically retry mutations: their outcome may be unknown.
        try:
            with self.opener.open(req, timeout=20) as response:
                content = response.read()
                return json.loads(content) if content else None
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise APIError(status) from None
        except GuardError:
            raise
        except Exception:
            raise APIError() from None

    def account(self): return self.request('GET','/v2/account')
    def clock(self): return self.request('GET','/v2/clock')
    def positions(self): return self.request('GET','/v2/positions')
    def orders(self, **params): return self.request('GET','/v2/orders',dict(limit=500,**params))
    def audit_orders(self, start):
        result=[];cursor=None;seen=set()
        for _ in range(100):
            params={'status':'all','direction':'asc'}
            if cursor: params['after_order_id']=cursor
            else: params['after']=start
            batch=self.orders(**params)
            if not isinstance(batch,list): raise GuardError('Invalid order audit response')
            result.extend(batch)
            if len(batch)<500:return result
            cursor=batch[-1]['id']
            if cursor in seen:raise GuardError('Repeated order audit page')
            seen.add(cursor)
        raise GuardError('Order audit pagination incomplete')
    def order(self, cid):
        try:
            return self.request('GET','/v2/orders:by_client_order_id',{'client_order_id':cid})
        except APIError as exc:
            if exc.status == 404: return None
            raise
    def submit(self, order): return self.request('POST','/v2/orders',body=order)
    def cancel(self, order_id): return self.request('DELETE','/v2/orders/'+quote(order_id,safe=''))
    def calendar(self, start, end): return self.request('GET','/v2/calendar',{'start':start,'end':end})
    def asset(self, symbol): return self.request('GET','/v2/assets/'+quote(symbol,safe=''))
    def quotes(self, symbols):
        return self.request('GET','/v2/stocks/quotes/latest',{'symbols':','.join(symbols),'feed':'sip'},data=True)['quotes']

    def bars(self, symbols, start, end):
        output={s:[] for s in symbols}
        token=None
        seen=set()
        for _ in range(100):
            params={'symbols':','.join(symbols),'start':start,'end':end,'timeframe':'15Min',
                    'feed':'sip','adjustment':'split','sort':'asc','limit':10000}
            if token: params['page_token']=token
            result=self.request('GET','/v2/stocks/bars',params,data=True)
            if not isinstance(result.get('bars'),dict):
                raise GuardError('Invalid historical bar response')
            for s,rows in result['bars'].items():
                if s not in output: raise GuardError('Unexpected market-data symbol')
                output[s].extend(rows)
            token=result.get('next_page_token')
            if not token: return output
            if not isinstance(token,str) or token in seen: raise GuardError('Repeated market-data page token')
            seen.add(token)
        raise GuardError('Historical data pagination incomplete')
