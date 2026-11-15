"""Evidence-backed conversation progress; suggestions are never spoken events."""
from copy import deepcopy
import re

VERSION = 'edtech-personal-1'
CLIENT_EVENTS = {'agenda_agreed', 'interest_shared', 'needs_confirmed', 'objection_opened',
                 'objection_resolved', 'ready_to_buy', 'next_step_agreed', 'declined', 'unavailable', 'questions_declined'}
MANAGER_EVENTS = {'greeted', 'presentation_delivered', 'offer_bridge_delivered'}
STAGES = {'greeted': 'Приветствие', 'agenda_agreed': 'Причина интереса',
          'interest_shared': 'Выявление потребностей', 'needs_confirmed': 'Задача подтверждена',
          'presentation_delivered': 'Презентация', 'objection_opened': 'Работа с сомнением',
          'objection_resolved': 'Следующий шаг', 'ready_to_buy': 'Оформление / следующий шаг',
          'next_step_agreed': 'Договорённость', 'declined': 'Отказ', 'unavailable': 'Перенос разговора'}


def is_price_question(text):
    text = text.casefold()
    return bool(re.search(r'сколько\s+(?:это\s+|будет\s+)?стоит|какая\s+(?:у\s+вас\s+)?цен[аы]|'
                          r'(?:назовите|скажите|озвучьте|сначала|узнать|интересует|какую)\s+(?:мне\s+)?цен[ау]|'
                          r'стоимость|цен[ау]\s*[?!]|цен[ау]\s+(?:скажите|назовите)', text))


def initial_state():
    return {'version': VERSION, 'stage': 'Начало', 'events': [], 'questions': [], 'objections': [],
            'price_requests': [], 'price_answered': False, 'needs_confirmed': False,
            'ready_to_buy': False, 'outcome': None}


def conversation_signals(session):
    turns = session.get('turns', [])
    latest = turns[-1] if turns else {'role':'manager', 'text':''}
    text = latest['text'].casefold() if latest['role']=='client' else ''
    state = session.get('dialogue_state', {})
    refusal = bool(re.search(r'не хочу[^.!?]*отвеч|без (?:дальнейших |лишних )?вопрос|никаких[^.!?]*вопрос|не надо[^.!?]*вопрос|больше[^.!?]*не спраш', text))
    tired = bool(re.search(r'устал[^.!?]*вопрос|вс[её][^.!?]*вопрос|хватит[^.!?]*вопрос|можете уже рассказать|слишком много[^.!?]*вопрос', text))
    offer = is_price_question(text) or bool(re.search(r'расскаж[^.!?]*программ|что[^.!?]*обучение|что конкретно[^.!?]*осво', text))
    bridge_ids = {e['source_turn'] for e in state.get('events', []) if e['kind']=='offer_bridge_delivered'}
    # Suggestions count only after the manager actually says them.
    for rec in session.get('recommendations', []):
        if rec.get('strategy',{}).get('action')=='ALIGN_BEFORE_PRESENTATION':
            bridge_ids.update(t['id'] for t in turns if t['role']=='manager' and t['text'].strip()==rec['suggestion'].strip())
    bridge_index = max((i for i,t in enumerate(turns) if t['id'] in bridge_ids), default=-1)
    presented = {e['source_turn'] for e in state.get('events', []) if e['kind']=='presentation_delivered'}
    presentation_index = max((i for i,t in enumerate(turns) if t['id'] in presented), default=-1)
    offer_due = bridge_index>presentation_index and latest['role']=='client' and len(turns)-1>bridge_index
    questions = sum(t['role']=='manager' and '?' in t['text'] for t in turns[presentation_index+1:])
    return {'client_declines_questions':refusal or state.get('questions_declined',False),
            'client_tired_of_questions':tired, 'offer_requested':offer,
            'manager_questions_since_offer':questions, 'offer_due_after_bridge':offer_due}


