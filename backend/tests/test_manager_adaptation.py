"""Offline multi-turn contract tests, not an evaluation of live DeepSeek quality."""
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.models import Analysis
from backend.providers import model_context
from backend.dialogue_policy import conversation_signals


def answer(s, phrase='', action='NO_ACTION', *, events=(), facts=(), topic='task', strategy=None):
    turn=s['turns'][-1]
    strategy=strategy or {'NO_ACTION':'NO_ACTION','QUESTION':'DISCOVERY','ANSWER':'ANSWER','NEXT_STEP':'FOLLOW_UP'}[action]
    diagnostic=action=='QUESTION' or strategy=='ALIGN_BEFORE_PRESENTATION'
    return Analysis.model_validate({'facts':[{'category':c,'value':v,'quote':turn['text'],'source_turn':turn['id'],'replaces':ids} for c,v,ids in facts],
      'events':[{'kind':k,'topic':t,'quote':turn['text'],'source_turn':turn['id']} for k,t in events],
      'recommendation':{'action_type':action,'suggestion':phrase,'reason':'Следующий шаг по фактическому диалогу','evidence_ids':[turn['id']],'target_gap':topic,'confidence':.8},
      'intelligence':{'strategy':{'action':strategy,'objective':'Продолжить подбор по текущей задаче','reason':'Учесть последний ответ клиента','evidence_ids':[turn['id']]},
       'sales_plan':{'commercial_goal':'Подобрать подходящее обучение','next_goal':topic,'significant_needs':[],'need_evidence_ids':[],
        'critical_gaps':[{'topic':topic,'impact':'Определяет аргумент и подбор'}] if diagnostic else [],'useful_gaps':[],
        'readiness':'NEEDS_CLARIFICATION' if diagnostic else 'READY_FOR_OFFER',
        'question':{'topic':topic,'new_information':'Текущая задача','decision_impact':'Выбор подходящей программы','already_answered':False,'changes_selection':True,'changes_argument':False,'essential_now':True} if diagnostic else None,
        'after_answer_action':'PRESENTATION','after_answer_goal':'Предложить решение под подтверждённую задачу'}}})


class Script:
    name='offline-script'
    def __init__(self,fn):self.fn=fn;self.requests=[]
    async def analyze(self,s,k):
        self.requests.append(deepcopy(model_context(s)))
        return self.fn(s),{}


def conversation(tmp_path,fn):
    provider=Script(fn);client=TestClient(create_app(tmp_path/'test.sqlite',provider))
    s=client.post('/api/sessions',json={'title':'Синтетический диалог'}).json()
    return client,provider,s


def say(c,s,role,text):
    r=c.post('/api/sessions/'+s['id']+'/turns',json={'role':role,'text':text,'request_id':str(uuid4()),'expected_version':s['version']})
    assert r.status_code==200
    result=r.json();assert result['analysis_error'] is None
    return result


def test_manager_ignores_question_and_asks_own(tmp_path):
    unused='Последнее уточнение перед предложением: какой у вас уровень Excel?'
    def respond(s):
        text=s['turns'][-1]['text']
        if text=='Хочу Excel':return answer(s,unused,'ANSWER',strategy='ALIGN_BEFORE_PRESENTATION',topic='experience')
        if s['turns'][-1]['role']=='manager':
            a=answer(s,events=[('question_asked','motivation')])
            if not s.get('validation_feedback'):a.events[0].quote=unused
            return a
        return answer(s,'Какие отчёты больше всего мешают закончить работу вовремя?','QUESTION',topic='task',events=[('question_answered','motivation')],facts=[('motivation','Освободить вечера для семьи',[])])
    c,p,s=conversation(tmp_path,respond)
    s=say(c,s,'client','Хочу Excel')
    s=say(c,s,'manager','Почему вам важно освоить Excel?')
    s=say(c,s,'client','Хочу освободить вечера для семьи')
    assert all(t['text']!=unused for t in s['turns'])
    assert [q['topic'] for q in s['dialogue_state']['questions']]==['motivation']
    assert not conversation_signals(s)['offer_due_after_bridge']
    assert s['facts'][-1]['source_turn']==s['turns'][-1]['id']
    original=p.requests[-1]['recommendations'][0]
    assert original['spoken_turn_ids']==[]
    assert s['recommendations'][-1]['suggestion']!=unused


def test_manager_presents_without_using_recommendation(tmp_path):
    def respond(s):
        if len(s['turns'])==1:return answer(s,'Какой у вас опыт?','QUESTION',topic='experience')
        if s['turns'][-1]['role']=='manager':return answer(s,events=[('presentation_delivered','')])
        return answer(s,'Куда удобно отправить условия для оформления?','NEXT_STEP',events=[('ready_to_buy','')],facts=[('decision','Готов обсудить оформление',[])])
    c,p,s=conversation(tmp_path,respond)
    s=say(c,s,'client','Хочу научиться делать отчёты')
    s=say(c,s,'manager','Для ваших отчётов полезны формулы и фильтры; покажу подходящую программу.')
    s=say(c,s,'client','Подходит, готов обсудить оформление')
    assert s['dialogue_state']['events'][0]['source_turn']==s['turns'][1]['id']
    assert s['dialogue_state']['events'][0]['kind']=='presentation_delivered'
    assert s['recommendations'][-1]['action_type']=='NEXT_STEP'
    assert all(f['source_turn']!=s['turns'][1]['id'] for f in s['facts'])
    assert p.requests[-1]['turns'][1]['text']==s['turns'][1]['text']


