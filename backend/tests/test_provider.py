import asyncio
import json
import httpx
import pytest
from backend.providers import CompatibleProvider

def test_compatible_request_and_output(monkeypatch):
    monkeypatch.setenv('LLM_MODEL', 'test-model')
    monkeypatch.setenv('LLM_BASE_URL', 'http://model.local/v1')
    monkeypatch.setenv('LLM_API_KEY', 'test-only-key')
    def handler(request):
        assert str(request.url) == 'http://model.local/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-only-key'
        payload = json.loads(request.content)
        assert payload['model'] == 'test-model'
        assert 'schema' in payload['messages'][1]['content']
        result = {'facts': [], 'recommendation': {'action_type':'NO_ACTION','suggestion':'','reason':'Ждём ответа','evidence_ids':[],'target_gap':'','confidence':.8}}
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(result)}}], 'usage':{'total_tokens':42}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    analysis, usage = asyncio.run(CompatibleProvider().analyze({'turns':[]}, []))
    assert analysis.recommendation.reason == 'Ждём ответа'
    assert usage['total_tokens'] == 42

def test_invalid_json_is_not_accepted(monkeypatch):
    monkeypatch.setenv('LLM_MODEL', 'test')
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200,json={'choices':[{'message':{'content':'не JSON'}}]}))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(ValueError):
        asyncio.run(CompatibleProvider().analyze({}, []))
