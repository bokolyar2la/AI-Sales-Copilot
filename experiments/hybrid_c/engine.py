"""Experimental compact decision, operating on the existing session dictionaries."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Union
import json
import re
from pydantic import Field
from backend.models import StrictModel, Fact, HypothesisUpdate, DialogueEvent
from backend.intelligence import apply_hypotheses, normalized
from backend.dialogue_policy import update_state
from backend.context import uid, now
from experiments.sales_turning_points.p23_safety import historical_audit

VERIFY_PHRASE='Сначала проверю точные условия по документам, затем обсудим следующий шаг.'
class FactDelta(StrictModel):
    kind: Literal['fact']
    value: Fact
class HypothesisDelta(StrictModel):
    kind: Literal['hypothesis']
    value: HypothesisUpdate
class EventDelta(StrictModel):
    kind: Literal['event']
    value: DialogueEvent
class Decision(StrictModel):
    action: Literal['ASK','ARGUE','PRESENT','ANSWER','VERIFY_TERMS','CLOSE','END']
    reason: str=Field(min_length=1,max_length=800)
    say_to_customer: str=Field(min_length=1,max_length=700)
    evidence_ids: list[str]=Field(min_length=1,max_length=10)
    claim_ids: list[str]=Field(max_length=5)
    memory_delta: list[Union[FactDelta,HypothesisDelta,EventDelta]]=Field(max_length=12)

def context(session,claims):
    # Whitelist: neither expert labels nor observed future continuation can enter.
    return {**{k:deepcopy(session.get(k,[] if k!='dialogue_state' else {}))
        for k in ('turns','facts','hypotheses','dialogue_state')},'approved_claims':deepcopy(claims)}

def validate_and_apply(session,decision,claims):
    d=decision;draft=deepcopy(session);turns={t['id']:t for t in draft['turns']};last=draft['turns'][-1]
    if any(i not in turns for i in d.evidence_ids):raise ValueError('unsupported_decision_evidence')
    known={c['id']:c['text'] for c in claims if c.get('approved') is True}
    if any(i not in known for i in d.claim_ids):raise ValueError('unsupported_claim_id')
    phrase=d.say_to_customer
    from .guards import check_speech
    clarification=check_speech(draft,d,claims,VERIFY_PHRASE)
    if d.action=='VERIFY_TERMS' and phrase!=VERIFY_PHRASE:raise ValueError('verify_phrase_must_not_assert_terms')
    if d.action=='ASK':
        for t in draft['turns']:
            if t['role']=='manager' and normalized(t['text'])==normalized(phrase):raise ValueError('repeated_spoken_question')
    audit=historical_audit({'turns':draft['turns'],'approved_claims':[]},{'say_to_customer':phrase})
    risky=audit['claim_types'] or re.search(r'закреплен|зафиксирован|прописан|по договору|откро[ею]|получите|предостав|наш[а-я]* (?:курс|программ)',phrase,re.I)
    if risky and phrase!=VERIFY_PHRASE and not clarification:
        if not d.claim_ids or phrase not in [known[i] for i in d.claim_ids]:
            raise ValueError('unverified_final_phrase_requires_exact_approved_claim')
    facts=[x.value for x in d.memory_delta if x.kind=='fact']
    hyps=[x.value for x in d.memory_delta if x.kind=='hypothesis']
    events=[x.value for x in d.memory_delta if x.kind=='event']
    for f in facts:
        if f.source_turn!=last['id'] or last['role']!='client' or f.quote not in last['text']:
            raise ValueError('fact_source_latest_exact_quote')
        for old_id in f.replaces:
            if not any(o['id']==old_id and o['category']==f.category for o in draft['facts']):raise ValueError('replacement_category')
    apply_hypotheses(draft,SimpleNamespace(facts=facts,intelligence=SimpleNamespace(hypotheses=hyps)))
    draft['dialogue_state']=update_state(draft,events)
    for f in facts:
        for old in draft['facts']:
            if old['id'] in f.replaces:old['status']='OUTDATED' if f.status=='CONFIRMED' else 'CONTRADICTED'
        if not any(o['value']==f.value and o['category']==f.category and o['status']==f.status for o in draft['facts']):
            draft['facts'].append({**f.model_dump(),'id':uid(),'created_at':now()})
    session.clear();session.update(draft)
    return d

async def decide(session,claims,provider):
    import os
    trace=[];prompt=Path(__file__).with_name('prompt.txt').read_text(encoding='utf-8-sig')
    for attempt in range(2):
        body=context(session,claims);body['schema']=Decision.model_json_schema()
        if trace:body['validation_feedback']=trace[-1]
        payload={'model':os.environ['LLM_MODEL'],'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps(body,ensure_ascii=False)}],
                 'temperature':.2,'max_tokens':min(3000,int(os.getenv('LLM_MAX_TOKENS','3000'))),'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}}
        data=await provider._complete(os.environ['LLM_BASE_URL'].rstrip('/'),{'Authorization':'Bearer '+os.environ['LLM_API_KEY']},payload)
        try:
            d=Decision.model_validate_json(data['choices'][0]['message']['content'])
            validate_and_apply(session,d,claims)
            trace.append({'attempt':attempt+1,'status':'passed'})
            return d,trace
        except ValueError as e:
            error=e.errors(include_input=False,include_url=False) if hasattr(e,'errors') else str(e)
            trace.append({'attempt':attempt+1,'status':'blocked','error':error})
    return None,trace

def make_runner(budget,warning):
    from experiments.sales_benchmark.live import make_runner as ab_runner, ObservedProvider
    from experiments.sales_benchmark.run import inputs,digest
    from time import perf_counter
    ab=ab_runner(budget,warning=warning,audit=historical_audit)
    async def execute(case,variant):
        if variant!='C':return await ab(case,variant)
        state,_=inputs(case);provider=ObservedProvider(budget,warning=warning);start=perf_counter();before=budget.used
        r={'scenario_id':case['id'],'scenario_hash':digest(case),'variant':'C','origin':'real','status':'error','output':None}
        try:
            d,trace=await decide(state,case['approved_claims'],provider)
            r.update(status='completed' if d else 'blocked',validation={'status':'passed' if d else 'blocked'},validation_trace=trace)
            if d:r['output']={'decision':d.action,'reason':d.reason,'say_to_customer':d.say_to_customer};r['session_after']=state
        except Exception as e:r['error_code']=type(e).__name__
        r['raw_responses']=provider.raw
        usage=[x.get('usage') for x in provider.raw]
        r['metrics']={'api_calls':budget.used-before,'latency_ms':(perf_counter()-start)*1000,
           'input_tokens':sum(x.get('prompt_tokens',0) for x in usage) if usage and all(isinstance(x,dict) for x in usage) else None,
           'output_tokens':sum(x.get('completion_tokens',0) for x in usage) if usage and all(isinstance(x,dict) for x in usage) else None,'cost_usd':None}
        r['product_audit']=historical_audit(case,r['output'])
        return r
    execute.budget=budget
    return execute
