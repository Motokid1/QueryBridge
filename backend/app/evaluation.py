import csv
import json
import time
from decimal import Decimal,ROUND_HALF_EVEN,InvalidOperation
from collections import Counter
from itertools import permutations
from .connectors import execute
from .agent import Agent,audit
from .llm import Ollama

def normalized(value):
    if value is None:return ('null','')
    if isinstance(value,bool):return ('bool',value)
    if isinstance(value,(int,float,Decimal)):
        try:return ('number',str(Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_HALF_EVEN)))
        except InvalidOperation:return ('number',str(value))
    return ('text',str(value))

def equivalent(gold,actual):
    if len(gold)!=len(actual):return False
    if not gold:return True
    gcols=list(gold[0]);acols=list(actual[0])
    if len(gcols)!=len(acols):return False
    grow=[tuple(normalized(r[c]) for c in gcols) for r in gold]
    arow=[tuple(normalized(r[c]) for c in acols) for r in actual]
    if set(gcols)==set(acols):candidates=[tuple(acols.index(c) for c in gcols)]
    else:
        if len(gcols)>8:return False
        gs=[Counter(r[i] for r in grow) for i in range(len(gcols))];ass=[Counter(r[i] for r in arow) for i in range(len(acols))]
        candidates=(p for p in permutations(range(len(acols))) if all(gs[i]==ass[p[i]] for i in range(len(p))))
    return any(Counter(grow)==Counter(tuple(r[i] for i in p) for r in arow) for p in candidates)

def import_review(store,settings,path):
    reviewed=0
    for row in csv.DictReader(open(path,encoding='utf-8-sig',newline='')):
        decision=row.get('decision','')
        if not decision:continue
        if decision not in {'add','dismiss'}:raise ValueError('Review decision must be add or dismiss')
        fid=int(row['feedback_id'])
        with store.db() as c:
            saved=c.execute("SELECT f.*,r.dataset_id,r.question FROM feedback f JOIN requests r ON r.id=f.request_id WHERE f.id=? AND f.rating='wrong'",(fid,)).fetchone()
        if not saved or saved['request_id']!=row['request_id'] or saved['dataset_id']!=row['dataset_id']:
            raise ValueError('Review does not match its recorded request and dataset')
        if saved['reviewed']:continue
        case_id='feedback-'+str(fid);gold=row.get('gold_sql','').strip();behavior=row.get('expected_behavior','answer') or 'answer'
        if decision=='add':
            if behavior not in {'answer','refuse'}:raise ValueError('Expected behavior must be answer or refuse')
            if behavior=='answer':execute(gold,store.get(saved['dataset_id']),settings)
        with store.db() as c:
            if decision=='add':c.execute('INSERT OR IGNORE INTO test_cases (id,dataset_id,question,gold_sql,expected_behavior,expected_assumption) VALUES (?,?,?,?,?,?)',
                (case_id,saved['dataset_id'],saved['question'],gold if behavior=='answer' else None,behavior,row.get('expected_assumption') or None))
            c.execute('UPDATE feedback SET reviewed=1,converted_case_id=? WHERE id=?',(case_id if decision=='add' else None,fid))
        audit(settings,saved['request_id'],'feedback_reviewed',decision=decision,case_id=case_id if decision=='add' else None);reviewed+=1
    return reviewed

def evaluate(store,settings,dataset_id):
    dataset=store.get(dataset_id);cases=store.cases(dataset_id)
    if not cases:raise ValueError('This dataset has no human-reviewed test cases')
    llm=Ollama(settings);agent=Agent(settings,llm);observations=[]
    try:
        for case in cases:
            gold=execute(case['gold_sql'],dataset,settings)[1] if case['expected_behavior']=='answer' else None
            answer=agent.answer(dataset,case['question'])
            passed=(answer['status']=='refused' and answer['executed_queries']==0) if gold is None else (answer['status']=='answered' and equivalent(gold,answer['rows']))
            if case['expected_assumption']:passed=passed and any(case['expected_assumption'].lower() in a.lower() for a in answer['assumptions'])
            observations.append({'case_id':case['id'],'passed':passed,'question':case['question'],'gold_rows':gold,'answer':answer})
    finally:llm.close()
    path=settings.storage/'evaluations';path.mkdir(exist_ok=True)
    target=path/(dataset_id+'-'+str(time.time_ns())+'.json')
    report={'dataset_id':dataset_id,'dataset_name':dataset['name'],'model':settings.model,'cases':len(cases),
            'accuracy':sum(r['passed'] for r in observations)/len(cases),
            'scope':'Human-reviewed dataset-specific regression cases. Live connected databases are not reset; changes in source data can affect results.',
            'observations':observations}
    target.write_text(json.dumps(report,indent=2,default=str),encoding='utf8');return target,report
