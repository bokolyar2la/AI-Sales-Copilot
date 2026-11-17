"""Explicitly authorized ABC only. Offline by default."""
import argparse,asyncio
from pathlib import Path
from contextlib import nullcontext
from experiments.sales_turning_points.p23 import prepare,sha,ROOT
from experiments.sales_turning_points.p23_safety import HISTORICAL_WARNING,experiment_registration
from experiments.sales_benchmark.live import Budget,configured
from experiments.sales_benchmark.run import run
from .engine import make_runner

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path)
    p.add_argument('--allow-paid',action='store_true');p.add_argument('--allow-private-transcripts',action='store_true')
    p.add_argument('--resume',action='store_true');p.add_argument('--authorize-repeat');p.add_argument('--max-calls',type=int,default=25)
    a=p.parse_args();private=ROOT/'work/p25-private'
    if not a.out.resolve().is_relative_to(private.resolve()):p.error('Use work/p25-private output')
    private.mkdir(exist_ok=True);(private/'.gitignore').write_text('*\n',encoding='ascii')
    if not 1<=a.max_calls<=25:p.error('Maximum 25 calls')
    if a.resume and not a.allow_paid:p.error('Resume needs explicit paid permission')
    cases=prepare();runner=None;model='not_called'
    import os
    generation={'temperature':.2,'thinking':'disabled','max_tokens':3000,'hybrid_files':{str(f.name):sha(f) for f in Path(__file__).parent.glob('*') if f.is_file()}}
    if a.allow_paid:
        if not a.allow_private_transcripts:p.error('Private transcript authorization required')
        model=configured()
        generation['max_tokens']=int(os.getenv('LLM_MAX_TOKENS','3000'))
        if not 1<=generation['max_tokens']<=3000:p.error('Output token ceiling: 3000')
        runner=make_runner(Budget(a.max_calls),HISTORICAL_WARNING)
    with experiment_registration(private,a.out,a.resume,a.authorize_repeat) if runner else nullcontext():
        asyncio.run(run(a.out,cases_override=cases,model=model,live_runner=runner,resume=a.resume,generation=generation,variants=('A','B','C')))
    print('Offline preparation only' if not runner else 'Finished; inspect summary.json. Maximum 25 calls including corrections.')
if __name__=='__main__':main()
