import json
import os
import re
from time import perf_counter
from .diagnostics import timed, add_call
from urllib.parse import urlparse
import httpx
from .models import Analysis, LiveAnalysis, Fact, Advice, LABELS
from .prompts import SYSTEM
from .dialogue_policy import update_state, conversation_signals, spoken_turn_ids


def model_context(session):
    # Keep every spoken turn and memory item, but not growing copies of old plans,
    # timings and validation traces inside recommendations.
    context = {k: v for k, v in session.items() if k not in ('recommendations', 'analysis_error')}
    context['recommendations'] = [
        {**{k: r[k] for k in ('turn_id','action_type','suggestion','strategy','feedback') if k in r},
         'source_type':'ai_suggestion', 'spoken_turn_ids':spoken_turn_ids(session,r)}
        for r in session.get('recommendations', [])[-3:]
    ]
    context['dialogue_source_rule'] = ('Источник истины — turns. recommendations — советы, не произнесённые реплики. '
        'spoken_turn_ids отмечает только дословные совпадения после выдачи совета; перефразирование оценивай по turns. '
        'Если менеджер выбрал другой вопрос, презентацию или аргумент, продолжай фактический разговор, '
        'пересмотри прежний sales_plan по новым ответам, сохраняя инициативу продажи. Feedback не подтверждает произнесение.')
    if session.get('turns'):
        context['dialogue_state'] = update_state(session, [])
        context['conversation_signals'] = conversation_signals({**session, 'dialogue_state':context['dialogue_state']})
    return context

class DemoProvider:
    name = 'demo'

    async def analyze(self, session, knowledge):
        turn = session['turns'][-1]
        text = turn['text'].lower()
        facts = []
        patterns = {
            'profile': r'бухгалтер|аналитик|экономист',
            'experience': r'уже изучал|проходил|умею|пробовал',
            'situation': r'вручную|собираю отч',
            'problem': r'вручную|не получается|сложно|ошибки',
            'consequence': r'час|задержива|теряю|устаю',
            'goal': r'хочу|моя цель|нужно научиться',
            'importance': r'важно|критично',
            'motivation': r'чтобы|ради',
            'urgency': r'месяц|полгода|начать|срок',
            'constraints': r'бюджет|рубл|времени на|минут в',
            'objection': r'дорого|сомневаюсь|не уверен|подумаю',
            'decision': r'готов купить|не интересно|не интересует|не звоните|отказываюсь',
            'next_step': r'договорились|пришлите|созвонимся',
        }
        if turn['role'] == 'client':
            for category, pattern in patterns.items():
                if re.search(pattern, text):
                    old = [f['id'] for f in session['facts'] if f['category'] == category and f['status'] == 'CONFIRMED']
                    facts.append(Fact(category=category, value=turn['text'], quote=turn['text'], source_turn=turn['id'], replaces=old if category in ('urgency', 'decision') else []))
        known = {f['category'] for f in session['facts'] if f['status'] == 'CONFIRMED'} | {f.category for f in facts}
        action, suggestion, reason, gap = 'NO_ACTION', '', 'Дождитесь ответа клиента.', ''
        if turn['role'] == 'client':
            if any(s in text for s in ('не интересно', 'не интересует', 'не звоните', 'отказываюсь')):
                reason = 'Клиент явно отказался. Уважайте его решение и завершите разговор.'
            elif 'objection' in {f.category for f in facts}:
                action, suggestion, reason = 'CLARIFY', 'Дело больше в сумме, условиях оплаты или сомнениях в пользе обучения?', 'Причина возражения ещё не подтверждена; не предлагайте рассрочку автоматически.'
            elif any(f['category'] == 'decision' and 'не интерес' in f['value'].lower() and f['status'] == 'CONFIRMED' for f in session['facts']):
                reason = 'Ранее клиент отказался. Нового согласия пока нет.'
            else:
                questions = [('problem', 'Какую рабочую задачу вы хотите решить с помощью Excel?'), ('consequence', 'Сколько времени уходит на эту задачу и как это влияет на вашу работу?'), ('goal', 'Какой результат обучения был бы для вас полезен?'), ('experience', 'Что вы уже умеете в Excel и что пробовали изучать?'), ('constraints', 'Сколько времени вы готовы выделять на обучение?')]
                for category, question in questions:
                    if category not in known:
                        action, suggestion, reason, gap = 'QUESTION', question, 'В истории пока нет подтверждённых сведений: «' + LABELS[category] + '». Уточните их перед предложением программы.', category
                        break
                else:
                    action, suggestion, reason = 'NEXT_STEP', 'Какой следующий шаг был бы для вас удобен?', 'Основные блоки потребности заполнены. Демо-режим не оценивает соответствие продукта по смыслу.'
        return Analysis(facts=facts, recommendation=Advice(action_type=action, suggestion=suggestion, reason=reason, evidence_ids=[turn['id']], target_gap=gap, confidence=0.45)), {}

