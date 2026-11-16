import asyncio
import pytest
from backend.context import apply_analysis
from backend.engine import process_turn
from backend.models import ProductClaim
from backend.tests.test_need_depth import session, step, analysis

CLAIM='Темы программы организованы последовательно.'
KB={'approved_claims':[{'id':'synthetic','text':CLAIM,'approved':True}]}

def history():
    s=session()
    step(s,'manager','Что хотите изменить?',lambda s:analysis(s))
    step(s,'client','Хочу сменить работу.',lambda s:analysis(s,'Что мешало учиться?','QUESTION',facts=[('goal','Сменить работу',[])]))
    step(s,'manager','Что мешало учиться самостоятельно?',lambda s:analysis(s,events=[('question_asked','past_attempts')]))
    step(s,'client','Смотрел видео, но бросал. После работы устаю, не знаю порядок тем.',lambda s:analysis(s,'Что было сложнее?','QUESTION',facts=[('problem','Усталость и непонятная последовательность',[]),('experience','Бросал видео',[])],events=[('question_answered','past_attempts')]))
    return s

def value(s,claims=True):
    a=analysis(s,CLAIM,'ANSWER',strategy='VALUE_BUILDING',depth='MOTIVE_OR_CHANGE',thread='load')
    a.intelligence.sales_plan.need_evidence_ids=['t2','t4']
    a.intelligence.sales_plan.readiness='NEEDS_CLARIFICATION'
    if claims:a.intelligence.product_claims=[ProductClaim(text=CLAIM,source_id='synthetic')]
    return a


def test_short_supported_value_with_open_question():
    s=history();apply_analysis(s,value(s),KB)
    assert s['recommendations'][-1]['validation']['status']=='passed'
    assert s['dialogue_state']['questions'][-1]['status']=='answered'

@pytest.mark.parametrize('missing',['product','need','full_offer'])
def test_no_bypass_for_unknown_product_or_premature_offer(missing):
    s=history();a=value(s)
    if missing=='need':s['facts']=[]
    if missing=='full_offer':a.recommendation.action_type='PRESENT';a.intelligence.strategy.action='PRESENTATION'
    apply_analysis(s,a,{} if missing=='product' else KB)
    assert s['recommendations'][-1]['validation']['status']=='blocked'


def test_useful_discovery_still_allowed():
    s=history();apply_analysis(s,analysis(s,'Сколько времени можете выделить на обучение?','QUESTION',thread='time'),{})
    assert s['recommendations'][-1]['validation']['status']=='passed'

@pytest.mark.parametrize('corrected',[True,False])
def test_bounded_retry_or_local_recovery(corrected):
    s=history()
    class Model:
        calls=0
        async def analyze(self,state,k):
            self.calls+=1
            if self.calls==2:
                assert state['validation_feedback']['need_decision']['priority_thread']['topic']=='load'
                if corrected:return analysis(state,'Какую нагрузку готовы рассмотреть?','QUESTION'),{}
            return value(state,False),{}
    m=Model();asyncio.run(process_turn(s,m,{}))
    rec=s['recommendations'][-1]
    assert m.calls==2 and rec['validation']['status']=='passed'
    assert rec['action_type']=='QUESTION'
    assert ('local_recovery' in [t['attempt'] for t in rec['validation_trace']]) is (not corrected)
    assert CLAIM not in rec['suggestion']


def test_no_safe_recovery_gives_diagnostic():
    s=history()
    step(s,'manager','Сколько времени можете учиться?',lambda s:analysis(s,events=[('question_asked','study_time')]))
    step(s,'client','Два часа в неделю.',lambda s:analysis(s,'Какие задачи важнее?','QUESTION',facts=[('constraints','Два часа в неделю',[])],events=[('question_answered','study_time')]))
    class Model:
        async def analyze(self,state,k):return value(state,False),{}
    asyncio.run(process_turn(s,Model(),{}))
    rec=s['recommendations'][-1]
    assert rec['validation']['status']=='blocked'
    assert 'Безопасное восстановление недоступно' in rec['reason']
