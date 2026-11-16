"""Per-request numeric timings. No dialogue, credentials or model reasoning."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from time import perf_counter
from uuid import uuid4

_current = ContextVar('copilot_diagnostics', default=None)


@contextmanager
def capture():
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    trace = {'trace_id': str(uuid4()), 'stages_ms': {}, 'calls': [], 'validations': []}
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)


@contextmanager
def timed(name):
    start = perf_counter()
    try:
        yield
    finally:
        trace = _current.get()
        if trace is not None:
            trace['stages_ms'][name] = trace['stages_ms'].get(name, 0) + (perf_counter()-start)*1000


def add_call(call):
    trace = _current.get()
    if trace is not None:
        trace['calls'].append(call)


def record_validation(result):
    trace = _current.get()
    if trace is not None:
        trace['validations'].append(deepcopy(result))


def snapshot(trace):
    result = deepcopy(trace)
    result['stages_ms'] = {k: round(v, 3) for k,v in result['stages_ms'].items()}
    result['request_count'] = len(result['calls'])
    result['retry_count'] = max(0, len(result['calls'])-1)
    result['retry_request_ms'] = round(sum(c.get('request_ms',0) for c in result['calls'][1:]),3)
    return result


def validation_failure(exc, stage, attempt):
    """Log only controlled metadata; never exception text, input or provider bodies."""
    import json
    import logging
    from .models import Analysis
    allowed = set()
    schema = Analysis.model_json_schema()
    for node in [schema, *schema.get('$defs', {}).values()]:
        allowed.update(node.get('properties', {}))
    fields = []
    if hasattr(exc, 'errors'):
        for e in exc.errors(include_input=False, include_context=False, include_url=False)[:12]:
            fields.append({'path':[x if isinstance(x,int) or x in allowed else '<unknown>' for x in e['loc']],
                           'type':e['type']})
    known = {
        'Unsupported fact source':('fact_source','facts.source_turn/quote'),
        'Invalid replacement':('fact_replacement','facts.replaces'),
        'Invalid evidence':('evidence_source','recommendation.evidence_ids'),
        'Unsupported need evidence':('need_source','intelligence.sales_plan.need_evidence_ids'),
        'Unsupported hypothesis source':('hypothesis_source','intelligence.hypotheses.evidence'),
        'Unknown strategy evidence':('strategy_source','intelligence.strategy.evidence_ids'),
        'Missing intelligence result':('missing_intelligence','intelligence'),
        'Missing sales plan':('missing_plan','intelligence.sales_plan'),
        'Missing sales_plan.need_depth':('missing_depth','intelligence.sales_plan.need_depth')}
    code, field = known.get(str(exc), ('validation_error', stage))
    if fields:
        code = 'invalid_json' if any(e['type']=='json_invalid' for e in fields) else 'invalid_schema'
    elif str(exc).startswith('Event '):
        code, field = 'event_source', 'events.source_turn/quote'
    entry = {'attempt':attempt, 'stage':stage, 'code':code, 'field':field, 'fields':fields}
    record_validation(entry)
    logging.getLogger('copilot.validation').warning('analysis_validation %s', json.dumps(entry, ensure_ascii=False))
    return entry
