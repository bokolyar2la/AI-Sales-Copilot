import asyncio
from copy import deepcopy
import pytest
from backend.models import Analysis
from backend.engine import process_turn
from backend.diagnostics import validation_failure
from backend.context import apply_analysis
from backend.providers import model_context
from backend.tests.test_need_depth import session, step, analysis
from backend.tests.test_manager_adaptation import conversation, say, answer

TEXT='С Excel почти не работал. Получаю 45 тысяч, хочу 65–70. Откликаться страшно.'

def prepared():
    s=session()
    step(s,'manager','Что хотите изменить?',lambda s:analysis(s))
    step(s,'client',TEXT,lambda s:analysis(s,'Что мешает откликнуться?','QUESTION',facts=[('situation','Доход 45 тысяч',[]),('goal','Доход 65–70 тысяч',[]),('objection','Страшно откликаться',[])]))
    return s

def test_false_choice_retried_with_motives_preserved():
    s=prepared()
    class Model:
        calls=0
        async def analyze(self,state,k):
            self.calls+=1
            context=model_context(state)
            assert len(context['facts'])==3 and context['turns'][-1]['text']==TEXT
            if self.calls==1:
                return analysis(state,'Вам важнее простые таблицы или сложные расчёты и автоматизация?','QUESTION',topic='new_topic'),{}
            assert 'known_novice_false_choice' in state['validation_feedback']['checks']
            return analysis(state,'Какие требования вакансий вызывают больше всего сомнений?','QUESTION',strategy='OBJECTION_DIAGNOSIS',topic='barrier'),{}
    m=Model();asyncio.run(process_turn(s,m,{}))
    assert m.calls==2 and s['recommendations'][-1]['validation']['status']=='passed'
    assert len(s['facts'])==3


def test_learning_already_answered():
    s=prepared()
    step(s,'manager','Пробовали учиться сами?',lambda s:analysis(s,events=[('question_asked','self_learning')]))
    step(s,'client','Пробовал видео, застрял на формулах.',lambda s:analysis(s,'На какой формуле остановились?','QUESTION',topic='formula',events=[('question_answered','self_learning')]))
    apply_analysis(s,analysis(s,'Пробовали самостоятельно изучать Excel?','QUESTION',topic='self_learning'),{})
    assert 'answered_topic_repeat' in s['recommendations'][-1]['validation']['issues']


def test_program_request_after_clear_need():
    s=prepared()
    step(s,'manager','Для офисной работы нужны отчёты; обсудим обучение.',lambda s:analysis(s))
    rec=step(s,'client','Да, нужны отчёты, покажите программу.',lambda s:analysis(s,'Под вашу задачу стоит проверить работу с отчётами и формулами в программе.','PRESENT',strategy='SELECT_SOLUTION',depth='MOTIVE_OR_CHANGE'))
    assert rec['validation']['status']=='passed'

@pytest.mark.parametrize('raw,code',[('{','invalid_json'),('{"facts":"secret-api-key"}','invalid_schema')])
def test_safe_schema_diagnostics(raw,code,caplog):
    with pytest.raises(ValueError) as err:Analysis.model_validate_json(raw)
    entry=validation_failure(err.value,'schema',2)
    assert entry['code']==code and entry['fields']
    assert 'secret-api-key' not in caplog.text


def test_api_retry_keeps_dialogue_and_memory(tmp_path,caplog):
    broken=[False]
    def respond(s):
        if broken[0]:
            a=answer(s,'Что хотите изменить?','QUESTION')
            a.recommendation.evidence_ids=['missing']
            return a
        return answer(s,'Какие требования вызывают сомнения?','QUESTION',facts=[('goal',s['turns'][-1]['text'],[])])
    c,p,s=conversation(tmp_path,respond)
    s=say(c,s,'client','Хочу офисную работу')
    old=deepcopy(s['facts']);broken[0]=True
    from uuid import uuid4
    s=c.post('/api/sessions/'+s['id']+'/turns',json={'role':'client','text':TEXT,'request_id':str(uuid4()),'expected_version':s['version']}).json()
    assert s['analysis_error'] and s['facts']==old and len(s['turns'])==2
    assert 'evidence_source' in caplog.text and 'sources_memory' in caplog.text
    broken[0]=False
    r=c.post('/api/sessions/'+s['id']+'/retry');assert r.status_code==200
    retried=r.json();assert retried['analysis_error'] is None and retried['turns']==s['turns']
    assert retried['facts'][:len(old)]==old
