"""Atomic experiment journal. Uncertain paid work is never automatically replayed."""
import json
import os
from pathlib import Path


def atomic(path,data):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


class Journal:
    def __init__(self,out,identity,limit,resume=False):
        self.out=Path(out);self.path=self.out/'run_state.json'
        if resume:
            if not self.path.exists():raise ValueError('Legacy run has no request journal/configuration: cannot safely resume; no API calls made')
            self.data=json.loads(self.path.read_text(encoding='utf-8'))
            if self.data['identity']!=identity or self.data['limit']!=limit:raise ValueError('Resume scenario/config/code/limit mismatch')
            if self.data.get('active') and self.data['active'].get('calls'):
                raise ValueError('Uncertain checkpoint with reserved API calls: automatic replay forbidden')
        else:
            self.data={'identity':identity,'limit':limit,'used':0,'status':'incomplete','active':None,'results':[],'calls':[]}
        self.save()
    def save(self):atomic(self.path,self.data)
    def start(self,key):
        self.data.update(status='running',active={'key':key,'calls':[]});self.save()
    def reserve(self):
        if self.data['used']>=self.data['limit']:raise RuntimeError('Call limit reached')
        index=len(self.data['calls'])
        self.data['used']+=1
        self.data['calls'].append({'status':'reserved_unknown','checkpoint':self.data['active']['key']})
        self.data['active']['calls'].append(index);self.save()
        return index
    def response(self,index,raw):
        self.data['calls'][index].update(status='response_received',raw=raw);self.save()
    def commit(self,result):
        self.data['results'].append(result);self.data.update(active=None,status='incomplete');self.save()
        atomic(self.out/'partial_results.json',self.data['results'])
    def status(self,status,error=None):
        self.data['status']=status
        if error:self.data['error_type']=error
        self.save();self.summary()
    def summary(self):
        atomic(self.out/'summary.json',{'run_status':self.data['status'],'saved_checkpoints':len(self.data['results']),
             'api_calls_reserved':self.data['used'],'call_limit':self.data['limit'],
             'active_checkpoint':self.data['active'],'error_type':self.data.get('error_type'),
             'quality_comparison':'not_performed','note':'Reserved requests may have been charged; uncertain checkpoints are not retried.'})
