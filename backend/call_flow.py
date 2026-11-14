from copy import deepcopy
from .context import uid, now

def opening(session):
    profile = session.get('sales_context', {})
    introduction = 'Здравствуйте!'
    if profile.get('manager'):
        introduction += ' Меня зовут ' + profile['manager'] + '.'
    if profile.get('school'):
        introduction += ' Я представляю ' + profile['school'] + '.'
    return {'id': uid(), 'action_type': 'GREETING', 'suggestion': introduction + ' Вам удобно сейчас поговорить об обучении?', 'reason': 'Представьтесь и уточните, удобно ли общаться. Затем согласуйте порядок беседы.', 'evidence_ids': [], 'feedback': None, 'created_at': now()}

def scoped_knowledge(session, knowledge):
    if not isinstance(knowledge, dict):
        return knowledge
    profile = session.get('sales_context')
    if profile is None:
        return knowledge
    school = profile.get('school', '').strip().casefold()
    course = profile.get('course', '').strip().casefold()
    if school not in ('eduson', 'eduson academy', 'эдюсон', 'эдюсон академия'):
        return {'status': 'Для этой школы продуктовая база не загружена. Выявляй потребность, но не обещай свойства и условия.', 'products': [], 'platform': {}, 'selected_context': profile}
    result = deepcopy(knowledge)
    if not any(word in course for word in ('excel', 'эксель', 'таблиц')):
        result['products'] = []
        result['status'] = 'Для выбранной программы нет карточки. Общие УТП школы не гарантируют свойства конкретного курса.'
    result['selected_context'] = profile
    return result
