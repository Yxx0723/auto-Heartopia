"""Observe cooking left-button SendInput calls without changing their inputs."""
import ctypes
import time
from datetime import datetime
import win32api
import win32gui
from utils.drivers.cooking_mouse_diagnostics import attempt


def now():return datetime.now().astimezone().isoformat()


def danger_timing(state,first_seen=None):
    result={'event_id':str(state['status_started']),'danger_first_seen_at':first_seen or now(),
            'danger_detected_at':now(),'first_seen_monotonic':time.perf_counter(),
            'danger_deadline':{'state':'unavailable'},'native_phase_started_at':{'state':'unavailable'}}
    raw=state['status_started']
    if raw>>62==1 and state['period_ms']>0:
        start=((raw&0x3fffffffffffffff)-621355968000000000)//10000
        result['native_phase_started_at']={'native_unix_ms':start,'raw_datetime':raw,'kind':raw>>62}
        result['danger_deadline']={'native_unix_ms':start+state['period_ms'],
            'clock_domain':'game phase clock; wall-clock alignment unverified'}
        result['deadline_source']='native CurrentStatusStartTime + PeriodTimeMs; not an inferred shorter UI acceptance window'
    return result


class ClickDiagnostics:
    def __init__(self,device,window,timing=None):
        self.device,self.window=device,window
        self.data={**(timing or {}),'game_received_input':{'state':'unavailable'},
                   'cooking_accepted_flip':{'state':'unavailable'}}
        for prefix in ('mouse_down','mouse_up'):
            for suffix in ('requested','inserted','last_error'):
                self.data[prefix+'_'+suffix]={'state':'unavailable','reason':'call not observed'}
        self.down_clock=None;self.native=None

    def __enter__(self):
        self.native=getattr(self.device,'native',None)
        self.original=getattr(self.native,'send',None)
        if self.original is None:return self
        def observed(count,events,size):
            event=events._obj;flags=event.mi.dwFlags if event.type==0 else 0
            alt_release=(event.type==1 and event.ki.wScan==0x38 and bool(event.ki.dwFlags&2))
            if alt_release:self.data['alt_release_at']=now()
            prefix='mouse_down' if flags==2 else 'mouse_up' if flags==4 else None
            if prefix:
                entry_error=ctypes.get_last_error()
                self.data['cursor_at_'+prefix]=attempt(self.window.pointer)
                self.data['alt_held_at_'+prefix]=attempt(lambda:bool(win32api.GetAsyncKeyState(0x12)&0x8000))
                self.data['foreground_window']=attempt(win32gui.GetForegroundWindow)
                stamp=time.perf_counter();self.data['click_'+('down' if flags==2 else 'up')+'_at']=now()
                self.data[prefix+'_requested']=count
                if flags==2:
                    self.down_clock=stamp
                    start=self.data.get('first_seen_monotonic')
                    self.data['danger_to_click_ms']=(stamp-start)*1000 if start is not None else {'state':'unavailable'}
                elif self.down_clock is not None:self.data['click_duration_ms']=(stamp-self.down_clock)*1000
                ctypes.set_last_error(entry_error)
            result=self.original(count,events,size);error=ctypes.get_last_error()
            if prefix:self.data.update({prefix+'_inserted':result,prefix+'_last_error':error})
            if alt_release:self.data.update(alt_release_inserted=result,alt_release_last_error=error)
            ctypes.set_last_error(error)
            return result
        self.native.send=observed
        return self

    def __exit__(self,*exc):
        if self.original is not None:self.native.send=self.original
        return False
