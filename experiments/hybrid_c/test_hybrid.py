from copy import deepcopy
import asyncio
import pytest
from experiments.hybrid_c.engine import Decision,validate_and_apply,context,VERIFY_PHRASE
from experiments.sales_benchmark.run import run,read,HERE
from experiments.sales_benchmark.test_resume import fake_runner

def session(text='Нужны навыки отчётности'):
 return {'turns':[{'id':'t1','role':'client','text':text}],'facts':[],'recommendations':[]}
def decision(**kw):
 return Decision(**dict({'action':'ASK','reason':'Уточнить рабочую задачу','say_to_customer':'Какую задачу хотите решить?','evidence_ids':['t1'],'claim_ids':[],'memory_delta':[]},**kw))
def test_facts_atomic():
 s=session();d=decision(memory_delta=[{'kind':'fact','value':{'category':'goal','value':'Навыки отчётности','quote':'Нужны навыки отчётности','source_turn':'t1'}}])
 validate_and_apply(s,d,[]);assert len(s['facts'])==1
 old=deepcopy(s);bad=decision(memory_delta=[{'kind':'fact','value':{'category':'goal','value':'x','quote':'not spoken','source_turn':'t1'}}])
 with pytest.raises(ValueError):validate_and_apply(s,bad,[])
 assert s==old

def test_reject_hypothesis():
 s=session('Нет, мне нужна текущая работа');s['hypotheses']=[{'id':'h1','statement':'Смена работы','status':'INFERRED','history':[]}]
 d=decision(memory_delta=[{'kind':'hypothesis','value':{'id':'h1','statement':'Смена работы','status':'REJECTED','evidence':[{'source_turn':'t1','quote':s['turns'][0]['text']}],'test_question':''}}])
 validate_and_apply(s,d,[]);assert s['hypotheses'][0]['status']=='REJECTED'

def test_questions_remain_open():
 s=session('Сколько длится обучение?')
 d=decision(action='VERIFY_TERMS',say_to_customer=VERIFY_PHRASE,memory_delta=[{'kind':'event','value':{'kind':'question_asked','source_turn':'t1','quote':s['turns'][0]['text'],'topic':'duration'}}])
 validate_and_apply(s,d,[]);assert s['dialogue_state']['questions'][0]['status']=='open'
 s['turns'].append({'id':'t2','role':'manager','text':'Что важно для вас?'})
 validate_and_apply(s,decision(action='VERIFY_TERMS',say_to_customer=VERIFY_PHRASE,evidence_ids=['t2']),[])
 assert s['dialogue_state']['questions'][0]['status']=='open'

def test_condition_and_refusal():
 s=session('Куплю, если можно отказаться')
 with pytest.raises(ValueError):validate_and_apply(s,decision(action='ANSWER',say_to_customer='Тогда оформим заявку.'),[])
 validate_and_apply(s,decision(action='VERIFY_TERMS',say_to_customer=VERIFY_PHRASE),[])
 s=session('Не готова к разговору')
 with pytest.raises(ValueError):validate_and_apply(s,decision(),[])
 validate_and_apply(s,decision(action='END',say_to_customer='Не буду отвлекать. Всего доброго!'),[])

@pytest.mark.parametrize('phrase',['Это условие зафиксировано в договоре.','Вернём деньги полностью.','Предоставим скидку.','Вы получите доступ навсегда.'])
def test_promises(phrase):
 with pytest.raises(ValueError):validate_and_apply(session(),decision(action='ANSWER',say_to_customer=phrase),[])

def test_repeat_and_leak():
 s=session();s['turns'].insert(0,{'id':'t0','role':'manager','text':'Какую задачу хотите решить?'})
 s['expert_rows']=['secret'];s['observed_continuation']=['future']
 assert 'expert_rows' not in context(s,[]) and 'observed_continuation' not in context(s,[])
 with pytest.raises(ValueError):validate_and_apply(s,decision(),[])

def test_abc_resume(tmp_path):
 cases=read(HERE/'pilot.json')[:1];runner=fake_runner('B')
 with pytest.raises(KeyboardInterrupt):asyncio.run(run(tmp_path,cases_override=cases,live_runner=runner,variants=('A','B','C')))
 runner=fake_runner();results=asyncio.run(run(tmp_path,cases_override=cases,live_runner=runner,variants=('A','B','C'),resume=True))
 assert len(results)==3 and runner.budget.used==3
 asyncio.run(run(tmp_path,cases_override=cases,live_runner=runner,variants=('A','B','C'),resume=True));assert runner.budget.used==3

def test_actual_c_transport_retry_and_raw(monkeypatch):
 import os,json
 from experiments.hybrid_c.engine import make_runner
 from experiments.sales_benchmark.live import Budget
 from backend.providers import CompatibleProvider
 from experiments.sales_turning_points.p23_safety import HISTORICAL_WARNING
 from experiments.sales_benchmark.run import validate
 for k,v in {'LLM_MODEL':'offline','LLM_API_KEY':'fake','LLM_BASE_URL':'https://api.deepseek.com'}.items():monkeypatch.setenv(k,v)
 captured=[]
 async def transport(self,endpoint,headers,payload):
  captured.append(payload)
  content='{}' if len(captured)==1 else decision().model_dump_json()
  return {'choices':[{'message':{'content':content},'finish_reason':'stop'}],'usage':{'prompt_tokens':5,'completion_tokens':3}}
 monkeypatch.setattr(CompatibleProvider,'_complete',transport)
 c={'id':'synthetic','turns':session()['turns'],'facts':[],'approved_claims':[]}
 runner=make_runner(Budget(2),HISTORICAL_WARNING)
 r=asyncio.run(runner(c,'C'))
 assert r['status']=='completed' and len(r['raw_responses'])==2
 assert r['metrics']['input_tokens']==10 and 'validation_feedback' in json.loads(captured[1]['messages'][1]['content'])
 assert len(r['validation_trace'])==2

def test_five_disclosed_cases_are_input_regressions_only():
 from experiments.sales_turning_points.p23 import PRIVATE,load_cases,PILOT
 from experiments.sales_benchmark.run import inputs
 if not (PRIVATE/'expert_review_phase2.csv').exists():pytest.skip('Private corpus unavailable')
 _,_,cases=load_cases();selected=[c for c in cases if c['id'] in PILOT];assert len(selected)==5
 for c in selected:
  a,b=inputs(c);ctx=context(a,c['approved_claims'])
  assert ctx['turns']==b['turns'] and ctx['facts']==b['facts']
  assert ctx['turns'][-1]['id']==c['checkpoint'] and ctx['approved_claims']==[]
  assert not {'expert_rows','annotation','observed_continuation'} & ctx.keys()
