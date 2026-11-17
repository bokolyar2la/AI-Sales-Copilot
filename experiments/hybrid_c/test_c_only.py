import asyncio,json
import pytest
from experiments.hybrid_c.c_only import frozen_cases,BASE,review_package
from experiments.hybrid_c.engine import make_runner,context
from experiments.sales_benchmark.run import inputs,read
from experiments.sales_benchmark.live import Budget
from experiments.sales_turning_points.p23_safety import HISTORICAL_WARNING
from backend.providers import CompatibleProvider

def test_frozen_context_and_review(tmp_path):
 if not BASE.exists():pytest.skip('Private P23 not distributed')
 cases=frozen_cases();saved=read(BASE/'inputs.json');assert len(cases)==5
 for c,old in zip(cases,saved):
  s,b=inputs(c);new=context(s,c['approved_claims'])
  for k in ('turns','facts','approved_claims'):assert new[k]==old['B']['input'][k]
  assert c['checkpoint']==new['turns'][-1]['id']
 review_package(tmp_path,cases,[])
 text=(tmp_path/'reviewer/answers.md').read_text(encoding='utf-8')
 assert text.count('## tp_')==5 and 'expert_rows' not in text
 assert 'observed_continuation' not in text

def test_ten_calls_all_recoveries(monkeypatch):
 for k,v in {'LLM_MODEL':'offline','LLM_API_KEY':'fake','LLM_BASE_URL':'https://api.deepseek.com'}.items():monkeypatch.setenv(k,v)
 seen=[]
 async def fake(self,endpoint,headers,payload):
  seen.append(payload)
  return {'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}}
 monkeypatch.setattr(CompatibleProvider,'_complete',fake)
 runner=make_runner(Budget(10),HISTORICAL_WARNING)
 c={'id':'offline','turns':[{'id':'t','role':'client','text':'Здравствуйте'}],'facts':[],'approved_claims':[]}
 async def go():
  for _ in range(5):
   r=await runner(c,'C');assert r['status']=='blocked' and len(r['raw_responses'])==2
  r=await runner(c,'C');assert r['error_code']=='CallLimit'
 asyncio.run(go());assert len(seen)==10 and runner.budget.used==10
