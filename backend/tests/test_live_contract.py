import asyncio
import json
import pytest
from backend.models import LiveAnalysis, Analysis
from backend.providers import CompatibleProvider
from backend.engine import process_turn
from backend.tests.test_manager_adaptation import answer


def sample():
    s={'turns':[{'id':'t1','role':'client','text':'Хочу освоить Excel для работы.'}],'facts':[],'recommendations':[]}
    a=answer(s,'Какие рабочие задачи хотите упростить?','QUESTION')
    a.intelligence.sales_plan.need_depth='GENERAL_GOAL'
    return s,a.model_dump()


def test_schema_matches_required_nested_contract():
    s,raw=sample();assert LiveAnalysis.model_validate(raw)
    schema=LiveAnalysis.model_json_schema()
    assert 'intelligence' in schema['required']
    assert 'sales_plan' in schema['$defs']['LiveIntelligence']['required']
    plan=schema['$defs']['LiveSalesPlan']
    assert 'need_depth' in plan['required'] and plan['properties']['need_depth']['type']=='string'
    assert 'priority_thread' in plan['properties']
    assert 'priority_thread' not in schema['$defs']['LiveIntelligence']['properties']
    assert schema['$defs']['LiveIntelligence']['additionalProperties'] is False

@pytest.mark.parametrize('kind',['missing','null','misplaced'])
def test_invalid_fields_have_exact_paths(kind):
    _,raw=sample()
    if kind=='missing':del raw['intelligence']['sales_plan']['need_depth']
    elif kind=='null':raw['intelligence']['sales_plan']['need_depth']=None
    else:
        raw['intelligence']['need_depth']=raw['intelligence']['sales_plan'].pop('need_depth')
        raw['intelligence']['priority_thread']=None
    with pytest.raises(ValueError) as err:LiveAnalysis.model_validate(raw)
    paths=[e['loc'] for e in err.value.errors()]
    assert ('intelligence','sales_plan','need_depth') in paths
    if kind=='misplaced':assert ('intelligence','priority_thread') in paths


def test_provider_sends_same_schema_and_recovery_path(monkeypatch):
    s,raw=sample();monkeypatch.setenv('LLM_MODEL','fixture')
    class Provider(CompatibleProvider):
        calls=0
        async def _complete(self,endpoint,headers,payload):
            self.calls+=1
            request=json.loads(payload['messages'][1]['content'])
            assert request['schema']==LiveAnalysis.model_json_schema()
            body=json.loads(json.dumps(raw))
            if self.calls==1:del body['intelligence']['sales_plan']['need_depth']
            else:
                feedback=request['session']['validation_feedback']
                assert {'path':['intelligence','sales_plan','need_depth'],'type':'missing'} in feedback['fields']
            return {'choices':[{'message':{'content':json.dumps(body)}}]}
    p=Provider();asyncio.run(process_turn(s,p,{}))
    assert p.calls==2 and s['recommendations'][-1]['validation']['status']=='passed'


def test_legacy_contract_still_readable():
    _,raw=sample();del raw['intelligence']['sales_plan']['need_depth']
    assert Analysis.model_validate(raw).intelligence.sales_plan.need_depth is None
