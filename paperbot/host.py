"""Persistent-host startup. Setup mode never accesses Alpaca or submits orders."""
import json
import os
import signal
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote


def launch_args(mode, directory):
    if mode not in ('setup','observe','paper'):
        raise ValueError('PAPERBOT_MODE must be setup, observe or paper')
    if mode=='setup':return None
    path=Path(directory)/'state.sqlite3'
    if not path.is_file():return None
    try:
        db=sqlite3.connect('file:'+quote(str(path.resolve()),safe='/')+'?mode=ro',uri=True)
        try:row=db.execute('SELECT body FROM state WHERE id=1').fetchone()
        finally:db.close()
        if not row or not isinstance(json.loads(row[0]),dict):return None
    except (sqlite3.Error,ValueError):
        raise ValueError('Saved state cannot be read; inspect or restore it, never auto-reset') from None
    args=['run','--state-dir',str(directory)]
    if mode=='paper':args.append('--enable-paper-orders')
    return args


def main():
    mode=os.environ.get('PAPERBOT_MODE','setup')
    directory=os.environ.get('PAPERBOT_STATE_DIR','/var/data/paperbot')
    args=launch_args(mode,directory)
    if args is not None:
        from .__main__ import main as bot_main
        return bot_main(args)
    stopped=False
    def stop(*unused):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    print('SETUP MODE: trading off. Use the Render Shell for preflight/init. Change PAPERBOT_MODE only after review.',flush=True)
    if mode!='setup':print('State missing/uninitialized. No account initialization was attempted.',flush=True)
    while not stopped:time.sleep(1)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except ValueError as exc:
        print('STARTUP BLOCKED: '+str(exc),flush=True);raise SystemExit(2)
