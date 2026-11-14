import asyncio
import json
import os
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from .models import NewSession, TurnInput, Feedback, CATEGORIES, LABELS
from .storage import Store
from .providers import get_provider
from .context import uid, now, apply_analysis
from .prompts import VERSION
from .configure_llm import error_message
from .call_flow import opening, scoped_knowledge

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

def create_app(database=None, provider=None):
    app = FastAPI(title='AI Sales Copilot Alpha', version='0.1.0')
    store = Store(database or ROOT / os.getenv('DATABASE_PATH', 'data/copilot.sqlite3'))
    engine = provider or get_provider()
    lock = asyncio.Lock()  # Single local process, serialized state mutations.
    knowledge = json.loads((ROOT / 'backend/knowledge.json').read_text(encoding='utf-8'))

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost', 'testserver'):
            return JSONResponse({'detail': 'Только локальный доступ.'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin not in ('http://127.0.0.1:8765', 'http://localhost:8765', 'http://localhost:5173', 'http://127.0.0.1:5173'):
            return JSONResponse({'detail': 'Недопустимый источник запроса.'}, status_code=403)
        return await call_next(request)

    def get(sid):
        s = store.get(sid)
        if not s:
            raise HTTPException(404, 'Сессия не найдена.')
        return s

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'provider': engine.name, 'prompt_version': VERSION}

    @app.get('/api/config')
    def config():
        return {'provider': engine.name, 'labels': LABELS, 'knowledge': knowledge}

    @app.get('/api/scenarios')
    def scenarios():
        return json.loads((ROOT / 'evaluation/scenarios.json').read_text(encoding='utf-8'))

    @app.get('/api/sessions')
    def sessions():
        return store.list()

    @app.post('/api/sessions', status_code=201)
    async def new_session(body: NewSession):
        async with lock:
            s = {'id': uid(), 'title': body.title, 'created_at': now(), 'version': 0, 'closed': False, 'turns': [], 'facts': [], 'recommendations': [], 'gaps': CATEGORIES.copy(), 'stage': 'Начало', 'analysis_error': None}
            s['sales_context'] = {'school': body.school, 'course': body.course, 'manager': body.manager}
            s['opening'] = opening(s)
            store.save(s)
            return s

    @app.get('/api/sessions/{sid}')
    def session(sid: str):
        return get(sid)

    @app.post('/api/sessions/{sid}/turns')
    async def turn(sid: str, body: TurnInput):
        async with lock:
            s = get(sid)
            previous = next((t for t in s['turns'] if t['request_id'] == body.request_id), None)
            if previous:
                if previous['text'] != body.text or previous['role'] != body.role:
                    raise HTTPException(409, 'Повторный идентификатор с другим содержимым.')
                return s
            if s['closed']:
                raise HTTPException(409, 'Сессия завершена. Создайте новую.')
            if body.expected_version != s['version']:
                raise HTTPException(409, 'Сессия изменилась. Откройте её заново и повторите ввод.')
            if len(s['turns']) >= 200:
                raise HTTPException(400, 'Лимит Alpha: 200 реплик на сессию.')
            s['turns'].append({'id': uid(), 'role': body.role, 'text': body.text, 'request_id': body.request_id, 'created_at': now()})
            s['version'] += 1
            s['analysis_error'] = None
            start = time.perf_counter()
            try:
                analysis, usage = await engine.analyze(s, scoped_knowledge(s, knowledge))
                apply_analysis(s, analysis)
                s['recommendations'][-1].update(provider=engine.name, latency_ms=round((time.perf_counter() - start) * 1000), usage=usage, prompt_version=VERSION)
            except Exception as exc:
                # Never return provider response bodies or credentials to the browser.
                s['analysis_error'] = 'Реплика сохранена. ' + error_message(exc)
            store.save(s)
            return s

    @app.post('/api/sessions/{sid}/retry')
    async def retry(sid: str):
        async with lock:
            s = get(sid)
            if not s['analysis_error'] or not s['turns']:
                return s
            try:
                analysis, _ = await engine.analyze(s, scoped_knowledge(s, knowledge))
                apply_analysis(s, analysis)
                s['analysis_error'] = None
                s['version'] += 1
            except Exception as exc:
                raise HTTPException(502, error_message(exc))
            store.save(s)
            return s

    @app.post('/api/sessions/{sid}/recommendations/{rid}/feedback')
    async def feedback(sid: str, rid: str, body: Feedback):
        async with lock:
            s = get(sid)
            rec = next((r for r in s['recommendations'] if r['id'] == rid), None)
            if not rec:
                raise HTTPException(404, 'Рекомендация не найдена.')
            rec['feedback'] = body.value
            store.save(s)
            return s

    @app.post('/api/sessions/{sid}/close')
    async def close(sid: str):
        async with lock:
            s = get(sid)
            s['closed'] = True
            store.save(s)
            return s

    @app.get('/api/sessions/{sid}/summary')
    def summary(sid: str):
        s = get(sid)
        return {'title': s['title'], 'fields': {LABELS[c]: [{'value': f['value'], 'source_turn': f['source_turn']} for f in s['facts'] if f['category'] == c and f['status'] == 'CONFIRMED'] or 'Неизвестно' for c in CATEGORIES}, 'unresolved': [LABELS[c] for c in s['gaps']], 'note': 'Сводка подтверждённых фактов, не экспертная оценка менеджера.'}

    @app.delete('/api/sessions/{sid}', status_code=204)
    async def delete(sid: str):
        async with lock:
            get(sid)
            store.delete(sid)

    dist = ROOT / 'frontend/dist'
    if dist.exists():
        app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    return app

app = create_app()
