from datetime import datetime, timezone
from uuid import uuid4
from .models import CATEGORIES
from .dialogue_policy import update_state, guard_recommendation
from copy import deepcopy
from .intelligence import apply_hypotheses, validate_recommendation

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return str(uuid4())

def apply_analysis(session, analysis, knowledge=None):
    # Publish a whole validated delta, never partially mutate stored memory.
    draft = deepcopy(session)
    _apply_analysis(draft, analysis, knowledge)
    session.clear()
    session.update(draft)


def _apply_analysis(session, analysis, knowledge):
    turns = {t['id']: t for t in session['turns']}
    latest = session['turns'][-1]
    # Validate the complete output before changing memory.
    for fact in analysis.facts:
        source = turns.get(fact.source_turn)
        if not source or source['id'] != latest['id'] or source['role'] != 'client' or fact.quote not in source['text']:
            raise ValueError('Unsupported fact source')
        for old_id in fact.replaces:
            if not any(f['id'] == old_id and f['category'] == fact.category for f in session['facts']):
                raise ValueError('Invalid replacement')
    rec = analysis.recommendation.model_dump()
    if any(e not in turns for e in rec['evidence_ids']):
        raise ValueError('Invalid evidence')
    if rec['action_type'] != 'NO_ACTION' and (not rec['evidence_ids'] or not rec['suggestion']):
        raise ValueError('Missing evidence')
    working = {**session, '_new_facts': [f.model_dump() for f in analysis.facts]}
    state = update_state(working, analysis.events)
    apply_hypotheses(session, analysis)
    for item in analysis.facts:
        f = item.model_dump()
        if any(old['category'] == f['category'] and old['value'] == f['value'] and old['status'] == f['status'] for old in session['facts']):
            continue
        for old in session['facts']:
            if old['id'] in f['replaces']:
                old['status'] = 'OUTDATED' if f['status'] == 'CONFIRMED' else 'CONTRADICTED'
        session['facts'].append({**f, 'id': uid(), 'created_at': now()})
    working['facts'] = session['facts']
    rec = guard_recommendation(working, rec, state)
    session['dialogue_state'] = state
    rejected = [r for r in session['recommendations'] if r.get('feedback') in ('rejected', 'hidden')]
    if any(r['suggestion'] == rec['suggestion'] for r in rejected):
        rec.update(action_type='NO_ACTION', suggestion='', reason='Этот совет уже отклонён или скрыт. Дождитесь новых сведений.')
        rec.setdefault('policy_flags', []).append('dismissed_repeat')
    rec = validate_recommendation(session, analysis, rec, knowledge)
    rec.update(id=uid(), created_at=now(), feedback=None, turn_id=latest['id'])
    session['recommendations'].append(rec)
    known = {f['category'] for f in session['facts'] if f['status'] == 'CONFIRMED'}
    session['gaps'] = [c for c in CATEGORIES if c not in known]
    session['stage'] = {'GREETING':'Приветствие','AGENDA':'Порядок беседы','INTEREST':'Причина интереса','CLARIFY': 'Уточнение', 'ANSWER': 'Ответ клиенту', 'SUMMARIZE': 'Подтверждение задачи', 'PRESENT': 'Презентация', 'NEXT_STEP': 'Следующий шаг', 'QUESTION': 'Выявление потребностей', 'NO_ACTION': 'Пауза'}[rec['action_type']]
    if state['events']:
        session['stage'] = state['stage']
