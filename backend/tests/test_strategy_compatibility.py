"""Opening-dialogue compatibility and bounded correction regressions (offline)."""
import asyncio
from copy import deepcopy
import pytest
from backend.engine import process_turn
from backend.context import apply_analysis
from backend.tests.test_manager_adaptation import answer


def opening():
    return {'turns':[
        {'id':'t1','role':'manager','text':'Здравствуйте! Я представляю Skillbox. Вам удобно сейчас поговорить об обучении?'},
        {'id':'t2','role':'client','text':'Привет, да, удобно'}], 'facts':[], 'recommendations':[]}


def response(s, action, strategy):
    a=answer(s,'Сначала обсудим ваши задачи, затем подходящие варианты. Что вызвало интерес к обучению?',
             'QUESTION',strategy=strategy,topic='interest')
    a.recommendation.action_type=action
    a.intelligence.sales_plan.need_depth='SURFACE_INTEREST'
    a.intelligence.sales_plan.after_answer_action='DISCOVERY'
    return a


@pytest.mark.parametrize('action,strategy', [('AGENDA','EXPLAIN_PROCESS'),('INTEREST','DEEP_DISCOVERY'),('QUESTION','DISCOVERY')])
def test_opening_can_continue_discovery(action,strategy):
    s=opening();apply_analysis(s,response(s,action,strategy),{})
    assert s['recommendations'][-1]['validation']['status']=='passed'
    assert s['recommendations'][-1]['suggestion']
    assert not s['facts'] and not s['dialogue_state']['ready_to_buy']


@pytest.mark.parametrize('corrected',[True,False])
def test_mismatch_retry_has_exact_pair_and_stays_bounded(corrected):
    s=opening()
    class Model:
        requests=[]
        async def analyze(self,state,knowledge):
            self.requests.append(deepcopy(state))
            return response(state,'QUESTION','DISCOVERY' if corrected and len(self.requests)==2 else 'CLOSING'),{}
    model=Model();_,usage=asyncio.run(process_turn(s,model,{}))
    assert len(model.requests)==usage['request_count']==2
    feedback=model.requests[1]['validation_feedback']
    detail=feedback['strategy_compatibility']
    assert detail['action_type']=='QUESTION' and detail['strategy_action']=='CLOSING'
    assert 'DISCOVERY' in detail['allowed_strategies'] and 'CLOSING' not in detail['allowed_strategies']
    rec=s['recommendations'][-1]
    assert rec['validation_trace'][0]['strategy_compatibility']==detail
    assert rec['validation']['status']==('passed' if corrected else 'blocked')
    if not corrected:
        assert 'QUESTION' in rec['reason'] and 'CLOSING' in rec['reason']
        assert not rec['suggestion']


def test_prompt_uses_validator_matrix():
    from backend.intelligence import ALLOWED
    from backend.prompts import SYSTEM
    for action,strategies in ALLOWED.items():
        assert action+' → '+', '.join(sorted(strategies)) in SYSTEM


def test_compatibility_covers_schema_without_unknown_values():
    from typing import get_args
    from backend.models import Advice, Strategy
    from backend.intelligence import ALLOWED
    assert set(ALLOWED)==set(get_args(Advice.model_fields['action_type'].annotation))
    strategies=set(get_args(Strategy.model_fields['action'].annotation))
    assert set.union(*ALLOWED.values())==strategies
