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
            errors = [{'path':list(e['loc']), 'type':e['type']} for e in exc.errors(include_input=False)][:12] if hasattr(exc,'errors') else []
            feedback = {'issue':'invalid_schema', 'fields':errors, 'instruction':'Исправь перечисленные поля строго по schema. facts — изменения без id и created_at; ID назначает приложение. intelligence и sales_plan обязательны. Не копируй служебные поля памяти в ответ.'}
            continue
        for key, value in used.items():
            if isinstance(value, (int, float)): usage[key] = usage.get(key, 0) + value
        draft = deepcopy(session)
        try:
            apply_analysis(draft, analysis, knowledge)
        except ValueError as exc:
            trace.append({'attempt':attempt+1, 'status':'invalid_sources_or_memory_delta'})
            if attempt: raise
            feedback = {'issue':'invalid_sources_or_memory_delta', 'check':str(exc), 'latest_turn':session['turns'][-1],
                        'instruction':'facts и events — только из последней реплики; quote точная непрерывная подстрока, без изменения пунктуации. Если последняя реплика менеджера, facts=[] и hypotheses=[]. replaces — ID факта ТОЙ ЖЕ category, не ID гипотезы. Пересмотр гипотезы через intelligence.hypotheses. Проверяй роль источника.'}
            continue
        validation = draft['recommendations'][-1]['validation']
        trace.append({'attempt':attempt+1, **validation})
        if validation['status'] == 'blocked' and attempt == 0:
            feedback = {'issue':'blocked_phrase', 'checks':validation['issues'],
                        'previous_phrase':analysis.recommendation.suggestion,
                        'instruction':'Исправь фразу, план и стратегию вместе. При offer_ready_stop_discovery/promised_offer_due/client_declined_diagnostics переходи к предложению без нового диагностического вопроса. При explain_and_limit_clarification допускается один критичный вопрос с объяснением через ALIGN_BEFORE_PRESENTATION. Нехватка базы — manager_note, а не проблема клиента. Не заменяй полезный ответ молчанием.'}
            continue
        draft['recommendations'][-1]['validation_trace'] = trace
        usage['request_count'] = attempt+1
        session.clear()
        session.update(draft)
        return analysis, usage
    raise ValueError('No valid analysis')
