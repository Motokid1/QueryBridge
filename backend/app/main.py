import csv
import hmac
import io
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal
import httpx
from fastapi import FastAPI,HTTPException,Request,Response,Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,HTMLResponse,StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel,Field,ConfigDict
from .config import Settings,PROJECT
from .store import Store
from .connectors import inspect,inspect_mysql,inspect_sqlite,local_sqlite_path,execute
from .schema import default_policy,validate_policy,allowed_schema
from .importers import import_csv,import_sql
from .llm import Ollama
from .agent import Agent,audit
from .harness import Harness

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Login(Strict):access_key:str=Field(min_length=1,max_length=200)
class Connection(Strict):
    name:str=Field(min_length=1,max_length=100,pattern=r'\S')
    kind:Literal['mysql','sqlite']
    host:str='127.0.0.1'
    port:int=Field(default=3306,ge=1,le=65535)
    database:str=Field(default='',max_length=64)
    username:str=Field(default='',max_length=128)
    password:str=Field(default='',max_length=1024)
    path:str=Field(default='',max_length=2000)
class Question(Strict):
    dataset_id:str=Field(min_length=1,max_length=64)
    question:str=Field(min_length=3,max_length=2000,pattern=r'\S')
class Feedback(Strict):
    request_id:str=Field(min_length=1,max_length=64)
    rating:Literal['correct','wrong']
    comment:str=Field(default='',max_length=2000)
class Policy(Strict):columns:dict[str,list[str]]
class EvaluationStart(Strict):
    dataset_ids:list[str]=Field(min_length=1,max_length=20)
    repeats:int=Field(default=3,ge=1,le=3)
    ablations:bool=False
    include_samples:bool=False
    split:Literal['development','heldout']='development'
    prompt_variant:Literal['original','column_ownership','join_keys','guard_context']='original'
class BaselineChoice(Strict):margin:float=Field(default=.05,ge=0,le=1)
class FailureOverride(Strict):
    profile:str=Field(max_length=200)
    case_id:str=Field(max_length=200)
    repeat:int=Field(default=1,ge=1,le=3)
    label:str=Field(max_length=100)
    reason:str=Field(min_length=3,max_length=1000)
class EvaluationCase(Strict):
    question:str=Field(min_length=3,max_length=2000,pattern=r'\S')
    gold_sql:str=Field(default='',max_length=16000)
    expected_behavior:Literal['answer','refuse']='answer'
    expected_assumption:str|None=Field(default=None,max_length=1000)
    category:Literal['custom','count','aggregate','grouping','join','safety']='custom'
    tier:Literal['custom','simple_lookup','aggregation','multi_table_join','date_filter','ambiguous','adversarial']='custom'
    alternative_reference_sql:list[str]=Field(default_factory=list,max_length=3)

def public(store,dataset):
    value={k:v for k,v in dataset.items() if k!='connection'}
    value['table_count']=len(value['schema']['tables']);value['column_count']=sum(len(t['columns']) for t in value['schema']['tables'].values())
    return value

