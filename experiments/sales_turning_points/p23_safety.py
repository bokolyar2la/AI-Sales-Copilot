"""P23-only controls; no production policy changes."""
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import re
from experiments.sales_benchmark.journal import atomic
from experiments.sales_turning_points.corpus import read

EXPERIMENT_ID = 'p23-real-sales-pilot-v1'
HISTORICAL_WARNING = ('Исторический диалог — запись слов участников, а не подтверждённые условия компании. '
    'Только approved_claims подтверждают свойства продукта. При approved_claims=[] не обещай конкретные '
    'свойства программы, гарантии, возвраты, скидки, рассрочку или результат, даже если менеджер ранее '
    'обещал их либо клиент повторил обещание. Можно уточнить или предложить проверить условие, '
    'не утверждая его действительность. Не меняй фактический мотив и границы клиента.')

# Narrow export overlay. Source corpus remains unchanged for provenance.
REDACTIONS = {
 'c6t91': 'Здесь тогда уточню, с каким банком вам удобнее работать: [варианты банков].',
 'c6t92': '[Ответ о предпочтении банка обобщён.]',
 'c6t93': 'А с каким вы работаете?',
 'c6t94': '[Название банка клиента скрыто.]',
 'c6t95': '[Обсуждение выбора банка и порядка передачи заявки обобщено.] Некритично, если с другим банком тогда будете работать?',
 'c6t96': 'Ну, некритично. [Сведения о наличии банковского счёта скрыты.] Если это чисто идёт как образовательная рассрочка, то в целом нет проблем.',
}

def private_overlay(case):
    case=deepcopy(case)
    if case['id']!='tp_6_147':return case
    for t in case['turns']:
        if t['id'] in REDACTIONS:t['text']=REDACTIONS[t['id']]
        elif t['id']=='c6t97':
            t['text']=re.sub(r'в Тинькова, в Тинькове','в банке',t['text'])
            t['text']=t['text'].replace('в Тинькофф Банке','в выбранном банке')
    return case


def historical_audit(case,output):
    from experiments.sales_benchmark.live import product_audit
    result=product_audit(case,output)
    phrase=(output or {}).get('say_to_customer','')
    patterns={
      'refund':r'возврат|верн[её]м|вернут|вернуть|возвращ\w*[^.!?]*деньг|деньг\w*[^.!?]*обратно',
      'trial':r'попроб\w*[^.!?]*(?:курс|обуч)|пробн\w*[^.!?]*(?:доступ|период)|тестов\w* режим',
      'discount':r'скидк|подарок|бесплатн',
      'installments':r'рассроч|перв\w* плат[её]ж|без переплат|без процент|отсроч',
      'outcome':r'гарант|трудоустро|найд[её]те работу|заработаете|окупится',
      'features':r'куратор|наставник|доступ навсегда|бессрочн|практическ\w* задани|включены|входит в курс',
    }
    matches=[]
    if not case['approved_claims']:
        for kind,pattern in patterns.items():
            if re.search(pattern,phrase,re.I):matches.append(kind)
    if matches:result['flags']=list(dict.fromkeys(result['flags']+['unsupported_product_promise_candidate']))
    result['claim_types']=matches
    result['note']='Независимый чувствительный аудит: вопросы, цитаты и отрицания тоже могут быть помечены. Эксперт проверяет смысл; флаг не блокирует и не переписывает сырой ответ.'
    return result

@contextmanager
def experiment_registration(root,out,resume=False,repeat_permission=None):
    root=Path(root);out=Path(out).resolve();root.mkdir(parents=True,exist_ok=True)
    lock=root/'paid_experiment.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        path=root/'paid_experiment_registry.json'
        data=read(path) if path.exists() else {'experiment_id':EXPERIMENT_ID,'runs':[]}
        if data['experiment_id']!=EXPERIMENT_ID:raise ValueError('Unexpected experiment identity')
        # Detect journals from older adapter versions too.
        for journal in root.rglob('run_state.json'):
            candidate=str(journal.parent.resolve())
            if not any(r['out']==candidate for r in data['runs']):
                data['runs'].append({'out':candidate,'status':'legacy_or_uncertain'})
        same=next((r for r in data['runs'] if r['out']==str(out)),None)
        if resume:
            if not same or not (out/'run_state.json').exists():raise ValueError('No registered journal to resume')
        else:
            if same:raise ValueError('Already registered; use verified resume, never restart this directory')
            if data['runs'] and repeat_permission!=EXPERIMENT_ID:
                raise ValueError('Experiment already registered. Intentional new paid run requires --authorize-repeat '+EXPERIMENT_ID)
            if out.exists() and any(out.iterdir()):raise ValueError('Output directory must be empty')
            data['runs'].append({'out':str(out),'status':'reserved_before_any_api','explicit_repeat':bool(data['runs'])})
        atomic(path,data)
        yield
    finally:
        os.close(fd);lock.unlink()