def test_manager_handles_objection_but_client_must_resolve_it(tmp_path):
    def respond(s):
        if len(s['turns'])==1:return answer(s,'Что именно вызывает сомнения?','QUESTION',topic='value',events=[('objection_opened','value')])
        if s['turns'][-1]['role']=='manager':
            a=answer(s)
            if not s.get('validation_feedback'):a=answer(s,events=[('objection_resolved','value')])
            return a
        return answer(s,'Обсудим условия подходящего варианта.','NEXT_STEP',events=[('objection_resolved','value')],facts=[('importance','Важна практика с проверкой',[])])
    c,p,s=conversation(tmp_path,respond)
    s=say(c,s,'client','Сомневаюсь, что обучение будет полезно')
    s=say(c,s,'manager','Давайте оценим пользу на вашем отчёте, а условия практики отдельно проверим.')
    assert s['dialogue_state']['objections'][0]['status']=='open'
    s=say(c,s,'client','Теперь польза понятна, важна практика с проверкой')
    assert s['dialogue_state']['objections'][0]['status']=='resolved'
    assert s['dialogue_state']['objections'][0]['resolution_turn']==s['turns'][-1]['id']
    assert s['recommendations'][-1]['action_type']=='NEXT_STEP'


def test_new_motivation_reopens_selection_after_readiness(tmp_path):
    def respond(s):
        if len(s['turns'])==1:return answer(s,'Обсудим оформление.','NEXT_STEP',events=[('ready_to_buy','')],facts=[('goal','Простые отчёты',[])])
        if s['turns'][-1]['role']=='manager':return answer(s,events=[('question_asked','priority')])
        old=next(f['id'] for f in s['facts'] if f['category']=='goal')
        return answer(s,'Какие показатели команды вам предстоит анализировать?','QUESTION',topic='analysis',strategy='DEEP_DISCOVERY',events=[('question_answered','priority')],facts=[('goal','Анализ показателей команды',[old]),('motivation','Подготовиться к роли руководителя',[])])
    c,p,s=conversation(tmp_path,respond)
    s=say(c,s,'client','Для простых отчётов вариант подходит, готов оформить')
    s=say(c,s,'manager','Какие изменения в работе сейчас важнее всего?')
    s=say(c,s,'client','Мне предложили руководить командой. Теперь важнее анализ её показателей, а не простые отчёты.')
    assert not s['dialogue_state']['ready_to_buy']
    assert [f['status'] for f in s['facts'] if f['category']=='goal']==['OUTDATED','CONFIRMED']
    assert any(f['category']=='motivation' for f in s['facts'])
    assert s['recommendations'][-1]['action_type']=='QUESTION'
    assert s['recommendations'][-1]['strategy']['action']=='DEEP_DISCOVERY'


def test_rejected_suggestion_is_corrected_after_manager_changes_strategy(tmp_path):
    rejected='Сколько часов готовы учиться?'
    def respond(s):
        if len(s['turns'])==1:return answer(s,rejected,'QUESTION',topic='time')
        if s['turns'][-1]['role']=='manager':return answer(s,events=[('question_asked','problem')])
        if not s.get('validation_feedback'):return answer(s,rejected,'QUESTION',topic='time',facts=[('problem','Ошибки при объединении файлов',[])])
        assert 'dismissed_repeat' in s['validation_feedback']['checks']
        return answer(s,'Файлы одинаковой структуры или отличаются колонками?','QUESTION',topic='structure',events=[('question_answered','problem')],facts=[('problem','Ошибки при объединении файлов',[])])
    c,p,s=conversation(tmp_path,respond)
    s=say(c,s,'client','Нужен Excel для работы')
    rid=s['recommendations'][-1]['id']
    s=c.post('/api/sessions/'+s['id']+'/recommendations/'+rid+'/feedback',json={'value':'rejected'}).json()
    s=say(c,s,'manager','Где сейчас чаще возникают ошибки?')
    s=say(c,s,'client','Ошибки появляются при объединении файлов')
    assert s['recommendations'][-1]['action_type']=='QUESTION'
    assert s['recommendations'][-1]['suggestion']!=rejected
    assert s['recommendations'][-1]['usage']['request_count']==2
    assert len(s['facts'])==1
    assert not any(q['topic']=='time' for q in s['dialogue_state']['questions'])


def test_later_suggestion_cannot_relabel_earlier_manager_words():
    text='Какой у вас уровень?'
    s={'turns':[{'id':'t1','role':'manager','text':text},{'id':'t2','role':'client','text':'Начальный'},{'id':'t3','role':'client','text':'Хочу разобраться в формулах'}],
       'recommendations':[{'turn_id':'t2','suggestion':text,'strategy':{'action':'ALIGN_BEFORE_PRESENTATION'}}]}
    assert not conversation_signals(s)['offer_due_after_bridge']


@pytest.mark.parametrize('feedback',['helpful','rejected','hidden'])
def test_feedback_does_not_override_actual_speech(feedback):
    phrase='Осталось уточнить уровень задач?'
    rec={'turn_id':'t1','suggestion':phrase,'feedback':feedback,'strategy':{'action':'ALIGN_BEFORE_PRESENTATION'}}
    s={'turns':[{'id':'t1','role':'client','text':'Хочу подобрать программу'}],'recommendations':[rec]}
    assert not conversation_signals(s)['offer_due_after_bridge']
    s['turns'] += [{'id':'t2','role':'manager','text':phrase},{'id':'t3','role':'client','text':'Простые отчёты'}]
    assert conversation_signals(s)['offer_due_after_bridge']
