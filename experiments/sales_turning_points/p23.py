"""P2.3 historical pilot. Offline preparation by default; no production changes."""
import argparse
import asyncio
import csv
import hashlib
import json
import os
import secrets
from pathlib import Path
from experiments.sales_turning_points.corpus import validate, benchmark_case, read
from experiments.sales_benchmark.run import run, inputs
from experiments.sales_benchmark.journal import atomic
from .p23_safety import EXPERIMENT_ID, HISTORICAL_WARNING, private_overlay, historical_audit, experiment_registration
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT/'work/p23-private'
CORPUS = ROOT/'experiments/sales_turning_points/private'
PILOT = ['tp_1_7','tp_3_23','tp_4_2','tp_5_23','tp_6_147']
FIELDS = ['facts','motive_or_barrier','timing','repetition','product_accuracy','client_boundaries',
          'commercial_appropriateness','rationale','strong_decisions','error_pattern','critical_violations']

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load_cases():
    corpus=validate(read(CORPUS/'corpus.json'),read(CORPUS/'transcripts_redacted.json'))
    with (PRIVATE/'expert_review_phase2.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f,delimiter=';'))
    expected={c['id'] for c in corpus if c['source_document']!='call_2'}
    ids=[r['ID контрольной точки'] for r in rows]
    if len(rows)!=20 or len(set(ids))!=20 or set(ids)!=expected:
        raise ValueError('Expert IDs do not match the 20 checked cases')
    for r in rows:
        if r['Оценка фактического действия менеджера (0–4)'] not in ('0','1','2','3','4'):
            raise ValueError('Historical manager score missing or invalid')
    # Labels and future continuations are deliberately absent from benchmark cases.
    cases=[]
    for c in corpus:
        if c['id'] not in expected:continue
        v=private_overlay(benchmark_case(c))
        v.update(description=c['id'],allowed_actions=[],undesirable_actions=[],critical_errors=[])
        cases.append(v)
    return corpus,rows,cases

def prepare():
    corpus,rows,cases=load_cases()
    atomic(PRIVATE/'evaluation_only.json',{'expert_rows':rows,'historical_cases':corpus,
        'warning':'Evaluation only. Never pass to provider. Historical manager scores are not model scores.'})
    atomic(PRIVATE/'model_cases.json',cases)
    atomic(PRIVATE/'import_manifest.json',{'count':20,'pilot':PILOT,'source_hashes':{
        n:sha(PRIVATE/n) for n in ['expert_review_phase2.csv','sales_turning_points_phase2_report.md']},
        'corpus_sha256':sha(CORPUS/'corpus.json'),'production_changed':False,
        'export_overlay':{'case':'tp_6_147','segments':['c6t91','c6t92','c6t94','c6t95','c6t96','c6t97'],'purpose':'Bank identifiers and account circumstances only; original source retained'},
        'historical_warning':HISTORICAL_WARNING})
    return [next(c for c in cases if c['id']==i) for i in PILOT]

def blind(out,cases,results):
    """Random per-case X/Y. Key and metrics remain outside the reviewer package."""
    out=Path(out);keypath=out/'sealed_mapping.json'
    if keypath.exists():key=read(keypath)
    else:
        key={}
        for c in cases:
            variants=['A','B'];secrets.SystemRandom().shuffle(variants)
            for label,v in zip(('X','Y'),variants):key[c['id']+':'+label]=v
        atomic(keypath,key)
    rows=[];md=['# Слепая оценка P2.3\nОцените действие по контексту, не по совпадению с эталонной фразой. Пустой ответ — отсутствующие данные, не нулевое качество.\n']
    for c in cases:
        md.append('## '+c['id']+'\n')
        for t in c['turns']:md.append(f"**{t['id']} · {t['role']}**: {t['text']}\n")
        for label in ('X','Y'):
            rid=c['id']+':'+label;v=key[rid]
            r=next((r for r in results if r['scenario_id']==c['id'] and r['variant']==v),{})
            o=r.get('output') if r.get('status')=='completed' else None
            o=o or {}
            md.append(f"### {label}\nДействие: {o.get('decision','Ответ отсутствует')}\n\nПричина: {o.get('reason','—')}\n\nФраза: {o.get('say_to_customer','—')}\n")
            rows.append(dict(response_id=rid,**{k:'' for k in FIELDS}))
    review=out/'reviewer';review.mkdir(exist_ok=True)
    (review/'anonymous_answers.md').write_text('\n'.join(md),encoding='utf-8')
    template=review/'assessment_template.csv'
    if not template.exists():
        with template.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['response_id']+FIELDS,delimiter=';');w.writeheader();w.writerows(rows)
    atomic(out/'review_binding.json',{'answers_sha256':sha(review/'anonymous_answers.md'),
        'results_sha256':sha(out/'results.json') if (out/'results.json').exists() else None})

