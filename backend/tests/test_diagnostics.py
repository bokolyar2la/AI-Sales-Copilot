import asyncio
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.diagnostics import capture, snapshot
from backend.providers import CompatibleProvider, DemoProvider
from backend.main import create_app


RESULT={'facts':[], 'recommendation':{'action_type':'NO_ACTION','suggestion':'','reason':'Ждём ответа','evidence_ids':[],'target_gap':'','confidence':.8}}


@pytest.mark.parametrize('stream',[False,True])
def test_provider_timings_and_unchanged_thinking(monkeypatch,stream):
    monkeypatch.setenv('LLM_MODEL','test-model')
    monkeypatch.setenv('LLM_BASE_URL','https://api.deepseek.com')
    content=json.dumps(RESULT)
    def handler(request):
        body=json.loads(request.content)
        assert body['thinking']=={'type':'disabled'}
        assert bool(body.get('stream'))==stream
        if not stream:
            return httpx.Response(200,json={'choices':[{'message':{'content':content},'finish_reason':'stop'}],'usage':{'total_tokens':42}})
        chunks=[{'choices':[{'delta':{'role':'assistant','content':''}}]},
                {'choices':[{'delta':{'content':content[:20]}}]},
                {'choices':[{'delta':{'content':content[20:]}}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'total_tokens':42}}]
        return httpx.Response(200,text=''.join('data: '+json.dumps(x)+'\n\n' for x in chunks)+'data: [DONE]\n\n')
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with capture() as trace:
        a,usage=asyncio.run(CompatibleProvider(diagnostic_stream=stream).analyze({'turns':[]},{}))
    d=snapshot(trace);call=d['calls'][0]
    assert a.recommendation.reason=='Ждём ответа' and usage['total_tokens']==42
    assert d['request_count']==1 and d['retry_count']==0
    assert call['request_ms']>=0 and d['stages_ms']['schema_validation']>=0
    assert (call['ttft_ms'] is not None)==stream
    if stream:assert call['request_ms']>=call['first_content_ms']>=0 and call['content_generation_ms']>=0


def test_truncated_stream_is_not_success(monkeypatch):
    monkeypatch.setenv('LLM_MODEL','test')
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda _:httpx.Response(200,text='data: {"choices":[]}\n\n'))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    with capture() as trace,pytest.raises(ValueError):
        asyncio.run(CompatibleProvider(diagnostic_stream=True).analyze({'turns':[]},{}))
    assert trace['calls'][0]['status']=='error'
    assert trace['calls'][0]['error_type']=='ValueError'


def test_trace_does_not_leak_between_requests():
    with capture() as first:
        first['calls'].append({'request_ms':10})
    with capture() as second:
        assert not second['calls'] and first['trace_id']!=second['trace_id']


def test_http_includes_saving_and_total_timings_without_dialogue(tmp_path):
    with TestClient(create_app(tmp_path/'timing.sqlite',DemoProvider())) as client:
        s=client.post('/api/sessions',json={'title':'test'}).json()
        response=client.post('/api/sessions/'+s['id']+'/turns',json={'role':'client','text':'Хочу Excel','request_id':'diag-test','expected_version':0})
        assert response.status_code==200
        raw=response.headers['X-Copilot-Diagnostics'];d=json.loads(raw)
        assert 'Хочу' not in raw and 'Authorization' not in raw
        assert d['stages_ms']['storage_save']>0
        assert d['stages_ms']['initial_save']>0 and d['stages_ms']['result_save']>0
        assert d['stages_ms']['server_response_ready']>=d['stages_ms']['engine']
        assert d['stages_ms']['validation']>=0
        saved=client.get('/api/sessions/'+s['id']).json()
        assert saved==response.json()


def test_retries_are_counted_even_if_first_response_invalid(monkeypatch):
    from backend.engine import process_turn
    from backend.diagnostics import add_call
    from backend.models import Analysis
    class Retry:
        calls=0
        async def analyze(self,session,knowledge):
            self.calls+=1
            add_call({'request_ms':10,'status':'ok'})
            if self.calls==1:raise ValueError('invalid schema')
            return Analysis.model_validate(RESULT),{}
    s={'turns':[{'id':'t1','role':'manager','text':'Вам удобно?'}],'facts':[],'recommendations':[]}
    with capture() as trace:
        asyncio.run(process_turn(s,Retry(),{}))
    result=snapshot(trace)
    assert result['request_count']==2 and result['retry_count']==1
    assert result['retry_request_ms']==10


def test_diagnostic_origin_cannot_be_remote(tmp_path):
    with pytest.raises(ValueError):
        create_app(tmp_path/'x.sqlite',DemoProvider(),diagnostic_origin='http://127.0.0.1:80@remote.example')


def test_synthetic_sales_map_references_and_nonproduction_status():
    from pathlib import Path
    d=json.loads((Path(__file__).resolve().parents[2]/'knowledge-design/excel.sales-map.synthetic.json').read_text(encoding='utf-8'))
    assert d['metadata']['classification']=='SYNTHETIC_ONLY'
    assert d['metadata']['approved_for_production'] is False
    for section in ('audiences','professional_goals','potential_needs','diagnostic_questions','capabilities'):
        assert len({r['id'] for r in d[section]})==len(d[section])
    needs={x['id'] for x in d['potential_needs']};caps={x['id'] for x in d['capabilities']}
    questions={x['id'] for x in d['diagnostic_questions']}
    goals={x['id'] for x in d['professional_goals']}
    assert all(set(x['goal_ids'])<=goals for x in d['audiences'])
    assert all(set(x['need_ids'])<=needs for x in d['professional_goals'])
    assert all(set(x['question_ids'])<=questions for x in d['potential_needs'])
    assert all(set(x['tests_need_ids'])<=needs for x in d['diagnostic_questions'])
    assert all(x['need_id'] in needs and set(x['capability_ids'])<=caps for x in d['need_capability_links'])
    assert all(x['price'] is None and set(x['capability_ids'])<=caps for x in d['tariff_selection']['candidates'])
