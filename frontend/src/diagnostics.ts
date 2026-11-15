// Local numeric diagnostics only; no dialogue text or external telemetry.
type Sample={trace_id?:string;api_ms:number;response_json_ms:number;display_proxy_ms?:number;render_wait_ms?:number;visible?:boolean;outcome:string;server?:unknown};
declare global {interface Window {__copilotDiagnostics?:Sample[]}}
const pending=new Map<string,{sample:Sample;start:number;received:number}>();
export function recordResponse(path:string,start:number,received:number,jsonMs:number,response:Response,data:unknown){
 if(!/\/(turns|retry)$/.test(path))return;
 const s=data as {id?:string;analysis_error?:string;recommendations?:{suggestion:string;validation?:{status:string}}[]};
 if(!s.id)return;
 const rec=s.recommendations?.at(-1);
 let server:{trace_id?:string}|undefined;
 try{server=JSON.parse(response.headers.get('X-Copilot-Diagnostics')||'null')||undefined;}catch{/* no diagnostics on old server */}
 const sample:Sample={trace_id:server?.trace_id,api_ms:received-start,response_json_ms:jsonMs,
  outcome:s.analysis_error?'error':rec?.validation?.status==='blocked'?'blocked':rec?.suggestion?'hint':'pause',server};
 pending.set(s.id,{sample,start,received});
}
export function recordRendered(sessionId:string){
 const item=pending.get(sessionId);if(!item)return;
 pending.delete(sessionId);
 // Two RAFs bracket a browser paint opportunity; not a physical-screen timestamp.
 requestAnimationFrame(()=>requestAnimationFrame(()=>{
  item.sample.display_proxy_ms=performance.now()-item.start;
  item.sample.render_wait_ms=performance.now()-item.received;
  item.sample.visible=document.visibilityState==='visible';
  window.__copilotDiagnostics=[...(window.__copilotDiagnostics||[]),item.sample].slice(-100);
  window.dispatchEvent(new CustomEvent('copilot-diagnostics',{detail:item.sample}));
 }));
}
