"""Offline commercial-decision contracts, not live-model quality claims."""
import asyncio
import pytest
from backend.engine import process_turn
from backend.context import apply_analysis
from backend.providers import model_context
from backend.models import SelectionGap
from backend.tests.test_manager_adaptation import answer

CAREER='Ну, скорее для поиска работы. Я сейчас администратором работаю, но если честно, хотелось бы что-то поменять. Смотрю вакансии, там часто Excel указывают. Вот и подумал, что неплохо бы его освоить'


def session():return {'turns':[],'facts':[],'recommendations':[]}


def analysis(s,phrase='',action='NO_ACTION',*,depth='GENERAL_GOAL',thread=None,**kw):
    a=answer(s,phrase,action,**kw)
    p=a.intelligence.sales_plan;p.need_depth=depth
    if thread:
        p.priority_thread=SelectionGap(topic=thread,impact='Определяет личную ценность обучения и аргумент предложения')
    p.need_evidence_ids=[t['id'] for t in s['turns'] if t['role']=='client'][-3:]
    return a


def step(s,role,text,build):
    s['turns'].append({'id':'t'+str(len(s['turns'])+1),'role':role,'text':text})
    a=build(s);apply_analysis(s,a,{})
    return s['recommendations'][-1]


def start(s):
    step(s,'manager','Для чего заинтересовал Excel?',lambda s:analysis(s,events=[('question_asked','goal')]))


@pytest.mark.parametrize('phrase',['Что вас сейчас подталкивает к смене работы?','Что прежде всего хотелось бы изменить в работе?'])
def test_career_goal_is_developed_not_deferred(phrase):
    s=session();start(s)
    s['turns'].append({'id':'t2','role':'client','text':CAREER})
    class Model:
        calls=0
        async def analyze(self,state,knowledge):
            self.calls+=1
            if self.calls==1:
                return analysis(state,'Уточню подходящие программы и вернусь с условиями.','ANSWER',strategy='FOLLOW_UP',thread='career_motive',facts=[('goal','Смена работы',[])]),{}
            assert 'develop_need_before_offer' in state['validation_feedback']['checks']
            return analysis(state,phrase,'QUESTION',strategy='EXPLORE_MOTIVATION',topic='career_motive',thread='career_motive',facts=[('goal','Смена работы',[])]),{}
    model=Model();asyncio.run(process_turn(s,model,{}))
    rec=s['recommendations'][-1]
    assert model.calls==2 and rec['strategy']['action']=='EXPLORE_MOTIVATION'
    assert rec['sales_plan']['readiness']=='NEEDS_CLARIFICATION'
    assert rec['sales_plan']['question']['decision_impact']
    assert {f['category'] for f in s['facts']}=={'goal'}  # No invented salary/fear/problem.


def test_known_problem_and_result_do_not_require_motivation_again():
    s=session();start(s)
    step(s,'client','На расчёт заказов трачу два часа, хочу считать формулами без ручных ошибок.',lambda s:analysis(s,'Какой отчёт считаете?','QUESTION',depth='MOTIVE_OR_CHANGE',facts=[('problem','Ручные ошибки',[]),('goal','Считать формулами',[])]))
    step(s,'manager','Почему важно убрать ручной расчёт?',lambda s:analysis(s,events=[('question_asked','motivation')]))
    rec=step(s,'client','Чтобы не задерживаться после работы, хочу заканчивать вовремя.',lambda s:analysis(s,'Для вашей задачи важны формулы и проверка расчётов.','PRESENT',strategy='SELECT_SOLUTION',depth='MOTIVE_OR_CHANGE',events=[('question_answered','motivation')],facts=[('motivation','Заканчивать работу вовремя',[])]))
    assert rec['action_type']=='PRESENT' and rec['validation']['status']=='passed'
    assert rec['sales_plan']['question'] is None
    a=analysis(s,'Почему хотите заканчивать вовремя?','QUESTION',topic='motivation',depth='MOTIVE_OR_CHANGE')
    apply_analysis(s,a,{})
    assert 'answered_topic_repeat' in s['recommendations'][-1]['validation']['issues']


def test_new_strong_motive_changes_priority_not_old_level_question():
    s=session();start(s)
    step(s,'client','Мне нужны простые отчёты.',lambda s:analysis(s,'Какие формулы знаете?','QUESTION',depth='MOTIVE_OR_CHANGE',topic='experience',facts=[('goal','Простые отчёты',[])]))
    step(s,'manager','Что изменилось в работе сейчас?',lambda s:analysis(s,events=[('question_asked','trigger')]))
    rec=step(s,'client','Мне предложили руководить отделом, хочу подготовиться к этой роли.',lambda s:analysis(s,'Какие новые задачи руководителя вызывают больше всего вопросов?','QUESTION',depth='GENERAL_GOAL',thread='new_role',topic='new_role',strategy='NEED_DEVELOPMENT',facts=[('motivation','Подготовиться к роли руководителя',[])]))
    assert rec['strategy']['action']=='NEED_DEVELOPMENT'
    assert rec['sales_plan']['priority_thread']['topic']=='new_role'
    assert rec['sales_plan']['question']['topic']!='experience'
    assert s['facts'][-1]['source_turn']==s['turns'][-1]['id']
    assert model_context(s)['turns'][-2]['text']=='Что изменилось в работе сейчас?'


