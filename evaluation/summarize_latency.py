"""Recompute numerical tables from saved observations; makes no API calls."""
import json
import hashlib
from pathlib import Path
from statistics import mean
ROOT=Path(__file__).resolve().parent
rows=json.loads((ROOT/'latency-results.json').read_text(encoding='utf-8'))['results']
reviews=json.loads((ROOT/'latency-quality-review.json').read_text(encoding='utf-8'))['results']
for r in rows:
    rating=next(q for q in reviews if q['case']==r['case'] and q['mode']==r['mode'])
    material=json.dumps({'recommendation':r.get('recommendation'),'error_type':r.get('error_type')},sort_keys=True,ensure_ascii=False)
    if rating.get('answer_sha256')!=hashlib.sha256(material.encode()).hexdigest():
        raise ValueError('Review is stale: reassess the new responses before aggregation')

def quantile(v,p):
    if not v:return None
    v=sorted(v);position=(len(v)-1)*p;i=int(position)
    return round(v[i]+(v[min(i+1,len(v)-1)]-v[i])*(position-i),3)
def distribution(v):return {'n':len(v),'p50':quantile(v,.5),'p95':quantile(v,.95)}
summary={}
for mode in ('current','stream_off'):
    group=[r for r in rows if r['mode']==mode]
    complete=[r for r in group if 'timing' in r]
    delivered=[r for r in complete if r['timing']['outcome'] in ('hint','pause')]
    calls=[c for r in complete for c in r['timing']['server']['calls']]
    quality=[r for r in reviews if r['mode']==mode]
    stages=sorted({k for r in complete for k in r['timing']['server']['stages_ms']})
    summary[mode]={
        'samples':len(group),'delivered_including_correct_pause':len(delivered),
        'failure_count':len(group)-len(delivered),'failure_rate':(len(group)-len(delivered))/len(group),
        'blocked':sum(r.get('timing',{}).get('outcome')=='blocked' for r in group),
        'client_timeout_count':sum(r.get('error_type')=='TimeoutError' for r in group),
        'observed_completion_or_timeout_ms':distribution([r.get('timing',{}).get('display_proxy_ms',r.get('elapsed_ms')) for r in group]),
        'delivered_display_proxy_ms':distribution([r['timing']['display_proxy_ms'] for r in delivered]),
        'calls_observed':len(calls),'calls_missing_samples':len(group)-len(complete),
        'retry_turns_observed':sum(r['timing']['server']['retry_count']>0 for r in complete),
        'retry_request_ms_total':round(sum(r['timing']['server']['retry_request_ms'] for r in complete),3),
        'call_metrics':{k:distribution([c[k] for c in calls if c.get(k) is not None]) for k in ('ttft_ms','first_content_ms','content_generation_ms','request_ms')},
        'stages_ms':{k:distribution([r['timing']['server']['stages_ms'].get(k,0) for r in complete]) for k in stages},
        'render_wait_ms':distribution([r['timing']['render_wait_ms'] for r in complete]),
        'prompt_tokens':distribution([c['usage']['prompt_tokens'] for c in calls if 'usage' in c]),
        'completion_tokens':distribution([c['usage']['completion_tokens'] for c in calls if 'usage' in c]),
        'quality_mean_delivered_score_out_of_12':round(mean(r['delivered_score'] for r in quality),3),
        'quality_mean_content_available_out_of_12':round(mean(sum(r['scores']) for r in quality if r['scores']),3),
        'provider_fraction_of_display_time_complete_samples':sum(r['timing']['server']['stages_ms']['provider_requests'] for r in complete)/sum(r['timing']['display_proxy_ms'] for r in complete)
    }
(ROOT/'latency-summary.json').write_text(json.dumps({'quantile':'linear interpolation, position=(n-1)*p; timeout observations are censored, not successful hint times','summary':summary},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({m:{k:v for k,v in d.items() if k in ('quality_mean_delivered_score_out_of_12','failure_rate','provider_fraction_of_display_time_complete_samples','retry_request_ms_total')} for m,d in summary.items()},ensure_ascii=False,indent=2))
