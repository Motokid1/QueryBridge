"""Explicit date-part conversion needed by the pinned SQLGlot SQLite generator."""
import sqlglot
from sqlglot import exp

def convert(sql,source,target='sqlite'):
    tree=sqlglot.parse_one(sql,read=source)
    if target=='sqlite':
        def dates(node):
            formats={exp.Year:'%Y',exp.Month:'%m',exp.Day:'%d'}
            if type(node) in formats:
                replacement=sqlglot.parse_one("CAST(STRFTIME('"+formats[type(node)]+"',__date_value) AS INTEGER)",read='sqlite')
                for c in replacement.find_all(exp.Column):
                    if c.name=='__date_value':c.replace(node.this.copy())
                return replacement
            return node
        tree=tree.transform(dates)
    return tree.sql(dialect=target)
