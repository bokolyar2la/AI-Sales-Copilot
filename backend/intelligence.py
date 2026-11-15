"""Small, auditable memory delta and strategy validation for iteration 1."""
from copy import deepcopy
from datetime import datetime, timezone
import re
from uuid import uuid4
from .dialogue_policy import conversation_signals

STRATEGY_LABELS = {
    'DISCOVERY': 'Выявление задачи', 'DEEP_DISCOVERY': 'Уточнение ситуации',
    'NEED_DEVELOPMENT': 'Помочь сформулировать потребность', 'RAPPORT': 'Установить контакт',
    'SALES_RECOVERY': 'Вернуть конструктивный диалог', 'SUMMARIZE': 'Подтвердить понимание',
    'PRESENTATION': 'Презентация', 'VALUE_BUILDING': 'Объяснить пользу',
    'OBJECTION_DIAGNOSIS': 'Выяснить причину сомнения', 'OBJECTION_HANDLING': 'Ответить на сомнение',
    'PRICE_PRESENTATION': 'Ответить о стоимости', 'CLOSING': 'Перейти к оформлению',
    'FOLLOW_UP': 'Согласовать следующий шаг', 'ANSWER': 'Ответить на вопрос', 'NO_ACTION': 'Дать клиенту ответить'}
STRATEGY_LABELS.update(EXPLAIN_PROCESS='Объяснить цель консультации', RETURN_TO_NEED='Вернуться к значимой задаче',
    EXPLORE_MOTIVATION='Уточнить мотив', EXPLORE_CONSEQUENCE='Понять последствия',
    ALIGN_BEFORE_PRESENTATION='Одно уточнение перед предложением', SELECT_SOLUTION='Подобрать решение')
ALLOWED = {
    'GREETING': {'RAPPORT'}, 'AGENDA': {'RAPPORT', 'SALES_RECOVERY'},
    'INTEREST': {'DISCOVERY', 'NEED_DEVELOPMENT', 'SALES_RECOVERY'},
    'QUESTION': {'DISCOVERY', 'DEEP_DISCOVERY', 'NEED_DEVELOPMENT', 'SALES_RECOVERY', 'OBJECTION_DIAGNOSIS'},
    'CLARIFY': {'DISCOVERY', 'DEEP_DISCOVERY', 'NEED_DEVELOPMENT', 'SALES_RECOVERY', 'OBJECTION_DIAGNOSIS', 'OBJECTION_HANDLING', 'FOLLOW_UP'},
    'ANSWER': {'ANSWER', 'PRICE_PRESENTATION', 'OBJECTION_HANDLING', 'VALUE_BUILDING', 'FOLLOW_UP', 'CLOSING'},
    'SUMMARIZE': {'SUMMARIZE'}, 'PRESENT': {'PRESENTATION', 'VALUE_BUILDING'},
    'NEXT_STEP': {'CLOSING', 'FOLLOW_UP'}, 'NO_ACTION': {'NO_ACTION'}}
DEFAULT = {'GREETING':'RAPPORT', 'AGENDA':'RAPPORT', 'INTEREST':'DISCOVERY', 'QUESTION':'DISCOVERY',
           'CLARIFY':'OBJECTION_DIAGNOSIS', 'ANSWER':'ANSWER', 'SUMMARIZE':'SUMMARIZE',
           'PRESENT':'PRESENTATION', 'NEXT_STEP':'FOLLOW_UP', 'NO_ACTION':'NO_ACTION'}
DIAGNOSTIC = {'DISCOVERY','DEEP_DISCOVERY','NEED_DEVELOPMENT','RETURN_TO_NEED','EXPLORE_MOTIVATION',
              'EXPLORE_CONSEQUENCE','ALIGN_BEFORE_PRESENTATION'}
for action in ('QUESTION','CLARIFY','ANSWER'):
    ALLOWED[action].update({'EXPLAIN_PROCESS','RETURN_TO_NEED','EXPLORE_MOTIVATION','EXPLORE_CONSEQUENCE','ALIGN_BEFORE_PRESENTATION','SELECT_SOLUTION'})
ALLOWED['PRESENT'].add('SELECT_SOLUTION')


