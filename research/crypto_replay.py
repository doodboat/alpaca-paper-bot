"""Diagnostic daily-open replay; NOT a fresh holdout or exact 00:15 quote simulation."""
import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paperbot.crypto_sim import SYMBOLS, initial, decide, report


def run(raw,start,end):
    bars={s:{b['timestamp'][:10]:b for b in raw['bars'][s]} for s in SYMBOLS}
    days=sorted(set(bars[SYMBOLS[0]]) & set(bars[SYMBOLS[1]]))
    selected=[d for d in days if start<=d<=end]
    first=datetime.fromisoformat(selected[0]).replace(tzinfo=timezone.utc,hour=0,minute=15)
    state=initial(first-timedelta(days=1));previous={k:45000. for k in state['portfolios']}
    returns={k:[] for k in previous}
    for d in selected:
        idx=days.index(d)
        if idx<200:raise ValueError('Insufficient warmup')
        history=days[idx-200:idx]
        if (datetime.fromisoformat(d)-datetime.fromisoformat(history[0])).days!=200:raise ValueError('Missing dates')
        c={s:[bars[s][h]['close'] for h in history] for s in SYMBOLS}
        now=datetime.fromisoformat(d).replace(tzinfo=timezone.utc,hour=0,minute=15)
        q={s:dict(bid=bars[s][d]['open']*.999,ask=bars[s][d]['open']*1.001,time=now.isoformat()) for s in SYMBOLS}
        decide(state,c,q,now)
        q={s:dict(bid=bars[s][d]['close']*.999,ask=bars[s][d]['close']*1.001,time=now.isoformat()) for s in SYMBOLS}
        r=report(state,q,now)
        for k,p in r['portfolios'].items():
            returns[k].append(p['equity']/previous[k]-1);previous[k]=p['equity']
    results={}
    for k,p in r['portfolios'].items():
        rr=returns[k]
        results[k]=dict(final_equity=p['equity'],total_return=p['net_return'],
            CAGR=(p['equity']/45000)**(365/len(rr))-1,
            daily_Sharpe_zero_cash=mean(rr)/stdev(rr)*math.sqrt(365) if stdev(rr) else None,
            max_daily_drawdown=p['max_observed_drawdown'],fills=p['fills'])
    return dict(start=selected[0],end=selected[-1],days=len(selected),results=results)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('data');args=parser.parse_args()
    raw=json.loads(Path(args.data).read_text())
    print(json.dumps(dict(note='Reused cached Alpaca data, not independently verified. Frozen rules, no parameter search. '
        'Daily open proxy rather than actual 00:15 fill; assumed 20bps full spread +10bps slippage per side +25bps fee. '
        'Trend includes standalone $106.25/month allowance; benchmark excludes it. No tax or cash yield. '
        'Mark-to-bid NAV, no terminal liquidation.',
        periods=[run(raw,a,b) for a,b in [('2022-01-01','2022-12-31'),('2023-01-01','2024-12-31'),('2025-01-01','2026-09-09')]]),indent=2))
