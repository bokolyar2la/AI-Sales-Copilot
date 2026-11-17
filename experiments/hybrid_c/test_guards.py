import pytest
from experiments.hybrid_c.test_hybrid import session,decision
from experiments.hybrid_c.engine import validate_and_apply,VERIFY_PHRASE

@pytest.mark.parametrize('action',['ASK','ARGUE','PRESENT','ANSWER','VERIFY_TERMS','CLOSE','END'])
def test_unverified_condition_all_labels(action):
 with pytest.raises(ValueError):validate_and_apply(session('Куплю, если возможен возврат'),decision(action=action,say_to_customer='Тогда оформляем заявку.'),[])

@pytest.mark.parametrize('text',['Не хочу сейчас разговаривать.','Давайте закончим.','Мне сейчас неудобно.','Пожалуйста, больше ничего не спрашивайте.','Я сама решу, если будет нужно.'])
def test_boundary_paraphrases(text):
 with pytest.raises(ValueError):validate_and_apply(session(text),decision(),[])
 validate_and_apply(session(text),decision(action='END',say_to_customer='Не буду отвлекать. Всего доброго!'),[])

@pytest.mark.parametrize('text',['Сколько длится обучение?','Сколько упражнений в программе?','Какие условия рассрочки?','Скажите стоимость курса.'])
def test_direct_question(text):
 with pytest.raises(ValueError):validate_and_apply(session(text),decision(say_to_customer='Что мотивирует вас учиться?'),[])
 validate_and_apply(session(text),decision(action='VERIFY_TERMS',say_to_customer=VERIFY_PHRASE),[])

def test_needed_clarification():
 validate_and_apply(session('Сколько длится обучение?'),decision(reason='Чтобы ответить, нужно название программы: длительность зависит от курса.',say_to_customer='О какой программе вы спрашиваете?'),[])

def test_unconditional_purchase():
 validate_and_apply(session('Все условия понятны. Оформите покупку.'),decision(action='CLOSE',say_to_customer='Тогда оформим заявку.'),[{'id':'x','approved':True,'text':'Полная стоимость 100 рублей.'}])

def test_supported_condition():
 validate_and_apply(session('Куплю, если возможен возврат'),decision(action='CLOSE',say_to_customer='Тогда оформим заявку.'),[{'id':'x','approved':True,'text':'Возможен возврат.'}])

def test_uncomfortable_installment_not_boundary():
 validate_and_apply(session('Мне неудобна такая рассрочка.'),decision(say_to_customer='Какой график платежей вам подходит?'),[])

def test_hypothetical_not_purchase_condition():
 validate_and_apply(session('А если передумаю, можно отказаться?'),decision(action='VERIFY_TERMS',say_to_customer=VERIFY_PHRASE),[])
 # A historical hypothetical does not block a later explicit purchase without conditions.
 s=session('А если передумаю?');s['turns'].append({'id':'t2','role':'client','text':'Все условия ясны. Хочу купить.'})
 validate_and_apply(s,decision(action='CLOSE',say_to_customer='Тогда оформим заявку.',evidence_ids=['t2']),[])

def test_temporary_not_permanent_memory():
 d=decision(action='END',say_to_customer='Всего доброго!',memory_delta=[{'kind':'event','value':{'kind':'declined','quote':'Мне сейчас неудобно.','source_turn':'t1'}}])
 with pytest.raises(ValueError,match='temporary'):validate_and_apply(session('Мне сейчас неудобно.'),d,[])

def test_reason_phrase_conflict():
 with pytest.raises(ValueError):validate_and_apply(session('Хочу купить'),decision(action='CLOSE',reason='Сначала проверить условия.',say_to_customer='Тогда оформим заявку.'),[])

def test_open_question_preserved_priority():
 s=session('Сколько заданий?');s['dialogue_state']={'questions':[{'role':'client','status':'open','source_turn':'t1'}]}
 s['turns'].append({'id':'t2','role':'client','text':'Я работаю руководителем'})
 with pytest.raises(ValueError,match='direct_question'):validate_and_apply(s,decision(evidence_ids=['t2']),[])
