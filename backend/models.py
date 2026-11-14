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

class Advice(StrictModel):
    action_type: Literal['GREETING', 'AGENDA', 'INTEREST', 'QUESTION', 'ANSWER', 'SUMMARIZE', 'PRESENT', 'CLARIFY', 'NEXT_STEP', 'NO_ACTION']
    suggestion: str = Field(max_length=600, description='Готовая реплика клиенту на вы: один вопрос или ответ, не инструкция менеджеру. Для NO_ACTION пусто.')
    reason: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str]
    target_gap: str
    confidence: float = Field(ge=0, le=1)

class Analysis(StrictModel):
    facts: list[Fact] = Field(max_length=30)
    recommendation: Advice

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
