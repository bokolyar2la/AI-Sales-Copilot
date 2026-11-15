import asyncio
from copy import deepcopy
import pytest
from backend.engine import process_turn
from backend.models import Analysis, Advice, Fact


def state():
    return {'turns':[{'id':'t1','role':'client','text':'Хочу учиться'}],'facts':[],'recommendations':[]}


def valid():
    return Analysis(facts=[Fact(category='goal',value='Учиться',quote='Хочу учиться',source_turn='t1')],
        recommendation=Advice(action_type='QUESTION',suggestion='Что заинтересовало вас в обучении?',reason='Нужно уточнить интерес.',evidence_ids=['t1'],target_gap='interest',confidence=.8))


def test_one_correction_and_atomic_memory():
    class Model:
        calls=0
        async def analyze(self,s,k):
            self.calls+=1
            assert not s['facts']
            a=valid()
            if self.calls==1: a.facts[0].source_turn='future'
            else: assert s['validation_feedback']['issue']=='invalid_sources_or_memory_delta'
            return a,{'total_tokens':10}
    model=Model(); s=state()
    _,usage=asyncio.run(process_turn(s,model,{}))
    assert len(s['facts'])==1 and len(s['recommendations'])==1
    assert usage=={'total_tokens':20,'request_count':2}
    assert len(s['recommendations'][0]['validation_trace'])==2


def test_two_invalid_attempts_do_not_mutate_memory():
    class Model:
        calls=0
        async def analyze(self,*args):
            self.calls+=1
            a=valid(); a.facts[0].source_turn='missing'
            return a,{}
    s=state(); before=deepcopy(s); model=Model()
    with pytest.raises(ValueError): asyncio.run(process_turn(s,model,{}))
    assert s==before and model.calls==2


def test_schema_retry_preserves_reported_usage():
    class Model:
        calls=0
        async def analyze(self,*args):
            self.calls+=1
            if self.calls==1:
                exc=ValueError('invalid schema'); exc.usage={'total_tokens':7}; raise exc
            return valid(),{'total_tokens':10}
    _,usage=asyncio.run(process_turn(state(),Model(),{}))
    assert usage['total_tokens']==17 and usage['request_count']==2


def test_still_blocked_phrase_is_safely_hidden_after_two_attempts():
    class Model:
        calls=0
        async def analyze(self,*args):
            self.calls+=1
            a=valid(); a.recommendation.suggestion='Уточните потребность клиента'
            return a,{}
    s=state(); model=Model(); asyncio.run(process_turn(s,model,{}))
    assert model.calls==2
    assert len(s['facts'])==1 and s['recommendations'][0]['suggestion']==''
    assert s['recommendations'][0]['validation']['status']=='blocked'


def test_denial_of_quoted_salary_is_not_invented_price():
    from backend.context import apply_analysis
    s=state(); s['turns'][0]['text']='Гарантируете зарплату от 150 тысяч?'
    a=valid(); a.facts=[]; a.recommendation.action_type='ANSWER'
    a.recommendation.suggestion='Гарантировать зарплату от 150 тысяч я не могу; подтверждённых условий у меня нет.'
    apply_analysis(s,a,{})
    assert s['recommendations'][0]['validation']['status']=='passed'
