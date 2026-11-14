import json
import os
import re
import httpx
from .models import Analysis, Fact, Advice, LABELS
from .prompts import SYSTEM

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
                    action, suggestion, reason = 'NEXT_STEP', 'Кратко подтвердите задачу клиента и согласуйте следующий удобный шаг.', 'Основные блоки потребности заполнены. Демо-режим не оценивает соответствие продукта по смыслу.'
        return Analysis(facts=facts, recommendation=Advice(action_type=action, suggestion=suggestion, reason=reason, evidence_ids=[turn['id']], target_gap=gap, confidence=0.45)), {}

class CompatibleProvider:
    name = 'compatible'

    async def analyze(self, session, knowledge):
        model = os.getenv('LLM_MODEL', '')
        if not model:
            raise ValueError('LLM_MODEL is missing')
        headers = {}
        if os.getenv('LLM_API_KEY'):
            headers['Authorization'] = 'Bearer ' + os.environ['LLM_API_KEY']
        payload = {'model': model, 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': json.dumps({'session': session, 'knowledge': knowledge, 'schema': Analysis.model_json_schema()}, ensure_ascii=False)}], 'response_format': {'type': 'json_object'}, 'temperature': 0.2}
        async with httpx.AsyncClient(timeout=float(os.getenv('LLM_TIMEOUT', '45'))) as client:
            response = await client.post(os.environ.get('LLM_BASE_URL', 'http://127.0.0.1:11434/v1').rstrip('/') + '/chat/completions', headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return Analysis.model_validate_json(data['choices'][0]['message']['content']), data.get('usage', {})

def get_provider():
    name = os.getenv('LLM_PROVIDER', 'demo')
    if name not in ('demo', 'compatible'):
        raise ValueError('LLM_PROVIDER must be demo or compatible')
    return DemoProvider() if name == 'demo' else CompatibleProvider()
