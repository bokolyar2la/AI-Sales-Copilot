from copy import deepcopy
import pytest
from backend.models import Analysis, SalesPlan
from backend.context import apply_analysis
from backend.dialogue_policy import conversation_signals


def session(text='Расскажите уже о программе и сколько стоит'):
    return {'turns':[{'id':'t1','role':'client','text':text}], 'facts':[], 'recommendations':[]}


def output(s, *, ready=False, question=True, strategy='ALIGN_BEFORE_PRESENTATION', action='ANSWER'):
    tid=s['turns'][-1]['id']
    return Analysis.model_validate({'facts':[], 'events':[],
        'recommendation':{'action_type':action,'suggestion':'Расскажу о содержании и цене; осталось уточнить уровень задач. Нужны простые отчёты или автоматизация?',
            'reason':'От сложности задач зависит выбор.','evidence_ids':[tid],'target_gap':'level','confidence':.8},
        'intelligence':{'strategy':{'action':strategy,'objective':'Подобрать вариант','reason':'Осталась развилка уровня','evidence_ids':[tid]},
            'sales_plan':{'commercial_goal':'Подобрать подходящее обучение','next_goal':'Определить уровень задач',
                'significant_needs':[],'need_evidence_ids':[],
                'critical_gaps':[] if ready else [{'topic':'level','impact':'Простые отчёты — базовый; объединение файлов — углублённый'}],
                'useful_gaps':[],'readiness':'READY_FOR_OFFER' if ready else 'NEEDS_CLARIFICATION',
                'question':{'topic':'level','new_information':'Сложность отчётов','decision_impact':'Выбор между базой и автоматизацией','already_answered':False,'changes_selection':True,'changes_argument':False,'essential_now':True} if question else None,
                'after_answer_action':'PRESENTATION','after_answer_goal':'Предложить выбранный вариант и стоимость'}}})


def issues(s): return s['recommendations'][-1]['validation']['issues']


def test_price_request_allows_one_valuable_bridge():
    s=session(); apply_analysis(s,output(s))
    assert s['recommendations'][-1]['strategy']['action']=='ALIGN_BEFORE_PRESENTATION'
    assert s['recommendations'][-1]['validation']['status']=='passed'


def test_ready_offer_without_ritual_confirmation():
    s=session(); a=output(s,ready=True,question=False,strategy='SELECT_SOLUTION',action='PRESENT')
    a.recommendation.suggestion='Для вашей задачи важен практический разбор рабочих отчётов.'
    apply_analysis(s,a)
    assert s['recommendations'][-1]['action_type']=='PRESENT'
    assert not s['dialogue_state']['needs_confirmed']


def test_shown_bridge_not_spoken_is_not_consumed():
    s=session(); apply_analysis(s,output(s))
    s['turns'].append({'id':'t2','role':'client','text':'Расскажите о программе'})
    assert not conversation_signals(s)['offer_due_after_bridge']


def test_spoken_bridge_requires_offer_after_answer():
    s=session(); apply_analysis(s,output(s))
    s['turns'].extend([{'id':'t2','role':'manager','text':s['recommendations'][-1]['suggestion']},
        {'id':'t3','role':'client','text':'Обычные отчёты, автоматизация не нужна'}])
    apply_analysis(s,output(s))
    assert 'promised_offer_due' in issues(s)


def test_no_further_questions_is_not_refusal_of_contact():
    s=session('Больше не спрашивайте, без вопросов расскажите программу.')
    apply_analysis(s,output(s))
    assert 'client_declined_diagnostics' in issues(s)
    assert s['dialogue_state']['outcome'] is None


def test_ready_plan_cannot_ask_another_discovery_question():
    s=session('Хочу обычные отчёты'); a=output(s,ready=True,strategy='DISCOVERY',action='QUESTION')
    apply_analysis(s,a)
    assert 'offer_ready_stop_discovery' in issues(s)


def test_optional_question_when_client_is_tired_is_blocked():
    s=session('Устал от вопросов')
    s['turns']=[{'id':str(i),'role':'manager','text':'Вопрос '+str(i)+'?'} for i in range(4)]+s['turns']
    a=output(s,strategy='DISCOVERY',action='QUESTION'); a.intelligence.sales_plan.critical_gaps=[]
    a.intelligence.sales_plan.question.essential_now=False
    apply_analysis(s,a)
    assert 'optional_question_after_long_discovery' in issues(s)


@pytest.mark.parametrize('defect',['already_answered','no_impact','no_question'])
def test_question_value_is_enforced(defect):
    s=session('Хочу Excel'); a=output(s,strategy='DISCOVERY',action='QUESTION')
    q=a.intelligence.sales_plan.question
    if defect=='already_answered': q.already_answered=True
    elif defect=='no_impact': q.changes_selection=q.changes_argument=False
    else:a.intelligence.sales_plan.question=None
    apply_analysis(s,a)
    assert s['recommendations'][-1]['validation']['status']=='blocked'


def test_internal_data_gap_is_a_manager_note():
    s=session('Какой вариант подходит?'); a=output(s,ready=True,question=False,strategy='SELECT_SOLUTION')
    a.recommendation.suggestion='В вашем случае главное — самостоятельно готовить рабочие отчёты.'
    apply_analysis(s,a,{})
    assert s['recommendations'][-1]['manager_note']
    assert s['recommendations'][-1]['validation']['status']=='passed'
    a.recommendation.suggestion='У меня нет данных в базе, поэтому подождите.'
    apply_analysis(s,a,{})
    assert 'internal_problem_in_customer_speech' in issues(s)


