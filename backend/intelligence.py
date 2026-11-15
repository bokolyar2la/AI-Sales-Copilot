"""Small, auditable memory delta and strategy validation for iteration 1."""
from copy import deepcopy
from datetime import datetime, timezone
import re
from uuid import uuid4

STRATEGY_LABELS = {
    'DISCOVERY': 'Выявление задачи', 'DEEP_DISCOVERY': 'Уточнение ситуации',
    'NEED_DEVELOPMENT': 'Помочь сформулировать потребность', 'RAPPORT': 'Установить контакт',
    'SALES_RECOVERY': 'Вернуть конструктивный диалог', 'SUMMARIZE': 'Подтвердить понимание',
    'PRESENTATION': 'Презентация', 'VALUE_BUILDING': 'Объяснить пользу',
    'OBJECTION_DIAGNOSIS': 'Выяснить причину сомнения', 'OBJECTION_HANDLING': 'Ответить на сомнение',
    'PRICE_PRESENTATION': 'Ответить о стоимости', 'CLOSING': 'Перейти к оформлению',
    'FOLLOW_UP': 'Согласовать следующий шаг', 'ANSWER': 'Ответить на вопрос', 'NO_ACTION': 'Дать клиенту ответить'}
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


def validate_recommendation(session, analysis, rec, knowledge):
    """Hard checks are independent of the model's own self-review."""
    result = deepcopy(rec)
    issues = list(rec.get('policy_flags', []))
    blocked = []
    phrase = result['suggestion']
    if analysis.intelligence:
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
        if not phrase or len(phrase) > 450 or phrase.count('?') > 1 or len(re.findall(r'[.!?](?:\s|$)', phrase)) > 3:
            blocked.append('phrase_length')
        if re.search(r'^(уточните|спросите|предложите|расскажите клиенту)\b', phrase, re.I):
            blocked.append('instruction_not_spoken_phrase')
        if re.search(r'продавать[^.!?]*не буду|не буду[^.!?]*продавать', phrase, re.I):
            blocked.append('false_no_sale_promise')
        if any(t['role'] == 'manager' and '?' in t['text'] and normalized(t['text']) == normalized(phrase) for t in session['turns']):
            blocked.append('already_spoken_question')
        if not knowledge_sources(knowledge):
            amounts = re.findall(r'\d[\d\s.,]*(?:₽|руб|тысяч|%)', phrase, re.I)
            client_text = ' '.join(t['text'] for t in session['turns'] if t['role']=='client')
            denial = bool(re.search(r'(?:гарантировать|обещать)[^.!?]*не могу|не (?:гарантирую|обещаю)', phrase, re.I))
            quoted_denial = denial and all(normalized(amount) in normalized(client_text) for amount in amounts)
            if amounts and not quoted_denial and result['action_type'] in ('ANSWER', 'PRESENT', 'NEXT_STEP'):
                blocked.append('unverified_commercial_amount')
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
    result['validation'] = {'status': 'blocked' if blocked else ('adjusted' if issues else 'passed'),
                            'issues': issues + blocked, 'checks_version': 'v2-iteration-1'}
    return result
