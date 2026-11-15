import asyncio
from copy import deepcopy
import pytest
from backend.context import apply_analysis
from backend.engine import process_turn
from backend.tests.test_need_depth import analysis, step, session

FEAR='Боюсь потратить деньги на обучение и остаться на прежней работе.'
EXIT='Как удобнее связаться, чтобы передать условия?'

def response(s,phrase=EXIT,action='NEXT_STEP',strategy='FOLLOW_UP',**kw):
    return analysis(s,phrase,action,strategy=strategy,depth='MOTIVE_OR_CHANGE',**kw)

def history():
    s=session()
    for question,text,category in [
        ('Зачем вам Excel?','Хочу сменить работу, в вакансиях нужен Excel.','goal'),
        ('Что хотите изменить?','Работаю администратором, зарплата не растёт, перспектив нет.','motivation'),
        ('Что вызывает сомнения?',FEAR,'objection')]:
        step(s,'manager',question,lambda s:analysis(s))
        step(s,'client',text,lambda s:response(s,'Что вы уже пробовали?','QUESTION','OBJECTION_DIAGNOSIS',
            topic='attempts',facts=[(category,text,[])],events=[('objection_opened','result_risk')] if category=='objection' else []))
    return s

@pytest.mark.parametrize('action,strategy',[('NEXT_STEP','FOLLOW_UP'),('ANSWER','ANSWER'),('QUESTION','DISCOVERY'),('PRESENT','VALUE_BUILDING')])
def test_null_thread_does_not_allow_exit_under_another_label(action,strategy):
    s=history();a=response(s,action=action,strategy=strategy)
    assert a.intelligence.sales_plan.priority_thread is None
    apply_analysis(s,a,{})
    assert 'unresolved_objection_before_exit' in s['recommendations'][-1]['validation']['issues']

@pytest.mark.parametrize('text,events',[
    ('Сколько стоит обучение?',[]),
    ('Пришлите условия программы.',[]),
    ('Да, готов оформить.', [('ready_to_buy','')]),
    ('Давайте завтра, сейчас неудобно.', [('unavailable','')]),
    ('Не хочу дальше обсуждать.', [('questions_declined','')])])
def test_client_requests_and_agreements_take_priority(text,events):
    s=history()
    step(s,'manager','Навык можно сопоставить с требованиями вакансий; работу обещать нельзя.',lambda s:analysis(s))
    rec=step(s,'client',text,lambda s:response(s,events=events))
    assert rec['validation']['status']=='passed'


def test_manager_own_argument_and_new_need_remain_source_of_truth():
    s=history()
    step(s,'manager','Для вас важнее новая должность или возможность выполнять отчёты самостоятельно?',lambda s:analysis(s,events=[('question_asked','independence')]))
    rec=step(s,'client','Сейчас важнее делать отчёты самому, чтобы не зависеть от коллег.',lambda s:response(s,
        'Какие отчёты приходится просить у коллег?','QUESTION','NEED_DEVELOPMENT',topic='reports',
        facts=[('goal','Самостоятельно делать отчёты',[])],events=[('question_answered','independence')]))
    assert rec['validation']['status']=='passed'
    assert s['facts'][-1]['source_turn']==s['turns'][-1]['id']
    assert any(q['topic']=='independence' and q['status']=='answered' for q in s['dialogue_state']['questions'])


def test_relevant_value_allowed_without_claiming_objection_resolved():
    s=history();apply_analysis(s,response(s,'Можно сопоставить требования вакансий с навыками Excel; обучение само по себе не гарантирует новую работу.','ANSWER','OBJECTION_HANDLING'),{})
    assert s['recommendations'][-1]['validation']['status']=='passed'
    assert s['dialogue_state']['objections'][0]['status']=='open'


def test_retry_gets_concrete_concern_and_corrects_decision():
    s=history()
    class Model:
        calls=0
        async def analyze(self,state,knowledge):
            self.calls+=1
            if self.calls==1:return response(state),{}
            detail=state['validation_feedback']['unanswered_objections'][0]
            assert detail['quote']==FEAR and detail['source_turn']==s['turns'][-1]['id']
            assert detail['reason']
            return response(state,'Вы уже пробовали откликаться на вакансии или пока изучаете требования?','QUESTION','OBJECTION_DIAGNOSIS',topic='applications'),{}
    m=Model();asyncio.run(process_turn(s,m,{}))
    assert m.calls==2 and s['recommendations'][-1]['validation']['status']=='passed'


def test_latest_fact_alone_also_blocks_exit():
    s=history();s['dialogue_state']['objections']=[]
    apply_analysis(s,response(s),{})
    assert 'unresolved_objection_before_exit' in s['recommendations'][-1]['validation']['issues']
