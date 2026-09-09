from datetime import timedelta
from .strategy import SYMBOLS, Bar, GuardError, session_time, completed_bars


class Feed:
    def __init__(self,api): self.api=api

    def calendar(self,now):
        day=now.date()
        return self.api.calendar((day-timedelta(days=65)).isoformat(),(day+timedelta(days=15)).isoformat())

    def warmup(self,calendar,day):
        sessions=[s for s in calendar if s['date']<day][-20:]
        if len(sessions)!=20: raise GuardError('Not enough prior exchange sessions')
        history={s:[] for s in SYMBOLS}
        for session in sessions:
            opening=session_time(session['date'],session['open'])
            result=self.api.bars(SYMBOLS,opening.isoformat(),(opening+timedelta(minutes=15,seconds=-1)).isoformat())
            for s in SYMBOLS:
                matches=[Bar.parse(b) for b in result.get(s,[]) if Bar.parse(b).time==opening]
                if len(matches)!=1: raise GuardError('Prior opening bar missing or duplicated: '+s)
                history[s].append(matches[0].volume)
        return history

    def today(self,session,now):
        opening=session_time(session['date'],session['open'])
        closing=session_time(session['date'],session['close'])
        if now < opening: return {s:[] for s in SYMBOLS},{}
        end=min(now,closing)
        raw=self.api.bars(SYMBOLS,opening.isoformat(),end.isoformat())
        result={};errors={}
        for s in SYMBOLS:
            try: result[s]=completed_bars(raw.get(s,[]),opening,closing,now)
            except GuardError as exc: errors[s]=str(exc)
        return result,errors
