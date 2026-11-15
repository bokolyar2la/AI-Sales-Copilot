"""Explicit opt-in experiment server. Temporary database; synthetic checkpoints only.
Run COPILOT_LATENCY_EXPERIMENT=1 and uvicorn evaluation.latency_server:app --port 8877.
"""
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import backend.main as application
from backend.main import create_app
from backend.providers import CompatibleProvider
from backend.storage import Store
from backend.context import now
from backend.models import CATEGORIES
from backend.dialogue_policy import initial_state
from .latency_cases import CASES, CATALOG

if os.getenv('COPILOT_LATENCY_EXPERIMENT')!='1':
    raise RuntimeError('Explicit experiment opt-in required')
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT.parents[1]/'work'/'latency-experiment.sqlite'
store=Store(DB)


class ControlledProvider:
    name='compatible'
    async def analyze(self,session,knowledge):
        config=session['experiment']
        clean={k:v for k,v in session.items() if k!='experiment'}
        return await CompatibleProvider(diagnostic_stream=config['mode']=='stream_off').analyze(clean,knowledge)


class Setup(BaseModel):
    case:str
    mode:str


# Fixture injection is confined to this opt-in process; engine and validator
# receive exactly the same catalog as the model. No production file is edited.
def synthetic_knowledge(session, ignored):
    case=next(c for c in CASES if c['id']==session['experiment']['case'])
    return deepcopy(CATALOG) if case.get('catalog',True) else {}
application.scoped_knowledge=synthetic_knowledge

app=FastAPI()
@app.post('/experiment/setup')
def setup(body:Setup):
    if body.mode not in ('current','stream_off'):raise HTTPException(400)
    case=next((c for c in CASES if c['id']==body.case),None)
    if not case:raise HTTPException(404)
    s={'id':str(uuid4()),'title':case['id'], 'created_at':now(),'version':0,'closed':False,
       'turns':[],'facts':[],'hypotheses':[],'recommendations':[],'gaps':CATEGORIES.copy(),
       'stage':'Начало','analysis_error':None,'dialogue_state':initial_state(),
       'sales_context':{'school':'СИНТЕТИЧЕСКАЯ ШКОЛА','course':'Excel','manager':''},
       'experiment':{'case':case['id'],'mode':body.mode}}
    for i,(role,text) in enumerate(case.get('history',[]),1):
        s['turns'].append({'id':'t'+str(i),'role':role,'text':text,'request_id':'seed'+str(i),'created_at':now()})
    if case['id']=='bridge_answer':
        s['dialogue_state']['events']=[{'kind':'offer_bridge_delivered','source_turn':'t2','quote':s['turns'][-1]['text'],'topic':'level'}]
    store.save(s)
    return {'id':s['id'],'text':case['text']}

app.mount('/',create_app(DB,ControlledProvider(),diagnostic_origin='http://127.0.0.1:8877'))
