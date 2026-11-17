"""Paired experiment. Offline by default; paid execution requires explicit opt-in."""
import argparse
import asyncio
import csv
import hashlib
import json
import random
import subprocess
from copy import deepcopy
from pathlib import Path
from backend.engine import process_turn
from backend.models import Analysis, LiveAnalysis, Fact
from backend.providers import DemoProvider

HERE=Path(__file__).parent
ROOT=HERE.parents[1]
CRITERIA=['commercial_priority','context','novelty','naturalness','deal_progress','product_accuracy','safety']

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,data):
    from .journal import atomic
    atomic(path,data)
def digest(data):return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def validate(case):
    required={'id','description','origin','turns','facts','approved_claims','checkpoint','allowed_actions','undesirable_actions','critical_errors'}
    if not required<=case.keys():raise ValueError('Incomplete scenario')
    if case['origin'] not in ('synthetic','historical','test'):raise ValueError('Invalid origin')
    turns={t['id']:t for t in case['turns']}
    if len(turns)!=len(case['turns']) or not turns:raise ValueError('Invalid turns')
    if case['checkpoint']!=case['turns'][-1]['id']:raise ValueError('Checkpoint must end input')
    for t in turns.values():
        if t['role'] not in ('client','manager') or not t['text']:raise ValueError('Invalid spoken turn')
    for f in case['facts']:
        Fact.model_validate({k:v for k,v in f.items() if k!='id'})
        t=turns.get(f['source_turn'])
        if not t or t['role']!='client' or f['quote'] not in t['text'] or f['status']!='CONFIRMED':raise ValueError('Unsupported fact')
    for c in case['approved_claims']:
        if c.get('approved') is not True or not c.get('id') or not c.get('text'):raise ValueError('Unapproved claim')
    return case

def inputs(case):
    common={k:deepcopy(case[k]) for k in ('turns','facts','approved_claims')}
    common['current_turn']=deepcopy(case['turns'][-1])
    current={'turns':deepcopy(common['turns']),'facts':deepcopy(common['facts']),'recommendations':[],
             'sales_context':{'school':case.get('school',''),'course':case.get('course','')}}
    return current,common

async def current_replay(case,outputs):
    """Replay complete raw Analysis objects through the actual production engine."""
    class Replay:
        index=0
        async def analyze(self,s,k):
            if self.index>=len(outputs):raise ValueError('Replay missing correction response')
            raw=outputs[self.index];self.index+=1
            a=LiveAnalysis.model_validate(raw)
            return a,{}
    s,_=inputs(case)
    await process_turn(s,Replay(),{'approved_claims':case['approved_claims']})
    r=s['recommendations'][-1]
    return {'decision':r['strategy']['action'],'reason':r['reason'],'say_to_customer':r['suggestion']},r['validation'],r.get('validation_trace',[])

def minimal_output(value):
    if set(value)!={'decision','reason','say_to_customer'} or not all(isinstance(v,str) for v in value.values()):raise ValueError('Invalid Minimal output')
    if not value['decision'] or not value['reason']:raise ValueError('Missing decision/reason')
    return value

def manifest(model):
    def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    files=sorted((ROOT/'backend').glob('*.py'))+[ROOT/'backend/intelligence_prompt.md',HERE/'minimal_prompt.txt',HERE/'run.py',HERE/'scenarios.json',HERE/'live.py',HERE/'pilot.json',HERE/'multiturn.json',HERE/'journal.py']
    return {'commit':git('rev-parse','HEAD'),'working_tree':git('status','--short'),
            'file_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            'model':model,'generation':{'temperature':0.2,'max_tokens':3000,'thinking':'disabled'},
            'historical_data':'Two original multi-turn conversations unavailable in inspected evaluation artifacts; all bundled cases synthetic.',
            'metrics_policy':'Only imported measured API metrics; offline replay time is not model latency.'}