def test_need_evidence_cannot_be_manager_or_future():
    s=session(); a=output(s); a.intelligence.sales_plan.need_evidence_ids=['future']
    before=deepcopy(s)
    with pytest.raises(ValueError):apply_analysis(s,a)
    assert s==before


def test_confirmed_need_not_summarized_again():
    s=session('Да, именно это'); s['dialogue_state']={'events':[],'questions':[],'objections':[],'price_requests':[], 'needs_confirmed':True,'ready_to_buy':False,'outcome':None}
    a=output(s,ready=True,question=False,strategy='SUMMARIZE',action='SUMMARIZE')
    apply_analysis(s,a)
    assert 'repeated_need_confirmation' in issues(s)


def test_some_approved_data_does_not_allow_any_price():
    s=session('Сколько стоит?'); a=output(s,ready=True,question=False,strategy='PRICE_PRESENTATION')
    a.recommendation.suggestion='Стоимость 99000 рублей.'
    apply_analysis(s,a,{'approved_claims':[{'id':'x','approved':True,'text':'Стоимость 12000 рублей.'}]})
    assert 'unverified_commercial_amount' in issues(s)


@pytest.mark.parametrize('amount,allowed',[('12 000 рублей',True),('12000 ₽',True),('12 тысяч рублей',True),('2000 рублей',False)])
def test_whole_price_equivalence(amount,allowed):
    s=session('Сколько стоит?'); a=output(s,ready=True,question=False,strategy='PRICE_PRESENTATION')
    a.recommendation.suggestion='Стоимость '+amount+'.'
    apply_analysis(s,a,{'approved_claims':[{'id':'x','approved':True,'text':'Стоимость 12000 рублей.'}]})
    assert ('unverified_commercial_amount' not in issues(s)) == allowed


def test_short_offer_can_have_four_sentences():
    s=session(); a=output(s,ready=True,question=False,strategy='SELECT_SOLUTION',action='PRESENT')
    a.recommendation.suggestion='Начнём с вашей задачи. Нужны простые отчёты. Главный критерий — самостоятельная работа. Подберём содержание под этот результат.'
    apply_analysis(s,a)
    assert 'phrase_length' not in issues(s)


@pytest.mark.parametrize('consent',[False,True])
def test_explain_after_request_or_consent_instead_of_asking_again(consent):
    s=session('Покажите на моём примере')
    if consent:
        s['turns']=[{'id':'t0','role':'manager','text':'Расскажу на примере?'},{'id':'t1','role':'client','text':'Да, давайте'}]
    a=output(s,ready=True,question=False,strategy='ANSWER')
    a.recommendation.suggestion='Можно разобрать вашу задачу. Хотите, покажу пример?'
    apply_analysis(s,a)
    assert 'explain_now_instead_of_permission' in issues(s)


def test_answer_cannot_hide_unassessed_question():
    s=session('Есть проверка заданий?'); a=output(s,ready=True,question=False,strategy='ANSWER')
    a.recommendation.suggestion='Уточню условия. Вам удобнее записи или нужен разбор?'
    apply_analysis(s,a)
    assert 'question_without_value' in issues(s)


def test_named_module_does_not_prove_practice_format():
    s=session('Будет практика?'); a=output(s,ready=True,question=False,strategy='ANSWER')
    a.recommendation.suggestion='Программа как раз про практику: вы освоите объединение файлов.'
    apply_analysis(s,a,{'approved_claims':[{'id':'x','approved':True,'text':'Объединение файлов и Power Query.'}]})
    assert 'unverified_practice_format' in issues(s)


def test_empty_catalog_cannot_promise_immediate_price():
    s=session('Назовите цену'); a=output(s,ready=True,question=False,strategy='ANSWER')
    a.recommendation.suggestion='Сейчас назову полную стоимость и расскажу программу.'
    apply_analysis(s,a,{})
    assert 'unavailable_offer_promised_now' in issues(s)


def test_contact_question_is_not_diagnostic():
    s=session('Пришлите условия'); a=output(s,ready=True,question=False,strategy='ANSWER')
    a.recommendation.suggestion='Уточню нагрузку. Как удобнее передать условия: сообщением или созвониться?'
    apply_analysis(s,a,{})
    assert s['recommendations'][-1]['validation']['status']=='passed'


def test_honest_followup_is_not_a_technical_problem():
    s=session('Есть проверка практики?'); a=output(s,ready=True,question=False,strategy='FOLLOW_UP')
    a.recommendation.suggestion='У меня пока нет проверенных условий по практике. Уточню их у школы; как удобнее передать ответ?'
    apply_analysis(s,a,{})
    assert s['recommendations'][-1]['validation']['status']=='passed'


def test_model_context_keeps_history_and_spoken_bridge_without_old_plan_copies():
    from backend.providers import model_context
    s=session(); apply_analysis(s,output(s))
    s['turns'].extend([{'id':'t2','role':'manager','text':s['recommendations'][-1]['suggestion']},
        {'id':'t3','role':'client','text':'Только простые отчёты'}])
    for _ in range(5):
        s['recommendations'].append({'suggestion':'', 'action_type':'NO_ACTION','validation_trace':['internal'], 'sales_plan':{'large':'obsolete'}})
    before=deepcopy(s); context=model_context(s)
    assert context['turns']==s['turns'] and context['facts']==s['facts']
    assert context['sales_plan']==s['sales_plan']
    assert context['conversation_signals']['offer_due_after_bridge']
    assert len(context['recommendations'])==3
    assert all('sales_plan' not in r and 'validation_trace' not in r for r in context['recommendations'])
    assert s==before
