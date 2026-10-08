import React,{useState,useEffect} from 'react';
import {BarChart,Bar,XAxis,YAxis,Tooltip,ResponsiveContainer,CartesianGrid} from 'recharts';
import {ArrowRight,ShieldCheck,Loader2,Check,X,Terminal,Layers,Clock,FileText} from 'lucide-react';
import {api} from '../api';
import {DataTable} from './SchemaPanel';

export default function Analyst({dataset,result,onAsk,busy,onRefreshMetrics}){
 const [question,setQuestion]=useState(''),[rating,setRating]=useState(''),[comment,setComment]=useState(''),[saved,setSaved]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(''),[audit,setAudit]=useState(null);
 useEffect(()=>{setQuestion('');setRating('');setComment('');setSaved(false);setError('');setAudit(null)},[dataset.id]);
 useEffect(()=>{setRating(result?.existing_rating||'');setComment('');setSaved(Boolean(result?.existing_rating));setAudit(null)},[result?.request_id]);
 async function rate(){setSaving(true);setError('');try{await api('/feedback',{method:'POST',body:{request_id:result.request_id,rating,comment}});setSaved(true);onRefreshMetrics()}catch(e){setError(e.message)}finally{setSaving(false)}}
 async function trace(){setError('');try{setAudit(await api('/requests/'+result.request_id+'/audit'))}catch(e){setError(e.message)}}
 const table=Object.keys(dataset.schema.tables).find(t=>dataset.policy[t]?.length);
 const suggestions=table?[`How many rows are in ${table}?`,`Summarize the numeric columns in ${table}.`,`Show 10 records from ${table}.`]:[];
 return <><div className="page-heading"><div><span className="eyebrow">ANALYZE · {dataset.kind.toUpperCase()}</span><h1>Good questions. Clearer answers.</h1><p>Explore <strong>{dataset.name}</strong> in plain English. See the data, the query, and the reasoning behind each answer.</p></div></div>
 <form className="ask-panel" onSubmit={e=>{e.preventDefault();onAsk(question)}}><label htmlFor="question">What would you like to know?</label><textarea id="question" maxLength={2000} rows={3} value={question} disabled={busy} onChange={e=>setQuestion(e.target.value)} placeholder="Ask about trends, totals, comparisons or individual records…"/><div className="ask-actions"><span><ShieldCheck size={15}/> Read-only · Selected dataset only</span><button className="primary" disabled={busy||question.trim().length<3}>{busy?<Loader2 className="spin" size={17}/>:<ArrowRight size={17}/>} {busy?'Analyzing…':'Ask analyst'}</button></div></form>
 {!result&&!busy&&<div className="suggestions">{suggestions.map(q=><button className="secondary" key={q} onClick={()=>setQuestion(q)}>{q}<ArrowRight size={14}/></button>)}</div>}
 {busy&&<div className="loading"><Loader2 className="spin" size={24}/><div><strong>Finding the right data</strong><p>Retrieving your schema, generating SQL and checking a read-only result. Local CPU inference can take a minute or more.</p></div></div>}
 {result&&<section className="answer-panel"><div className="answer-top"><span className={'status-badge '+result.status}>{result.status==='answered'?<Check size={15}/>:<ShieldCheck size={15}/>} {result.status==='answered'?'Answer ready':result.status==='refused'?'Request refused':'Could not answer reliably'}</span><span><Clock size={14}/> {result.latency_seconds.toFixed(1)}s · {result.attempts} attempts · {result.tokens} tokens</span></div><h2>Your answer</h2><p className="answer-text">{result.answer}</p>
 {!!result.assumptions.length&&<div className="assumptions"><strong>Assumptions</strong><ul>{result.assumptions.map((a,i)=><li key={i}>{a}</li>)}</ul></div>}
 {result.chart&&<div className="chart"><ResponsiveContainer width="100%" height={320}><BarChart data={result.chart.data} margin={{bottom:40,left:5,right:20}}><CartesianGrid strokeDasharray="3 3" vertical={false}/><XAxis dataKey={result.chart.x_key} angle={-20} textAnchor="end" interval={0} tick={{fontSize:11}}/><YAxis tick={{fontSize:11}}/><Tooltip/><Bar isAnimationActive={false} dataKey={result.chart.y_key} fill="#287a60" radius={[4,4,0,0]}/></BarChart></ResponsiveContainer><small>{result.chart.note}</small></div>}
 {!!result.rows.length&&<DataTable rows={result.rows}/>}
 <details><summary><Layers size={16}/> Retrieved schema</summary><div className="retrieved">{Object.entries(result.retrieved_tables).map(([t,cols])=><p key={t}><strong>{t}</strong><br/>{cols.join(', ')}</p>)}</div></details>
 {result.sql&&<details><summary><Terminal size={16}/> Executed SQL</summary><pre>{result.sql}</pre></details>}
 {!!result.trace.length&&<details><summary>Retry history · {result.trace.length} errors</summary>{result.trace.map((t,i)=><div className="trace" key={i}><strong>Attempt {t.attempt} · {t.category}</strong><p>{t.reason}</p>{t.sql&&<pre>{t.sql}</pre>}</div>)}</details>}
 <div className="audit-link"><button className="text-button" onClick={trace}><FileText size={16}/> View audit record</button><code>{result.request_id}</code></div>{audit&&<details open><summary>Request audit</summary><pre>{JSON.stringify(audit,null,2)}</pre></details>}
 <div className="feedback"><h3>Was this answer correct?</h3><p>A rating creates a review signal. It does not automatically train the model or change the test set.</p>{saved?<div className="success"><Check size={17}/> Feedback saved.</div>:<><div className="rating-buttons"><button className={'secondary '+(rating==='correct'?'selected':'')} onClick={()=>setRating('correct')}><Check size={16}/> Correct</button><button className={'secondary '+(rating==='wrong'?'selected wrong':'')} onClick={()=>setRating('wrong')}><X size={16}/> Wrong</button></div><label htmlFor="feedback-comment">Optional comment</label><textarea id="feedback-comment" rows={2} maxLength={2000} value={comment} onChange={e=>setComment(e.target.value)} placeholder="What should a reviewer check?"/><button className="primary" disabled={!rating||saving} onClick={rate}>{saving?'Saving…':'Save feedback'}</button></>}{error&&<p role="alert" className="error">{error}</p>}</div>
 </section>}
 </>;
}
