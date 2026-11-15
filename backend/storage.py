import json
import sqlite3
from pathlib import Path
from .diagnostics import timed

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def get(self, sid):
        with self.connect() as db:
            row = db.execute('SELECT body FROM sessions WHERE id=?', (sid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, session):
        with timed('storage_save'):
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)', (session['id'], json.dumps(session, ensure_ascii=False)))

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT body FROM sessions ORDER BY rowid DESC').fetchall()
        return [{k: s[k] for k in ('id', 'title', 'created_at', 'version', 'closed')} for s in map(lambda r: json.loads(r[0]), rows)]

    def delete(self, sid):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (sid,))
