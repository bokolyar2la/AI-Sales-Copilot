from copy import deepcopy
import pytest
from backend.context import apply_analysis
from backend.models import Analysis, Advice, Intelligence, Strategy, HypothesisUpdate, Evidence, Fact, ProductClaim
from backend.storage import Store


def state(text='Просто смотрю программы', role='client'):
    return {'turns':[{'id':'t1','role':role,'text':text}], 'facts':[], 'recommendations':[]}


def output(s, phrase='Что привлекло ваше внимание в этом направлении?', action='QUESTION', strategy='NEED_DEVELOPMENT'):
    return Analysis(facts=[], recommendation=Advice(action_type=action,suggestion=phrase,reason='Цель пока не сформулирована.',evidence_ids=[s['turns'][-1]['id']],target_gap='interest',confidence=.8),
        intelligence=Intelligence(strategy=Strategy(action=strategy, objective='Понять интерес',reason='Клиент пока выбирает',evidence_ids=[s['turns'][-1]['id']])))


def hypothesis(s):
    t=s['turns'][-1]
    return HypothesisUpdate(statement='Возможно, изучает новое направление для себя',evidence=[Evidence(source_turn=t['id'],quote=t['text'])],test_question='Что заинтересовало вас в направлении?')


def test_hypothesis_is_not_confirmed_fact_and_persists(tmp_path):
    s=state(); a=output(s); a.intelligence.hypotheses=[hypothesis(s)]
    apply_analysis(s,a)
    assert not s['facts'] and s['hypotheses'][0]['status']=='INFERRED'
    s.update(id='test', title='test', closed=False, created_at='now', version=1)
    store=Store(tmp_path/'db'); store.save(s)
    assert store.get('test')['hypotheses']==s['hypotheses']
    assert s['recommendations'][-1]['strategy']['action']=='NEED_DEVELOPMENT'


def test_rejection_preserves_history():
    s=state(); a=output(s); a.intelligence.hypotheses=[hypothesis(s)]; apply_analysis(s,a)
    hid=s['hypotheses'][0]['id']
    s['turns'].append({'id':'t2','role':'client','text':'Нет, это нужно для моей работы'})
    a=output(s); h=hypothesis(s); h.id=hid; h.status='REJECTED'; a.intelligence.hypotheses=[h]
    apply_analysis(s,a)
    assert s['hypotheses'][0]['status']=='REJECTED'
    assert s['hypotheses'][0]['history'][0]['status']=='INFERRED'
    assert not s['facts']


def test_confirmation_requires_new_client_fact():
    s=state(); a=output(s); a.intelligence.hypotheses=[hypothesis(s)]; apply_analysis(s,a)
    hid=s['hypotheses'][0]['id']
    s['turns'].append({'id':'t2','role':'client','text':'Да, хочу новое направление для себя'})
    a=output(s); a.facts=[Fact(category='goal',value='Новое направление для себя',quote=s['turns'][-1]['text'],source_turn='t2',confirms_hypothesis=hid)]
    apply_analysis(s,a)
    assert s['hypotheses'][0]['status']=='CONFIRMED'
    assert s['hypotheses'][0]['confirmation_turn']=='t2'


@pytest.mark.parametrize('defect',['manager','quote','id','strategy_source'])
def test_invalid_delta_is_atomic(defect):
    s=state(); a=output(s); h=hypothesis(s); a.intelligence.hypotheses=[h]
    if defect=='manager': s['turns'][0]['role']='manager'
    if defect=='quote': h.evidence[0].quote='Выдуманная цитата'
    if defect=='id': h.id='unknown'
    if defect=='strategy_source': a.intelligence.strategy.evidence_ids=['future']
    before=deepcopy(s)
    with pytest.raises(ValueError): apply_analysis(s,a)
    assert s==before


@pytest.mark.parametrize('phrase,action,strategy,issue',[
    ('Уточните мотивацию клиента','QUESTION','DISCOVERY','instruction_not_spoken_phrase'),
    ('Продавать вам ничего не буду. Что вас интересует?','QUESTION','SALES_RECOVERY','false_no_sale_promise'),
    ('Что хотите? Почему? Когда?','QUESTION','DISCOVERY','phrase_length'),
    ('Курс стоит 99000 рублей.','ANSWER','PRICE_PRESENTATION','unverified_commercial_amount'),
    ('Обучение даёт поддержку куратора.','ANSWER','ANSWER','unverified_product_promise'),
    ('Что привлекло ваше внимание?','QUESTION','CLOSING','strategy_phrase_mismatch')])
def test_validator_blocks_bad_phrases(phrase,action,strategy,issue):
    s=state(); apply_analysis(s,output(s,phrase,action,strategy))
    rec=s['recommendations'][-1]
    assert rec['validation']['status']=='blocked' and issue in rec['validation']['issues']
    assert rec['suggestion']=='' and rec['strategy']['action']=='NO_ACTION'


def test_unapproved_product_claim_blocked():
    s=state(); a=output(s,'Есть поддержка.','ANSWER','ANSWER')
    a.intelligence.product_claims=[ProductClaim(text='Есть поддержка.',source_id='invented')]
    apply_analysis(s,a,{})
    assert 'unsupported_product_claim' in s['recommendations'][-1]['validation']['issues']


def test_exact_spoken_repeat_blocked():
    s=state('Что привлекло ваше внимание?', 'manager')
    s['turns'].append({'id':'t2','role':'client','text':'Люблю рисовать'})
    apply_analysis(s,output(s,'Что привлекло ваше внимание?'))
    assert 'already_spoken_question' in s['recommendations'][-1]['validation']['issues']


def test_changed_goal_keeps_old_and_invalidates_confirmation():
    s=state('Хочу сменить профессию'); a=output(s)
    a.facts=[Fact(category='goal',value='Смена профессии',quote=s['turns'][0]['text'],source_turn='t1')]
    apply_analysis(s,a); old=s['facts'][0]['id']; s['dialogue_state']['needs_confirmed']=True
    s['turns'].append({'id':'t2','role':'client','text':'Нет, хочу управлять своей командой'})
    a=output(s); a.facts=[Fact(category='goal',value='Управлять командой',quote=s['turns'][-1]['text'],source_turn='t2',replaces=[old])]
    apply_analysis(s,a)
    assert [f['status'] for f in s['facts']]==['OUTDATED','CONFIRMED']
    assert not s['dialogue_state']['needs_confirmed']