@pytest.mark.parametrize('client_request',['Покажите программу','Сколько стоит обучение?','Больше без вопросов, расскажите курс'])
def test_explicit_request_takes_priority_over_depth(client_request):
    s=session();start(s)
    step(s,'client',CAREER,lambda s:analysis(s,'Что хочется изменить?','QUESTION',thread='career_motive',strategy='EXPLORE_MOTIVATION'))
    step(s,'manager','Готов обсудить ваше направление.',lambda s:analysis(s))
    rec=step(s,'client',client_request,lambda s:analysis(s,'Проверю содержание и полную стоимость подходящего варианта и передам условия.','ANSWER',strategy='FOLLOW_UP',thread='career_motive'))
    assert rec['validation']['status']=='passed'
    assert rec['sales_plan']['question'] is None


def test_detailed_need_and_program_request_allow_offer():
    s=session();start(s)
    step(s,'client','Из-за сменного графика не вижу семью. Ищу офисную работу с предсказуемым временем и хочу освоить отчёты для неё.',lambda s:analysis(s,'Какие отчёты встречаются в вакансиях?','QUESTION',depth='MOTIVE_OR_CHANGE',facts=[('motivation','Предсказуемый график ради семьи',[])]))
    step(s,'manager','Подберём обучение под отчётность.',lambda s:analysis(s))
    rec=step(s,'client','Причины обсудили, покажите программу.',lambda s:analysis(s,'Для вашей цели важно освоить рабочие отчёты; конкретное содержание проверю перед предложением.','PRESENT',strategy='SELECT_SOLUTION',depth='MOTIVE_OR_CHANGE'))
    assert rec['action_type']=='PRESENT' and rec['validation']['status']=='passed'


def test_early_price_is_not_blocked_by_missing_motive():
    s=session();start(s)
    rec=step(s,'client','Хочу сменить работу, но сначала скажите цену.',lambda s:analysis(s,'Полную стоимость нужно проверить для выбранного варианта.','ANSWER',strategy='PRICE_PRESENTATION',thread='career_motive'))
    assert rec['validation']['status']=='passed'
    assert rec['strategy']['action']=='PRICE_PRESENTATION'


def test_empty_catalog_is_not_reason_to_end_discovery():
    s=session();start(s)
    rec=step(s,'client',CAREER,lambda s:analysis(s,'Проверю варианты обучения и вернусь позже.','NEXT_STEP',strategy='FOLLOW_UP',thread='career_motive'))
    assert 'develop_need_before_offer' in rec['validation']['issues']
    a=analysis(s,'Что для вас должно измениться после перехода на другую работу?','QUESTION',strategy='EXPLORE_MOTIVATION',topic='career_motive',thread='career_motive')
    apply_analysis(s,a,{})
    assert s['recommendations'][-1]['validation']['status']=='passed'
    assert s['recommendations'][-1]['manager_note']  # Unknown product data stays separate.


def test_managers_own_question_can_already_reveal_motivation():
    s=session();start(s)
    step(s,'client',CAREER,lambda s:analysis(s,'Какой сейчас уровень Excel?','QUESTION',topic='experience'))
    step(s,'manager','Что заставило искать новую работу именно сейчас?',lambda s:analysis(s,events=[('question_asked','trigger')]))
    rec=step(s,'client','Отдел закрывают. Ищу офисную работу и хочу уверенно делать требуемые в ней отчёты.',lambda s:analysis(s,'Какие задачи с таблицами чаще встречаются в выбранных вакансиях?','QUESTION',depth='MOTIVE_OR_CHANGE',topic='vacancy_tasks',events=[('question_answered','trigger')],facts=[('motivation','Закрытие отдела побудило искать работу',[])]))
    assert rec['sales_plan']['need_depth']=='MOTIVE_OR_CHANGE'
    assert rec['sales_plan']['question']['topic']=='vacancy_tasks'
    assert [q['topic'] for q in s['dialogue_state']['questions']]==['goal','trigger']
    assert rec['validation']['status']=='passed'


def test_useful_question_not_blocked_by_fixed_count():
    s=session()
    for i in range(5):
        step(s,'manager','Уточнение '+str(i)+'?',lambda s:analysis(s))
        step(s,'client','Продолжим обсуждение',lambda s:analysis(s))
    rec=step(s,'client',CAREER,lambda s:analysis(s,'Что хочется изменить в работе?','QUESTION',strategy='EXPLORE_MOTIVATION',thread='career_motive'))
    assert rec['validation']['status']=='passed'


def test_ready_buyer_does_not_need_artificial_motivation_interview():
    s=session();start(s)
    rec=step(s,'client','Я уже выбрал программу, готов оформить.',lambda s:analysis(s,'Куда отправить проверенные условия оформления?','NEXT_STEP',strategy='CLOSING',thread='career_motive',events=[('ready_to_buy','')]))
    assert rec['action_type']=='NEXT_STEP' and rec['validation']['status']=='passed'
