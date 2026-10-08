import csv
import re
import sqlite3
import time
from pathlib import Path
import sqlglot
from sqlglot import exp
from .connectors import quote,inspect_sqlite

def identifier(name,index):
    text=re.sub(r'[^a-zA-Z0-9_]','_',name.strip()).strip('_').lower()[:60] or f'column_{index}'
    if text[0].isdigit():text='c_'+text
    return text

def scalar(text,typ):
    if text is None or text=='':return None
    if typ=='INTEGER':return int(text)
    if typ=='REAL':return float(text)
    return text

def infer(values):
    nonempty=[v for v in values if v!='']
    if not nonempty:return 'TEXT'
    if all(re.fullmatch(r'-?(?:0|[1-9][0-9]*)',v) and -(2**63)<=int(v)<2**63 for v in nonempty):return 'INTEGER'
    try:
        import math
        if all(math.isfinite(float(v)) and not (re.fullmatch(r'0[0-9]+',v)) for v in nonempty):return 'REAL'
    except (ValueError,OverflowError):pass
    return 'TEXT'

def import_csv(source,target,settings):
    csv.field_size_limit(1024*1024)
    with Path(source).open('r',encoding='utf-8-sig',newline='') as file:
        sample=file.read(65536);file.seek(0)
        try:dialect=csv.Sniffer().sniff(sample,delimiters=',;\t|')
        except csv.Error:dialect=csv.excel
        reader=csv.reader(file,dialect);header=next(reader,None)
        if not header or len(header)>200 or any(not h.strip() for h in header):raise ValueError('CSV needs a non-empty header with at most 200 columns')
        names=[identifier(h,i+1) for i,h in enumerate(header)]
        if len(set(names))!=len(names):raise ValueError('CSV headers conflict after normalization; use distinct names')
        first=[]
        for _,row in zip(range(1000),reader):
            if len(row)!=len(names):raise ValueError('CSV row has a different column count than the header')
            first.append(row)
        types=[infer([r[i] for r in first]) for i in range(len(names))]
        conn=sqlite3.connect(target);total=0;deadline=time.monotonic()+60
        try:
            conn.execute('CREATE TABLE data ('+', '.join(quote(n)+' '+t for n,t in zip(names,types))+')')
            sql='INSERT INTO data VALUES ('+','.join('?' for _ in names)+')'
            import itertools
            batch=[]
            for row in itertools.chain(first,reader):
                if len(row)!=len(names):raise ValueError('CSV row has a different column count than the header')
                total+=1
                if total>settings.max_import_rows or time.monotonic()>deadline:raise ValueError('CSV exceeds row or import-time limits')
                try:batch.append(tuple(scalar(v,t) for v,t in zip(row,types)))
                except (ValueError,OverflowError):raise ValueError('CSV column types change after the first 1000 rows; make the column consistent')
                if len(batch)>=1000:conn.executemany(sql,batch);batch.clear()
            if batch:conn.executemany(sql,batch)
            if not total:raise ValueError('CSV contains no data rows')
            conn.commit()
        finally:conn.close()
    schema=inspect_sqlite(target)
    return schema,{'rows':total,'header_mapping':dict(zip(header,names)),'table':'data'}

def literal(node):
    if isinstance(node,exp.Null):return None
    if isinstance(node,exp.Boolean):return bool(node.this)
    if isinstance(node,exp.Neg):
        result=literal(node.this)
        if isinstance(result,(int,float)) and not isinstance(result,bool):return -result
        raise ValueError('Invalid negative SQL literal')
    if not isinstance(node,exp.Literal):raise ValueError('INSERT supports literal VALUES only, not functions or SELECT')
    if node.is_string:return node.this
    try:return int(node.this)
    except ValueError:return float(node.this)

