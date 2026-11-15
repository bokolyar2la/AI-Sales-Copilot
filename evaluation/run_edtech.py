"""Run explicitly authorised paid synthetic checks: python -m evaluation.run_edtech --live."""
import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from dotenv import load_dotenv
from backend.context import apply_analysis
from backend.providers import CompatibleProvider
from backend.prompts import VERSION
from .edtech_cases import CASES

ROOT = Path(__file__).resolve().parents[1]


async def run_case(case, semaphore):
    async with semaphore:
        turns = [{'id': f't{i}', 'role': role, 'text': text} for i, (role, text) in enumerate(case['history'])]
        turns.append({'id': 'latest', 'role': 'client', 'text': case['text']})
        s = {'turns': turns, 'facts': [], 'recommendations': [], 'sales_context': {'school': 'Skillbox', 'course': '', 'manager': ''}}
        # Reconstruct deterministic price request counts from actual history.
        from backend.dialogue_policy import update_state
        prefix = {**s, 'turns': []}
        for turn in turns[:-1]:
            prefix['turns'].append(turn)
            prefix['dialogue_state'] = update_state(prefix, [])
        if prefix.get('dialogue_state'):
            s['dialogue_state'] = prefix['dialogue_state']
        start = time.perf_counter()
        try:
            analysis, usage = await CompatibleProvider().analyze(s, {'products': [], 'status': 'Подтверждённые цены и свойства программ не загружены.'})
            raw = analysis.model_dump()
            apply_analysis(s, analysis)
            rec = s['recommendations'][-1]
            checks = {'action': rec['action_type'] in case['actions'],
                      'one_question': rec['suggestion'].count('?') <= 1,
                      'no_instructions': not re.search(r'^(Уточните|Спросите|Предложите|Расскажите клиенту)', rec['suggestion']),
                      'no_invented_amount': not re.search(r'\d[\d .,]*(?:руб|₽|тысяч)', rec['suggestion'], re.I)}
            if case['group'] == 'refusal':
                checks['refusal_recorded'] = s['dialogue_state']['outcome'] == 'declined'
            if case['group'] == 'opening' and case['id'] in ('opening-1', 'opening-2', 'opening-3'):
                checks['no_false_need_confirmation'] = not s['dialogue_state']['needs_confirmed']
                checks['no_false_agreement_fact'] = not any(f['category'] in ('goal', 'next_step', 'decision') for f in s['facts'])
            return {'id': case['id'], 'text': case['text'], 'checks': checks, 'passed': all(checks.values()),
                    'latency_ms': round((time.perf_counter()-start)*1000), 'usage': usage,
                    'raw': raw, 'recommendation': rec, 'state': s['dialogue_state']}
        except Exception as exc:
            # No exception message: provider bodies may contain sensitive details.
            return {'id': case['id'], 'passed': False, 'error_type': type(exc).__name__}


async def main(args):
    load_dotenv(ROOT / '.env')
    cases = [c for c in CASES if not args.group or c['group'] == args.group][:args.limit]
    semaphore = asyncio.Semaphore(3)
    rows = await asyncio.gather(*(run_case(c, semaphore) for c in cases))
    report = {'prompt_version': VERSION, 'scope': 'Синтетический набор разработки; не экспертная приёмка и не измерение роста продаж.',
              'total': len(rows), 'passed': sum(r['passed'] for r in rows), 'results': rows}
    target = ROOT / 'evaluation' / args.output
    if target.parent.resolve() != (ROOT / 'evaluation').resolve():
        raise ValueError('Output must be a filename in evaluation')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'total': report['total'], 'passed': report['passed'], 'failed': [r['id'] for r in rows if not r['passed']], 'report': str(target)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Платные проверки вымышленных диалогов через настроенный API')
    parser.add_argument('--live', action='store_true', help='Подтверждает отправку синтетических сценариев провайдеру')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--group', default='')
    parser.add_argument('--output', default='edtech-live-result.json')
    args = parser.parse_args()
    if not args.live:
        parser.error('Для отправки сценариев и расходования API-баланса требуется --live')
    asyncio.run(main(args))
