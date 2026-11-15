"""One complete turn, at most one bounded correction, atomic memory commit."""
from copy import deepcopy
from .context import apply_analysis
from .diagnostics import capture, timed, snapshot, record_validation


async def process_turn(session, provider, knowledge):
    with capture() as trace:
        with timed('engine'):
            result = await _process_turn(session, provider, knowledge)
        session['recommendations'][-1]['diagnostics'] = snapshot(trace)
        return result


async def _process_turn(session, provider, knowledge):
    usage = {}
    trace = []
    feedback = None
    for attempt in range(2):
        with timed('context_copy'):
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
            record_validation(trace[-1])
            if attempt: raise
            errors = [{'path':list(e['loc']), 'type':e['type']} for e in exc.errors(include_input=False)][:12] if hasattr(exc,'errors') else []
            feedback = {'issue':'invalid_schema', 'fields':errors, 'instruction':'Исправь перечисленные поля строго по schema. facts — изменения без id и created_at; ID назначает приложение. intelligence, sales_plan и sales_plan.need_depth обязательны. Не копируй служебные поля памяти в ответ.'}
            continue
        for key, value in used.items():
            if isinstance(value, (int, float)): usage[key] = usage.get(key, 0) + value
        draft = deepcopy(session)
        try:
            with timed('validation'):
                apply_analysis(draft, analysis, knowledge)
        except ValueError as exc:
            trace.append({'attempt':attempt+1, 'status':'invalid_sources_or_memory_delta'})
            record_validation(trace[-1])
            if attempt: raise
            feedback = {'issue':'invalid_sources_or_memory_delta', 'check':str(exc), 'latest_turn':session['turns'][-1],
                        'instruction':'facts и events — только из последней реплики; quote точная непрерывная подстрока, без изменения пунктуации. Если последняя реплика менеджера, facts=[] и hypotheses=[]. replaces — ID факта ТОЙ ЖЕ category, не ID гипотезы. Пересмотр гипотезы через intelligence.hypotheses. Проверяй роль источника.'}
            continue
        validation = draft['recommendations'][-1]['validation']
        trace.append({'attempt':attempt+1, **validation})
        record_validation(trace[-1])
        if validation['status'] == 'blocked' and attempt == 0:
            feedback = {'issue':'blocked_phrase', 'checks':validation['issues'],
                        'previous_phrase':analysis.recommendation.suggestion,
                        'instruction':'Исправь фразу, план и стратегию вместе. При develop_need_before_offer развивай актуальную нераскрытую мотивацию/желаемое изменение; отсутствие базы не повод прерывать разговор. Не меняй оценку глубины без новых оснований лишь для обхода проверки. При dismissed_repeat не повторяй отклонённый совет: выбери полезный следующий шаг по реально произнесённым репликам менеджера и новым ответам клиента. При offer_ready_stop_discovery/promised_offer_due/client_declined_diagnostics переходи к предложению без нового диагностического вопроса. При explain_and_limit_clarification допускается один критичный вопрос с объяснением через ALIGN_BEFORE_PRESENTATION. Нехватка базы — manager_note, а не проблема клиента. Не заменяй полезный ответ молчанием.'}
            if 'unanswered_objections' in validation:
                feedback['unanswered_objections'] = validation['unanswered_objections']
                feedback['instruction'] += ' При unresolved_objection_before_exit отработай указанное сомнение; не обходи проверку сменой action_type/readiness или удалением факта/события без подтверждения клиента.'
            if 'strategy_compatibility' in validation:
                feedback['strategy_compatibility'] = validation['strategy_compatibility']
                feedback['instruction'] += ' При strategy_phrase_mismatch согласуй тип фразы и стратегию по допустимой таблице; strategy_compatibility содержит ошибочную пару и допустимые стратегии. Сохрани полезный смысл продолжения диалога.'
            continue
        draft['recommendations'][-1]['validation_trace'] = trace
        usage['request_count'] = attempt+1
        session.clear()
        session.update(draft)
        return analysis, usage
    raise ValueError('No valid analysis')