def validate_plan(session, analysis, rec):
    plan = analysis.intelligence.sales_plan if analysis.intelligence else None
    if plan is None:
        return []  # Legacy/demo sessions remain readable; live adapter requires a plan.
    turns = {t['id']:t for t in session['turns']}
    if any(i not in turns or turns[i]['role']!='client' for i in plan.need_evidence_ids):
        raise ValueError('Unsupported need evidence')
    issues = []
    signals = conversation_signals(session)
    action = analysis.intelligence.strategy.action
    state = session.get('dialogue_state', {})
    explicit_next_step = (signals['offer_requested'] or signals['client_declines_questions']
        or signals['offer_due_after_bridge'] or state.get('ready_to_buy')
        or state.get('outcome') in ('declined','unavailable','next_step_agreed'))
    needs_development = plan.need_depth in ('SURFACE_INTEREST','GENERAL_GOAL') or plan.priority_thread is not None
    if plan.priority_thread and not plan.need_evidence_ids:
        issues.append('thread_without_client_evidence')
    if session['turns'][-1]['role']=='client' and needs_development and not explicit_next_step:
        if (plan.readiness in ('READY_FOR_OFFER','READY_TO_BUY') or
            action in ('PRESENTATION','SELECT_SOLUTION','VALUE_BUILDING','FOLLOW_UP','CLOSING','NO_ACTION') or
            rec['action_type'] in ('PRESENT','NEXT_STEP')):
            issues.append('develop_need_before_offer')
    if rec['action_type']=='NO_ACTION':
        return issues
    diagnostic = action in DIAGNOSTIC or (plan.question is not None and action not in ('CLOSING','FOLLOW_UP','PRESENTATION','VALUE_BUILDING'))
    last_question = re.split(r'[.!]', rec['suggestion'])[-1]
    hidden_diagnostic = rec['action_type']=='ANSWER' and '?' in last_question and bool(re.search(r'вам (?:удобнее|важнее)[^?]*(?:запис|разбор|куратор)|какие[^?]*(?:задач|отч[её]т)|сколько[^?]*(?:учиться|времени на обучение)', last_question, re.I))
    if (rec['action_type'] in ('QUESTION','CLARIFY','INTEREST') or hidden_diagnostic) and action not in ('CLOSING','FOLLOW_UP') and plan.question is None:
        issues.append('question_without_value')
    if plan.significant_needs and not plan.need_evidence_ids:
        issues.append('needs_without_evidence')
    if plan.readiness in ('READY_FOR_OFFER','READY_TO_BUY') and plan.critical_gaps:
        issues.append('readiness_conflict')
    if diagnostic:
        q = plan.question
        if q is None:
            issues.append('question_without_value')
        else:
            if q.already_answered or not (q.changes_selection or q.changes_argument):
                issues.append('question_adds_no_value')
            if any(x['topic']==q.topic and x['role']=='manager' and x['status']=='answered' for x in session.get('dialogue_state',{}).get('questions',[])) and not any(f.replaces for f in analysis.facts):
                issues.append('answered_topic_repeat')
            essential = q.essential_now and any(g.topic==q.topic for g in plan.critical_gaps)
            if signals['client_tired_of_questions'] and not essential:
                issues.append('optional_question_after_long_discovery')
            if signals['offer_requested'] and (not essential or action!='ALIGN_BEFORE_PRESENTATION'):
                issues.append('explain_and_limit_clarification')
        if plan.readiness in ('READY_FOR_OFFER','READY_TO_BUY','NO_MORE_QUESTIONS'):
            issues.append('offer_ready_stop_discovery')
        if signals['client_declines_questions']:
            issues.append('client_declined_diagnostics')
        if signals['offer_due_after_bridge']:
            issues.append('promised_offer_due')
    if action=='ALIGN_BEFORE_PRESENTATION' and plan.after_answer_action not in ('SELECT_SOLUTION','PRESENTATION','PRICE_PRESENTATION'):
        issues.append('bridge_without_offer_commitment')
    if rec['action_type']=='SUMMARIZE' and session.get('dialogue_state',{}).get('needs_confirmed'):
        issues.append('repeated_need_confirmation')
    if rec['action_type']=='PRESENT' and plan.readiness=='NEEDS_CLARIFICATION' and not explicit_next_step:
        issues.append('presentation_before_selection')
    if signals['offer_requested'] and action=='NO_ACTION':
        issues.append('offer_request_unanswered')
    return issues


def knowledge_sources(knowledge):
    # Only explicit approved claims. The legacy educational notes are not an offer.
    if not isinstance(knowledge, dict):
        return {}
    return {c['id']: c['text'] for c in knowledge.get('approved_claims', [])
            if isinstance(c, dict) and c.get('id') and c.get('text') and c.get('approved') is True}


