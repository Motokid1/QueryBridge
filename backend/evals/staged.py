"""Durable staged evaluation coordinator. Uses the running authenticated backend."""
import argparse
import json
import time
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import httpx
from app.config import Settings,PROJECT
from .analysis import compare

def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2),encoding='utf8');temp.replace(path)

def table(report):
    return [{'profile':name,'answer_accuracy':p['metrics']['answer_accuracy'],
        'answer_n':p['metrics']['answer']['n'],'safety':p['metrics']['safety_pass_rate'],
        'failures':p['metrics']['failure_labels']} for name,p in report['profiles'].items()]

def update_readme(state):
    path=PROJECT/'README.md';text=path.read_text(encoding='utf8')
    start='<!-- STAGED_RESULTS_START -->';end='<!-- STAGED_RESULTS_END -->'
    lines=[start,'','## Staged ClassicModels experiment','',
      '43 curated development cases + 6 generated checks; 14 held-out cases. References are agent-reviewed; human approval is pending.',
      '',f"Current stage: {state['stage']}. This section is generated from saved reports, not estimated scores.",'',
      '| Stage | Profile | Answer accuracy | Answer n | Safety | Top failure |',
      '| --- | --- | --- | ---: | --- | --- |']
    for stage in ('core_once','core_three','fixed_three','heldout_once'):
        record=state.get(stage,{})
        if 'report' not in record:continue
        for row in table(record['report']):
            rate=lambda v:'n/a' if v is None else f'{100*v:.1f}%'
            top=max(row['failures'],key=row['failures'].get) if row['failures'] else 'none'
            lines.append(f"| {stage} | {row['profile']} | {rate(row['answer_accuracy'])} | {row['answer_n']} | {rate(row['safety'])} | {top} |")
    if state.get('selected_fix'):lines += ['',f"Selected single change: {state['selected_fix']}. Selection used development failures only."]
    if state.get('comparison'):lines += ['', 'Before/after comparison:','', '```json',json.dumps(state['comparison'],indent=2),'```']
    if state.get('failure_story'):lines += ['', 'Observed failure and targeted change:','', '```json',json.dumps(state['failure_story'],indent=2),'```']
    lines += ['', 'Held-out results are reported once and must not be used for further tuning. A failure is recorded as a failure; improvement is not assumed.', '',end]
    replacement='\n'.join(lines)
    if start in text and end in text:
        a=text.index(start);b=text.index(end,a)+len(end);text=text[:a]+replacement+text[b:]
    else:text+='\n\n'+replacement+'\n'
    path.write_text(text,encoding='utf8')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-id',required=True)
    parser.add_argument('--stop-after',choices=['core_once'],help='Run the first stage only; no overnight scheduling')
    parser.add_argument('--not-before',help='Timezone-aware ISO time for stages after core_once')
    args=parser.parse_args();settings=Settings()
    folder=settings.storage/'evaluations'/'staged';folder.mkdir(parents=True,exist_ok=True)
    state_path=folder/(args.dataset_id+'.json')
    state=json.loads(state_path.read_text()) if state_path.exists() else {'dataset_id':args.dataset_id,'stage':'starting'}
    not_before=datetime.fromisoformat(args.not_before) if args.not_before else None
    if not_before and not_before.tzinfo is None:parser.error('--not-before requires timezone offset')
    def save():write(state_path,state);update_readme(state)
    with httpx.Client(base_url=f'http://127.0.0.1:{settings.app_port}',headers={'Authorization':'Bearer '+settings.access_key},timeout=120,trust_env=False) as client:
        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs);response.raise_for_status();return response.json()
        def run(stage,repeats,split='development',variant='original'):
            record=state.setdefault(stage,{})
            if record.get('report'):return record['report']
            state['stage']=stage
            if not record.get('run_id'):
                result=request('POST','/api/v1/evaluation/runs',json={'dataset_ids':[args.dataset_id],'repeats':repeats,'ablations':False,'include_samples':False,'split':split,'prompt_variant':variant})
                record['run_id']=result['run_ids'][0];save()
            while True:
                current=request('GET','/api/v1/evaluation/runs/'+record['run_id'])
                record['status']=current['status'];record['progress']=current['progress'];write(state_path,state)
                if current['status'] not in ['queued','running']:break
                time.sleep(15)
            if current['status']!='completed':raise RuntimeError(current.get('error') or current['status'])
            record['report']=current['report'];save();return record['report']
        try:
            if not state.get('pack_verified'):
                state['reference_check']=request('POST','/api/v1/evaluation/curated/'+args.dataset_id)
                suite=request('GET','/api/v1/evaluation/cases/'+args.dataset_id)
                assert len([c for c in suite if c.get('metadata',{}).get('split')=='heldout'])==14
                state['pack_verified']=True;save()
            run('core_once',1)
            if args.stop_after=='core_once':
                state['stage']='core_once_completed';save();return
            if not_before and datetime.now(timezone.utc)<not_before:
                state['stage']='waiting_for_overnight_window';state['not_before']=not_before.isoformat();save()
                while datetime.now(timezone.utc)<not_before:time.sleep(30)
            before=run('core_three',3)
            if not state.get('selected_fix'):
                counts=Counter(label for p in before['profiles'].values() for o in p['observations'] if o['expected_behavior']=='answer' and not o['passed'] for label in [o.get('manual_failure_label') or o.get('failure_label')])
                state['top_patterns']=counts.most_common(2)
                # Predeclared choices, selected from development diagnostics only.
                top=[label for label,_ in counts.most_common(2)]
                if any(label in top for label in ['HALLUCINATED_COLUMN','WRONG_TABLE']):variant='column_ownership'
                elif any(label in top for label in ['BAD_JOIN','WRONG_AGGREGATION']):variant='join_keys'
                elif 'UNEXPECTED_REFUSAL' in top:variant='guard_context'
                else:variant='original'
                state['selected_fix']=variant
                example=next((o for p in before['profiles'].values() for o in p['observations'] if not o['passed'] and o.get('failure_label') in ['HALLUCINATED_COLUMN','WRONG_TABLE','BAD_JOIN','WRONG_AGGREGATION','UNEXPECTED_REFUSAL']),None)
                if example:
                    a=example.get('answer') or {};state['failure_story']={'question':example['question'],'bad_sql':a.get('sql') or (a.get('trace') or [{}])[-1].get('sql'),'reference_sql':example['gold_sql'],'failure_label':example['failure_label'],'diagnostic_evidence':example.get('diagnostic_evidence'),'change':variant}
                save()
            variant=state['selected_fix']
            after=run('fixed_three',3,variant=variant)
            state['comparison']=compare(before,after);save()
            request('POST','/api/v1/evaluation/runs/'+state['core_three']['run_id']+'/baseline',json={'margin':.05})
            run('heldout_once',1,split='heldout',variant=variant)
            state['stage']='completed';state['completed_at']=datetime.now(timezone.utc).isoformat();save()
        except Exception as exc:
            state['stage']='needs_attention';state['error']=str(exc)[:1000];save();raise

if __name__=='__main__':main()
