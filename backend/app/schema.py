import re

SENSITIVE=re.compile(r'(password|passwd|secret|token|credential|private.?key|ssn|social.?security|credit.?card|email|phone|address)',re.I)

def default_policy(schema):
    return {name:[c for c in table['columns'] if not SENSITIVE.search(c)] for name,table in schema['tables'].items()}

def validate_policy(schema,policy):
    if not isinstance(policy,dict):raise ValueError('Policy must map table names to column lists')
    result={}
    for name,columns in policy.items():
        if name not in schema['tables'] or not isinstance(columns,list):raise ValueError('Unknown table or invalid column list')
        if len(columns)!=len(set(columns)):raise ValueError('Duplicate columns')
        for col in columns:
            if col not in schema['tables'][name]['columns']:raise ValueError('Unknown column: '+col)
            if SENSITIVE.search(col):raise ValueError('Sensitive columns cannot be enabled')
        result[name]=columns
    if not any(result.values()):raise ValueError('Enable at least one non-sensitive column')
    return result

def allowed_schema(dataset):
    return {name:{col:table['columns'][col] for col in dataset['policy'].get(name,[]) if col in table['columns'] and not SENSITIVE.search(col)}
            for name,table in dataset['schema']['tables'].items() if dataset['policy'].get(name)}

def describe(dataset,names=None):
    schema=allowed_schema(dataset);names=names or list(schema)
    lines=[f'{name} ('+', '.join(f'{c}: {t}' for c,t in schema[name].items())+')' for name in names if name in schema]
    lines += [f'{r[0]}.{r[1]} = {r[2]}.{r[3]}' for r in dataset['schema'].get('relations',[]) if r[0] in names and r[2] in names
              and r[1] in schema.get(r[0],{}) and r[3] in schema.get(r[2],{})]
    return '\n'.join(lines)
