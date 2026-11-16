import asyncio
import json
from copy import deepcopy
import pytest
from backend.models import Analysis
from backend.engine import process_turn
from backend.tests.test_manager_adaptation import answer
from experiments.sales_benchmark.run import run,read,HERE,inputs
from experiments.sales_benchmark.live import Budget
from experiments.sales_benchmark.journal import Journal


def cases():return read(HERE/'pilot.json')[:1]

def test_correct_and_misplaced_sales_plan():
    state,_=inputs(cases()[0]);a=answer(state,'Что мешает поиску?','QUESTION')
    a.intelligence.sales_plan.need_depth='GENERAL_GOAL'
    raw=a.model_dump();assert Analysis.model_validate(raw).intelligence.sales_plan.need_depth
    raw['intelligence']['need_depth']=raw['intelligence']['sales_plan'].pop('need_depth')
    with pytest.raises(ValueError) as err:Analysis.model_validate(raw)
    assert err.value.errors()[0]['type']=='extra_forbidden'


def test_schema_optional_but_live_requirement_is_stricter():
    schema=Analysis.model_json_schema()['$defs']['SalesPlan']
    assert 'need_depth' not in schema['required']
    assert schema['properties']['need_depth']['default'] is None


def test_json_feedback_preserves_context():
    state,_=inputs(cases()[0]);original=deepcopy(state['turns'])
    class Model:
        n=0
        async def analyze(self,s,k):
            self.n+=1
            if self.n==1:Analysis.model_validate_json('{')
            assert s['turns']==original
            assert s['validation_feedback']['fields'][0]['type']=='json_invalid'
            a=answer(s,'Какие вакансии вы уже рассматривали?','QUESTION');return a,{}
    asyncio.run(process_turn(state,Model(),{}))
    assert state['recommendations'][-1]['validation']['status']=='passed'


def fake_runner(fail=None):
    async def execute(c,v):
        if fail==v:raise KeyboardInterrupt()
        token=execute.budget.take()
        if execute.budget.journal:execute.budget.journal.response(token,{'content':'fixture'})
        return {'scenario_id':c['id'],'variant':v,'origin':'real','status':'completed','output':{'decision':'fixture','reason':'fixture','say_to_customer':'fixture'},'metrics':{'api_calls':1},'validation':{'status':'schema_only' if v=='B' else 'passed'}}
    execute.budget=Budget(3);return execute


def test_interrupt_and_resume_skips_saved_and_keeps_budget(tmp_path):
    first=fake_runner('B')
    with pytest.raises(KeyboardInterrupt):asyncio.run(run(tmp_path,model='fixture',cases_override=cases(),live_runner=first))
    data=read(tmp_path/'run_state.json')
    assert data['used']==1 and data['status']=='interrupted' and len(data['results'])==1
    assert read(tmp_path/'summary.json')['run_status']=='interrupted'
    second=fake_runner()
    results=asyncio.run(run(tmp_path,model='fixture',cases_override=cases(),live_runner=second,resume=True))
    assert len(results)==2 and second.budget.used==2
    third=fake_runner()
    asyncio.run(run(tmp_path,model='fixture',cases_override=cases(),live_runner=third,resume=True))
    assert third.budget.used==2


def test_uncertain_reserved_call_never_replayed(tmp_path):
    j=Journal(tmp_path,{'code':'fixture'},2);j.start(['income','A']);j.reserve()
    with pytest.raises(ValueError,match='Uncertain'):Journal(tmp_path,{'code':'fixture'},2,True)
    assert read(tmp_path/'run_state.json')['used']==1

@pytest.mark.parametrize('identity,limit',[({'code':'changed'},2),({'code':'fixture'},3)])
def test_resume_checks_code_config_and_limit(tmp_path,identity,limit):
    Journal(tmp_path,{'code':'fixture'},2)
    with pytest.raises(ValueError,match='mismatch'):Journal(tmp_path,identity,limit,True)


def test_legacy_cannot_be_resumed_or_overwritten(tmp_path):
    original=b'[]';(tmp_path/'partial_results.json').write_bytes(original)
    with pytest.raises(ValueError,match='Legacy'):Journal(tmp_path,{},12,True)
    assert (tmp_path/'partial_results.json').read_bytes()==original
