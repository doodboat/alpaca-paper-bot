import argparse
import csv
import getpass
import json
import os
import signal
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from .api import Alpaca,APIError
from .engine import Engine,account_ready
from .state import Store,process_lock
from .strategy import SYMBOLS,UTC,GuardError,timestamp


def credentials():
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=os.environ.get('ALPACA_API_KEY') or getpass.getpass('PAPER API key (hidden): ')
        secret=os.environ.get('ALPACA_SECRET_KEY') or getpass.getpass('PAPER secret (hidden): ')
    return key.strip(),secret.strip()


def preflight(api):
    account=api.account();account_ready(account)
    print('PAPER CONNECTION: OK')
    positions=api.positions();orders=api.orders(status='open')
    print('CLEAN ACCOUNT: '+('OK' if not positions and not orders else 'REVIEW EXISTING POSITIONS/ORDERS'))
    print('BUDGET: USD 45,000; no balance changes made')
    clock=api.clock();now=timestamp(clock['timestamp'])
    if abs((datetime.now(UTC)-now).total_seconds())>15:raise GuardError('Host/broker clock mismatch')
    try:
        quotes=api.quotes(SYMBOLS)
    except APIError as exc:
        print('SIP ACCESS: '+('HTTP '+str(exc.status) if exc.status else 'NETWORK ERROR'))
        print('Orders remain disabled. Confirm data entitlement before the paper pilot.')
        return False
    missing=[s for s in SYMBOLS if not (quotes.get(s) or {}).get('t')]
    if missing:raise GuardError('SIP quotes missing for part of the watchlist')
    print('SIP ACCESS: OK')
    if clock['is_open']:
        fresh=all(-5<=(now-timestamp(quotes[s]['t'])).total_seconds()<=10 for s in SYMBOLS)
        print('QUOTE FRESHNESS: '+('OK' if fresh else 'WAIT / INVESTIGATE STALE QUOTES'))
    else:print('QUOTE FRESHNESS: Check again during regular US market hours')
    print('TRADING: OFF. Historical warm-up and runtime reconciliation are checked at startup.')
    return not positions and not orders


def main(argv=None):
    parser=argparse.ArgumentParser(description='Alpaca paper-only strategy pilot. Default run mode is read-only.')
    parser.add_argument('command',choices=['demo','preflight','init','run','status','pause','resume','flatten','export'])
    parser.add_argument('--state-dir',default=os.environ.get('PAPERBOT_STATE_DIR','state'))
    parser.add_argument('--enable-paper-orders',action='store_true')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args(argv)
    root=Path(args.state_dir)
    if args.command=='demo':
        from .demo import run_demo
        return run_demo()
    if args.command in ('pause','resume','flatten'):
        root.mkdir(parents=True,exist_ok=True)
        if args.command=='pause':(root/'PAUSE').touch()
        elif args.command=='flatten':(root/'FLATTEN').touch()
        else:
            (root/'PAUSE').unlink(missing_ok=True)
            (root/'FLATTEN').unlink(missing_ok=True)
        print('Control flag updated. Flatten only acts when an order-enabled process is running and the market is open.')
        return 0
    if args.command=='status':
        path=root/'status.json'
        print(path.read_text() if path.exists() else 'No heartbeat yet. The bot may not be running.')
        return 0
    if args.command=='export':
        store=Store(root)
        path=root/'events.csv'
        with path.open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(['id','time','event','details'])
            writer.writerows(store.db.execute('SELECT id,time,kind,body FROM events ORDER BY id'))
        store.close();print(str(path));return 0
    cross_requested=os.environ.get('PAPERBOT_CROSS_ASSET','0')=='1' and args.command=='run'
    if cross_requested and args.enable_paper_orders:
        raise GuardError('Cross-asset forward testing requires observe mode')
    shadow_requested=os.environ.get('PAPERBOT_SHADOW','0')=='1' and args.command=='run'
    if shadow_requested and args.enable_paper_orders:
        raise GuardError('Shadow monitoring requires observe mode; paper orders must be disabled')
    key,secret=credentials()
    api=Alpaca(key,secret,enable_orders=args.command=='run' and args.enable_paper_orders)
    key=secret=None
    if args.command=='preflight':return 0 if preflight(api) else 2
    with process_lock(root):
        store=Store(root)
        shadow=None
        cross_process=None
        try:
            engine=Engine(api,store,enable_orders=args.enable_paper_orders)
            if args.command=='init':
                if engine.s is not None:raise GuardError('State already initialized; do not reset it')
                clock=api.clock();now=timestamp(clock['timestamp'])
                engine.initialize(api.account(),now,api.positions(),api.orders(status='open'))
                print('PAPER STATE INITIALIZED: USD 45,000 allocation. No orders placed.')
                return 0
            if shadow_requested:
                from .shadow import Shadow
                shadow=Shadow(api,store,root/'shadow')
                print('SHADOW SIMULATION: prospective assumed fills; no broker orders.',flush=True)
            if cross_requested:
                cross_process=subprocess.Popen([sys.executable,'-u','-m','paperbot.cross_asset','run',
                                                '--state-dir',str(root/'cross_asset')])
            stopping=False
            def stop(signum,frame):
                nonlocal stopping
                stopping=True
            signal.signal(signal.SIGTERM,stop)
            if hasattr(signal,'SIGINT'):signal.signal(signal.SIGINT,stop)
            print('MODE: '+('PAPER ORDERS ENABLED' if args.enable_paper_orders else 'OBSERVE ONLY'))
            while not stopping:
                try:
                    result=engine.tick()
                    print(json.dumps(result),flush=True)
                except GuardError as exc:
                    result={'time':datetime.now(UTC).isoformat(),'entries':'blocked','message':str(exc),
                            'mode':'paper' if args.enable_paper_orders else 'observe'}
                    store.status(result);store.event(datetime.now(UTC),'guard',reason=str(exc))
                    print(json.dumps(result),flush=True)
                    if args.once:return 2
                except Exception:
                    # Do not print HTTP objects, headers, traceback locals or broker response bodies.
                    store.status({'time':datetime.now(UTC).isoformat(),'entries':'blocked','message':'Unexpected internal error; process stopped. Inspect local code/state.'})
                    raise GuardError('Unexpected internal error; process stopped') from None
                if shadow is not None:
                    try:
                        print(json.dumps(shadow.tick()),flush=True)
                    except GuardError as exc:
                        print(json.dumps({'mode':'shadow_simulation','message':str(exc),'status':'blocked'}),flush=True)
                if cross_process is not None and cross_process.poll() is not None:
                    print(json.dumps({'mode':'cross_asset_simulation','status':'stopped',
                                      'message':'Cross-asset process exited; inspect its logs. Existing observer continues.'}),flush=True)
                    cross_process=None
                if args.once:return 0
                for _ in range(10):
                    if stopping:break
                    time.sleep(1)
            print('Process stopped. Existing paper positions are NOT automatically liquidated.')
        finally:
            if cross_process is not None:
                cross_process.terminate()
                try:cross_process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    cross_process.kill();cross_process.wait()
            if shadow is not None:shadow.close()
            store.close()
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (GuardError,getpass.GetPassWarning) as exc:
        print('STOPPED: '+str(exc));raise SystemExit(2)
    except (KeyboardInterrupt,EOFError):
        print('Cancelled. No automatic liquidation.');raise SystemExit(130)
