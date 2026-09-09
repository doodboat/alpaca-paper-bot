import json
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from .strategy import GuardError


class Store:
    def __init__(self, directory):
        self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.directory/'state.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time TEXT, kind TEXT, body TEXT)')
        self.db.commit()

    def load(self):
        row=self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def save(self,state):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(state,allow_nan=False),))

    def event(self,now,kind,**values):
        with self.db:
            self.db.execute('INSERT INTO events(time,kind,body) VALUES(?,?,?)',
                            (now.isoformat(),kind,json.dumps(values,allow_nan=False)))

    def status(self,payload):
        path=self.directory/'status.json'
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
        os.replace(temp,path)

    def close(self): self.db.close()


@contextmanager
def process_lock(directory):
    path=Path(directory);path.mkdir(parents=True,exist_ok=True)
    handle=(path/'process.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
            try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: raise GuardError('Another bot process holds this state directory') from None
        else:
            import fcntl
            try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: raise GuardError('Another bot process holds this state directory') from None
        yield
    finally:
        handle.close()
