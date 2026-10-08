import hashlib
import json
import re
from collections import deque
import numpy as np
from .schema import allowed_schema

def terms(text):
    words=re.findall(r'[a-z0-9]+',text.lower())
    return {w[:-3]+'y' if len(w)>4 and w.endswith('ies') else w[:-1] if len(w)>3 and w.endswith('s') and not w.endswith(('ss','us')) else w for w in words}

class Retriever:
    def __init__(self,settings,llm):self.settings=settings;self.llm=llm;self.question_cache={}
    def retrieve(self,dataset,question):
        schema=allowed_schema(dataset);names=list(schema)
        if not names:raise ValueError('This dataset has no enabled columns')
        docs=[n+' ('+', '.join(f'{c}: {t}' for c,t in list(schema[n].items())[:100])+')' for n in names]
        key=hashlib.sha256(json.dumps([dataset['id'],self.settings.embed_model,docs,dataset['schema'].get('relations',[])]).encode()).hexdigest()
        path=self.settings.storage/'embeddings'/f'{key}.npy'
        if path.exists():vectors=np.load(path,allow_pickle=False)
        else:
            vectors=[]
            for start in range(0,len(docs),16):vectors.extend(self.llm.embed(docs[start:start+16]))
            vectors=np.asarray(vectors,dtype=float);np.save(path,vectors,allow_pickle=False)
        qkey=(key,question)
        if qkey not in self.question_cache:
            if len(self.question_cache)>=256:self.question_cache.pop(next(iter(self.question_cache)))
            self.question_cache[qkey]=self.llm.embed([question])[0]
        q=np.asarray(self.question_cache[qkey]);scores=vectors@q/(np.linalg.norm(vectors,axis=1)*max(np.linalg.norm(q),1e-12)+1e-12)
        top=[names[i] for i in np.argsort(scores)[::-1][:4]]
        words=terms(question)
        # Exact schema-name matches complement semantic retrieval, including common plural forms.
        # Compound table names require every component to be mentioned; this avoids matching every *_id table.
        anchors=[n for n in names if terms(n) and terms(n)<=words]
        if len(anchors)>8:raise ValueError('Question names too many tables; ask a narrower question')
        top=list(dict.fromkeys(anchors+top));selected=set(top)
        graph={n:set() for n in names}
        for a,ac,b,bc in dataset['schema'].get('relations',[]):
            if a in schema and b in schema and ac in schema[a] and bc in schema[b]:graph[a].add(b);graph[b].add(a)
        routes=[]
        for target in top[1:]:
            queue=deque([[top[0]]]);seen=set()
            while queue:
                path_nodes=queue.popleft();last=path_nodes[-1]
                if last==target:
                    selected.update(path_nodes)
                    if len(path_nodes)>2:routes.append(' -> '.join(path_nodes))
                    break
                if last in seen:continue
                seen.add(last);queue.extend(path_nodes+[n] for n in sorted(graph[last]-seen))
        # Bounded context: retain likely columns and join keys rather than silently overflowing the model context.
        lines=[];included={}
        for name in names:
            if name not in selected:continue
            keys={r[1] for r in dataset['schema'].get('relations',[]) if r[0]==name}|{r[3] for r in dataset['schema'].get('relations',[]) if r[2]==name}
            columns=sorted(schema[name],key=lambda c:(c in keys or c.endswith('_id'),len(words&terms(c))),reverse=True)[:30]
            included[name]=columns;lines.append(name+' ('+', '.join(f'{c}: {schema[name][c]}' for c in columns)+')')
        lines += [f'{a}.{ac} = {b}.{bc}' for a,ac,b,bc in dataset['schema'].get('relations',[]) if ac in included.get(a,[]) and bc in included.get(b,[])]
        if routes:lines.append('Join routes: use the intermediate tables and listed equality keys.\n'+'\n'.join(routes))
        prompt='\n'.join(lines)
        if len(prompt)>12000:raise ValueError('Retrieved schema exceeds context limits; narrow the dataset policy')
        return prompt,included
