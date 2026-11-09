"""Read acknowledgement from the already verified cooking button, no input."""
import math
import time
import traceback
import json
from copy import deepcopy
from utils.drivers.cooking_click_diagnostics import now


class CookingUiClickAck:
    def __init__(self,reader,target):
        self.reader=reader;self.target=target
        self.raw_observations=[];self._current_reads=[];self._observation_error=None
        try:self.pre_click_snapshot=deepcopy(reader.hud_diagnostics)
        except Exception:self.pre_click_snapshot={'state':'unavailable','reason':'existing HUD evidence could not be copied'}
        d=reader.hud_diagnostics
        rows=[row for row in d['widgets'] if row.get('event_model_match') and row.get('button')]
        if d.get('kind')!='stir' or d.get('event_id')!=target['event_id'] \
                or d.get('widget_match_count')!=1 or not d.get('binding_stable') or len(rows)!=1:
            raise ValueError('翻锅 UI 确认没有唯一的已验证按钮，未点击')
        row=rows[0];m=reader.ui.meta
        self.button,self.icon,self.model=row['button'],row['icon'],row['callback_target']
        self.ui=m.ref(self.icon,'ui');self.button_cb=m.ref(self.button,'_clickHandler')
        self.icon_cb=m.ref(self.icon,'_clickHandler')
        self.methods=(reader.integer(self.button_cb,'method',24,'Q',8),
                      reader.integer(self.icon_cb,'method',24,'Q',8))
        self.token=reader.integer(self.model,'<token>k__BackingField')
        self.before=self.observe()

    def _observe(self):
        r=self.reader;m=r.ui.meta
        state=self._read('native_state',r.state)
        if state['scope']!=self.target['scope'] or state['stove_identity']!=self.target['stove_identity'] \
                or (state['status']==3 and str(state['status_started'])!=self.target['event_id']):
            raise ValueError('等待 UI 点击确认时灶台、场景或 Danger 身份变化，未重试点击')
        def stable():
            return (self._read('icon.ui',m.ref,self.icon,'ui')==self.ui and self._read('button',m.ref,self.ui,'icon_btn')==self.button
                and self._read('button.callback',m.ref,self.button,'_clickHandler')==self.button_cb
                and self._read('button.callback.target',m.ref,self.button_cb,'_target')==self.icon
                and self._read('icon.callback',m.ref,self.icon,'_clickHandler')==self.icon_cb
                and self._read('icon.callback.target',m.ref,self.icon_cb,'_target')==self.model
                and (self._read('button.callback.method',r.integer,self.button_cb,'method',24,'Q',8),self._read('icon.callback.method',r.integer,self.icon_cb,'method',24,'Q',8))==self.methods
                and self._read('event.token',r.integer,self.model,'<token>k__BackingField')==self.token)
        if not stable():raise ValueError('等待 UI 确认时原按钮或回调绑定变化，未重试点击')
        value=self._read('last_click_time',r.integer,self.button,'_lastClickTime',12,'f',4)
        if not math.isfinite(value) or not stable():raise ValueError('UI 点击时间或按钮身份不稳定，未重试点击')
        return {'button':self.button,'icon':self.icon,'event_model':self.model,
                'event_id':self.target['event_id'],'last_click_time':value,'observed_at':now(),
                'native_state':state}

    def _read(self,field,source,*args):
        try:
            value=source(*args)
        except Exception as exc:
            try:self._current_reads.append((field,None,exc,time.perf_counter()))
            except Exception:pass
            raise
        try:self._current_reads.append((field,value,None,time.perf_counter()))
        except Exception:pass
        return value

    def observe(self):
        self._current_reads=[]
        started=time.perf_counter();error=None
        try:return self._observe()
        except Exception as exc:
            error=exc
            raise
        finally:
            try:self.raw_observations.append((started,time.perf_counter(),self._current_reads,error))
            except Exception:pass

    def finalize(self,failed=False):
        """Only called after the original click/ALT cleanup. Never changes outcome."""
        started=time.perf_counter();rows=[];baseline={};first=None;first_at=None
        for index,(begin,end,reads,error) in enumerate(self.raw_observations):
            checks=[];changed=[]
            for field,value,read_error,at in reads:
                if read_error is not None:
                    after={'state':'unavailable','error_type':type(read_error).__name__,'message':str(read_error)}
                    difference=None
                else:
                    # First successful baseline read is immutable, including repeated
                    # fields in the initial two-pass stability observation.
                    had_baseline=field in baseline
                    difference=baseline[field]!=value if had_baseline else False if index==0 else None
                    if index==0 and not had_baseline:baseline[field]=value
                    after={'value':value}
                before=baseline.get(field,{'state':'unavailable'})
                checks.append({'field':field,'before':before,'after':after,'changed':difference,'observed_monotonic':at})
                if difference:
                    changed.append(field)
                    if first is None:first=field;first_at=at
            rows.append({'ack_observation_index':index,'event_id':self.target['event_id'],
                'ack_binding_checks':checks,'ack_binding_failed_fields':changed,
                'ack_binding_before':dict(baseline),
                'ack_binding_after':{x['field']:x['after'] for x in checks},
                'ack_native_phase':next((x['after'] for x in checks if x['field']=='native_state'),{'state':'unavailable'}),
                'ack_first_changed_field':first,'ack_first_changed_at':first_at,
                'ack_first_changed_semantics':'first discovered in original read order; not game mutation order',
                'ack_observation_duration_ms':(end-begin)*1000,
                'error':None if error is None else {'type':type(error).__name__,'message':str(error),'traceback':''.join(traceback.format_exception(type(error),error,error.__traceback__))}})
        snapshot_start=time.perf_counter()
        try:post=self._snapshot() if failed else {'state':'unavailable','reason':'post-failure read not requested or inputs not confirmed released'}
        except Exception as exc:post={'state':'unavailable','error_type':type(exc).__name__,'message':str(exc)}
        result={'pre_click_snapshot':self.pre_click_snapshot,'pre_click_snapshot_source':'existing validated HUD evidence; not re-read',
            'confirmation_observed_values':rows,'post_failure_snapshot':post,
            'post_failure_snapshot_semantics':'read after cleanup; not failure-instant evidence or first-change evidence',
            'ack_snapshot_duration_ms':(time.perf_counter()-snapshot_start)*1000,
            'ack_diagnostic_read_count':{'confirmation_extra_logical_reads':0,'physical_reads':'unavailable'},
            'ack_analysis_duration_ms':(time.perf_counter()-started)*1000}
        t=time.perf_counter()
        try:
            encoded,errors=_diagnostic_json_copy(result)
            encoded['diagnostic_serialization_errors']=errors
            json.dumps(encoded,ensure_ascii=False,allow_nan=False)
        except Exception as exc:
            # Preserve a minimal record even if optional diagnostic encoding fails.
            encoded={'state':'diagnostic_encoding_failed','diagnostic_serialization_errors':[
                {'error_type':type(exc).__name__,'error':str(exc)}],
                'event_id':self.target.get('event_id') if isinstance(self.target.get('event_id'),str) else None}
        encoded['ack_diagnostic_serialization_ms']=(time.perf_counter()-t)*1000
        return encoded

    def _snapshot(self):
        r=self.reader;m=r.ui.meta
        reads={
            'icon.ui':lambda:m.ref(self.icon,'ui'),
            'button':lambda:m.ref(self.ui,'icon_btn'),
            'button.callback':lambda:m.ref(self.button,'_clickHandler'),
            'button.callback.target':lambda:m.ref(self.button_cb,'_target'),
            'button.callback.method':lambda:r.integer(self.button_cb,'method',24,'Q',8),
            'event.token':lambda:r.integer(self.model,'<token>k__BackingField'),
            'last_click_time':lambda:r.integer(self.button,'_lastClickTime',12,'f',4),
            'gameObject.active':lambda:r.layout.active(m.ref(self.ui,'<gameObject>k__BackingField')),
            'root_visible.active':lambda:r.layout.active(m.ref(self.ui,'root_visible_go')),
            'native_state':r.state,
        }
        result={}
        for field,read in reads.items():
            try:result[field]={'available':True,'value':read()}
            except Exception as exc:result[field]={'available':False,'state':'unavailable','error_type':type(exc).__name__,'error':str(exc),'traceback':traceback.format_exc()}
        result.update(self._callback_snapshots())
        # No verified generic interactability reader: never infer it from visibility.
        result['button.interactable']={'available':False,'state':'unavailable','reason':'no verified read-only accessor in acknowledgement reader'}
        widgets=self.reader.hud_diagnostics.get('widgets',[])
        row=next((x for x in widgets if x.get('button')==self.button),{})
        group=row.get('parent_list')
        result['list']={'available':False,'state':'unavailable','reason':'live list reference not retained by existing acknowledgement contract'}
        if group:
            try:result['list.members']={'available':True,'value':m.list_refs(m.ref(group,'_children'))}
            except Exception as exc:result['list.members']={'available':False,'state':'unavailable','error_type':type(exc).__name__,'error':str(exc)}
        return result

    def _callback_snapshots(self):
        """Post-cleanup evidence only; every value retains its actual owner."""
        started=time.perf_counter();r=self.reader;m=r.ui.meta
        def read(owner,field,fn):
            try:return {'owner':owner,'field':field,'available':True,'value':fn()}
            except Exception as exc:
                return {'owner':owner,'field':field,'available':False,'state':'unavailable',
                    'error_type':type(exc).__name__,'error':str(exc),'traceback':traceback.format_exc()}
        def unavailable(owner,field,reason):
            return {'owner':owner,'field':field,'available':False,'state':'unavailable','reason':reason}
        def describe(address):
            info=m.obj(address)
            return {'class_address':info['address'],'name':info['name'],'namespace':info['namespace']}
        def parse(address):
            if not address:
                return {'owner':address,'delegate_type':unavailable(address,'type','null or unavailable delegate'),
                    'target':unavailable(address,'_target','null or unavailable delegate'),
                    'method':unavailable(address,'method','null or unavailable delegate')}
            return {'owner':address,
                'delegate_type':read(address,'type',lambda:describe(address)),
                'target':read(address,'_target',lambda:m.ref(address,'_target')),
                'method':read(address,'method',lambda:r.integer(address,'method',24,'Q',8))}
        first=read(self.icon,'_clickHandler',lambda:m.ref(self.icon,'_clickHandler'))
        current=parse(first['value'] if first.get('available') else None)
        after=read(self.icon,'_clickHandler',lambda:m.ref(self.icon,'_clickHandler'))
        stable=(first['value']==after['value']) if first.get('available') and after.get('available') else None
        current['reference_stable']=stable
        current['evidence_scope']='one captured delegate address; not a stable current binding' if stable is not True else 'current icon reference equal before and after parsing; ABA changes not excluded'
        old=parse(self.icon_cb)
        def equal(field):
            a,b=old[field],current[field]
            return a['value']==b['value'] if stable is True and a.get('available') and b.get('available') else None
        return {'old_callback_snapshot':old,'current_callback_snapshot':current,
            'callback_reference_checks':{'icon_owner':self.icon,'first':first,'after_current_parse':after,
                'reference_stable':stable,'same_address':self.icon_cb==first['value'] if first.get('available') else None,
                'same_target':equal('target'),'same_method':equal('method'),
                'comparison_scope':'post-cleanup values; unavailable or unstable comparisons are null'},
            'callback_snapshot_duration_ms':(time.perf_counter()-started)*1000}