def apply_hypotheses(session, analysis):
    if analysis.intelligence is None:
        if any(f.confirms_hypothesis for f in analysis.facts):
            raise ValueError('Missing hypothesis context')
        return
    turns = {t['id']: t for t in session['turns']}
    latest = session['turns'][-1]
    memory = session.setdefault('hypotheses', [])
    for update in analysis.intelligence.hypotheses:
        data = update.model_dump()
        if latest['role'] != 'client' or not any(e['source_turn'] == latest['id'] for e in data['evidence']):
            raise ValueError('Hypothesis update needs new client evidence')
        for e in data['evidence']:
            t = turns.get(e['source_turn'])
            if not t or t['role'] != 'client' or e['quote'] not in t['text']:
                raise ValueError('Unsupported hypothesis source')
        old = next((h for h in memory if h['id'] == update.id), None) if update.id else None
        if update.id and old is None:
            raise ValueError('Unknown hypothesis ID')
        if not old and update.status == 'REJECTED':
            raise ValueError('Cannot reject an unknown hypothesis')
        if old and old['status'] == 'CONFIRMED':
            raise ValueError('Confirmed hypothesis must be revised through its fact')
        stamp = datetime.now(timezone.utc).isoformat()
        if old:
            old.setdefault('history', []).append({k: deepcopy(v) for k, v in old.items() if k != 'history'})
            old.update(**{k: v for k, v in data.items() if k != 'id'}, updated_at=stamp)
        elif not any(h['statement'].casefold() == update.statement.casefold() for h in memory):
            memory.append({**data, 'id': str(uuid4()), 'created_at': stamp, 'updated_at': stamp, 'history': []})
    for fact in analysis.facts:
        if not fact.confirms_hypothesis:
            continue
        old = next((h for h in memory if h['id'] == fact.confirms_hypothesis), None)
        if not old or fact.status != 'CONFIRMED' or latest['role'] != 'client':
            raise ValueError('Hypothesis confirmation requires confirmed client fact')
        old.setdefault('history', []).append({k: deepcopy(v) for k, v in old.items() if k != 'history'})
        old.update(status='CONFIRMED', confirmation_turn=fact.source_turn, confirmation_quote=fact.quote,
                   updated_at=datetime.now(timezone.utc).isoformat())


def normalized(text):
    return re.sub(r'[^\w\s]', '', text.casefold().replace('ё', 'е')).strip()


def commercial_amounts(text):
    # Compare entire amounts: 2000 must never match an approved 12000.
    from decimal import Decimal
    result = set()
    for number, unit in re.findall(r'(\d[\d\s]*(?:[.,]\d+)?)\s*(₽|руб|тысяч|%)', text, re.I):
        value = Decimal(re.sub(r'\s+', '', number).replace(',', '.'))
        if unit.casefold() == 'тысяч':
            value *= 1000
        result.add((value, '%' if unit == '%' else 'RUB'))
    return result


