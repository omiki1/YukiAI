export async function request(url:string,body?:unknown,signal?:AbortSignal){
 return fetch('/api/'+url,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal});
}
export async function api(url:string,body?:unknown,signal?:AbortSignal){
 const response=await request(url,body,signal);const data=await response.json();
 if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'连接没有成功，请稍后再试。');return data;
}
