import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from cryptography.fernet import Fernet

class Store:
    def __init__(self,settings):
        self.settings=settings; self.path=settings.storage/'control.sqlite'
        key=settings.storage/'credentials.key'
        if not key.exists(): key.write_bytes(Fernet.generate_key())
        self.vault=Fernet(key.read_bytes())
        with self.db() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS datasets (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,connection TEXT NOT NULL,
              schema_json TEXT NOT NULL,policy_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS requests (
              id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,question TEXT NOT NULL,response TEXT NOT NULL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS feedback (
              id INTEGER PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,rating TEXT NOT NULL CHECK(rating IN ('correct','wrong')),
              comment TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              reviewed INTEGER NOT NULL DEFAULT 0,converted_case_id TEXT,
              FOREIGN KEY(request_id) REFERENCES requests(id));
            CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS test_cases (id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,question TEXT NOT NULL,
              gold_sql TEXT,expected_behavior TEXT NOT NULL,expected_assumption TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')
            columns={r[1] for r in c.execute('PRAGMA table_info(feedback)')}
            if 'reviewed' not in columns:c.execute('ALTER TABLE feedback ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0')
            if 'converted_case_id' not in columns:c.execute('ALTER TABLE feedback ADD COLUMN converted_case_id TEXT')
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try: yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
    def session(self):
        value=secrets.token_urlsafe(32)
        with self.db() as c:
            c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            c.execute('INSERT INTO sessions VALUES (?,?)',(hashlib.sha256(value.encode()).hexdigest(),time.time()+43200))
        return value
    def authenticated(self,value):
        if not value:return False
        with self.db() as c:
            return bool(c.execute('SELECT 1 FROM sessions WHERE digest=? AND expires>?',(hashlib.sha256(value.encode()).hexdigest(),time.time())).fetchone())
    def logout(self,value):
        with self.db() as c:c.execute('DELETE FROM sessions WHERE digest=?',(hashlib.sha256(value.encode()).hexdigest(),))
    def register(self,dataset_id,name,kind,connection,schema,policy):
        encrypted=self.vault.encrypt(json.dumps(connection).encode()).decode()
        with self.db() as c:c.execute('INSERT INTO datasets (id,name,kind,connection,schema_json,policy_json) VALUES (?,?,?,?,?,?)',
            (dataset_id,name,kind,encrypted,json.dumps(schema),json.dumps(policy)))
    def list(self):
        with self.db() as c:
            rows=c.execute('SELECT id,name,kind,created_at,schema_json,policy_json FROM datasets ORDER BY created_at DESC,id').fetchall()
        return [self.public(dict(r)) for r in rows]
    def public(self,row):
        schema=json.loads(row.pop('schema_json'));policy=json.loads(row.pop('policy_json'))
        row.update(table_count=len(schema['tables']),column_count=sum(len(t['columns']) for t in schema['tables'].values()),
                   schema=schema,policy=policy)
        row.pop('connection',None);return row
    def get(self,dataset_id):
        with self.db() as c:row=c.execute('SELECT * FROM datasets WHERE id=?',(dataset_id,)).fetchone()
        if not row:raise KeyError('Dataset not found')
        value=dict(row);value['connection']=json.loads(self.vault.decrypt(value['connection'].encode()))
        value['schema']=json.loads(value.pop('schema_json'));value['policy']=json.loads(value.pop('policy_json'))
        return value
    def policy(self,dataset_id,policy):
        with self.db() as c:c.execute('UPDATE datasets SET policy_json=? WHERE id=?',(json.dumps(policy),dataset_id))
    def refresh(self,dataset_id,schema,policy):
        with self.db() as c:c.execute('UPDATE datasets SET schema_json=?,policy_json=? WHERE id=?',(json.dumps(schema),json.dumps(policy),dataset_id))
    def save_request(self,dataset_id,question,response):
        with self.db() as c:c.execute('INSERT INTO requests (id,dataset_id,question,response) VALUES (?,?,?,?)',
            (response['request_id'],dataset_id,question,json.dumps(response,default=str)))
    def feedback(self,request_id,rating,comment):
        with self.db() as c:
            if not c.execute('SELECT 1 FROM requests WHERE id=?',(request_id,)).fetchone():raise KeyError('Request not found')
            try:c.execute('INSERT INTO feedback (request_id,rating,comment) VALUES (?,?,?)',(request_id,rating,comment))
            except sqlite3.IntegrityError:raise ValueError('This answer has already been rated')
    def history(self,dataset_id=None):
        with self.db() as c:
            rows=c.execute('SELECT r.*,f.rating,f.comment FROM requests r LEFT JOIN feedback f ON r.id=f.request_id '+
                           ('WHERE r.dataset_id=? ' if dataset_id else '')+'ORDER BY r.created_at DESC,r.rowid DESC LIMIT 50',
                           (dataset_id,) if dataset_id else ()).fetchall()
        return [{**dict(r),'response':json.loads(r['response'])} for r in rows]
    def review_rows(self):
        with self.db() as c:return [dict(r) for r in c.execute("SELECT f.id AS feedback_id,r.id AS request_id,r.dataset_id,r.question,r.response,f.comment,f.created_at FROM feedback f JOIN requests r ON f.request_id=r.id WHERE rating='wrong' AND f.reviewed=0 ORDER BY f.id")]
    def cases(self,dataset_id):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT * FROM test_cases WHERE dataset_id=? ORDER BY id',(dataset_id,))]
    def metrics(self,dataset_id=None):
        with self.db() as c:
            row=c.execute("SELECT COUNT(*) AS rated,SUM(f.rating='correct') AS correct FROM feedback f JOIN requests r ON r.id=f.request_id"+
                (' WHERE r.dataset_id=?' if dataset_id else ''),(dataset_id,) if dataset_id else ()).fetchone()
        return {'rated':row['rated'],'correct':row['correct'] or 0,'user_accuracy':row['correct']/row['rated'] if row['rated'] else None}