def _diagnostic_json_copy(value):
    """JSON-safe evidence copy only. Never normalize business/read values in place."""
    errors=[];active=set()
    def convert(item,path):
        if item is None or type(item) in (str,bool,int):return item
        if type(item) is float:
            if math.isfinite(item):return item
            return {'state':'non_finite','original_type':'float',
                    'meaning':'NaN' if math.isnan(item) else 'positive_infinity' if item>0 else 'negative_infinity'}
        if type(item) in (dict,list,tuple):
            if id(item) in active:
                errors.append({'path':path,'error_type':'CircularReference'})
                return {'state':'unavailable','reason':'circular diagnostic value'}
            active.add(id(item))
            try:
                if type(item) is dict:
                    result={}
                    for key,child in item.items():
                        if type(key) is not str:
                            errors.append({'path':path,'error_type':'UnsupportedKey','original_type':type(key).__name__})
                            continue
                        result[key]=convert(child,path+'.'+key)
                    return result
                return [convert(child,path+'['+str(i)+']') for i,child in enumerate(item)]
            finally:active.remove(id(item))
        errors.append({'path':path,'error_type':'UnsupportedValue','original_type':type(item).__name__})
        return {'state':'unavailable','reason':'unsupported diagnostic value','original_type':type(item).__name__}
    return convert(value,'$'),errors