def reveal(out,review):
    out=Path(out)
    with Path(review).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f,delimiter=';'))
    key=read(out/'sealed_mapping.json');binding=read(out/'review_binding.json')
    if binding['answers_sha256']!=sha(out/'reviewer/anonymous_answers.md') or binding['results_sha256']!=sha(out/'results.json'):
        raise ValueError('Answers changed after blind export')
    if len(rows)!=len(key) or {r['response_id'] for r in rows}!=set(key):raise ValueError('Incomplete/duplicate review IDs')
    records=read(out/'results.json')
    for row in rows:
        case,label=row['response_id'].split(':');variant=key[row['response_id']]
        record=next(r for r in records if r['scenario_id']==case and r['variant']==variant)
        if not row['commercial_appropriateness'] or not row['rationale']:
            raise ValueError('Fix assessments and rationale before unblinding, including unavailable answers')
        row.update(variant=variant,scenario_id=case,technical=record)
    atomic(out/'fixed_expert_review.json',{'sha256':sha(review),'rows':rows})
    report=['# Сравнение после фиксации экспертных оценок\nПобедитель автоматически не определяется. Стратегии не сравниваются по строковому совпадению.\n']
    _,expert_rows,_=load_cases()
    expert_by_id={r['ID контрольной точки']:r for r in expert_rows}
    for row in rows:
        report.append(f"## {row['response_id']} → {row['variant']}\n")
        report.extend(f"- {k}: {row.get(k,'')}\n" for k in FIELDS)
        report.append('Исходная экспертная разметка ситуации (не обязательный ответ): '+json.dumps(expert_by_id[row['scenario_id']],ensure_ascii=False)+'\n')
        report.append('Технические показатели: '+json.dumps(row['technical'].get('metrics'),ensure_ascii=False)+'\n')
        report.append('Проверки и ошибки: '+json.dumps({k:row['technical'].get(k) for k in ('status','validation','validation_trace','error_code')},ensure_ascii=False)+'\n')
    groups={}
    for r in rows:
        if r['error_pattern'].strip():groups.setdefault(r['error_pattern'].strip(),[]).append(r['response_id'])
    report.append('## Повторяющиеся ошибки по меткам эксперта\n'+json.dumps({k:v for k,v in groups.items() if len(v)>1},ensure_ascii=False,indent=2))
    (out/'comparison.md').write_text('\n'.join(report),encoding='utf-8')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=Path,default=PRIVATE/'preview')
    p.add_argument('--allow-paid',action='store_true');p.add_argument('--allow-private-transcripts',action='store_true')
    p.add_argument('--max-calls',type=int,default=15);p.add_argument('--resume',action='store_true')
    p.add_argument('--review',type=Path)
    p.add_argument('--authorize-repeat',help='Separate explicit authorization: exact experiment ID')
    args=p.parse_args()
    # Keep every export inside the ignored private tree.
    if not args.out.resolve().is_relative_to(PRIVATE.resolve()):p.error('Output must be inside work/p23-private')
    if args.review:return reveal(args.out,args.review)
    cases=prepare()
    runner=None;model='not_called';generation={'thinking':'disabled','temperature':.2,'max_tokens':3000,
        'experiment_id':EXPERIMENT_ID,'safety_sha256':sha(ROOT/'experiments/sales_turning_points/p23_safety.py'),'historical_warning':HISTORICAL_WARNING,'adapter_sha256':sha(__file__),'corpus_adapter_sha256':sha(ROOT/'experiments/sales_turning_points/corpus.py')}
    if args.max_calls<1 or args.max_calls>15:p.error('Hard ceiling: 15 calls')
    if args.allow_paid:
        if not args.allow_private_transcripts:p.error('Explicit permission for real transcript transmission required')
        from experiments.sales_benchmark.live import Budget,configured,make_runner
        model=configured();generation['max_tokens']=int(os.getenv('LLM_MAX_TOKENS','3000'))
        if generation['max_tokens']>3000:p.error('Output ceiling is 3000 tokens per call')
        runner=make_runner(Budget(args.max_calls),warning=HISTORICAL_WARNING,audit=historical_audit)
    elif args.resume:p.error('Resume requires explicit paid and transcript permission')
    print(json.dumps({'pilot':PILOT,'paid':bool(runner),'baseline_calls':10,'maximum_calls':args.max_calls,
        'maximum_output_tokens':args.max_calls*generation['max_tokens'],'dollar_cap':None}))
    try:
        with (experiment_registration(PRIVATE,args.out,args.resume,args.authorize_repeat) if runner else nullcontext()):
            results=asyncio.run(run(args.out,cases_override=cases,live_runner=runner,model=model,
                resume=args.resume,generation=generation))
    finally:
        path=args.out/'results.json'
        if not path.exists():path=args.out/'partial_results.json'
        if path.exists():blind(args.out,cases,read(path))
    atomic(args.out/'p23_manifest.json',{'origin':'historical_redacted','expert_labels_in_model':False,
        'variants':['A','B'],'B_validation':'schema_only','generation':generation,
        'comparison_limit':'Same dialogue and facts; different prompts, response contracts and validation. No current product catalog.'})

if __name__=='__main__':main()
