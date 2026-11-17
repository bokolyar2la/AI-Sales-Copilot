import csv
from copy import deepcopy
import pytest
from experiments.sales_turning_points import p23
from experiments.sales_benchmark.run import inputs
from experiments.sales_benchmark.journal import atomic

pytestmark = pytest.mark.skipif(not (p23.PRIVATE/"expert_review_phase2.csv").exists(), reason="Private expert corpus not distributed with repository")

def test_import_and_no_future_or_expert_leak():
    original,rows,cases=p23.load_cases()
    assert len(rows)==len(cases)==20
    for c in cases:
        source=next(x for x in original if x['id']==c['id'])
        assert c['turns']==p23.private_overlay({'id':c['id'],'turns':[{k:t[k] for k in ('id','role','text')} for t in source['history']]})['turns']
        assert c['checkpoint']==c['turns'][-1]['id'] and c['approved_claims']==[]
        assert not c['allowed_actions'] and not c['undesirable_actions']
        a,b=inputs(c)
        assert a['turns']==b['turns'] and a['facts']==b['facts']
        assert not {'annotation','expert_rows','observed_continuation','expert_score'} & (a.keys()|b.keys())

def test_pilot_diversity():
    original,_,_=p23.load_cases()
    pilot=[c for c in original if c['id'] in p23.PILOT]
    assert len({c['source_document'] for c in pilot})==5

def test_blind_empty_not_fabricated(tmp_path):
    _,_,cases=p23.load_cases();cases=cases[:1]
    p23.blind(tmp_path,cases,[])
    text=(tmp_path/'reviewer/anonymous_answers.md').read_text(encoding='utf-8')
    assert '### X' in text and '### Y' in text and 'Ответ отсутствует' in text
    assert 'schema_only' not in text
    with (tmp_path/'reviewer/assessment_template.csv').open(encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f,delimiter=';'))
    assert len(rows)==2 and all(not r['commercial_appropriateness'] for r in rows)

def test_unblind_requires_fixed_reviews(tmp_path):
    _,_,cases=p23.load_cases();cases=cases[:1]
    atomic(tmp_path/'results.json',[])
    p23.blind(tmp_path,cases,[])
    with pytest.raises((ValueError,StopIteration)):
        p23.reveal(tmp_path,tmp_path/'reviewer/assessment_template.csv')
