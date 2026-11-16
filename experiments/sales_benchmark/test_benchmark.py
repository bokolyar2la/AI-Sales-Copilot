import asyncio
import csv
from copy import deepcopy
import pytest
from experiments.sales_benchmark.run import HERE,read,validate,inputs,run,summarize,current_replay,digest,write
from backend.tests.test_manager_adaptation import answer


def test_fifteen_source_valid_scenarios():
    cases=read(HERE/'scenarios.json')
    assert len(cases)==15 and len({c['id'] for c in cases})==15
    for c in cases:validate(c)
    assert any(c['school']=='Eduson' and c['approved_claims'] for c in cases)
    assert any(c['school']=='Skillbox' and not c['approved_claims'] for c in cases)


def test_bad_fact_rejected_and_no_solution_leak():
    c=read(HERE/'scenarios.json')[0];a,b=inputs(c)
    assert a['turns']==b['turns'] and a['facts']==b['facts']
    assert not {'recommendations','sales_plan','allowed_actions'} & b.keys()
    c['facts'][0]['source_turn']='t2'
    with pytest.raises(ValueError):validate(c)


def test_missing_outputs_and_blind_export(tmp_path):
    results=asyncio.run(run(tmp_path))
    assert len(results)==30 and all(r['status']=='missing' and r['metrics'] is None for r in results)
    rows=list(csv.DictReader((tmp_path/'blind_review.csv').open(encoding='utf-8-sig')))
    assert len(rows)==30 and 'variant' not in rows[0]
    assert read(tmp_path/'summary.json')['expert_scores'] is None
    assert summarize(tmp_path,tmp_path/'blind_review.csv')['means']=={'A':None,'B':None}


def test_mock_is_not_real_quality(tmp_path):
    results=asyncio.run(run(tmp_path,mode='mock'))
    assert all(r['origin']=='mock' and r['metrics'] is None for r in results)
    assert read(tmp_path/'summary.json')['real_completed']==0


def test_current_replay_runs_production_validator():
    c=read(HERE/'scenarios.json')[0];s,_=inputs(c)
    a=answer(s,'Какие рабочие задачи хотелось бы упростить?','QUESTION')
    a.intelligence.sales_plan.need_depth='GENERAL_GOAL'
    output,validation,trace=asyncio.run(current_replay(c,[a.model_dump()]))
    assert output['say_to_customer'] and validation['status']=='passed'
    bad=a.model_copy(deep=True);bad.intelligence.strategy.action='CLOSING'
    output,validation,trace=asyncio.run(current_replay(c,[bad.model_dump(),a.model_dump()]))
    assert len(trace)==2 and validation['status']=='passed'


def test_saved_minimal_and_measurements(tmp_path):
    c=read(HERE/'scenarios.json')[0]
    saved=tmp_path/'saved.json'
    write(saved,[{'scenario_id':c['id'],'scenario_hash':digest(c),'variant':'B','origin':'real','model':'test-model',
        'generation':{'temperature':.2,'max_tokens':3000,'thinking':'disabled'},'source_id':'fixture-not-live',
        'output':{'decision':'DISCOVERY','reason':'test','say_to_customer':'Что заинтересовало?'},'metrics':{'latency_ms':42}}])
    result=asyncio.run(run(tmp_path/'out',saved=saved,model='test-model'))
    assert result[1]['metrics']=={'latency_ms':42} and result[0]['status']=='missing'
    # Test data uses real label solely to exercise importer; no API was called.


def test_blind_scores_are_paired_and_missing_not_zero(tmp_path):
    from experiments.sales_benchmark.run import CRITERIA
    asyncio.run(run(tmp_path))
    mapping=read(tmp_path/'private_mapping.json')
    rows=list(csv.DictReader((tmp_path/'blind_review.csv').open(encoding='utf-8-sig')))
    for row in rows[:2]:
        mapping[row['response_id']].update(origin='real',status='completed')
        row.update({k:'3' for k in CRITERIA})
    rows[0]['critical_violations']='Тестовая отметка'
    write(tmp_path/'private_mapping.json',mapping)
    review=tmp_path/'rated.csv'
    with review.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    result=summarize(tmp_path,review)
    assert result['paired_cases']==1 and result['reviewed']=={'A':1,'B':1}
    assert result['paired_difference_A_minus_B']['context']==0
    assert len(result['critical_violations'])==1


def test_cannot_score_missing_response(tmp_path):
    from experiments.sales_benchmark.run import CRITERIA
    asyncio.run(run(tmp_path))
    path=tmp_path/'blind_review.csv'
    rows=list(csv.DictReader(path.open(encoding='utf-8-sig')))
    rows[0].update({k:'0' for k in CRITERIA})
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    with pytest.raises(ValueError):summarize(tmp_path,path)
