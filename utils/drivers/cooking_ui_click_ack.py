"""Read acknowledgement from the already verified cooking button, no input."""
import math
from utils.drivers.cooking_click_diagnostics import now


class CookingUiClickAck:
    def __init__(self,reader,target):
        self.reader=reader;self.target=target
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

    def observe(self):
        r=self.reader;m=r.ui.meta
        state=r.state()
        if state['scope']!=self.target['scope'] or state['stove_identity']!=self.target['stove_identity'] \
                or (state['status']==3 and str(state['status_started'])!=self.target['event_id']):
            raise ValueError('等待 UI 点击确认时灶台、场景或 Danger 身份变化，未重试点击')
        def stable():
            return (m.ref(self.icon,'ui')==self.ui and m.ref(self.ui,'icon_btn')==self.button
                and m.ref(self.button,'_clickHandler')==self.button_cb
                and m.ref(self.button_cb,'_target')==self.icon
                and m.ref(self.icon,'_clickHandler')==self.icon_cb
                and m.ref(self.icon_cb,'_target')==self.model
                and (r.integer(self.button_cb,'method',24,'Q',8),r.integer(self.icon_cb,'method',24,'Q',8))==self.methods
                and r.integer(self.model,'<token>k__BackingField')==self.token)
        if not stable():raise ValueError('等待 UI 确认时原按钮或回调绑定变化，未重试点击')
        value=r.integer(self.button,'_lastClickTime',12,'f',4)
        if not math.isfinite(value) or not stable():raise ValueError('UI 点击时间或按钮身份不稳定，未重试点击')
        return {'button':self.button,'icon':self.icon,'event_model':self.model,
                'event_id':self.target['event_id'],'last_click_time':value,'observed_at':now(),
                'native_state':state}
