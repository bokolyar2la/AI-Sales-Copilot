"""Bounded local speech-act checks, not a universal semantic classifier."""
import re
from backend.intelligence import normalized

def has(pattern,text):return bool(re.search(pattern,text,re.I))

def boundary(text):
    if has(r'не звоните|не хочу (?:больше )?(?:покупать|обучаться)|отказываюсь от покупки',text):return 'declined'
    if has(r'не (?:хочу|могу|готов[аы]?)\s+(?:сейчас\s+)?(?:разговаривать|говорить|к разговору)|давайте закончим|мне сейчас неудобно(?:[.!?,]|$)|ничего не спрашивайте|сам[а]? решу',text):return 'unavailable'
    return None

CLOSE=r'оформ(?:им|ляем|ить|ляйте)|заполн(?:ите|им|ять)\s+(?:анкет|заявк)|оплатите|подпиш(?:ите|ем)|приступим к оплате'
END=r'до свидания|всего доброго|не буду (?:отвлекать|задерживать)|завершим|хорошего дня'
TOPICS={
 'duration':r'сколько.*(?:длит|времени)|срок|длительн',
 'price':r'цен|стоимост|сколько стоит',
 'practice':r'упражнен|задани|тренаж[её]р|практик',
 'terms':r'возврат|рассроч|гарант|договор|отказаться|вернуть',
 'format':r'формат|заняти|запис|куратор|доступ',
}

def check_speech(session,d,claims,verify_phrase):
    turns=session['turns'];last=turns[-1];phrase=d.say_to_customer
    closing=has(CLOSE,phrase);ending=has(END,phrase) and '?' not in phrase and not closing and not has(r'расскажите|скажите|выберите|предлагаю|давайте обсудим|подумайте о',phrase)
    if d.action=='END' and not ending:raise ValueError('END_requires_noncommercial_farewell')
    if closing and d.action!='CLOSE':raise ValueError('closing_phrase_action_mismatch')
    if ending and d.action!='END':raise ValueError('farewell_action_mismatch')
    if closing and has(r'сначала.*провер|не подтвержден|нельзя оформ|до.*провер',d.reason):
        raise ValueError('reason_phrase_contradiction')
    b=boundary(last['text']) if last['role']=='client' else None
    if b and (d.action!='END' or not ending):raise ValueError('respect_contact_boundary')
    # Never let temporary contact unavailability become product refusal in memory.
    if b=='unavailable':
        for delta in d.memory_delta:
            if delta.kind=='event' and delta.value.kind=='declined':raise ValueError('temporary_unavailable_not_product_decline')
            if delta.kind=='fact' and delta.value.category=='decision' and has(r'отказ|не хочет (?:курс|покуп)',delta.value.value):
                raise ValueError('temporary_unavailable_not_product_decline')
    conditions=[]
    for t in turns:
        if t['role']!='client':continue
        text=t['text']
        if has(r'куплю|готов[а]?|соглас(?:ен|на)|оформ',text) and has(r'если|при условии|только при',text) and not boundary(text):
            parts=re.split(r'если|при условии|только при',text,flags=re.I)
            clause=parts[-1].strip(' .,!?')
            if clause:conditions.append(clause)
    # Exact textual support is intentionally conservative. No model-set readiness shortcut.
    supported=lambda c:any(normalized(c) in normalized(x['text']) for x in claims if x.get('approved') is True)
    if (closing or d.action=='CLOSE') and any(not supported(c) for c in conditions):
        raise ValueError('unverified_purchase_condition')
    questions=[]
    if last['role']=='client' and ('?' in last['text'] or has(r'скажите|подскажите|сколько|какова',last['text'])):
        questions.append(last['text'])
    by={t['id']:t for t in turns}
    for q in session.get('dialogue_state',{}).get('questions',[]):
        if q.get('role')=='client' and q.get('status')=='open' and q.get('source_turn') in by:
            questions.append(by[q['source_turn']]['text'])
    topics={k for q in questions for k,p in TOPICS.items() if has(p,q)}
    clarification=False
    if topics and not b and not ending:
        verification=phrase==verify_phrase
        relevant=any(has(TOPICS[k],phrase) for k in topics)
        # A narrow, auditable clarification: identify WHICH product/format affects the requested answer.
        clarification=(phrase.count('?')==1 and phrase.rstrip().endswith('?') and not has(r'гарантир|получите|предостав|у нас|зафиксирован|закреплен',phrase) and has(r'како[йегому]+.*(?:курс|программ|формат)|о как(?:ой|ом).*?(?:курс|программ)|название.*(?:курс|программ)',phrase)
            and has(r'для ответа|чтобы (?:ответить|назвать|уточнить)|зависит от|не указан|неизвест',d.reason))
        if not verification and not clarification and (d.action=='ASK' or '?' in phrase or not relevant):
            raise ValueError('answer_direct_question_before_discovery')
    return clarification