async def run(out,mode='missing',saved=None,model='unspecified',seed=17,cases_override=None,live_runner=None,resume=False,generation=None,variants=('A','B')):
    import os
    if not live_runner:
        return await _run(out,mode,saved,model,seed,cases_override,live_runner,resume,generation,variants)
    out=Path(out)
    if not resume and out.exists() and any(out.iterdir()):raise ValueError('Output directory must be empty')
    out.mkdir(parents=True,exist_ok=True)
    lock=out/'.run.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(fd,str(os.getpid()).encode())
        return await _run(out,mode,saved,model,seed,cases_override,live_runner,resume,generation,variants)
    except BaseException as exc:
        journal=getattr(live_runner.budget,'journal',None)
        if journal:
            journal.status('interrupted',type(exc).__name__)
        raise
    finally:
        os.close(fd);lock.unlink()


async def _run(out,mode='missing',saved=None,model='unspecified',seed=17,cases_override=None,live_runner=None,resume=False,generation=None,variants=('A','B')):
    out=Path(out)
    if out.exists() and any(p.name!='.run.lock' for p in out.iterdir()) and not resume:raise ValueError('Output directory must be empty')
    out.mkdir(parents=True,exist_ok=True)
    if not variants or len(set(variants))!=len(variants) or set(variants)-{'A','B','C'}:raise ValueError('Invalid variants')
    if 'C' in variants and mode!='missing' and not live_runner:raise ValueError('C requires its runner; no mock substitution')
    cases=[validate(c) for c in (cases_override if cases_override is not None else read(HERE/'scenarios.json'))]
    if len({c['id'] for c in cases})!=len(cases):raise ValueError('Duplicate scenario')
    supplied=read(saved) if saved else []
    keyed={}
    for item in supplied:
        key=(item['scenario_id'],item['variant'])
        if key in keyed or key[0] not in {c['id'] for c in cases} or key[1] not in ('A','B'):raise ValueError('Invalid saved key')
        if item['origin'] not in ('real','mock'):raise ValueError('Invalid response origin')
        keyed[key]=item
    journal=None
    if live_runner:
        from .journal import Journal
        version=manifest(model)
        identity={'commit':version['commit'],'file_hashes':version['file_hashes'],'scenarios':digest(cases),
                  'model':model,'generation':generation or version['generation'],'seed':seed,'variants':list(variants)}
        journal=Journal(out,identity,live_runner.budget.limit,resume)
        live_runner.budget.journal=journal;live_runner.budget.used=journal.data['used']
        write(out/'manifest.json',{**version,'generation':identity['generation'],'scenario_ids':[c['id'] for c in cases]})
    elif resume:
        raise ValueError('Resume requires paid runner and verified journal; use dry-run inspection first')
    results=deepcopy(journal.data['results']) if journal else [];prepared=[]
    for c in cases:
        a,b=inputs(c);case_hash=digest(c)
        prepared.append({'scenario_id':c['id'],'scenario_hash':case_hash,'A':{'session':a,'knowledge':{'approved_claims':c['approved_claims']}},'B':{'system':(HERE/'minimal_prompt.txt').read_text(encoding='utf-8'),'input':b}})
        if 'C' in variants:
            from experiments.hybrid_c.engine import context
            prepared[-1]['C']={'input':context(a,c['approved_claims'])}
        for variant in variants:
            if live_runner:
                if any(r['scenario_id']==c['id'] and r['variant']==variant for r in results):continue
                journal.start([c['id'],variant])
                try:
                    result=await live_runner(c,variant)
                    journal.commit(result)
                    results.append(result)
                except BaseException as exc:
                    journal.status('interrupted',type(exc).__name__)
                    raise
                continue
            item=keyed.get((c['id'],variant))
            record={'scenario_id':c['id'],'variant':variant,'scenario_hash':case_hash,'origin':'missing','status':'missing','output':None,'metrics':None}
            if item:
                if item['scenario_hash']!=case_hash:raise ValueError('Saved scenario mismatch')
                if item['model']!=model or item['generation']!={'temperature':0.2,'max_tokens':3000,'thinking':'disabled'}:raise ValueError('Saved model/settings mismatch')
                record['origin']=item['origin']
                try:
                    if variant=='A':
                        value,validation,trace=await current_replay(c,item['raw_outputs'])
                        record.update(validation=validation,validation_trace=trace)
                        record['status']='blocked' if validation['status']=='blocked' else 'completed'
                    else:value=minimal_output(item['output']);record['status']='completed'
                    record['output']=value
                except (ValueError,KeyError):record['status']='invalid';record['error_code']='invalid_or_incomplete_saved_response'
                if item['origin']=='real':
                    record['metrics']={k:v for k,v in item.get('metrics',{}).items() if k in ('latency_ms','input_tokens','output_tokens','cost_usd') and isinstance(v,(int,float)) and v>=0}
                record['source_id']=item.get('source_id')
            elif mode=='mock':
                record.update(origin='mock',status='completed')
                if variant=='A':
                    state=deepcopy(a);await process_turn(state,DemoProvider(),{'approved_claims':c['approved_claims']})
                    r=state['recommendations'][-1]
                    record.update(output={'decision':r['strategy']['action'],'reason':r['reason'],'say_to_customer':r['suggestion']},validation=r['validation'])
                    if r['validation']['status']=='blocked':record['status']='blocked'
                else:record['output']={'decision':'DISCOVERY','reason':'MOCK: проверка стенда, не оценка качества','say_to_customer':'Что для вас важно в обучении?'}
            results.append(record)
    write(out/'manifest.json',{**manifest(model),'seed':seed,'scenario_ids':[c['id'] for c in cases]})
    write(out/'inputs.json',prepared);write(out/'results.json',results)
    rng=random.Random(seed);blind=[];mapping={}
    for c in cases:
        pair=[r for r in results if r['scenario_id']==c['id']];rng.shuffle(pair)
        for r in pair:
            label='R'+str(len(blind)+1).zfill(3);mapping[label]={'scenario_id':c['id'],'variant':r['variant'],'origin':r['origin'],'status':r['status']}
            blind.append({'response_id':label,'scenario_id':c['id'],'scenario':json.dumps(c,ensure_ascii=False),
                          **(r['output'] or {'decision':'','reason':'','say_to_customer':''}),
                          **{k:'' for k in CRITERIA},'critical_violations':'','diagnostic_category':'','notes':''})
    write(out/'private_mapping.json',mapping)
    with (out/'blind_review.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(blind[0]));writer.writeheader();writer.writerows(blind)
    real=sum(r['origin']=='real' and r['status']=='completed' for r in results)
    write(out/'summary.json',{'cases':len(cases),'real_completed':real,'missing':sum(r['status']=='missing' for r in results),'quality_comparison':'not_performed','expert_scores':None})
    (out/'README.txt').write_text(f'Случаев: {len(cases)}. Реальных завершённых ответов: {real}. Коммерческое сравнение не выполнено: требуется независимая слепая оценка. Пустые ответы — отсутствующие данные, не нулевое качество. Эксперту передавать только blind_review.csv. private_mapping.json хранить отдельно.\n',encoding='utf-8')
    if journal:journal.status('completed' if all(r['status']=='completed' for r in results) else 'completed_with_errors')
    return results

def summarize(out,review):
    out=Path(out);mapping=read(out/'private_mapping.json');scores={v:[] for v in sorted({m['variant'] for m in mapping.values()})};seen=set();critical=[];paired={};categories=[]
    with Path(review).open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            rid=row['response_id']
            if rid not in mapping or rid in seen:raise ValueError('Unknown/duplicate review ID')
            seen.add(rid);meta=mapping[rid]
            category=row.get('diagnostic_category','')
            if category and category not in ('MODEL_DECISION','PROMPT_OR_CONTEXT','VALIDATOR_BLOCK','RECOVERY_DEGRADATION','KNOWLEDGE_GAP','OTHER','UNKNOWN'):raise ValueError('Unknown diagnostic category')
            if category:categories.append({'response_id':rid,'category':category,'status':'expert_hypothesis'})
            values=[row[k] for k in CRITERIA]
            if not any(values):continue
            if meta['origin']!='real' or meta['status']!='completed':raise ValueError('Cannot score missing/mock/blocked response as real quality')
            if not all(v in ('0','1','2','3','4') for v in values):raise ValueError('All seven scores must be 0..4')
            score=dict(zip(CRITERIA,map(int,values)))
            scores[meta['variant']].append(score)
            paired.setdefault(meta['scenario_id'],{})[meta['variant']]=score
            if row['critical_violations']:critical.append({'response_id':rid,'violation':row['critical_violations']})
    result={'reviewed':{v:len(rows) for v,rows in scores.items()},'means':{v:{k:sum(r[k] for r in rows)/len(rows) for k in CRITERIA} if rows else None for v,rows in scores.items()},'critical_violations':critical,'note':'Описательные оценки; превосходство не установлено. Сравнивайте одинаковые парные случаи.'}
    complete=[p for p in paired.values() if {'A','B'}<=set(p)]
    result['diagnostic_categories']=categories
    result['paired_cases']=len(complete)
    result['paired_difference_A_minus_B']={k:sum(p['A'][k]-p['B'][k] for p in complete)/len(complete) for k in CRITERIA} if complete else None
    write(out/'expert_summary.json',result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--mode',choices=['missing','mock'],default='missing');p.add_argument('--saved');p.add_argument('--model',default='unspecified');p.add_argument('--review')
    p.add_argument('--resume',action='store_true');p.add_argument('--pilot',action='store_true');p.add_argument('--multiturn',action='store_true');p.add_argument('--cases',nargs='+');p.add_argument('--allow-paid',action='store_true');p.add_argument('--max-calls',type=int,default=12)
    args=p.parse_args()
    if args.review:return summarize(args.out,args.review)
    if args.pilot and args.multiturn:p.error('Choose pilot or multiturn')
    cases=read(HERE/('pilot.json' if args.pilot else 'multiturn.json' if args.multiturn else 'scenarios.json'))
    if args.cases:
        if set(args.cases)-{c['id'] for c in cases}:p.error('Unknown scenario ID')
        cases=[c for c in cases if c['id'] in args.cases]
    for c in cases:
        validate(c)
        if c['origin']!='synthetic' and args.allow_paid:p.error('Paid runner only accepts bundled synthetic cases')
    if args.max_calls<1:p.error('max-calls must be positive')
    if args.resume and not args.allow_paid:
        path=Path(args.out)/'run_state.json'
        if not path.exists():p.error('Legacy run: no journal/configuration, safe automatic resume unavailable; no API calls made')
        state=read(path);print(json.dumps({'status':state['status'],'used':state['used'],'limit':state['limit'],'active':state['active'],'saved_checkpoints':len(state['results'])},ensure_ascii=False));return
    runner=None;model=args.model;generation={'temperature':.2,'max_tokens':3000,'thinking':'disabled'}
    if args.allow_paid:
        if args.mode!='missing' or args.saved:p.error('Paid execution cannot mix mocks/imports')
        from .live import Budget,configured,make_runner
        import os
        model=configured();generation['max_tokens']=int(os.getenv('LLM_MAX_TOKENS','3000'))
        runner=make_runner(Budget(args.max_calls))
    plan={'dry_run':not args.allow_paid,'scenarios':[c['id'] for c in cases], 'baseline_calls':2*len(cases),'max_engine_calls':3*len(cases),
          'hard_call_limit':args.max_calls,'max_output_tokens_bound':min(args.max_calls,3*len(cases))*generation['max_tokens'],
          'input_token_estimate':None,'cost_estimate':None,
          'limits_note':'Input tokenization and current pricing unavailable; output bound is not total tokens or dollar budget.',
          'comparison_limits':'Fixed checkpoints with equal manually confirmed facts. A reconstructs state from this input, not a continuous prior model session; includes up to one regeneration and local recovery. B one request, no recovery. A/B answer schemas differ. Product claims independently require human review.'}
    print(json.dumps(plan,ensure_ascii=False))
    asyncio.run(run(args.out,args.mode,args.saved,model,cases_override=cases,live_runner=runner,resume=args.resume,generation=generation))
    write(Path(args.out)/'execution_plan.json',plan)
    meta=read(Path(args.out)/'manifest.json');meta['generation']=generation;write(Path(args.out)/'manifest.json',meta)

if __name__=='__main__':main()
