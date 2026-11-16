import asyncio
import json
import pytest
from backend.providers import CompatibleProvider
from backend.tests.test_manager_adaptation import answer
from experiments.sales_benchmark.run import HERE,read,inputs,validate,main
from experiments.sales_benchmark.live import Budget,CallLimit,ObservedProvider,make_runner,product_audit


def test_pilot_and_fixed_multiturn():
    pilot=read(HERE/'pilot.json');multi=read(HERE/'multiturn.json')
    assert len(pilot)==4 and len(multi)==8
    for c in pilot+multi:
        validate(c);a,b=inputs(c);assert a['turns']==b['turns'] and a['facts']==b['facts']
    for prev,nxt in zip(multi,multi[1:]):assert nxt['turns'][:-2]==prev['turns']
    assert not pilot[-1]['approved_claims']


def test_default_dry_run_never_calls_transport(tmp_path,monkeypatch):
    async def forbidden(*args):pytest.fail('Network called in dry run')
    monkeypatch.setattr(CompatibleProvider,'_complete',forbidden)
    monkeypatch.setattr('sys.argv',['benchmark','--out',str(tmp_path),'--pilot'])
    main()
    assert read(tmp_path/'execution_plan.json')['dry_run']
    assert len(read(tmp_path/'results.json'))==8


def test_hard_limit_before_transport(monkeypatch):
    calls=[]
    async def fake(*args):
        calls.append(1);return {'choices':[{'message':{'content':'{}'}}]}
    monkeypatch.setattr(CompatibleProvider,'_complete',fake)
    p=ObservedProvider(Budget(1))
    asyncio.run(p._complete('https://api.deepseek.com',{},{}))
    with pytest.raises(CallLimit):asyncio.run(p._complete('https://api.deepseek.com',{},{}))
    assert len(calls)==1


def test_real_adapter_uses_same_model_and_captures_retry(monkeypatch):
    monkeypatch.setenv('LLM_MODEL','fixture-model');monkeypatch.setenv('LLM_API_KEY','sk-secret-fixture-123');monkeypatch.setenv('LLM_BASE_URL','https://api.deepseek.com');monkeypatch.setenv('LLM_MAX_TOKENS','3000')
    c=read(HERE/'pilot.json')[0];s,_=inputs(c);payloads=[]
    a=answer(s,'Что мешает откликнуться на подходящую вакансию?','QUESTION');a.intelligence.sales_plan.need_depth='GENERAL_GOAL'
    bad=a.model_copy(deep=True);bad.intelligence.strategy.action='CLOSING'
    async def fake(self,endpoint,headers,payload):
        payloads.append(payload)
        if len(payloads)==1:content=bad.model_dump_json()
        elif len(payloads)==2:content=a.model_dump_json()
        else:content=json.dumps({'decision':'DISCOVERY','reason':'fixture','say_to_customer':'Что вызывает сомнения?'})
        return {'model':'fixture-model','choices':[{'message':{'content':content},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':20}}
    monkeypatch.setattr(CompatibleProvider,'_complete',fake)
    execute=make_runner(Budget(3))
    ar=asyncio.run(execute(c,'A'));br=asyncio.run(execute(c,'B'))
    assert ar['status']=='completed' and len(ar['raw_responses'])==2
    assert ar['metrics']['api_calls']==2 and br['metrics']['api_calls']==1
    assert all(p['model']=='fixture-model' and p['thinking']=={'type':'disabled'} for p in payloads)
    assert 'validation_feedback' not in payloads[-1]['messages'][1]['content']
    assert 'sk-secret-fixture-123' not in json.dumps([ar,br])


def test_network_failure_no_retry_and_no_secret(monkeypatch):
    monkeypatch.setenv('LLM_MODEL','fixture');monkeypatch.setenv('LLM_API_KEY','secret-fixture');monkeypatch.setenv('LLM_BASE_URL','https://api.deepseek.com')
    async def fail(*args):raise RuntimeError('secret-fixture')
    monkeypatch.setattr(CompatibleProvider,'_complete',fail)
    r=asyncio.run(make_runner(Budget(2))(read(HERE/'pilot.json')[0],'A'))
    assert r['metrics']['api_calls']==1 and r['status']=='error'
    assert 'secret-fixture' not in json.dumps(r)


def test_shared_product_screening_is_not_quality_verdict():
    c=read(HERE/'pilot.json')[-1]
    audit=product_audit(c,{'say_to_customer':'Наш курс включает куратора. Цена 99000 рублей.'})
    assert len(audit['flags'])==2 and audit['status']=='human_review_required'
