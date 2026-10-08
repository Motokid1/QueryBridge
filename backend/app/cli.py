import argparse
import csv
import json
from pathlib import Path
from .config import Settings
from .store import Store

def main():
    p=argparse.ArgumentParser(description='Local Lens administrative utilities')
    p.add_argument('command',choices=['key','datasets','export-feedback','import-review','evaluate','curated'])
    p.add_argument('--file');p.add_argument('--dataset-id')
    p.add_argument('--repeats',type=int,default=3,choices=[1,3]);p.add_argument('--ablations',action='store_true');p.add_argument('--samples',action='store_true')
    p.add_argument('--out',default='storage/feedback-review.csv');args=p.parse_args();settings=Settings()
    if args.command=='key':print(settings.access_key);return
    store=Store(settings)
    if args.command=='import-review':
        if not args.file:p.error('import-review requires --file')
        from .evaluation import import_review
        print(f'Imported {import_review(store,settings,args.file)} human-reviewed decisions.');return
    if args.command=='curated':
        if not args.dataset_id:p.error('curated requires --dataset-id')
        import httpx
        with httpx.Client(base_url='http://127.0.0.1:'+str(settings.app_port),headers={'Authorization':'Bearer '+settings.access_key},timeout=120) as client:
            r=client.post('/api/v1/evaluation/curated/'+args.dataset_id);r.raise_for_status();print(json.dumps(r.json(),indent=2))
        return
    if args.command=='evaluate':
        if not args.dataset_id:p.error('evaluate requires --dataset-id')
        import httpx,time
        with httpx.Client(base_url='http://127.0.0.1:'+str(settings.app_port),headers={'Authorization':'Bearer '+settings.access_key},timeout=30) as client:
            response=client.post('/api/v1/evaluation/runs',json={'dataset_ids':[args.dataset_id],'repeats':args.repeats,'ablations':args.ablations,'include_samples':args.samples});response.raise_for_status()
            rid=response.json()['run_ids'][0];print('Evaluation started: '+rid,flush=True)
            while True:
                response=client.get('/api/v1/evaluation/runs/'+rid);response.raise_for_status();run=response.json()
                if run['status'] not in {'queued','running'}:break
                time.sleep(2)
            if run['status']!='completed':raise ValueError(run['error'] or 'Evaluation did not complete')
            print(json.dumps({profile:result['metrics'] for profile,result in run['report']['profiles'].items()},indent=2))
            print('Report: '+str(settings.storage/'evaluations'/(rid+'.json')))
        return
    if args.command=='datasets':
        print(json.dumps([{k:v for k,v in d.items() if k not in {'schema','policy'}} for d in store.list()],indent=2));return
    target=Path(args.out);target.parent.mkdir(parents=True,exist_ok=True)
    fields=['feedback_id','request_id','dataset_id','question','generated_sql','comment','decision','gold_sql','expected_behavior','expected_assumption']
    with target.open('w',encoding='utf8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in store.review_rows():
            values={k:row.get(k,'') for k in fields};values['generated_sql']=json.loads(row['response']).get('sql') or ''
            writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for k,v in values.items()})
    print('Exported Wrong ratings for manual review to '+str(target))

if __name__=='__main__':main()
