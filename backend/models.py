from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

CATEGORIES = ['profile', 'experience', 'situation', 'problem', 'consequence', 'importance', 'goal', 'motivation', 'urgency', 'constraints', 'objection', 'decision', 'next_step']
LABELS = dict(zip(CATEGORIES, ['Профиль', 'Прошлый опыт', 'Текущая ситуация', 'Проблема', 'Последствия', 'Значимость', 'Желаемый результат', 'Мотивация', 'Сроки', 'Ограничения', 'Возражения', 'Решение', 'Следующий шаг']))

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Fact(StrictModel):
    category: Literal['profile', 'experience', 'situation', 'problem', 'consequence', 'importance', 'goal', 'motivation', 'urgency', 'constraints', 'objection', 'decision', 'next_step']
    value: str = Field(min_length=1, max_length=1000)
    quote: str = Field(min_length=1, max_length=2000)
    source_turn: str
    status: Literal['CONFIRMED', 'INFERRED'] = 'CONFIRMED'
    replaces: list[str] = Field(default_factory=list)
    confirms_hypothesis: str = Field(default='', description='ID ранее сохранённой гипотезы, только при прямом подтверждении клиентом.')

class Advice(StrictModel):
    action_type: Literal['GREETING', 'AGENDA', 'INTEREST', 'QUESTION', 'ANSWER', 'SUMMARIZE', 'PRESENT', 'CLARIFY', 'NEXT_STEP', 'NO_ACTION']
    suggestion: str = Field(max_length=600, description='Готовая реплика клиенту на вы: один вопрос или ответ, не инструкция менеджеру. Для NO_ACTION пусто.')
    reason: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str]
    target_gap: str
    confidence: float = Field(ge=0, le=1)

class DialogueEvent(StrictModel):
    kind: Literal['greeted', 'agenda_agreed', 'interest_shared', 'needs_confirmed', 'presentation_delivered', 'objection_opened', 'objection_resolved', 'ready_to_buy', 'next_step_agreed', 'declined', 'unavailable', 'question_asked', 'question_answered', 'offer_bridge_delivered', 'questions_declined']
    source_turn: str
    quote: str = Field(min_length=1, max_length=2000)
    topic: str = Field(default='', max_length=120, description='Стабильная смысловая тема: goal, experience, price, time, value, decision и т.п. Для вопроса/возражения обязательна.')

class Evidence(StrictModel):
    source_turn: str
    quote: str = Field(min_length=1, max_length=2000)

class HypothesisUpdate(StrictModel):
    id: str = Field(default='', description='Пусто для новой гипотезы; существующий ID для пересмотра.')
    statement: str = Field(min_length=1, max_length=500)
    status: Literal['INFERRED', 'REJECTED'] = 'INFERRED'
    evidence: list[Evidence] = Field(min_length=1, max_length=5)
    test_question: str = Field(max_length=400)

class Strategy(StrictModel):
    action: Literal['DISCOVERY', 'DEEP_DISCOVERY', 'NEED_DEVELOPMENT', 'RAPPORT', 'SALES_RECOVERY', 'SUMMARIZE', 'PRESENTATION', 'VALUE_BUILDING', 'OBJECTION_DIAGNOSIS', 'OBJECTION_HANDLING', 'PRICE_PRESENTATION', 'CLOSING', 'FOLLOW_UP', 'ANSWER', 'NO_ACTION', 'EXPLAIN_PROCESS', 'RETURN_TO_NEED', 'EXPLORE_MOTIVATION', 'EXPLORE_CONSEQUENCE', 'ALIGN_BEFORE_PRESENTATION', 'SELECT_SOLUTION']
    objective: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=800)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)

class ProductClaim(StrictModel):
    text: str = Field(min_length=1, max_length=600)
    source_id: str

class SelectionGap(StrictModel):
    topic: str = Field(min_length=1, max_length=100)
    impact: str = Field(min_length=1, max_length=300, description='Как разные ответы изменят подбор или аргументацию.')

class QuestionValue(StrictModel):
    topic: str = Field(min_length=1, max_length=100)
    new_information: str = Field(min_length=1, max_length=300)
    decision_impact: str = Field(min_length=1, max_length=300)
    already_answered: bool
    changes_selection: bool
    changes_argument: bool
    essential_now: bool

class SalesPlan(StrictModel):
    commercial_goal: str = Field(min_length=1, max_length=300)
    next_goal: str = Field(min_length=1, max_length=300)
    significant_needs: list[str] = Field(max_length=4)
    need_evidence_ids: list[str] = Field(max_length=8)
    critical_gaps: list[SelectionGap] = Field(max_length=2)
    useful_gaps: list[str] = Field(max_length=4)
    readiness: Literal['NEEDS_CLARIFICATION', 'READY_FOR_OFFER', 'READY_TO_BUY', 'NO_MORE_QUESTIONS']
    question: QuestionValue | None
    after_answer_action: Literal['DISCOVERY', 'SELECT_SOLUTION', 'PRESENTATION', 'PRICE_PRESENTATION', 'FOLLOW_UP', 'CLOSING', 'NO_ACTION']
    after_answer_goal: str = Field(max_length=300)

class Intelligence(StrictModel):
    hypotheses: list[HypothesisUpdate] = Field(default_factory=list, max_length=4)
    strategy: Strategy
    product_claims: list[ProductClaim] = Field(default_factory=list, max_length=5)
    sales_plan: SalesPlan | None = None
    manager_note: str = Field(default='', max_length=700, description='Служебная заметка, не произносить клиенту.')

class Analysis(StrictModel):
    facts: list[Fact] = Field(max_length=30)
    recommendation: Advice
    events: list[DialogueEvent] = Field(default_factory=list, max_length=12)
    intelligence: Intelligence | None = None

class NewSession(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    school: str = Field(default='', max_length=120)
    course: str = Field(default='', max_length=120)
    manager: str = Field(default='', max_length=80)

class TurnInput(StrictModel):
    role: Literal['client', 'manager']
    text: str = Field(min_length=1, max_length=6000)
    request_id: str = Field(min_length=8, max_length=100)
    expected_version: int = Field(ge=0)

class Feedback(StrictModel):
    value: Literal['helpful', 'rejected', 'hidden']
