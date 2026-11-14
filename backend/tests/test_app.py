import json
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.providers import DemoProvider
from backend.models import Analysis, Advice, Fact

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'test.sqlite', DemoProvider())) as c:
        yield c

def new(client):
    return client.post('/api/sessions', json={'title': 'Тест Excel'}).json()

def send(client, s, text, role='client', **extra):
    payload = {'role': role, 'text': text, 'request_id': str(uuid4()), 'expected_version': s['version'], **extra}
    return client.post(f"/api/sessions/{s['id']}/turns", json=payload)

def test_memory_persistence_and_delete(tmp_path):
    path = tmp_path / 'db.sqlite'
    c = TestClient(create_app(path, DemoProvider()))
    s = send(c, new(c), 'Я бухгалтер, собираю отчёты вручную.').json()
    assert any(f['category'] == 'profile' for f in s['facts'])
    assert all(f['source_turn'] == s['turns'][0]['id'] for f in s['facts'])
    c2 = TestClient(create_app(path, DemoProvider()))
    assert c2.get('/api/sessions/' + s['id']).json() == s
    assert c2.delete('/api/sessions/' + s['id']).status_code == 204
    assert c2.get('/api/sessions/' + s['id']).status_code == 404

def test_manager_not_evidence(client):
    s = send(client, new(client), 'Вы бухгалтер и хотите отчёты?', 'manager').json()
    assert not s['facts']
    assert s['recommendations'][-1]['action_type'] == 'NO_ACTION'

def test_changed_dates_preserve_history(client):
    s = send(client, new(client), 'Начать в следующем месяце.').json()
    s = send(client, s, 'Отложу на полгода.').json()
    assert [f['status'] for f in s['facts'] if f['category'] == 'urgency'] == ['OUTDATED', 'CONFIRMED']

def test_idempotency_and_version(client):
    s = new(client)
    key = str(uuid4())
    first = send(client, s, 'Я бухгалтер', request_id=key).json()
    assert send(client, s, 'Я бухгалтер', request_id=key).json() == first
    assert send(client, s, 'Другое', request_id=key).status_code == 409
    assert send(client, s, 'Ещё реплика').status_code == 409

def test_invalid_input(client):
    s = new(client)
    assert send(client, s, '   ').status_code == 422
    assert send(client, s, 'Текст', role='system').status_code == 422
    assert send(client, s, 'x' * 6001).status_code == 422

def test_refusal(client):
    s = send(client, new(client), 'Не интересно, не звоните.').json()
    assert s['recommendations'][-1]['action_type'] == 'NO_ACTION'

def test_objection_is_diagnosed(client):
    s = send(client, new(client), 'Дорого, подумаю.').json()
    assert s['recommendations'][-1]['action_type'] == 'CLARIFY'
    assert 'рассроч' not in s['recommendations'][-1]['suggestion']

def test_feedback_suppresses_repeat(client):
    s = send(client, new(client), 'Здравствуйте').json()
    rid = s['recommendations'][-1]['id']
    client.post(f"/api/sessions/{s['id']}/recommendations/{rid}/feedback", json={'value': 'rejected'})
    s = send(client, s, 'Слушаю вас').json()
    assert s['recommendations'][-1]['action_type'] == 'NO_ACTION'

def test_model_failure_preserves_turn(tmp_path):
    class Broken:
        name = 'broken'
        async def analyze(self, *_):
            raise RuntimeError('secret-api-key')
    c = TestClient(create_app(tmp_path / 'db', Broken()))
    s = send(c, new(c), 'Хочу учиться').json()
    assert len(s['turns']) == 1 and s['analysis_error']
    assert 'secret' not in json.dumps(s)
    assert not s['recommendations']

def test_invalid_fact_rejected_atomically(tmp_path):
    class Invalid:
        name = 'invalid'
        async def analyze(self, s, _):
            return Analysis(facts=[Fact(category='goal', value='Выдумка', quote='несуществующая цитата', source_turn=s['turns'][-1]['id'])], recommendation=Advice(action_type='NO_ACTION', suggestion='', reason='Пауза', evidence_ids=[], target_gap='', confidence=.5)), {}
    c = TestClient(create_app(tmp_path / 'db', Invalid()))
    s = send(c, new(c), 'Здравствуйте').json()
    assert s['analysis_error'] and not s['facts']

def test_close_and_summary(client):
    s = new(client)
    assert client.get(f"/api/sessions/{s['id']}/summary").json()['fields']['Профиль'] == 'Неизвестно'
    client.post(f"/api/sessions/{s['id']}/close")
    assert send(client, s, 'Новая реплика').status_code == 409

def test_cross_origin_blocked(client):
    assert client.post('/api/sessions', headers={'Origin': 'https://evil.test'}, json={'title': 'bad'}).status_code == 403

def test_full_excel_dialogue(client):
    scenario = json.loads((Path(__file__).parents[2] / 'evaluation/scenarios.json').read_text(encoding='utf-8'))[0]
    s = new(client)
    for turn in scenario['turns']:
        s = send(client, s, turn['text'], turn['role']).json()
        assert not s['analysis_error']
    assert len(s['turns']) == 18
    assert {'profile', 'goal', 'consequence', 'next_step'} <= {f['category'] for f in s['facts']}
    assert len(s['recommendations']) == 18
