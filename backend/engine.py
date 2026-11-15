"""One complete turn, at most one bounded correction, atomic memory commit."""
from copy import deepcopy
from .context import apply_analysis


async def process_turn(session, provider, knowledge):
    usage = {}
    trace = []
    feedback = None
    for attempt in range(2):
        request = deepcopy(session)
        if feedback:
            request['validation_feedback'] = feedback
        try:
            analysis, used = await provider.analyze(request, knowledge)
        except ValueError as exc:
            used = getattr(exc, 'usage', {})
            for key, value in used.items():
                if isinstance(value, (int, float)): usage[key] = usage.get(key, 0) + value
            trace.append({'attempt':attempt+1, 'status':'invalid_schema'})
            if attempt: raise
            feedback = {'issue':'invalid_schema', 'instruction':'Верни полную схему, intelligence обязателен. Не добавляй поля; статусы и типы только из schema.'}
            continue
        for key, value in used.items():
            if isinstance(value, (int, float)): usage[key] = usage.get(key, 0) + value
        draft = deepcopy(session)
        try:
            apply_analysis(draft, analysis, knowledge)
        except ValueError:
            trace.append({'attempt':attempt+1, 'status':'invalid_sources_or_memory_delta'})
            if attempt: raise
            feedback = {'issue':'invalid_sources_or_memory_delta',
                        'instruction':'Проверь цитаты и ID. facts только из последней реплики клиента. replaces — ID факта ТОЙ ЖЕ category, не ID гипотезы. Не заменяй факт другой категории; сохрани его и добавь новый. Для пересмотра гипотезы используй её id в intelligence.hypotheses.'}
            continue
        validation = draft['recommendations'][-1]['validation']
        trace.append({'attempt':attempt+1, **validation})
        if validation['status'] == 'blocked' and attempt == 0:
            feedback = {'issue':'blocked_phrase', 'checks':validation['issues'],
                        'instruction':'Исправь фразу и согласуй стратегию с action_type. Не повторяй известный ответ, не утверждай неподтверждённые условия. Не заменяй полезный вопрос молчанием, если можно задать другой уместный вопрос.'}
            continue
        draft['recommendations'][-1]['validation_trace'] = trace
        usage['request_count'] = attempt+1
        session.clear()
        session.update(draft)
        return analysis, usage
    raise ValueError('No valid analysis')
