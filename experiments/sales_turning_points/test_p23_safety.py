import asyncio
import json
import pytest
from experiments.sales_turning_points.p23_safety import *
from experiments.sales_benchmark.live import Budget,make_runner
from backend.providers import CompatibleProvider

@pytest.mark.parametrize('phrase',[
 'Вы можете попробовать курс три дня и вернуть деньги полностью.',
 'Если не понравится, вернём всю оплату.',
 'Первый платёж через месяц, рассрочка без переплат.',
 'Вам предоставляется скидка и второй курс в подарок.',
 'После обучения гарантируем трудоустройство.',
 'За вами закрепят наставника, доступ останется навсегда.',
 'Практические задания входят в курс.',
])
def test_promises_flagged_without_rewriting(phrase):
    o={'say_to_customer':phrase}
    assert 'unsupported_product_promise_candidate' in historical_audit({'approved_claims':[],'turns':[]},o)['flags']
    assert o['say_to_customer']==phrase

def test_repeat_registry(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b'
    with experiment_registration(tmp_path,a):pass
    with pytest.raises(ValueError,match='authorize-repeat'):
        with experiment_registration(tmp_path,b):pass
    with experiment_registration(tmp_path,b,repeat_permission=EXPERIMENT_ID):pass
    with pytest.raises(ValueError,match='Already registered'):
        with experiment_registration(tmp_path,b,repeat_permission=EXPERIMENT_ID):pass

def test_registry_lock_and_resume(tmp_path):
    a=tmp_path/'a'
    with experiment_registration(tmp_path,a):
        with pytest.raises(FileExistsError):
            with experiment_registration(tmp_path,tmp_path/'b'):pass
    a.mkdir();atomic(a/'run_state.json',{})
    with experiment_registration(tmp_path,a,resume=True):pass

def test_transport_warning_retry_budget(monkeypatch):
    for k,v in {'LLM_MODEL':'offline','LLM_API_KEY':'fake','LLM_BASE_URL':'https://api.deepseek.com'}.items():monkeypatch.setenv(k,v)
    captured=[]
    async def transport(self,endpoint,headers,payload):
        captured.append(payload)
        return {'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],'usage':{}}
    monkeypatch.setattr(CompatibleProvider,'_complete',transport)
    budget=Budget(15);runner=make_runner(budget,warning=HISTORICAL_WARNING,audit=historical_audit)
    c={'id':'test','turns':[{'id':'t1','role':'client','text':'Что входит в курс?'}], 'facts':[], 'approved_claims':[]}
    async def run_all():
        for _ in range(5):await runner(c,'A');await runner(c,'B')
        await runner(c,'A')
    asyncio.run(run_all())
    assert budget.used==len(captured)==15
    assert all(p['messages'][0]['content'].endswith(HISTORICAL_WARNING) for p in captured)
    for offset in range(0,15,3):
        a=json.loads(captured[offset]['messages'][1]['content']);b=json.loads(captured[offset+2]['messages'][1]['content'])
        assert a['session']['turns']==b['turns']==c['turns']
        assert a['knowledge']['approved_claims']==b['approved_claims']==[]
