"""Local historical corpus validation and offline-only benchmark adapter."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def validate(cases, documents):
    docs = {d['id']: d for d in documents}
    if len(docs) != len(documents) or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate identifiers')
    for c in cases:
        d = docs[c['source_document']]
        turns = d['turns']
        by_id = {t['id']: t for t in turns}
        if len(by_id) != len(turns):
            raise ValueError('Duplicate turn')
        for t in turns:
            s = t['source']
            if (s['document_id'] != d['id'] or s['pdf_sha256'] != d['pdf_sha256']
                    or s['page'] != t['page'] or s['timestamp'] != t['time']
                    or len(s['raw_turn_sha256']) != 64):
                raise ValueError('Broken source reference')
        n = next(i for i,t in enumerate(turns) if t['id'] == c['checkpoint'])
        if c['history'] != turns[:n+1] or turns[n]['role'] != 'client':
            raise ValueError('History must be exact source prefix ending with client')
        ids = {t['id'] for t in c['history']}
        a = c['annotation']
        if not set(a['confirmed_goal']['evidence_ids']) <= ids:
            raise ValueError('Future goal evidence')
        for f in a['facts']:
            if (f['source_turn'] not in ids or by_id[f['source_turn']]['role'] != 'client'
                    or f['quote'] not in by_id[f['source_turn']]['text']):
                raise ValueError('Unsupported client fact')
        continuation = c['observed_continuation']
        span = continuation['intervening_turns']
        if span != turns[n+1:n+1+len(span)]:
            raise ValueError('Continuation must preserve source order')
        for key,role in [('manager_turns','manager'),('client_reaction_turns','client')]:
            for t in continuation[key]:
                if t not in span or t['role'] != role:
                    raise ValueError('Invalid observed speaker or source')
        if c['approved_claims']:
            raise ValueError('Historical claims must not become approved claims')
        if a['expert_score'] is not None and a['status'] != 'expert_reviewed':
            raise ValueError('Draft is not expert ground truth')
    return cases

def benchmark_case(case):
    """Draft offline input. No API execution path; labels kept out by benchmark.inputs."""
    from experiments.sales_benchmark.run import validate as validate_benchmark
    if any(t['role'] not in ('client','manager') for t in case['history']):
        raise ValueError('Unresolved speaker; manually review before benchmark export')
    a = case['annotation']
    value = dict(id=case['id'], description=a['active_theme'], origin='historical',
        turns=[{k:t[k] for k in ('id','role','text')} for t in case['history']],
        checkpoint=case['checkpoint'], facts=[dict(f,id=f'f{i}') for i,f in enumerate(a['facts'])],
        approved_claims=[], allowed_actions=a['allowed_action_families'],
        undesirable_actions=a['inappropriate_actions'],
        critical_errors=['Unverified historical product claim treated as current', 'Ignoring explicit stop'],
        privacy_status=case['privacy_status'], benchmark_eligible=False,
        source_note='Historical draft, local offline use only; separate permission required for API')
    return validate_benchmark(value)

def paired_inputs(case, variants=('A','B','C')):
    from experiments.sales_benchmark.run import inputs
    a,b = inputs(benchmark_case(case))
    # C is a future input contract, not an implemented third engine.
    return {v:deepcopy(a if v == 'A' else b) for v in variants}

def main():
    parser = argparse.ArgumentParser(description='Offline validation only; no API calls')
    parser.add_argument('--private-dir',type=Path,default=HERE/'private')
    parser.add_argument('--export-offline',action='store_true')
    args = parser.parse_args()
    docs=read(args.private_dir/'transcripts_redacted.json')
    cases=validate(read(args.private_dir/'corpus.json'),docs)
    exported=[]; blocked=[]
    for c in cases:
        try:
            exported.append(benchmark_case(c))
        except ValueError as e:
            blocked.append({'id':c['id'],'reason':str(e)})
    if args.export_offline:
        data={'mode':'offline_only','api_authorized':False,'cases':exported,'blocked':blocked}
        (args.private_dir/'offline_inputs.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'validated':len(cases),'offline_exportable':len(exported),
                      'unresolved_speaker':len(blocked),'api_authorized':False}))

if __name__ == '__main__':
    main()
