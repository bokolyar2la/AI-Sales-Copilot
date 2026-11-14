from fastapi.testclient import TestClient
from backend.main import create_app
from backend.providers import DemoProvider
from backend.call_flow import scoped_knowledge

def test_new_session_starts_with_spoken_greeting(tmp_path):
    c=TestClient(create_app(tmp_path/'flow.sqlite',DemoProvider()))
    s=c.post('/api/sessions',json={'title':'Звонок','school':'Школа А','course':'Дизайн','manager':'Даниил'}).json()
    assert s['opening']['action_type']=='GREETING'
    assert 'Даниил' in s['opening']['suggestion']
    assert 'Школа А' in s['opening']['suggestion']
    assert not s['facts'] and not s['turns']
    assert c.get('/api/sessions/'+s['id']).json()['sales_context']['course']=='Дизайн'

def test_unknown_school_does_not_inherit_eduson_claims():
    kb={'products':[{'name':'Excel'}],'platform':{'features':['Тренажёры']}}
    result=scoped_knowledge({'sales_context':{'school':'Другая школа','course':'Excel'}},kb)
    assert result['products']==[] and result['platform']=={}
    assert kb['products']

def test_other_program_does_not_inherit_excel_modules():
    kb={'products':[{'name':'Excel'}],'platform':{'features':['Тренажёры']}}
    result=scoped_knowledge({'sales_context':{'school':'Eduson Academy','course':'Управление'}},kb)
    assert result['products']==[]
    assert kb['products']
