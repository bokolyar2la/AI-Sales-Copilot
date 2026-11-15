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
