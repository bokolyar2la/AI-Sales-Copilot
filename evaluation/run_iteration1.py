import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from dotenv import load_dotenv
from backend.providers import CompatibleProvider
from backend.context import apply_analysis
from backend.engine import process_turn
from backend.prompts import VERSION
from .iteration1_cases import CASES

ROOT = Path(__file__).resolve().parents[1]


async def evaluate(case, semaphore):
    async with semaphore:
        session={'turns':[], 'facts':[], 'hypotheses':[], 'recommendations':[],
                 'sales_context':{'school':'Skillbox','course':'','manager':''}}
        knowledge={'approved_claims':[], 'products':[], 'status':'Проверенные условия не загружены'}
        rows=[]
        raw=None
        try:
            for i,(role,text) in enumerate(case['turns']):
                session['turns'].append({'id':f't{i+1}','role':role,'text':text})
                start=time.perf_counter()
                analysis,usage=await process_turn(session,CompatibleProvider(),knowledge)
                raw=analysis.model_dump()
                rows.append({'turn':session['turns'][-1], 'raw':raw, 'shown':session['recommendations'][-1],
                             'latency_ms':round((time.perf_counter()-start)*1000),'usage':usage})
            last=rows[-1]['shown']
            checks={'strategy':last['strategy']['action'] in case['strategies'],
                    'not_blocked':last['validation']['status']!='blocked',
                    'spoken_or_deliberate_silence':bool(last['suggestion']) or case['id']=='clear_refusal',
                    'hypotheses_separate':all(h['status'] in ('INFERRED','REJECTED','CONFIRMED') for h in session['hypotheses'])}
            if case['id']=='goal_change':
                checks['old_goal_retired']=any(f['category']=='goal' and f['status']=='OUTDATED' for f in session['facts'])
            return {'id':case['id'],'title':case['title'],'passed':all(checks.values()),'checks':checks,'rows':rows,'session':session}
        except Exception as exc:
            from pydantic import ValidationError
            detail=exc.errors(include_input=False,include_url=False) if isinstance(exc,ValidationError) else None
            local_error=str(exc) if type(exc) is ValueError else None
            return {'id':case['id'],'title':case['title'],'passed':False,'error_type':type(exc).__name__,'schema_errors':detail,'local_error':local_error,'last_raw':raw,'rows':rows}


async def main(args):
    load_dotenv(ROOT/'.env')
    sem=asyncio.Semaphore(3)
    cases=[c for c in CASES if not args.case or c['id']==args.case]
    rows=await asyncio.gather(*(evaluate(c,sem) for c in cases))
    latencies=sorted(r['latency_ms'] for c in rows for r in c['rows'])
    report={'version':VERSION,'date':'2026-11-15','scope':'Синтетический набор разработки, без будущих реплик. Автоматические проверки не равны экспертной оценке.',
            'passed':sum(r['passed'] for r in rows),'total':len(rows),
            'p95_ms':latencies[math.ceil(.95*len(latencies))-1] if latencies else None,
            'total_tokens':sum(r['usage'].get('total_tokens',0) for c in rows for r in c['rows']), 'results':rows}
    target=ROOT/'evaluation'/args.output
    if target.parent.resolve()!=(ROOT/'evaluation').resolve(): raise ValueError('Use a filename')
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
    print('Failed:',[r['id'] for r in rows if not r['passed']])


if __name__=='__main__':
    p=argparse.ArgumentParser(description='Платная проверка синтетических ситуаций через текущую модель')
    p.add_argument('--live',action='store_true');p.add_argument('--case',default='');p.add_argument('--output',default='iteration1-live.json')
    a=p.parse_args()
    if not a.live: p.error('Для обращения к платному API явно укажите --live')
    asyncio.run(main(a))
