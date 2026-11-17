"""Frozen P23 context, C-only. Offline preparation never calls a provider."""
import argparse
import asyncio
import csv
import json
import os
from pathlib import Path
from contextlib import nullcontext
from experiments.sales_benchmark.run import run,read,validate,write
from experiments.sales_benchmark.live import Budget,configured
from experiments.sales_turning_points.p23 import ROOT,sha,FIELDS
from experiments.sales_turning_points.p23_safety import HISTORICAL_WARNING,experiment_registration
from .engine import make_runner

BASE=ROOT/'work/p23-private/live-01'
PRIVATE=ROOT/'work/p26-private'

def frozen_cases(base=BASE):
    saved=read(base/'inputs.json');cases=[]
    for row in saved:
        a=row['A'];b=row['B']['input']
        if a['session']['turns']!=b['turns'] or a['session']['facts']!=b['facts'] or a['knowledge']['approved_claims']!=b['approved_claims']:
            raise ValueError('P23 inputs differ')
        c={'id':row['scenario_id'],'origin':'historical','description':row['scenario_id'],
           **{k:b[k] for k in ('turns','facts','approved_claims')},'checkpoint':b['turns'][-1]['id'],
           'allowed_actions':[],'undesirable_actions':[],'critical_errors':[]}
        cases.append(validate(c))
    expected={'tp_1_7','tp_3_23','tp_4_2','tp_5_23','tp_6_147'}
    if len(cases)!=5 or {c['id'] for c in cases}!=expected:raise ValueError('Unexpected P23 pilot')
    return cases

def review_package(out,cases,results):
    folder=out/'reviewer';folder.mkdir(exist_ok=True)
    text=['# Независимая оценка пяти ответов\nОцените по фактическому контексту. Нет прошлых оценок, A/B или будущего диалога. Отсутствующий ответ — технический пропуск, не нулевая оценка продажи.\n']
    rows=[]
    for c in cases:
        text.append('## '+c['id']+'\n')
        for t in c['turns']:text.append(f"**{t['id']} · {t['role']}**: {t['text']}\n")
        r=next((r for r in results if r['scenario_id']==c['id']),{})
        o=(r.get('output') or {}) if r.get('status')=='completed' else {}
        text.append('Действие: '+o.get('decision','Ответ отсутствует')+'\n\nПричина: '+o.get('reason','—')+'\n\nФраза: '+o.get('say_to_customer','—')+'\n')
        rows.append({'response_id':c['id'],**{k:'' for k in FIELDS}})
    (folder/'answers.md').write_text('\n'.join(text),encoding='utf-8')
    if not (folder/'assessment_template.csv').exists():
        with (folder/'assessment_template.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['response_id']+FIELDS,delimiter=';');w.writeheader();w.writerows(rows)

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--allow-paid',action='store_true');p.add_argument('--allow-private-transcripts',action='store_true')
    p.add_argument('--max-calls',type=int,default=10);p.add_argument('--resume',action='store_true');p.add_argument('--authorize-repeat')
    a=p.parse_args()
    if not a.out.resolve().is_relative_to(PRIVATE.resolve()):p.error('Use work/p26-private')
    if not 1<=a.max_calls<=10:p.error('Hard limit: 10')
    if a.resume and not a.allow_paid:p.error('Resume requires explicit paid permission')
    PRIVATE.mkdir(exist_ok=True);(PRIVATE/'.gitignore').write_text('*\n',encoding='ascii')
    cases=frozen_cases();baseline=read(BASE/'run_state.json')['identity'];runner=None;model=baseline['model']
    generation={'temperature':.2,'thinking':'disabled','max_tokens':3000,'p23_inputs_sha256':sha(BASE/'inputs.json'),
       'hybrid_files':{f.name:sha(f) for f in Path(__file__).parent.iterdir() if f.is_file()}}
    if a.allow_paid:
        if not a.allow_private_transcripts:p.error('Private transcript permission required')
        current=configured()
        if current!=model or int(os.getenv('LLM_MAX_TOKENS','3000'))!=3000:p.error('Model/output cap differs from frozen P23 settings')
        runner=make_runner(Budget(a.max_calls),HISTORICAL_WARNING)
    with experiment_registration(PRIVATE,a.out,a.resume,a.authorize_repeat) if runner else nullcontext():
        try:
            asyncio.run(run(a.out,cases_override=cases,model=model,live_runner=runner,resume=a.resume,generation=generation,variants=('C',)))
        finally:
            result=a.out/'results.json'
            if not result.exists():result=a.out/'partial_results.json'
            if result.exists():review_package(a.out,cases,read(result))
    write(a.out/'comparison_constraints.json',{'baseline_model':model,'context_equal_to_saved_p23':True,
       'baseline_input_hash':sha(BASE/'inputs.json'),'baseline_generation':baseline['generation'],
       'limits':['Sequential not simultaneous comparison','Provider alias may change server-side','Prompts/contracts/validators intentionally differ','Five previously disclosed regression points, not holdout'],
       'paid_calls_this_command':bool(runner),'maximum_calls':a.max_calls})
    print('Prepared C-only; 5 primary requests, at most 10 with recovery. No A/B executed.' if not runner else 'Finished C-only; see summary.json and reviewer.')
if __name__=='__main__':main()