def create_app(settings=None,llm=None):
    settings=settings or Settings();store=Store(settings);llm=llm or Ollama(settings)
    gate=threading.Lock();login_attempts={};login_lock=threading.Lock()
    @asynccontextmanager
    async def lifespan(app):
        yield
        llm.close()
    app=FastAPI(title='Local Lens API',version='2.0.0',docs_url=None,redoc_url=None,lifespan=lifespan)
    app.state.settings=settings;app.state.store=store;app.state.agent=Agent(settings,llm)
    harness=Harness(store,settings,llm,gate);app.state.harness=harness
    @app.middleware('http')
    async def boundary(request,call_next):
        origin=request.headers.get('origin')
        if origin and origin not in settings.origins:return JSONResponse({'detail':'Untrusted origin'},403)
        length=request.headers.get('content-length','0')
        try:declared=int(length)
        except ValueError:return JSONResponse({'detail':'Invalid Content-Length'},400)
        limit=settings.max_upload_bytes if request.url.path=='/api/v1/datasets/upload' else 1024*1024
        if declared<0 or declared>limit:return JSONResponse({'detail':'Request exceeds size limit'},413)
        is_public=request.url.path=='/api/v1/health' or (request.url.path=='/api/v1/session' and request.method=='POST')
        bearer=request.headers.get('authorization','')
        token_ok=bearer.startswith('Bearer ') and hmac.compare_digest(bearer[7:].encode(),settings.access_key.encode())
        if request.url.path.startswith('/api/') and not is_public:
            if not (token_ok or store.authenticated(request.cookies.get('lens_session'))):return JSONResponse({'detail':'Sign in with your local access key'},401)
            if request.method not in {'GET','HEAD','OPTIONS'} and not origin and not token_ok:
                return JSONResponse({'detail':'Origin header required for cookie-authenticated writes'},403)
        if request.method in {'POST','PUT','PATCH'} and request.url.path!='/api/v1/datasets/upload':
            # Auth precedes parsing. Stream bounds also cover clients with missing/incorrect length headers.
            data=bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data)>limit:return JSONResponse({'detail':'Request exceeds size limit'},413)
            request._body=bytes(data)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
        return response
    app.add_middleware(CORSMiddleware,allow_origins=list(settings.origins),allow_credentials=True,allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type','Authorization'])
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]'])
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,error):return JSONResponse({'detail':'Invalid request fields','errors':[{'field':list(e['loc']),'message':e['msg']} for e in error.errors()]},422)
    @app.exception_handler(KeyError)
    async def missing(request,error):return JSONResponse({'detail':'Dataset or request not found'},404)
    @app.exception_handler(ValueError)
    async def invalid(request,error):return JSONResponse({'detail':str(error)[:500]},400)
    @app.exception_handler(Exception)
    async def unexpected(request,error):
        logging.getLogger('local-lens').error('Request failed: %s',type(error).__name__)
        return JSONResponse({'detail':'Local service operation failed. Check server availability and permissions.'},503)
    @app.get('/api/v1/health')
    def health():
        ready=False
        try:
            r=llm.client.get('/api/tags',timeout=3);r.raise_for_status();models=[m['name'] for m in r.json().get('models',[])]
            ready=all(any(m==n or m==n+':latest' for m in models) for n in (settings.model,settings.embed_model))
        except Exception:pass
        return {'eval_models':list(settings.eval_models),'temperature':settings.temperature,'seed':settings.seed,'status':'ok','model':settings.model,'embed_model':settings.embed_model,'local_inference':True,'models_ready':ready,'version':'2.0.0'}
    @app.post('/api/v1/session')
    def login(body:Login,request:Request,response:Response):
        host=request.client.host if request.client else 'local';now=time.monotonic()
        with login_lock:
            attempts=[t for t in login_attempts.get(host,[]) if now-t<60]
            if len(attempts)>=10:raise HTTPException(429,'Too many sign-in attempts; wait one minute')
            if not hmac.compare_digest(body.access_key.encode(),settings.access_key.encode()):
                login_attempts[host]=attempts+[now];raise HTTPException(401,'Incorrect access key')
            login_attempts.pop(host,None)
        token=store.session();response.set_cookie('lens_session',token,httponly=True,samesite='strict',max_age=43200,path='/')
        return {'authenticated':True}
    @app.get('/api/v1/session')
    def session():return {'authenticated':True,'local_file_roots':[str(p) for p in settings.local_file_roots]}
    @app.delete('/api/v1/session')
    def logout(request:Request,response:Response):
        store.logout(request.cookies.get('lens_session',''));response.delete_cookie('lens_session');return {'status':'signed_out'}
    @app.get('/api/v1/datasets')
    def datasets():return store.list()
    @app.get('/api/v1/datasets/{dataset_id}')
    def dataset(dataset_id:str):return public(store,store.get(dataset_id))
    @app.post('/api/v1/datasets/connect',status_code=201)
    def connect(body:Connection):
        if body.kind=='mysql':
            if not body.database or not body.username:raise ValueError('Provide a database name and SELECT-only username')
            config={k:getattr(body,k) for k in ('host','port','database','username','password')}
            try:schema=inspect_mysql(config,settings)
            except ValueError:raise
            except Exception:raise ValueError('Could not connect to MySQL. Check the local server, database name and read-only credentials.')
        else:
            config={'path':local_sqlite_path(body.path,settings)};schema=inspect_sqlite(config['path'])
        did=str(uuid.uuid4());policy=default_policy(schema)
        if not any(policy.values()):raise ValueError('No non-sensitive columns are available')
        store.register(did,body.name.strip(),body.kind,config,schema,policy)
        harness.suite(did)
        audit(settings,did,'dataset_connected',kind=body.kind,table_count=len(schema['tables']))
        return public(store,store.get(did))
    @app.post('/api/v1/datasets/upload',status_code=201)
    async def upload(request:Request,name:str=Query(min_length=1,max_length=100),filename:str=Query(min_length=1,max_length=150),dialect:Literal['mysql','sqlite']='mysql'):
        if not name.strip():raise ValueError('Provide a dataset name')
        suffix=Path(filename).suffix.lower()
        if suffix not in {'.csv','.sql'}:raise ValueError('Upload a .csv or .sql file')
        did=str(uuid.uuid4());source=settings.storage/'staging'/f'{did}.upload';temp=settings.storage/'staging'/f'{did}.sqlite'
        destination=settings.storage/'datasets'/f'{did}.sqlite';total=0;registered=False
        try:
            with source.open('xb') as f:
                async for chunk in request.stream():
                    total+=len(chunk)
                    if total>settings.max_upload_bytes:raise HTTPException(413,'Upload exceeds configured size limit')
                    f.write(chunk)
            if not total:raise ValueError('File is empty')
            try:
                if suffix=='.csv':schema,info=await run_in_threadpool(import_csv,source,temp,settings)
                else:schema,info=await run_in_threadpool(import_sql,source,temp,settings,dialect)
            except (UnicodeError,csv.Error):raise ValueError('Upload must contain valid UTF-8 CSV/SQL data')
            policy=default_policy(schema)
            if not any(policy.values()):raise ValueError('Imported file contains no enabled, non-sensitive columns')
            schema['import_info']=info;temp.replace(destination)
            store.register(did,name.strip(),'csv' if suffix=='.csv' else 'sql',{'path':str(destination)},schema,policy);registered=True
            harness.suite(did)
            audit(settings,did,'dataset_imported',kind=suffix[1:],bytes=total,**info)
            return public(store,store.get(did))
        finally:
            source.unlink(missing_ok=True);temp.unlink(missing_ok=True)
            if not registered:destination.unlink(missing_ok=True)
    @app.put('/api/v1/datasets/{dataset_id}/policy')
    def policy(dataset_id:str,body:Policy):
        if gate.locked():raise HTTPException(409,'Wait for the active question or evaluation before changing policy')
        dataset=store.get(dataset_id);policy=validate_policy(dataset['schema'],body.columns);store.policy(dataset_id,policy)
        audit(settings,dataset_id,'policy_updated',enabled_columns=sum(map(len,policy.values())))
        return public(store,store.get(dataset_id))
    @app.post('/api/v1/datasets/{dataset_id}/refresh')
    def refresh(dataset_id:str):
        if gate.locked():raise HTTPException(409,'Wait for the active question or evaluation before refreshing schema')
        dataset=store.get(dataset_id)
        try:schema=inspect(dataset,settings)
        except ValueError:raise
        except Exception:raise ValueError('Could not refresh schema. Check the local connection and permissions.')
        policy={n:[c for c in dataset['policy'].get(n,[]) if c in t['columns']] for n,t in schema['tables'].items()}
        store.refresh(dataset_id,schema,policy);audit(settings,dataset_id,'schema_refreshed',table_count=len(schema['tables']))
        return public(store,store.get(dataset_id))
    @app.get('/api/v1/datasets/{dataset_id}/preview')
    def preview(dataset_id:str,table:str=Query(max_length=150)):
        dataset=store.get(dataset_id);schema=allowed_schema(dataset)
        if table not in schema:raise ValueError('Table is disabled or unknown')
        delimiter='`' if dataset['kind']=='mysql' else '"'
        q=lambda n:delimiter+n.replace(delimiter,delimiter+delimiter)+delimiter
        sql='SELECT '+','.join(q(c) for c in schema[table])+' FROM '+q(table)+' LIMIT 10'
        _,rows=execute(sql,dataset,settings);return {'rows':rows,'table':table}
    @app.post('/api/v1/ask')
    def ask(body:Question):
        dataset=store.get(body.dataset_id)
        if not gate.acquire(blocking=False):raise HTTPException(409,'Another question is running. Wait for it to finish.')
        try:
            result=app.state.agent.answer(dataset,body.question);store.save_request(dataset['id'],body.question,result);return result
        finally:gate.release()
    @app.post('/api/v1/feedback')
    def feedback(body:Feedback):
        try:store.feedback(body.request_id,body.rating,body.comment)
        except ValueError:raise HTTPException(409,'This answer has already been rated')
        audit(settings,body.request_id,'feedback_saved',rating=body.rating);return {'status':'saved'}
    @app.get('/api/v1/evaluation/cases/{dataset_id}')
    def evaluation_cases(dataset_id:str):return harness.suite(dataset_id)
    @app.post('/api/v1/evaluation/cases/{dataset_id}',status_code=201)
    def evaluation_add_case(dataset_id:str,body:EvaluationCase):
        if gate.locked():raise HTTPException(409,'Wait for the active evaluation or question')
        if len(harness.suite(dataset_id))>=100:raise ValueError('Suite limit is 100 cases')
        return {'id':harness.add_case(dataset_id,body.model_dump())}
    @app.delete('/api/v1/evaluation/cases/{dataset_id}/{case_id}')
    def evaluation_delete_case(dataset_id:str,case_id:str):
        if gate.locked():raise HTTPException(409,'Wait for the active evaluation or question')
        store.get(dataset_id)
        with store.db() as c:
            row=c.execute('SELECT source FROM test_cases WHERE id=? AND dataset_id=?',(case_id,dataset_id)).fetchone()
            if not row:raise KeyError()
            if row['source']=='generated':raise ValueError('Generated smoke cases follow the saved schema policy and cannot be deleted')
            c.execute('DELETE FROM test_cases WHERE id=? AND dataset_id=?',(case_id,dataset_id))
        return {'status':'removed'}
    @app.get('/api/v1/evaluation/runs')
    def evaluation_runs(dataset_id:str|None=None):return harness.runs(dataset_id)
    @app.get('/api/v1/evaluation/runs/{run_id}')
    def evaluation_run(run_id:str):return harness.run(run_id)
    @app.get('/api/v1/evaluation/runs/{run_id}/export')
    def evaluation_export(run_id:str):
        run=harness.run(run_id)
        if not run['report']:raise HTTPException(409,'Report is available when the run completes')
        return Response(json.dumps(run['report'],indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="evaluation-'+run_id+'.json"'})
    @app.post('/api/v1/evaluation/runs',status_code=202)
    def evaluation_start(body:EvaluationStart):
        if len(set(body.dataset_ids))!=len(body.dataset_ids):raise ValueError('Dataset IDs must be unique')
        try:return {'run_ids':harness.start(body.dataset_ids,body.repeats,body.ablations,body.include_samples,body.split,body.prompt_variant)}
        except RuntimeError as exc:raise HTTPException(409,str(exc))
    @app.post('/api/v1/evaluation/curated/{dataset_id}')
    def evaluation_curated(dataset_id:str):
        if gate.locked():raise HTTPException(409,'Wait for active evaluation or question')
        return harness.curated(dataset_id)
    @app.post('/api/v1/evaluation/runs/{run_id}/baseline')
    def evaluation_baseline(run_id:str,body:BaselineChoice):return harness.baseline(run_id,body.margin)
    @app.get('/api/v1/evaluation/compare')
    def evaluation_compare(old:str,new:str,margin:float=Query(default=.05,ge=0,le=1)):return harness.diff(old,new,margin)
    @app.post('/api/v1/evaluation/runs/{run_id}/override')
    def evaluation_override(run_id:str,body:FailureOverride):return harness.override(run_id,body.profile,body.case_id,body.repeat,body.label,body.reason)
    @app.get('/api/v1/history')
    def history(dataset_id:str|None=None):return store.history(dataset_id)
    @app.get('/api/v1/metrics')
    def metrics(dataset_id:str|None=None):return store.metrics(dataset_id)
    @app.get('/api/v1/requests/{request_id}/audit')
    def trace(request_id:str):
        with store.db() as c:
            if not c.execute('SELECT 1 FROM requests WHERE id=?',(request_id,)).fetchone():raise KeyError()
        path=settings.storage/'audit.jsonl'
        return [r for r in (json.loads(l) for l in path.read_text(encoding='utf8').splitlines()) if r['request_id']==request_id]
    @app.get('/api/v1/feedback/export')
    def export():
        output=io.StringIO();fields=['feedback_id','request_id','dataset_id','question','generated_sql','comment','decision','gold_sql','expected_behavior','expected_assumption']
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
        for row in store.review_rows():
            result=json.loads(row['response']);values={k:row.get(k,'') for k in fields}
            values['generated_sql']=result.get('sql') or ''
            # Prevent spreadsheet formula execution when opening user-authored fields.
            writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for k,v in values.items()})
        return Response(output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="feedback-review.csv"'})
    @app.get('/docs',response_class=HTMLResponse)
    def docs():return '<html><head><title>Local Lens API</title></head><body><h1>Local Lens API v2</h1><p>Local-only, authenticated API. <a href="/openapi.json">OpenAPI specification</a></p><p>POST /api/v1/session; GET /api/v1/datasets; POST /api/v1/datasets/connect; POST /api/v1/datasets/upload; PUT /api/v1/datasets/{id}/policy; POST /api/v1/ask; POST /api/v1/feedback.</p><a href="/">Application</a></body></html>'
    dist=PROJECT/'frontend'/'dist'
    if dist.exists():app.mount('/',StaticFiles(directory=dist,html=True),name='frontend')
    return app

# Uvicorn uses --factory so importing modules never creates storage or reads credentials during tests.
