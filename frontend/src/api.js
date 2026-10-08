export async function api(path,{method='GET',body,raw=false}={}){
 const response=await fetch('/api/v1'+path,{method,credentials:'include',headers:body&&!raw?{'Content-Type':'application/json'}:{},body:body?(raw?body:JSON.stringify(body)):undefined});
 const data=await response.json().catch(()=>({detail:'The local server returned an unexpected response.'}));
 if(response.status===401&&!path.startsWith('/session'))window.dispatchEvent(new Event('lens-auth-expired'));
 if(!response.ok){const error=new Error(typeof data.detail==='string'?data.detail:'Check the supplied fields.');error.status=response.status;throw error;}
 return data;
}