def update_state(session, events):
    state = deepcopy(session.get('dialogue_state') or initial_state())
    latest = session['turns'][-1]
    # Validate all events before any persistent state is changed.
    for event in events:
        if event.source_turn != latest['id']:
            raise ValueError('Event '+event.kind+': source_turn must be latest turn ID')
        if event.quote not in latest['text']:
            raise ValueError('Event '+event.kind+': quote must be an exact substring of latest turn, preserve punctuation')
        if event.kind in CLIENT_EVENTS and latest['role'] != 'client':
            raise ValueError('Client confirmation required')
        if event.kind in MANAGER_EVENTS and latest['role'] != 'manager':
            raise ValueError('Manager event required')
        if event.kind.startswith(('question_', 'objection_')) and not event.topic:
            raise ValueError('Event topic required')
    # Count actual client turns, never generated recommendations or retries.
    if latest['role'] == 'client' and is_price_question(latest['text']):
        if latest['id'] not in state['price_requests']:
            state['price_requests'].append(latest['id'])
            state['price_answered'] = False
    for event in events:
        item = event.model_dump()
        if item in state['events']:
            continue
        state['events'].append(item)
        kind, topic = event.kind, event.topic
        if kind in STAGES:
            state['stage'] = STAGES[kind]
        if kind == 'needs_confirmed':
            state['needs_confirmed'] = True
        elif kind == 'ready_to_buy':
            state['ready_to_buy'] = True
            state['outcome'] = None
        elif kind in ('declined', 'unavailable', 'next_step_agreed'):
            state['outcome'] = kind
        elif kind == 'interest_shared':
            state['outcome'] = None
        if kind == 'questions_declined':
            state['questions_declined'] = True
        if kind == 'question_asked':
            state['questions'].append({'topic': topic, 'role': latest['role'], 'source_turn': latest['id'], 'status': 'open'})
        elif kind == 'question_answered':
            for question in state['questions']:
                if question['topic'] == topic and question['role'] != latest['role']:
                    question.update(status='answered', answer_turn=latest['id'])
            if topic == 'price' and latest['role'] == 'manager':
                # A promise to discuss price later is not an answer.
                if re.search(r'\d[\d\s.,]*(?:₽|руб|тысяч)', latest['text'], re.I):
                    state['price_answered'] = True
        elif kind == 'objection_opened':
            if not any(o['topic'] == topic and o['status'] == 'open' for o in state['objections']):
                state['objections'].append({'topic': topic, 'source_turn': latest['id'], 'quote': event.quote, 'status': 'open'})
        elif kind == 'objection_resolved':
            for objection in state['objections']:
                if objection['topic'] == topic:
                    objection.update(status='resolved', resolution_turn=latest['id'])
    # New corrections require a fresh confirmation before recommending a product.
    if any(f.get('replaces') and f['category'] in ('goal', 'problem', 'constraints') for f in session.get('_new_facts', [])):
        state['needs_confirmed'] = False
    return state


def guard_recommendation(session, rec, state):
    latest = session['turns'][-1]
    rec = deepcopy(rec)
    flags = []
    plan = session.get('_sales_plan')

    def replace(action, phrase, reason, flag, gap=''):
        rec.update(action_type=action, suggestion=phrase, reason=reason,
                   evidence_ids=[latest['id']], target_gap=gap)
        flags.append(flag)

    if state['outcome'] == 'declined':
        replace('NO_ACTION', '', 'Клиент отказался: завершите разговор без дальнейшего убеждения.', 'respect_refusal')
    elif latest['role'] == 'manager' and '?' in latest['text']:
        replace('NO_ACTION', '', 'Вопрос уже произнесён. Дождитесь ответа клиента.', 'wait_for_answer')
    elif not plan and latest['role'] == 'client' and is_price_question(latest['text']):
        # Never let discovery/presentation replace a direct price response.
        if rec['action_type'] != 'ANSWER':
            replace('ANSWER', 'Точную стоимость нужно проверить для выбранной программы; сейчас подтверждённой суммы у меня нет. Хотите сначала уточнить цену?',
                    'Вопрос о цене требует ответа; нельзя подменять его исследованием потребностей.', 'price_answer_required', 'price')
        if len(state['price_requests']) > 1 and re.search(r'потом|позже|перейд[её]м|верн[её]мся|сначала.*(?:цел|задач|обсуд|вопрос)', rec['suggestion'], re.I):
            replace('ANSWER', 'Понимаю, вы хотите сначала узнать стоимость. Подтверждённой суммы у меня сейчас нет — её нужно уточнить, прежде чем продолжать подбор.',
                    'Клиент повторно запросил цену: повторный перенос разговора о стоимости скрыт.', 'no_repeated_price_deferral', 'price')
    elif rec['action_type'] == 'PRESENT' and not (state['needs_confirmed'] or state['ready_to_buy'] or (plan and plan['readiness'] in ('READY_FOR_OFFER','READY_TO_BUY','NO_MORE_QUESTIONS'))):
        goals = [f for f in session['facts'] if f['category'] == 'goal' and f['status'] == 'CONFIRMED']
        if goals:
            phrase = 'Правильно понимаю, основной результат, который вы хотите получить: ' + goals[-1]['value'][:350].rstrip('.?!') + '?'
            replace('SUMMARIZE', phrase, 'Перед подбором программы подтвердите желаемый результат.', 'confirm_before_presenting')
        else:
            replace('QUESTION', 'Какой результат вы хотели бы получить от обучения?', 'Для рекомендации программы пока нет подтверждённой цели.', 'goal_before_presenting', 'goal')
    elif state['ready_to_buy'] and rec['action_type'] in ('QUESTION', 'INTEREST', 'AGENDA'):
        replace('NEXT_STEP', 'Какие условия вам осталось уточнить перед оформлением?', 'Клиент выразил готовность: не начинайте исследование заново.', 'respect_readiness', 'next_step')
    if not plan and rec['action_type'] in ('QUESTION', 'CLARIFY', 'INTEREST') and rec['target_gap']:
        if any(q['topic'] == rec['target_gap'] and q['role'] == 'manager' and q['status'] == 'answered' for q in state['questions']):
            if not any(f.get('replaces') for f in session.get('_new_facts', [])):
                replace('NO_ACTION', '', 'На эту тему уже получен ответ. Повторный вопрос скрыт; уточнение возможно при изменении контекста.', 'answered_topic_repeat')
    rec['policy_flags'] = flags
    return rec
