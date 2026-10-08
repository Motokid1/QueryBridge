import re
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from .schema import allowed_schema

class UnsafeSQL(ValueError):pass
SAFE={'COUNT','SUM','AVG','MIN','MAX','ROUND','ABS','COALESCE','IF','IFNULL','NULLIF','CAST','EXTRACT',
      'YEAR','MONTH','DAY','DATE','DATE_FORMAT','DATEDIFF','TIMESTAMPDIFF','TIME_TO_STR','TS_OR_DS_TO_DATE','TS_OR_DS_TO_TIMESTAMP',
      'CONCAT','CONCAT_WS','LOWER','UPPER','LENGTH','CHAR_LENGTH','SUBSTRING','TRIM','ROW_NUMBER','RANK','DENSE_RANK',
      'CASE','AND','OR','NOT','BETWEEN','IN','IS','EXISTS','STRFTIME','JULIANDAY'}

def validate(sql,dataset,settings):
    dialect='mysql' if dataset['kind']=='mysql' else 'sqlite'
    if not sql or len(sql)>16000:raise ValueError('SQL is empty or too long')
    if '\x00' in sql:raise UnsafeSQL('Null bytes are not allowed')
    try:statements=sqlglot.parse(sql,read=dialect)
    except sqlglot.errors.ParseError as exc:raise ValueError('Invalid '+dialect+' SQL syntax') from exc
    if len(statements)!=1 or not isinstance(statements[0],(exp.Select,exp.Union)):raise UnsafeSQL('Only one SELECT is allowed')
    tree=statements[0];schema=allowed_schema(dataset)
    denied=(exp.Insert,exp.Update,exp.Delete,exp.Create,exp.Drop,exp.Alter,exp.Command,exp.Into,exp.Lock,exp.Var,exp.Parameter,exp.SessionParameter)
    for node in tree.walk():
        if node.comments or isinstance(node,exp.Hint):raise UnsafeSQL('SQL comments and hints are not allowed')
        if isinstance(node,denied):raise UnsafeSQL('Forbidden SQL operation or variable')
        if isinstance(node,exp.With) and node.args.get('recursive'):raise UnsafeSQL('Recursive queries are disabled')
        if isinstance(node,exp.CTE) and any(t.name.lower()==node.alias.lower() for t in node.this.find_all(exp.Table)):
            raise UnsafeSQL('Self-referencing CTEs are disabled')
        if isinstance(node,exp.Func):
            name=node.name.upper() if isinstance(node,exp.Anonymous) else node.sql_name()
            if name not in SAFE:raise UnsafeSQL('Function is not allowlisted: '+name)
        if isinstance(node,exp.Join) and (node.args.get('kind')=='CROSS' or not (node.args.get('on') or node.args.get('using'))):
            raise UnsafeSQL('Joins require explicit keys')
    ctes={c.alias.lower() for c in tree.find_all(exp.CTE)};physical=[]
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog:raise UnsafeSQL('Cross-database queries are blocked')
        if table.name.lower() not in ctes:
            if table.name not in schema:raise UnsafeSQL('Table is disabled or belongs to a different dataset: '+table.name)
            physical.append(table.name)
    if not physical:raise UnsafeSQL('Read an approved dataset table')
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent,exp.Count):raise UnsafeSQL('Select explicit approved columns')
    try:qualify(tree.copy(),dialect=dialect,schema=schema,validate_qualify_columns=True,quote_identifiers=False)
    except sqlglot.errors.OptimizeError as exc:raise ValueError('Unknown, ambiguous or disabled column: '+str(exc)[:240]) from exc
    limit=tree.args.get('limit')
    if limit:
        value=limit.expression
        if not isinstance(value,exp.Literal) or not value.is_int or int(value.this)<1:raise UnsafeSQL('Invalid LIMIT')
        limit.set('expression',exp.Literal.number(min(int(value.this),settings.max_rows)))
    else:tree=tree.limit(settings.max_rows)
    offset=tree.args.get('offset')
    if offset and (not isinstance(offset.expression,exp.Literal) or not offset.expression.is_int or not 0<=int(offset.expression.this)<=10000):
        raise UnsafeSQL('Invalid OFFSET')
    return tree.sql(dialect=dialect)
