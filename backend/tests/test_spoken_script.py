import pytest
from backend.models import Analysis, Advice
from backend.context import apply_analysis

@pytest.mark.parametrize('action,stage', [('ANSWER','Ответ клиенту'),('SUMMARIZE','Подтверждение задачи')])
def test_spoken_actions_preserve_sources(action, stage):
    session={'turns':[{'id':'t1','role':'client','text':'Хочу научиться считать заказы'}], 'facts':[], 'recommendations':[]}
    analysis=Analysis(facts=[],recommendation=Advice(action_type=action,suggestion='Правильно понимаю, вам нужен расчёт заказов в таблице?',reason='Проверяем понимание цели.',evidence_ids=['t1'],target_gap='',confidence=.8))
    apply_analysis(session,analysis)
    assert session['stage']==stage
    assert session['recommendations'][0]['evidence_ids']==['t1']
