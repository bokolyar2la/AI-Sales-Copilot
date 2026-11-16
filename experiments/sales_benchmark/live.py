"""Bounded experiment adapter; reuses production HTTP transport unchanged."""
import json
import os
import re
from time import perf_counter
from urllib.parse import urlparse
from backend.providers import CompatibleProvider
from backend.engine import process_turn
from backend.diagnostics import capture, snapshot
from backend.intelligence import commercial_amounts

class CallLimit(RuntimeError):pass
class Budget:
    def __init__(self,limit):
        if limit<1:raise ValueError('max_calls must be positive')
        self.limit=limit;self.used=0;self.journal=None
    def take(self):
        if self.used>=self.limit:raise CallLimit('API call limit reached')
        token=self.journal.reserve() if self.journal else None
        self.used+=1
        return token

def scrub(value):
    if isinstance(value,str):
        secret=os.getenv('LLM_API_KEY','')
        if secret:value=value.replace(secret,'[REDACTED]')
        return re.sub(r'(?i)(?:Bearer\s+\S+|sk-[a-z0-9_-]{8,})','[REDACTED]',value)
    if isinstance(value,list):return [scrub(v) for v in value]
    if isinstance(value,dict):return {k:scrub(v) for k,v in value.items()}
    return value

class ObservedProvider(CompatibleProvider):
    def __init__(self,budget):
        super().__init__();self.budget=budget;self.raw=[]
    async def _complete(self,endpoint,headers,payload):
        token=self.budget.take()
        data=await super()._complete(endpoint,headers,payload)
        self.raw.append(scrub({'content':data['choices'][0]['message']['content'],
                              'finish_reason':data['choices'][0].get('finish_reason'),
                              'response_model':data.get('model'),'usage':data.get('usage')}))
        if self.budget.journal:
            self.budget.journal.response(token,self.raw[-1])
        return data


def product_audit(case,output):
    """Conservative shared screening, not semantic proof or quality scoring."""
    phrase=output.get('say_to_customer','') if output else ''
    approved=' '.join(c['text'] for c in case['approved_claims'])
    client=' '.join(t['text'] for t in case['turns'] if t['role']=='client')
    flags=[]
    if commercial_amounts(phrase)-commercial_amounts(approved)-commercial_amounts(client):flags.append('unconfirmed_amount_candidate')
    if not approved and re.search(r'наш[^.!?]*(?:курс|программ)[^.!?]*(?:включает|даёт|доступ|куратор)|мы гарантируем',phrase,re.I):flags.append('unsupported_product_candidate')
    return {'flags':flags,'status':'human_review_required','note':'Каждое продуктовое утверждение проверяет независимый эксперт; отсутствие флага не доказывает точность.'}


def configured():
    from dotenv import load_dotenv
    from .run import ROOT
    load_dotenv(ROOT/'.env',override=False)
    endpoint=os.getenv('LLM_BASE_URL','').rstrip('/')
    if urlparse(endpoint).hostname!='api.deepseek.com' or not endpoint.startswith('https://'):raise ValueError('Expected existing HTTPS DeepSeek configuration')
    if not os.getenv('LLM_MODEL') or not os.getenv('LLM_API_KEY'):raise ValueError('Existing DeepSeek configuration incomplete')
    return os.environ['LLM_MODEL']


def make_runner(budget):
    async def execute(case,variant):
        from .run import inputs,HERE,digest,minimal_output
        state,common=inputs(case);provider=ObservedProvider(budget)
        record={'scenario_id':case['id'],'scenario_hash':digest(case),'variant':variant,'origin':'real','status':'missing','output':None,'metrics':None}
        if budget.used>=budget.limit:
            record.update(origin='missing',status='not_run',error_code='call_limit');return record
        start=perf_counter();before=budget.used
        with capture() as trace:
            try:
                if variant=='A':
                    await process_turn(state,provider,{'approved_claims':case['approved_claims']})
                    rec=state['recommendations'][-1]
                    record.update(output={'decision':rec['strategy']['action'],'reason':rec['reason'],'say_to_customer':rec['suggestion']},validation=rec['validation'],validation_trace=rec.get('validation_trace',[]))
                    record['status']='blocked' if rec['validation']['status']=='blocked' else 'completed'
                else:
                    payload={'model':os.environ['LLM_MODEL'],'messages':[{'role':'system','content':(HERE/'minimal_prompt.txt').read_text(encoding='utf-8')},{'role':'user','content':json.dumps(common,ensure_ascii=False)}],
                             'response_format':{'type':'json_object'},'temperature':.2,'max_tokens':int(os.getenv('LLM_MAX_TOKENS','3000')),'thinking':{'type':'disabled'}}
                    data=await provider._complete(os.environ['LLM_BASE_URL'].rstrip('/'),{'Authorization':'Bearer '+os.environ['LLM_API_KEY']},payload)
                    record.update(output=minimal_output(json.loads(data['choices'][0]['message']['content'])),status='completed',validation={'status':'schema_only'})
            except Exception as exc:
                # Do not serialize HTTP exceptions, URLs, headers or response bodies.
                record.update(status='error',error_code=type(exc).__name__)
            record['diagnostics']=scrub(snapshot(trace))
        usage=[r.get('usage') for r in provider.raw]
        record['raw_responses']=provider.raw
        record['metrics']={'latency_ms':round((perf_counter()-start)*1000,2),'api_calls':budget.used-before,
                           'input_tokens':sum(u.get('prompt_tokens',0) for u in usage) if usage and all(isinstance(u,dict) and 'prompt_tokens' in u for u in usage) else None,
                           'output_tokens':sum(u.get('completion_tokens',0) for u in usage) if usage and all(isinstance(u,dict) and 'completion_tokens' in u for u in usage) else None,'cost_usd':None}
        record['product_audit']=product_audit(case,record['output'])
        record['diagnostic_category']='UNKNOWN'
        return scrub(record)
    execute.budget=budget
    return execute
