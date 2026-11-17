from copy import deepcopy
import hashlib
import pytest
from experiments.sales_turning_points.corpus import validate, benchmark_case, paired_inputs

@pytest.fixture
def sample():
    turns=[]
    for i,(role,text) in enumerate([('manager','Добрый день'),('client','Хочу освоить отчёты'),('manager','Какие отчёты?'),('client','Еженедельные')],1):
        turns.append(dict(id=f't{i}',role=role,text=text,page=1,time=f'00:00:0{i}',
            source=dict(document_id='d',pdf_sha256='a'*64,page=1,timestamp=f'00:00:0{i}',raw_turn_sha256=hashlib.sha256(text.encode()).hexdigest())))
    c=dict(id='test',source_document='d',checkpoint='t2',history=deepcopy(turns[:2]),approved_claims=[],privacy_status='review_required',
        annotation=dict(confirmed_goal=dict(evidence_ids=['t2']),facts=[dict(category='goal',value=turns[1]['text'],quote=turns[1]['text'],source_turn='t2',status='CONFIRMED')],
            active_theme='discovery',allowed_action_families=['DISCOVERY'],inappropriate_actions=['Ignore client'],expert_score=None,status='draft_requires_expert_review'),
        observed_continuation=dict(intervening_turns=deepcopy(turns[2:]),manager_turns=[deepcopy(turns[2])],client_reaction_turns=[deepcopy(turns[3])]))
    return [c],[dict(id='d',pdf_sha256='a'*64,turns=turns)]

def test_valid_sources(sample):
    assert len(validate(*sample)) == 1

@pytest.mark.parametrize('defect',['future','hash','fact','reaction','claim','score','duplicate'])
def test_invalid_corpus(sample,defect):
    cases,docs=sample;c=cases[0]
    if defect=='future':c['history'].append(docs[0]['turns'][2])
    if defect=='hash':docs[0]['turns'][0]['source']['pdf_sha256']='b'*64
    if defect=='fact':c['annotation']['facts'][0]['source_turn']='t3'
    if defect=='reaction':c['observed_continuation']['client_reaction_turns'][0]['text']='Invented'
    if defect=='claim':c['approved_claims']=[{'approved':True,'text':'Historical promise'}]
    if defect=='score':c['annotation']['expert_score']=5
    if defect=='duplicate':cases.append(deepcopy(c))
    with pytest.raises(ValueError):validate(cases,docs)

def test_no_future_or_labels_in_model_inputs(sample):
    bundle=paired_inputs(sample[0][0])
    assert bundle['A']['turns']==bundle['B']['turns']==bundle['C']['turns']
    assert all(len(v['turns'])==2 for v in bundle.values())
    assert all(not {'observed_continuation','allowed_actions','annotation'} & v.keys() for v in bundle.values())
    assert bundle['B']['approved_claims']==[]
    case=benchmark_case(sample[0][0])
    assert case['origin']=='historical' and case['benchmark_eligible'] is False

def test_unknown_role_not_invented(sample):
    sample[0][0]['history'][0]['role']='unknown'
    with pytest.raises(ValueError,match='speaker'):benchmark_case(sample[0][0])

def test_future_goal_rejected(sample):
    sample[0][0]['annotation']['confirmed_goal']['evidence_ids']=['t4']
    with pytest.raises(ValueError,match='Future'):validate(*sample)
