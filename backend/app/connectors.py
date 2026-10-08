import math
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path
import pymysql
from .validator import validate

def quote(name):return '"'+name.replace('"','""')+'"'

def validate_grants(grants):
    for grant in grants:
        if 'WITH GRANT OPTION' in grant.upper():raise ValueError('The dataset account must not have GRANT OPTION')
        clause=re.match(r'^GRANT\s+(.+?)\s+ON\s+',grant,re.I)
        if not clause or not re.fullmatch(r'(?:USAGE|SELECT(?:\s*\([^)]*\))?)',clause[1],re.I):
            raise ValueError('Connect with a dedicated SELECT-only MySQL account; admin/write privileges are rejected')

def local_sqlite_path(path,settings):
    resolved=Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file() or not any(resolved.is_relative_to(root) for root in settings.local_file_roots):
        raise ValueError('SQLite path must be inside a configured LOCAL_FILE_ROOTS directory')
    if resolved.is_relative_to(settings.storage):raise ValueError('Application metadata cannot be connected as a dataset')
    with resolved.open('rb') as f:
        if f.read(16)!=b'SQLite format 3\x00':raise ValueError('Not a SQLite database file')
    return str(resolved)

@contextmanager
def mysql(config,settings):
    if config['host'] not in {'localhost','127.0.0.1','::1'}:raise ValueError('Only local MySQL servers are allowed')
    c=pymysql.connect(host=config['host'],port=int(config['port']),user=config['username'],password=config['password'],
        database=config['database'],charset='utf8mb4',autocommit=True,connect_timeout=5,
        read_timeout=settings.query_timeout_ms/1000+3,write_timeout=5,cursorclass=pymysql.cursors.DictCursor)
    try:yield c
    finally:c.close()

def inspect_mysql(config,settings):
    tables={};relations=[]
    with mysql(config,settings) as c,c.cursor() as cur:
        cur.execute('SHOW GRANTS')
        validate_grants([next(iter(row.values())) for row in cur.fetchall()])
        cur.execute("SELECT c.TABLE_NAME,c.COLUMN_NAME,c.DATA_TYPE FROM information_schema.COLUMNS c JOIN information_schema.TABLES t ON c.TABLE_SCHEMA=t.TABLE_SCHEMA AND c.TABLE_NAME=t.TABLE_NAME WHERE c.TABLE_SCHEMA=%s AND t.TABLE_TYPE='BASE TABLE' ORDER BY c.TABLE_NAME,c.ORDINAL_POSITION",(config['database'],))
        for row in cur.fetchall():
            name=row['TABLE_NAME']
            if name.startswith(('sys_','mysql_')):continue
            tables.setdefault(name,{'columns':{}})['columns'][row['COLUMN_NAME']]=row['DATA_TYPE'].upper()
        cur.execute('SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL',(config['database'],config['database']))
        relations=[[r[k] for k in ('TABLE_NAME','COLUMN_NAME','REFERENCED_TABLE_NAME','REFERENCED_COLUMN_NAME')] for r in cur.fetchall()]
    if not tables:raise ValueError('No readable tables found in this database')
    if len(tables)>500 or sum(len(t['columns']) for t in tables.values())>10000:raise ValueError('Schema exceeds local application limits (500 tables / 10000 columns)')
    return {'tables':tables,'relations':relations,'dialect':'mysql'}

def sqlite_open(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    c.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,2*1024*1024)
    c.execute('PRAGMA query_only=ON');return c

def inspect_sqlite(path):
    c=sqlite_open(path);tables={};relations=[]
    try:
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            columns={r[1]:(r[2] or 'TEXT').upper() for r in c.execute('PRAGMA table_info('+quote(name)+')')}
            tables[name]={'columns':columns}
            relations.extend([name,r[3],r[2],r[4]] for r in c.execute('PRAGMA foreign_key_list('+quote(name)+')') if r[2] and r[4])
    finally:c.close()
    if not tables:raise ValueError('Database contains no tables')
    if len(tables)>500 or sum(len(t['columns']) for t in tables.values())>10000:raise ValueError('Schema exceeds local application limits')
    return {'tables':tables,'relations':relations,'dialect':'sqlite'}

def inspect(dataset,settings):
    return inspect_mysql(dataset['connection'],settings) if dataset['kind']=='mysql' else inspect_sqlite(dataset['connection']['path'])

def value(v):
    if isinstance(v,Decimal):return float(v)
    if isinstance(v,(datetime,date)):return v.isoformat()
    if isinstance(v,bytes):return '[binary excluded]'
    if isinstance(v,float) and not math.isfinite(v):return None
    if isinstance(v,str) and len(v)>5000:return v[:5000]+'… [truncated]'
    return v

def execute(sql,dataset,settings):
    clean=validate(sql,dataset,settings)
    if dataset['kind']=='mysql':
        with mysql(dataset['connection'],settings) as c,c.cursor() as cur:
            cur.execute('SET SESSION MAX_EXECUTION_TIME=%s',(settings.query_timeout_ms,))
            cur.execute('SET SESSION TRANSACTION READ ONLY')
            cur.execute(clean);columns=[d[0] for d in cur.description];raw=cur.fetchmany(settings.max_rows)
            if len(columns)!=len(set(columns)):raise ValueError('Use unique output aliases')
            rows=[{k:value(v) for k,v in r.items()} for r in raw]
    else:
        c=sqlite_open(dataset['connection']['path']);deadline=time.monotonic()+settings.query_timeout_ms/1000
        allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
        c.set_authorizer(lambda action,*args:sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:
            cur=c.execute(clean);columns=[d[0] for d in cur.description]
            if len(columns)!=len(set(columns)):raise ValueError('Use unique output aliases')
            rows=[dict(zip(columns,map(value,r))) for r in cur.fetchmany(settings.max_rows)]
        finally:c.close()
    return clean,rows