class CompatibleProvider:
    name = 'compatible'

    def __init__(self, *, diagnostic_stream=False):
        # Opt-in only: production configuration keeps buffered responses.
        self.diagnostic_stream = diagnostic_stream

    async def analyze(self, session, knowledge):
        model = os.getenv('LLM_MODEL', '')
        if not model:
            raise ValueError('LLM_MODEL is missing')
        headers = {}
        if os.getenv('LLM_API_KEY'):
            headers['Authorization'] = 'Bearer ' + os.environ['LLM_API_KEY']
        response_contract = LiveAnalysis if session.get('turns') else Analysis
        with timed('context_prepare'):
            context = model_context(session)
            payload = {'model': model, 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': json.dumps({'session': context, 'knowledge': knowledge, 'schema': response_contract.model_json_schema()}, ensure_ascii=False)}], 'response_format': {'type': 'json_object'}, 'temperature': 0.2}
            payload['max_tokens'] = int(os.getenv('LLM_MAX_TOKENS', '3000'))
        endpoint = os.environ.get('LLM_BASE_URL', 'http://127.0.0.1:11434/v1').rstrip('/')
        if urlparse(endpoint).hostname == 'api.deepseek.com':
            payload['thinking'] = {'type': 'disabled'}
        if self.diagnostic_stream:
            payload.update(stream=True, stream_options={'include_usage': True})
        data = await self._complete(endpoint, headers, payload)
        try:
            with timed('schema_validation'):
                analysis = response_contract.model_validate_json(data['choices'][0]['message']['content'])
        except ValueError as exc:
            exc.usage = data.get('usage', {})
            raise
        return analysis, data.get('usage', {})

    async def _complete(self, endpoint, headers, payload):
        start = perf_counter()
        call = {'stream': self.diagnostic_stream, 'model': payload['model'],
                'thinking': payload.get('thinking', {}).get('type', 'unspecified'),
                'ttft_ms': None, 'first_content_ms': None, 'content_generation_ms': None}
        add_call(call)
        def elapsed(): return round((perf_counter()-start)*1000, 3)
        try:
            with timed('provider_requests'):
                async with httpx.AsyncClient(timeout=float(os.getenv('LLM_TIMEOUT', '45'))) as client:
                    if not self.diagnostic_stream:
                        response = await client.post(endpoint + '/chat/completions', headers=headers, json=payload)
                        response.raise_for_status()
                        data = response.json()
                        call['finish_reason'] = data['choices'][0].get('finish_reason')
                        call['response_model'] = data.get('model')
                    else:
                        content, usage, finish, done = [], {}, None, False
                        last_content = None
                        async with client.stream('POST', endpoint + '/chat/completions', headers=headers, json=payload) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if not line.startswith('data:'): continue
                                raw = line[5:].strip()
                                if raw == '[DONE]':
                                    done = True
                                    break
                                chunk = json.loads(raw)
                                call['response_model'] = chunk.get('model', call.get('response_model'))
                                if chunk.get('usage'): usage = chunk['usage']
                                for choice in chunk.get('choices', []):
                                    delta = choice.get('delta', {})
                                    if (delta.get('content') or delta.get('reasoning_content')) and call['ttft_ms'] is None:
                                        call['ttft_ms'] = elapsed()
                                    if delta.get('content'):
                                        if call['first_content_ms'] is None: call['first_content_ms'] = elapsed()
                                        content.append(delta['content'])
                                        last_content = elapsed()
                                    if choice.get('finish_reason'): finish = choice['finish_reason']
                        if not done or finish is None:
                            raise ValueError('Incomplete provider stream')
                        call['finish_reason'] = finish
                        if last_content is not None:
                            call['content_generation_ms'] = round(last_content-call['first_content_ms'],3)
                        data = {'choices':[{'message':{'content':''.join(content)},'finish_reason':finish}], 'usage':usage}
            call['status'] = 'ok'
            call['usage'] = data.get('usage', {})
            return data
        except Exception as exc:
            call['status'] = 'error'
            call['error_type'] = type(exc).__name__
            raise
        finally:
            call['request_ms'] = elapsed()


def get_provider():
    name = os.getenv('LLM_PROVIDER', 'demo')
    if name not in ('demo', 'compatible'):
        raise ValueError('LLM_PROVIDER must be demo or compatible')
    return DemoProvider() if name == 'demo' else CompatibleProvider()
