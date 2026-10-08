"""Frozen, approved-column-only SQLite snapshots for all evaluation profiles."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from app.schema import allowed_schema
from app.connectors import mysql,sqlite_open,quote,inspect_sqlite

def freeze(dataset,settings,run_id):
    folder=settings.storage/'evaluations'/'snapshots';folder.mkdir(parents=True,exist_ok=True)
    destination=folder/(run_id+'.sqlite');schema=allowed_schema(dataset);out=sqlite3.connect(destination)
    fingerprints={};total=0;deadline=time.monotonic()+120
    source=None;context=None
    try:
        if dataset['kind']=='mysql':
            context=mysql(dataset['connection'],settings);source=context.__enter__()
            with source.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        else:
            source=sqlite_open(dataset['connection']['path']);source.execute('BEGIN')
        for table,columns in sorted(schema.items()):
            typ=lambda t:'INTEGER' if 'INT' in t else 'NUMERIC' if any(v in t for v in ('DECIMAL','NUMERIC','REAL','FLOAT','DOUBLE')) else 'TEXT'
            out.execute('CREATE TABLE '+quote(table)+' ('+','.join(quote(c)+' '+typ(t) for c,t in columns.items())+')')
            delimiter='`' if dataset['kind']=='mysql' else '"'
            q=lambda name:delimiter+name.replace(delimiter,delimiter*2)+delimiter
            sql='SELECT '+','.join(q(c) for c in columns)+' FROM '+q(table)
            cur=source.cursor();cur.execute(sql);hashes=[]
            while True:
                rows=cur.fetchmany(1000)
                if not rows:break
                total+=len(rows)
                if total>settings.max_import_rows or time.monotonic()>deadline:raise ValueError('Snapshot exceeds row/time limit; use a smaller imported dataset')
                values=[tuple(str(v) if not isinstance(v,(str,int,float,bytes,type(None))) else v for v in (r.values() if isinstance(r,dict) else r)) for r in rows]
                out.executemany('INSERT INTO '+quote(table)+' VALUES ('+','.join('?' for _ in columns)+')',values)
                hashes.extend(hashlib.sha256(json.dumps(v,default=str,ensure_ascii=False).encode()).hexdigest() for v in values)
            cur.close();fingerprints[table]={'rows':len(hashes),'hash':hashlib.sha256(''.join(sorted(hashes)).encode()).hexdigest()}
        out.commit()
    except BaseException:
        out.close();destination.unlink(missing_ok=True);raise
    finally:
        out.close()
        if source:
            source.rollback()
            if context:context.__exit__(None,None,None)
            else:source.close()
    frozen_schema=inspect_sqlite(destination)
    frozen_schema['relations']=[r for r in dataset['schema'].get('relations',[]) if r[1] in schema.get(r[0],{}) and r[3] in schema.get(r[2],{})]
    result={**dataset,'kind':'sql','connection':{'path':str(destination)},'schema':frozen_schema,
        'policy':{t:list(cols) for t,cols in schema.items()},'_evaluation_snapshot':True}
    digest=hashlib.sha256(json.dumps(fingerprints,sort_keys=True).encode()).hexdigest()
    return result,digest,fingerprints

def unchecked(sql,dataset,settings):
    """Bypass application validator only in our private frozen read-only snapshot."""
    path=Path(dataset['connection']['path']).resolve();root=(settings.storage/'evaluations'/'snapshots').resolve()
    if not dataset.get('_evaluation_snapshot') or not path.is_relative_to(root):raise ValueError('Validator-off is limited to evaluation snapshots')
    c=sqlite_open(path);deadline=time.monotonic()+settings.query_timeout_ms/1000
    allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
    c.set_authorizer(lambda action,*args:sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
    try:
        cur=c.execute(sql)
        if not cur.description:raise ValueError('No read-only result set')
        cols=[r[0] for r in cur.description]
        if len(cols)!=len(set(cols)):raise ValueError('Duplicate result aliases')
        from app.connectors import value
        return sql,[dict(zip(cols,map(value,row))) for row in cur.fetchmany(settings.max_rows)]
    finally:c.close()
