"""One complete turn, at most one bounded correction, atomic memory commit."""
from copy import deepcopy
from .context import apply_analysis
from .diagnostics import capture, timed, snapshot, record_validation, validation_failure


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
            trace.append({'status':'invalid_schema', **validation_failure(exc, 'schema', attempt+1)})
            record_validation(trace[-1])
            if attempt: raise
            errors = [{'path':list(e['loc']), 'type':e['type']} for e in exc.errors(include_input=False)][:12] if hasattr(exc,'errors') else []
            feedback = {'issue':'invalid_schema', 'fields':errors, 'instruction':'Исправь перечисленные поля строго по schema. facts — изменения без id и created_at; ID назначает приложение. intelligence.sales_plan и intelligence.sales_plan.need_depth обязательны. need_depth и priority_thread размещаются только внутри intelligence.sales_plan, не непосредственно в intelligence. Не копируй служебные поля памяти в ответ.'}
            continue
        for key, value in used.items():
            if isinstance(value, (int, float)): usage[key] = usage.get(key, 0) + value
        draft = deepcopy(session)
        try:
            with timed('validation'):
                apply_analysis(draft, analysis, knowledge)
        except ValueError as exc:
            trace.append({'status':'invalid_sources_or_memory_delta', **validation_failure(exc, 'sources_memory', attempt+1)})
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
                        'instruction':'Исправь фразу, план и стратегию вместе. При known_novice_false_choice клиент уже сообщил начальный уровень; не предлагай ему выбирать основы или автоматизацию. Выбери полезный шаг по актуальным мотивам, препятствиям и уже данным ответам. При develop_need_before_offer развивай актуальную нераскрытую мотивацию/желаемое изменение; отсутствие базы не повод прерывать разговор. Не меняй оценку глубины без новых оснований лишь для обхода проверки. При dismissed_repeat не повторяй отклонённый совет: выбери полезный следующий шаг по реально произнесённым репликам менеджера и новым ответам клиента. При offer_ready_stop_discovery/promised_offer_due/client_declined_diagnostics переходи к предложению без нового диагностического вопроса. При explain_and_limit_clarification допускается один критичный вопрос с объяснением через ALIGN_BEFORE_PRESENTATION. Нехватка базы — manager_note, а не проблема клиента. Не заменяй полезный ответ молчанием.'}
            if 'need_decision' in validation:
                feedback['need_decision'] = validation['need_decision']
            if 'unanswered_objections' in validation:
                feedback['unanswered_objections'] = validation['unanswered_objections']
                feedback['instruction'] += ' При unresolved_objection_before_exit отработай указанное сомнение; не обходи проверку сменой action_type/readiness или удалением факта/события без подтверждения клиента.'
            if 'strategy_compatibility' in validation:
                feedback['strategy_compatibility'] = validation['strategy_compatibility']
                feedback['instruction'] += ' При strategy_phrase_mismatch согласуй тип фразы и стратегию по допустимой таблице; strategy_compatibility содержит ошибочную пару и допустимые стратегии. Сохрани полезный смысл продолжения диалога.'
            continue
        if validation['status']=='blocked' and attempt==1:
            recovered = recover_question(session, draft, analysis, knowledge)
            if recovered:
                draft, analysis = recovered
                trace.append({'attempt':'local_recovery', **draft['recommendations'][-1]['validation']})
            else:
                draft['recommendations'][-1]['reason'] += ' Безопасное восстановление недоступно: нет неотвеченного подтверждённого основания для резервного вопроса. Причины: ' + ', '.join(validation['issues'])
        draft['recommendations'][-1]['validation_trace'] = trace
        usage['request_count'] = attempt+1
        session.clear()
        session.update(draft)
        return analysis, usage
    raise ValueError('No valid analysis')


def recover_question(session, draft, analysis, knowledge):
    """One narrow local recovery for stated fatigue, always through all validators."""
    from .models import QuestionValue
    from .dialogue_policy import conversation_signals
    import re
    signals = conversation_signals(draft)
    state = draft.get('dialogue_state', {})
    if session['turns'][-1]['role']!='client' or signals['offer_requested'] or signals['client_declines_questions'] or state.get('ready_to_buy') or state.get('outcome'):
        return None
    facts = [f for f in draft.get('facts',[]) if f['status']=='CONFIRMED']
    fatigue = next((f for f in facts if f['category'] in ('problem','constraints','situation')
                    and re.search('уста|устал', f['quote'], re.I)), None)
    if not fatigue or any(re.search(r'час|минут|в неделю|времени[^.!?]*обуч', f['value'], re.I) for f in facts):
        return None
    if any(q['topic'] in ('time','study_time','constraints') for q in state.get('questions', [])):
        return None
    candidate = analysis.model_copy(deep=True)
    if not candidate.intelligence or not candidate.intelligence.sales_plan:
        return None
    candidate.recommendation.action_type='QUESTION'
    candidate.recommendation.suggestion='С учётом усталости после работы, сколько времени в неделю вы реально могли бы выделять на обучение?'
    candidate.recommendation.reason='Клиент сообщил об усталости; доступное время поможет оценить посильную нагрузку.'
    candidate.recommendation.evidence_ids=[fatigue['source_turn']]
    candidate.recommendation.target_gap='study_time'
    candidate.intelligence.strategy.action='DISCOVERY'
    candidate.intelligence.strategy.reason=candidate.recommendation.reason
    candidate.intelligence.strategy.objective='Оценить посильную нагрузку'
    candidate.intelligence.strategy.evidence_ids=[fatigue['source_turn']]
    candidate.intelligence.product_claims=[]
    plan=candidate.intelligence.sales_plan
    plan.readiness='NEEDS_CLARIFICATION'
    plan.question=QuestionValue(topic='study_time',new_information='Доступное время',decision_impact='Посильная нагрузка',already_answered=False,changes_selection=True,changes_argument=True,essential_now=False)
    plan.after_answer_action='DISCOVERY'
    plan.after_answer_goal='Сопоставить нагрузку с задачей клиента'
    restored=deepcopy(session)
    try:
        apply_analysis(restored,candidate,knowledge)
    except ValueError:
        return None
    if restored['recommendations'][-1]['validation']['status']=='blocked':
        return None
    return restored,candidate