def import_sql(source,target,settings,dialect='mysql'):
    text=Path(source).read_text(encoding='utf-8-sig')
    if len(text)>10*1024*1024:raise ValueError('SQL imports are limited to 10 MB; connect large databases instead')
    try:statements=sqlglot.parse(text,read=dialect,error_level=sqlglot.ErrorLevel.RAISE)
    except Exception:raise ValueError('SQL dump could not be parsed for the selected dialect')
    if len(statements)>10000:raise ValueError('Too many SQL statements')
    conn=sqlite3.connect(target);total=0;table_names=set();deadline=time.monotonic()+60
    try:
        for tree in statements:
            if tree is None:continue
            if time.monotonic()>deadline:raise ValueError('SQL import exceeded its time limit')
            # Common dump wrappers have no effect on this isolated SQLite destination.
            if isinstance(tree,(exp.Use,exp.Set,exp.Transaction,exp.Commit,exp.Rollback)):continue
            if isinstance(tree,exp.Create):
                if str(tree.args.get('kind')).upper()!='TABLE' or not isinstance(tree.this,exp.Schema) or tree.expression:
                    raise ValueError('SQL import supports CREATE TABLE only; views, routines and CREATE AS are disabled')
                table=tree.this.this
                if not isinstance(table,exp.Table) or table.db or table.catalog:raise ValueError('Use unqualified table names')
                name=table.name
                if name.startswith('sqlite_') or name in table_names:raise ValueError('Reserved or duplicate table name')
                columns=[];primary=[];foreign=[]
                for col in tree.this.expressions:
                    if isinstance(col,exp.ColumnDef):
                        typ=col.args.get('kind');label=typ.sql().upper() if typ else 'TEXT'
                        affinity='INTEGER' if re.search(r'INT|BOOL',label) else 'REAL' if re.search(r'FLOAT|DOUBLE|REAL',label) else 'NUMERIC' if re.search(r'DECIMAL|NUMERIC',label) else 'TEXT'
                        columns.append(quote(col.name)+' '+affinity)
                        if any(isinstance(c.args.get('kind'),exp.PrimaryKeyColumnConstraint) for c in col.args.get('constraints',[])):primary.append(col.name)
                    elif isinstance(col,exp.PrimaryKey):primary.extend(c.name for c in col.expressions)
                    elif isinstance(col,exp.ForeignKey):foreign.append(col)
                    elif isinstance(col,exp.Constraint):
                        for part in col.expressions:
                            if isinstance(part,exp.ForeignKey):foreign.append(part)
                            elif isinstance(part,exp.PrimaryKey):primary.extend(c.name for c in part.expressions)
                    # Secondary indexes/checks/defaults are not executed. Source data remains unchanged.
                if not columns or len(columns)>200:raise ValueError('Imported table needs 1–200 columns')
                if primary:columns.append('PRIMARY KEY ('+','.join(quote(c) for c in primary)+')')
                for fk in foreign:
                    reference=fk.args.get('reference')
                    if reference and isinstance(reference.this,exp.Schema) and isinstance(reference.this.this,exp.Table):
                        rt=reference.this.this
                        if rt.db or rt.catalog:raise ValueError('Cross-database foreign keys are disabled')
                        columns.append('FOREIGN KEY ('+','.join(quote(c.name) for c in fk.expressions)+') REFERENCES '+quote(rt.name)+' ('+','.join(quote(c.name) for c in reference.this.expressions)+')')
                conn.execute('CREATE TABLE '+quote(name)+' ('+','.join(columns)+')');table_names.add(name)
            elif isinstance(tree,exp.Insert):
                if tree.args.get('overwrite') or tree.args.get('conflict') or tree.args.get('alternative'):
                    raise ValueError('Replacement/conflict INSERT modes are disabled')
                target_node=tree.this;cols=[]
                if isinstance(target_node,exp.Schema):cols=[c.name for c in target_node.expressions];target_node=target_node.this
                if not isinstance(target_node,exp.Table) or target_node.db or target_node.catalog or target_node.name not in table_names:
                    raise ValueError('INSERT must target a table created in this uploaded file')
                if not isinstance(tree.expression,exp.Values):raise ValueError('Only INSERT ... VALUES is supported')
                rows=[]
                for row in tree.expression.expressions:
                    if not isinstance(row,exp.Tuple):raise ValueError('Invalid VALUES row')
                    values=tuple(literal(n) for n in row.expressions);total+=1
                    if total>settings.max_import_rows:raise ValueError('SQL row limit exceeded')
                    rows.append(values)
                if rows:
                    conn.executemany('INSERT INTO '+quote(target_node.name)+(' ('+','.join(quote(c) for c in cols)+')' if cols else '')+
                                     ' VALUES ('+','.join('?' for _ in rows[0])+')',rows)
            else:raise ValueError('Unsupported SQL statement. Upload CREATE TABLE / INSERT VALUES files; never executable routines or admin commands.')
        if not table_names:raise ValueError('SQL file contains no supported tables')
        conn.commit()
    finally:conn.close()
    return inspect_sqlite(target),{'rows':total,'tables':len(table_names),'source_dialect':dialect,
        'note':'Imported into isolated SQLite; indexes, defaults, checks and MySQL engine options are not retained. Connect MySQL for original semantics.'}