def validate_recommendation(session, analysis, rec, knowledge):
    """Hard checks are independent of the model's own self-review."""
    result = deepcopy(rec)
    issues = list(rec.get('policy_flags', []))
    blocked = ['dismissed_repeat'] if 'dismissed_repeat' in issues else []
    phrase = result['suggestion']
    if analysis.intelligence:
        blocked.extend(validate_plan(session, analysis, rec))
        strategy = analysis.intelligence.strategy.model_dump()
        turns = {t['id'] for t in session['turns']}
        if any(e not in turns for e in strategy['evidence_ids']):
            raise ValueError('Unknown strategy evidence')
        sources = knowledge_sources(knowledge)
        for claim in analysis.intelligence.product_claims:
            if claim.source_id not in sources or claim.text != sources[claim.source_id]:
                blocked.append('unsupported_product_claim')
        if strategy['action'] not in ALLOWED[result['action_type']]:
            if issues:  # An explicit policy changed the proposed action.
                strategy = None
            else:
                blocked.append('strategy_phrase_mismatch')
    else:
        strategy = None
    if result['action_type'] != 'NO_ACTION':
        bridge = analysis.intelligence and analysis.intelligence.strategy.action=='ALIGN_BEFORE_PRESENTATION'
        offer = result['action_type'] in ('PRESENT','ANSWER') and analysis.intelligence and analysis.intelligence.sales_plan
        if not phrase or len(phrase) > (600 if bridge or offer else 450) or phrase.count('?') > 1:
            blocked.append('phrase_length')
        if re.search(r'^(уточните|спросите|предложите|расскажите клиенту)\b', phrase, re.I):
            blocked.append('instruction_not_spoken_phrase')
        if re.search(r'продавать[^.!?]*не буду|не буду[^.!?]*продавать', phrase, re.I):
            blocked.append('false_no_sale_promise')
        if any(t['role'] == 'manager' and '?' in t['text'] and normalized(t['text']) == normalized(phrase) for t in session['turns']):
            blocked.append('already_spoken_question')
        if analysis.intelligence and analysis.intelligence.sales_plan:
            if re.search(r'в (?:моей |нашей )?базе|не загружен|нет данных', phrase, re.I):
                blocked.append('internal_problem_in_customer_speech')
            latest=session['turns'][-1]
            request = latest['text'] if latest['role']=='client' else ''
            question_sentence = re.split(r'[.!]', phrase)[-1].strip()
            asks_permission = bool(re.search(r'^(?:хотите[^?]*|(?:вам )?удобно[^?]*|)(?:расскажу|покажу|рассказать|показать|объяснить)[^?]*\?', question_sentence, re.I))
            already_requested = bool(re.search(r'расскаж|покаж|объясн', request, re.I))
            previous = next((t['text'] for t in reversed(session['turns'][:-1]) if t['role']=='manager'), '')
            consent = bool(re.match(r'да\b', request, re.I)) and bool(re.search(r'расскажу|покажу|объясн', previous, re.I))
            if asks_permission and (already_requested or consent):
                blocked.append('explain_now_instead_of_permission')
        amounts = commercial_amounts(phrase)
        approved_text = ' '.join(knowledge_sources(knowledge).values())
        # Narrow regression guard: naming a module does not establish practical work.
        if re.search(r'программ[аы][^.!?]{0,70}(?:про практику|практическ|включает практику)', phrase, re.I) and not re.search(r'практик', approved_text, re.I):
            blocked.append('unverified_practice_format')
        client_text = ' '.join(t['text'] for t in session['turns'] if t['role']=='client')
        denial = bool(re.search(r'(?:гарантировать|обещать)[^.!?]*не могу|не (?:гарантирую|обещаю)', phrase, re.I))
        if amounts - commercial_amounts(approved_text) - (commercial_amounts(client_text) if denial else set()) and result['action_type'] in ('ANSWER','PRESENT','NEXT_STEP'):
            blocked.append('unverified_commercial_amount')
        if not knowledge_sources(knowledge):
            if re.search(r'(?:сейчас|сразу)[^.!?]{0,25}(?:назову|расскажу|озвучу)[^.!?]*(?:стоимост|цен[уы]|осваива|программ)', phrase, re.I):
                blocked.append('unavailable_offer_promised_now')
            if re.search(r'(?:у нас|наш[а-я]* курс|обучение|программа)\s+(?:включает|да[её]т|предоставляет|гарантирует)|мы гарантируем', phrase, re.I):
                blocked.append('unverified_product_promise')
            if re.search(r'(?:таких гарантий|гарантии) обучение не да[её]т|у нас нет (?:поддержки|рассрочки|гарантий)', phrase, re.I):
                blocked.append('unverified_product_denial')
    if blocked:
        result.update(action_type='NO_ACTION', suggestion='', reason='Фраза скрыта проверкой: ' + ', '.join(blocked) + '. Уточните контекст или повторите анализ.')
        strategy = None
    if strategy is None:
        strategy = {'action': DEFAULT[result['action_type']], 'objective': result['reason'][:300],
                    'reason': result['reason'], 'evidence_ids': result['evidence_ids']}
    strategy['label'] = STRATEGY_LABELS[strategy['action']]
    result['strategy'] = strategy
    if analysis.intelligence:
        note = analysis.intelligence.manager_note
        if not knowledge_sources(knowledge):
            note = note or 'Перед предложением проверьте актуальную карточку программы: содержание под задачу клиента, формат и полную стоимость. Не называйте неподтверждённые условия.'
        result['manager_note'] = note
        if analysis.intelligence.sales_plan:
            result['sales_plan'] = analysis.intelligence.sales_plan.model_dump()
            result['conversation_signals'] = conversation_signals(session)
            if not blocked:
                session['sales_plan'] = result['sales_plan']
    result['validation'] = {'status': 'blocked' if blocked else ('adjusted' if issues else 'passed'),
                            'issues': list(dict.fromkeys(issues + blocked)), 'checks_version': 'v2-sales-leadership-1'}
    return result
