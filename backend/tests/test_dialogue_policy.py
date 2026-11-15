from copy import deepcopy
import pytest
from backend.context import apply_analysis
from backend.models import Analysis, Advice, DialogueEvent, Fact
from backend.dialogue_policy import initial_state, update_state, is_price_question


def session(text='Хочу учиться', role='client'):
    return {'turns': [{'id': 't1', 'role': role, 'text': text}], 'facts': [], 'recommendations': []}


def analysis(s, action='QUESTION', suggestion='Какова ваша цель?', events=(), gap='goal'):
    t = s['turns'][-1]
    return Analysis(facts=[], events=[DialogueEvent(kind=k, topic=topic, source_turn=t['id'], quote=t['text']) for k, topic in events],
                    recommendation=Advice(action_type=action, suggestion=suggestion, reason='Основание', evidence_ids=[t['id']], target_gap=gap, confidence=.8))


@pytest.mark.parametrize('text', ['Сколько стоит?', 'Какая цена?', 'Сначала цену скажите', 'Интересует стоимость', 'Назовите мне цену'])
def test_price_cannot_be_replaced_by_discovery(text):
    s = session(text)
    apply_analysis(s, analysis(s))
    assert s['recommendations'][-1]['action_type'] == 'ANSWER'
    assert s['dialogue_state']['price_requests'] == ['t1']
    assert not s['dialogue_state']['objections']


def test_price_retry_is_idempotent_and_repeated_deferral_blocked():
    s = session('Сколько стоит?')
    s['dialogue_state'] = update_state(s, [])
    assert update_state(s, [])['price_requests'] == ['t1']
    s['turns'].append({'id': 't2', 'role': 'client', 'text': 'Скажите цену!'})
    apply_analysis(s, analysis(s, 'ANSWER', 'Сначала обсудим ваши цели, потом вернёмся к цене.'))
    assert 'no_repeated_price_deferral' in s['recommendations'][-1]['policy_flags']


def test_shown_summary_does_not_confirm_needs():
    s = session()
    apply_analysis(s, analysis(s, 'SUMMARIZE', 'Вам нужно освоить дизайн?'))
    assert not s['dialogue_state']['needs_confirmed']
    apply_analysis(s, analysis(s, 'PRESENT', 'Вам подходит базовый курс.'))
    assert s['recommendations'][-1]['action_type'] == 'QUESTION'


def test_false_manager_confirmation_rejected_atomically():
    s = session('Вы согласны?', 'manager')
    before = deepcopy(s)
    with pytest.raises(ValueError):
        apply_analysis(s, analysis(s, events=[('needs_confirmed', '')]))
    assert s == before


def test_old_event_source_rejected_before_fact_mutation():
    s = session()
    a = analysis(s)
    a.facts = [Fact(category='goal', value='Учиться', quote='Хочу учиться', source_turn='t1')]
    a.events = [DialogueEvent(kind='interest_shared', source_turn='old', quote='Хочу учиться')]
    with pytest.raises(ValueError):
        apply_analysis(s, a)
    assert not s['facts']


def test_manager_answer_cannot_resolve_client_objection():
    s = session('Это удобно', 'manager')
    with pytest.raises(ValueError):
        apply_analysis(s, analysis(s, events=[('objection_resolved', 'study_time')]))


def test_ready_buyer_not_sent_back_to_discovery():
    s = session('Готов оплатить, как оформить?')
    apply_analysis(s, analysis(s, events=[('ready_to_buy', '')]))
    assert s['recommendations'][-1]['action_type'] == 'NEXT_STEP'


def test_refusal_and_real_reopening():
    s = session('Не звоните')
    apply_analysis(s, analysis(s, events=[('declined', '')]))
    assert s['recommendations'][-1]['action_type'] == 'NO_ACTION'
    s['turns'].append({'id': 't2', 'role': 'client', 'text': 'Я передумал, готов оформить'})
    apply_analysis(s, analysis(s, events=[('ready_to_buy', '')]))
    assert s['dialogue_state']['outcome'] is None
    assert s['recommendations'][-1]['action_type'] == 'NEXT_STEP'


def test_question_answer_tracking_and_repetition():
    s = session('Чему хотите научиться?', 'manager')
    apply_analysis(s, analysis(s, events=[('question_asked', 'goal')]))
    assert s['recommendations'][-1]['action_type'] == 'NO_ACTION'
    s['turns'].append({'id': 't2', 'role': 'client', 'text': 'Хочу делать сайты'})
    apply_analysis(s, analysis(s, events=[('question_answered', 'goal')]))
    assert s['dialogue_state']['questions'][0]['status'] == 'answered'
    assert 'answered_topic_repeat' in s['recommendations'][-1]['policy_flags']


def test_stage_survives_pause_and_price_interruption():
    s = session('Да, задача именно такая')
    apply_analysis(s, analysis(s, 'NO_ACTION', '', [('needs_confirmed', '')]))
    s['turns'].append({'id': 't2', 'role': 'client', 'text': 'Сколько стоит?'})
    apply_analysis(s, analysis(s, 'ANSWER', 'Цену нужно уточнить.'))
    assert s['stage'] == 'Задача подтверждена'


def test_price_promise_is_not_answer():
    s = session('Цену обсудим позже', 'manager')
    apply_analysis(s, analysis(s, 'NO_ACTION', '', [('question_answered', 'price')]))
    assert not s['dialogue_state']['price_answered']


def test_entire_conversation_state_to_agreement():
    s = session('Здравствуйте', 'manager')
    sequence = [('manager', 'Здравствуйте', 'greeted', ''),
                ('client', 'Да, такой порядок подходит', 'agenda_agreed', ''),
                ('client', 'Хочу делать сайты', 'interest_shared', ''),
                ('client', 'Да, вы верно поняли задачу', 'needs_confirmed', ''),
                ('manager', 'Вот как содержание связано с вашей задачей', 'presentation_delivered', ''),
                ('client', 'Боюсь не справиться', 'objection_opened', 'ability'),
                ('client', 'Да, теперь понятно, этот вопрос снят', 'objection_resolved', 'ability'),
                ('client', 'Давайте завтра в 15 обсудим условия', 'next_step_agreed', '')]
    s['turns'] = []
    for i, (role, text, kind, topic) in enumerate(sequence):
        s['turns'].append({'id': str(i), 'role': role, 'text': text})
        apply_analysis(s, analysis(s, 'NO_ACTION', '', [(kind, topic)]))
    assert s['dialogue_state']['needs_confirmed']
    assert s['dialogue_state']['objections'][0]['status'] == 'resolved'
    assert s['dialogue_state']['outcome'] == 'next_step_agreed'
    assert s['stage'] == 'Договорённость'
