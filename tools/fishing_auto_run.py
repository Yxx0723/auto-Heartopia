"""Foreground fishing runner with observed aim and bounded input."""
import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import struct
import sys
import time

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

# The T panel needs a rendered frame before its icons can be clicked, and one
# more before it is gone again. Both values match the existing tool-panel
# helpers in utils/vision/repair_box.py so the fishing flow behaves the same.
TOOL_PANEL_TAP_SECONDS = .1
TOOL_PANEL_RENDER_SECONDS = .4

from utils.core import thresholds as policy
from utils.core.fishing_log import fishing_event, format_fishing_event
from utils.core.fishing_policy import FishTarget, TensionControl, confirmed_catch
from utils.drivers.fishing_archive import FishingArchive
from utils.drivers.fishing_input import FishingInput
from utils.drivers.fishing_state import (FoodInventoryReader, fish_inventory, player_fishing_status,
                                         read_player_fishing)
from utils.drivers.mono_scene import MemorySceneMap
from utils.drivers.town_window import TimelineInput, TownWindow
from utils.vision.aim_ring import find_ring
from utils.vision.fish_shadow import find_shadows
from utils.vision.fishing_line import FishingLine
from utils.vision.fishing_rod import FishingRodMenu, rod_template


def find_game():
    games = []
    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip()=='心动小镇':
            games.append(hwnd)
    win32gui.EnumWindows(visit,None)
    if len(games)!=1:
        raise RuntimeError('未找到唯一的游戏窗口')
    return games[0]


class BindingError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class FishingRunner:
    def __init__(self, duration=None, out_dir=None, verbose=True, *, profile=None,
                 hwnd=None, stop_requested=None, on_event=None, catch_target=None):
        if duration is not None and (not math.isfinite(duration) or duration<=0):
            raise ValueError('钓鱼运行时长必须大于零')
        self.duration,self.catch_target = duration,catch_target
        self.out = Path(out_dir) if out_dir else ROOT/'fishing-runs'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.out.mkdir(parents=True,exist_ok=True)
        self.verbose,self.on_event = verbose,on_event
        self.stop_requested = stop_requested or (lambda:False)
        self.hwnd = hwnd if hwnd is not None else find_game()
        self.pid = win32process.GetWindowThreadProcessId(self.hwnd)[1]
        self.size = win32gui.GetClientRect(self.hwnd)[2:]
        self.window = TownWindow('心动小镇')
        self.window.hwnd = self.hwnd
        self.device = TimelineInput(self.window)
        self.check_input()
        self.profile = Path(profile or ROOT/'webui/coordinate-monitor/profile.json')
        if catch_target is not None and (type(catch_target) is not int or catch_target<1):
            raise ValueError('目标钓获数量必须大于零')
        self.verified_images = set()
        self.inventory_reader = None
        self.scene = MemorySceneMap(self.pid,self.profile)
        self.input = FishingInput(self.device,self.check_input)
        self.phase = 'idle'
        self.started = self.phase_at = time.monotonic()
        self.stats = Counter()
        self.failures = Counter()
        self.consecutive_failures = self.screen_failures = self.read_failures = 0
        self.last_game_state = self.ring = None
        self.target = FishTarget()
        self.line = FishingLine()
        self.tension = TensionControl()
        self.cast_open = self.cast_effective = False
        self.cycle_seconds = 0.
        self.cycle_count = 0
        self.inventory_before = None
        self.aim_poll_at = 0.
        self.last_color = None
        self.aim_evidence = None
        self.landing_evidence = None
        self.archive = None
        # True while this runner has the T panel open for the user. Only opened
        # once per binding attempt so a wrong keypress can never repeat.
        self.tool_panel_open = False
        # T-panel icon matcher and the number of automatic equip tries already
        # spent; both stay empty when the rod artwork was not installed.
        self.rod_icon_menu = None
        self.panel_menu_cache = None
        self.equip_attempts = 0

    @property
    def casts(self):
        return self.stats['casts']

    def identity(self):
        trace = self.scene.memory.snapshot()['paths'][0]['trace']
        entity = next((t['pointer'] for t in trace if t['field']=='<entity>k__BackingField'),None)
        if not entity:
            raise ValueError('找不到当前玩家对象')
        return entity

    def bind_rod(self):
        meta = self.scene.meta
        entity = self.identity()
        def players():
            return [p for p in meta.dictionary(meta.ref(entity,'_components'),max_items=128)
                    if meta.obj(p)['name']=='LocalPlayerComponent']
        found = players()
        if len(found)!=1:
            raise BindingError('PLAYER_UNAVAILABLE','玩家对象未确认')
        lpc = found[0]
        rod = meta.ref(lpc,'_interactTool')
        rod_info = meta.obj(rod) if rod else None
        tool = rod_info['name'] if rod_info else None
        if tool!='HandHoldFishingRod':
            if self.identity()!=entity or players()!=found or meta.ref(lpc,'_interactTool')!=rod:
                raise BindingError('TOOL_REFERENCE_INVALID','手持工具引用正在变化')
            status = 'OTHER_TOOL' if tool else 'NO_TOOL'
            raise BindingError(status,f'当前工具：{tool or "未装备"}')
        binding = player_fishing_status(meta,lpc,self.verified_images)
        float_component = meta.ref(rod,'floatComponent')
        if not float_component:
            raise BindingError('FLOAT_INITIALIZING','等待浮标初始化')
        float_info = meta.obj(float_component)
        if float_info['name']!='FloatComponent':
            raise BindingError('TOOL_REFERENCE_INVALID','鱼竿浮标类型不符')
        observed = self.read_binding_state(meta,rod,rod_info,float_component,float_info,binding)
        if self.identity()!=entity or players()!=found or meta.ref(lpc,'_interactTool')!=rod \
                or meta.ref(rod,'floatComponent')!=float_component or meta.ref(lpc,'Status')!=binding[0]:
            raise BindingError('TOOL_REFERENCE_INVALID','读取期间钓鱼对象归属变化')
        self.meta,self.entity,self.lpc = meta,entity,lpc
        self.rod,self.rod_info = rod,rod_info
        self.float_component,self.float_info,self.player_binding = float_component,float_info,binding
        self.identity_at = time.monotonic()
        return observed

    def tap_key(self, key):
        """Press and release one key through the same device used for playback."""
        self.input.guard()
        try:
            self.device.execute({'type': 'key_down', 'key': key})
            time.sleep(TOOL_PANEL_TAP_SECONDS)
        finally:
            self.device.execute({'type': 'key_up', 'key': key})

    def open_tool_panel(self, status):
        """Open the T panel and leave the rod for the user to pick.

        There is no verified icon template for the fishing rod, so nothing is
        clicked here. The runner only removes the need to find the panel; an
        unverified coordinate can never select the wrong tool. Once the memory
        binding reports the rod, the panel is closed again automatically.
        """
        if self.tool_panel_open:
            return
        self.tool_panel_open = True
        try:
            self.tap_key('t')
        except OSError:
            self.tool_panel_open = False
            return
        time.sleep(TOOL_PANEL_RENDER_SECONDS)
        self.log('tool_panel', '当前未持鱼竿，已打开工具面板，请点击鱼竿，程序确认后自动开始',
                 binding_status=status)

    def close_tool_panel(self):
        """Put the T panel away again so casting never lands on its buttons."""
        if not self.tool_panel_open:
            return
        self.tool_panel_open = False
        try:
            self.tap_key('t')
        except OSError:
            return
        time.sleep(TOOL_PANEL_RENDER_SECONDS)

    def panel_menu(self):
        """Matcher for the axe-pick tile: the panel's stable presence marker."""
        if self.panel_menu_cache is None:
            from utils.vision.axe_pick import AxePickMenu
            self.panel_menu_cache = AxePickMenu(self.profile.parent/'axe_pick_icon.png')
        return self.panel_menu_cache

    def panel_visible(self):
        try:
            return self.panel_menu().locate(self.window.snapshot()) is not None
        except (OSError,ValueError):
            return False

    def close_panel_if_open(self):
        """Close the T panel only when it is actually on screen.

        Selecting a tile usually closes it by itself, so an unconditional tap
        would reopen the panel right before the first cast and swallow it.
        """
        for key in ('t','esc'):
            if not self.panel_visible():
                break
            try:
                self.tap_key(key)
            except OSError:
                break
            time.sleep(TOOL_PANEL_RENDER_SECONDS)
        self.tool_panel_open = False

    def rod_icon(self):
        """Load the captured rod tile once; ``None`` when it is not installed."""
        if self.rod_icon_menu is None:
            template = rod_template(self.profile.parent)
            if template is None:
                return None
            self.rod_icon_menu = FishingRodMenu(template)
        return self.rod_icon_menu

    def equip_rod(self):
        """Open the T panel, click the rod tile and confirm it through memory.

        Every click is followed by a memory binding read, so a wrong tile is
        detected instead of being trusted. Missing artwork keeps the previous
        manual prompt: an unverified coordinate never gets clicked.
        """
        if self.equip_attempts >= policy.FISHING_EQUIP_ATTEMPTS:
            return False
        try:
            menu = self.rod_icon()
        except (OSError,ValueError) as exc:
            self.log('binding_wait',f'鱼竿图标素材无效：{exc}',error_type=type(exc).__name__)
            return False
        if menu is None:
            return False
        self.equip_attempts += 1

        def check():
            self.input.guard()

        def wait(seconds):
            check()
            time.sleep(max(0.,min(.6,float(seconds))))

        observed = None
        try:
            target = menu.prepare(self.window,self.tap_key,wait,check)
            self.tool_panel_open = True
            self.log('tool_panel',f"工具面板已定位鱼竿（{target['x']}, {target['y']}），正在点击装备",
                     binding_status='NO_TOOL',attempt=self.equip_attempts)
            menu.use(target,self.device,check,wait)
            # The hand-hold object is created a few frames after the click, so
            # one immediate read would report NO_TOOL for a successful click.
            deadline = time.monotonic()+policy.FISHING_EQUIP_CONFIRM_SECONDS
            while time.monotonic() < deadline:
                wait(.15)
                try:
                    observed = self.bind_rod()
                except (OSError,ValueError,KeyError,StopIteration,struct.error):
                    observed = None
                if observed is not None:
                    break
            if observed is None:
                raise ValueError('点击鱼竿后仍未绑定到手持工具')
        except (OSError,ValueError,KeyError,StopIteration,struct.error) as exc:
            self.log('binding_wait',f'自动装备鱼竿未成功：{exc}',
                     error_type=type(exc).__name__,binding_status=getattr(exc,'status','NO_TOOL'))
        finally:
            try:
                self.close_panel_if_open()
            except (OSError,ValueError) as exc:
                self.log('binding_wait',f'工具面板未关闭：{exc}',error_type=type(exc).__name__)
        if observed is None:
            return False
        self.log('tool_equipped','已在工具面板中自动装备鱼竿',state=observed[0],attempt=self.equip_attempts)
        return True

    def await_binding(self, *, recovery=False):
        deadline = time.monotonic() + (policy.FISHING_REBIND_LIMIT if recovery else policy.FISHING_START_BIND_LIMIT)
        last_issue = None
        attempt = 0
        while time.monotonic() < deadline:
            self.input.guard()
            attempt += 1
            try:
                observed = self.bind_rod()
            except (OSError,ValueError,KeyError,StopIteration,struct.error) as exc:
                issue = str(exc)
                status = getattr(exc,'status','STATE_UNCONFIRMED')
                if recovery and status in ('NO_TOOL','OTHER_TOOL') and attempt >= 2 and issue == last_issue:
                    raise InterruptedError(f'工具已切换：{issue}') from exc
                if issue != last_issue:
                    self.log('binding_wait',issue,attempt=attempt,error_type=type(exc).__name__,binding_status=status)
                    last_issue = issue
                if status in ('NO_TOOL','OTHER_TOOL') and not recovery:
                    # Try the captured rod tile first. The manual prompt is the
                    # fallback, so a missing or stale icon never blocks the run.
                    if not self.equip_rod():
                        # Waiting on its own never produced a rod. Open the panel
                        # so only one click is left; recovery stays hands-off
                        # because a tool switched mid-session is the user's own
                        # doing.
                        self.open_tool_panel(status)
                if recovery and attempt >= policy.FISHING_MAX_READ_FAILURES:
                    break
                time.sleep(min(policy.FISHING_BIND_POLL, max(0.,deadline-time.monotonic())))
            else:
                self.input.guard()
                if self.tool_panel_open:
                    # The panel covers part of the screen; close it and confirm
                    # the rod survived before any casting click is sent.
                    self.close_tool_panel()
                    try:
                        observed = self.bind_rod()
                    except (OSError,ValueError,KeyError,StopIteration,struct.error):
                        continue
                if time.monotonic() >= deadline:
                    break
                self.log('bound','已确认鱼竿和浮标',attempt=attempt,binding_status='READY_ROD',state=observed[0],player_stage=observed[2]['stage'])
                return observed
        self.close_tool_panel()
        raise InterruptedError(f'鱼竿及浮标绑定超时（{attempt} 次）：{last_issue or "读取超过时限"}')

    @staticmethod
    def read_field(meta, pointer, info, name, fmt):
        offset = info['fields'][name]['offset']
        size = struct.calcsize(fmt)
        if not 16 <= offset <= info['size']-size:
            raise ValueError(f'钓鱼字段范围无效：{name}')
        return struct.unpack(fmt,meta.data(pointer+offset,size))[0]

    def state(self):
        if time.monotonic()-self.identity_at >= .5:
            if self.identity()!=self.entity:
                raise ValueError('玩家对象引用已变化')
            self.identity_at = time.monotonic()
        if self.meta.ref(self.lpc,'_interactTool')!=self.rod or \
                self.meta.ref(self.rod,'floatComponent')!=self.float_component:
            raise ValueError('鱼竿或浮标引用已变化')
        return self.read_binding_state(self.meta,self.rod,self.rod_info,self.float_component,
                                       self.float_info,self.player_binding)

    def read_binding_state(self,meta,rod,rod_info,float_component,float_info,binding):
        state = self.read_field(meta,float_component,float_info,'<State>k__BackingField','<i')
        ready = self.read_field(meta,rod,rod_info,'CanThrow','<B')
        player = read_player_fishing(meta,binding)
        if ready not in (0,1) or state not in range(7) or player['stage'] not in range(6):
            raise BindingError('STATE_UNCONFIRMED','浮标、抛竿标志或玩家阶段未确认')
        return state,bool(ready),player

    def check_input(self):
        if self.stop_requested() or win32api.GetAsyncKeyState(win32con.VK_F9)&0x8000:
            raise InterruptedError('已停止自动钓鱼')
        if not win32gui.IsWindow(self.hwnd) or win32process.GetWindowThreadProcessId(self.hwnd)[1]!=self.pid:
            raise InterruptedError('游戏窗口已关闭或进程变化')
        if win32gui.GetForegroundWindow()!=self.hwnd or win32gui.IsIconic(self.hwnd):
            raise InterruptedError('游戏失去前台，已停止自动钓鱼')
        if win32gui.GetClientRect(self.hwnd)[2:]!=self.size:
            raise InterruptedError('游戏窗口尺寸变化，停止当前钓鱼')

    def grab(self):
        self.input.guard()
        try:
            pic = self.window.snapshot()
            frame = cv2.cvtColor(np.asarray(pic.convert('RGB')),cv2.COLOR_RGB2BGR) if pic is not None else None
        except (OSError,ValueError,cv2.error):
            frame = None
        if frame is None or frame.shape[:2]!=self.size[::-1]:
            self.screen_failures += 1
            if self.screen_failures>=policy.FISHING_MAX_SCREEN_FAILURES:
                raise InterruptedError('连续截屏失败，已停止')
            return None
        self.screen_failures = 0
        return frame

    def metrics(self):
        elapsed = max(0.,time.monotonic()-self.started)
        casts,effective,bites,catches = (self.stats[k] for k in ('casts','effective','bites','catches'))
        return {**dict.fromkeys(('casts','effective','bites','catches','escapes','unconfirmed','timeouts','recoveries'),0),
                **self.stats,'phase':self.phase,'elapsed':round(elapsed,1),
                'effective_rate':effective/casts if casts else None,
                'bite_rate':min(1.,self.stats['cast_bites']/effective) if effective else None,
                'catch_rate':self.stats['cast_catches']/casts if casts and self.inventory_reader is not None else None,
                'cycle_average_seconds':self.cycle_seconds/self.cycle_count if self.cycle_count else None,
                'catches_per_hour':catches*3600/elapsed if elapsed and self.inventory_reader is not None else None,
                'catch_verification_available':self.inventory_reader is not None}

    def log(self,event,reason,**extra):
        row = fishing_event(event,reason,dt=round(time.monotonic()-self.started,2),**extra)
        self.archive.add(row)
        if self.verbose and sys.stdout is not None:
            print(format_fishing_event(row)[0],end='')
        if self.on_event is not None:
            self.on_event(row)

    def enter(self,phase):
        self.phase,self.phase_at = phase,time.monotonic()

    def close_cycle(self):
        if self.cast_open:
            self.cycle_seconds += time.monotonic()-self.cast_at
            self.cycle_count += 1
            self.cast_open = False

    def recover(self,event,reason,phase='idle'):
        self.input.release()
        if event != 'reference_retry':
            self.close_cycle()
        self.failures[event] += 1
        self.consecutive_failures += 1
        self.stats['recoveries'] += 1
        self.target = FishTarget()
        self.ring = None
        self.enter(phase)
        self.log(event,reason,attempt=self.failures[event])
        if self.consecutive_failures>=policy.FISHING_MAX_CONSECUTIVE_FAILURES:
            raise InterruptedError('连续恢复达到上限：'+reason)

    def inventory(self):
        try:
            memory = self.scene.memory
            if self.inventory_reader is None:
                self.inventory_reader = FoodInventoryReader(memory)
            return fish_inventory(memory,self.inventory_reader,self.verified_images)
        except (ValueError,OSError,KeyError,struct.error):
            self.log('inventory_unavailable','背包暂不可核验，本轮可能无法确认钓获')
            return None

    def cast_feedback(self,state):
        if self.cast_open and not self.cast_effective and state==2:
            self.cast_effective = True
            self.stats['effective'] += 1
            self.log('cast_effective','游戏已确认浮标入水' if state==2 else '游戏已确认咬钩，抛竿已生效')

    def observe_aim(self, now, aiming, can_throw=True):
        frame = self.grab()
        if frame is None:
            return
        shadow = self.target.update(find_shadows(frame),time.monotonic(),self.size,locked=aiming)
        if not aiming:
            if shadow is not None and self.target.stable and shadow.confidence>=.55:
                self.input.press()
                self.ring = None
                self.enter('aiming')
                self.log('fish_detected','已锁定连续移动鱼影',fish=shadow.center,confidence=shadow.confidence)
            return
        sx,sy = (value/ref for value,ref in zip(self.size,policy.FISHING_WINDOW_REFERENCE))
        previous = self.ring
        self.ring = find_ring(frame,prev=previous,x_range=(380*sx,980*sx),y_range=(180*sy,395*sy),
                              min_radius=max(1,round(13*sy)),max_radius=max(2,round(42*sy)))
        if not can_throw or shadow is None or self.ring is None or previous is None or not self.target.stable:
            return
        ring = self.ring
        if math.hypot((ring.cx-previous.cx)/sx,(ring.cy-previous.cy)/sy)>22:
            return
        if time.monotonic()-self.target.last_at > policy.FISHING_LOCK_MAX_GAP:
            return
        target = self.target.predict(policy.FISHING_PREDICTION_SECONDS)
        dx,dy = (target[0]-ring.cx)/sx,(target[1]-ring.cy)/sy
        mx,my = round(dx/policy.FISHING_AIM_GAIN[0]),round(dy/policy.FISHING_AIM_GAIN[1])
        if abs(mx)>420 or abs(my)>300 or shadow.confidence<.55 or ring.confidence<.18:
            return
        self.input.release(move=lambda:self.device.native.mouse(win32con.MOUSEEVENTF_MOVE,mx,my))
        self.begin_cast()
        self.aim_evidence = dict(fish=shadow.center,predicted=target,ring=ring.center,
                                 correction=[mx,my],confidence=shadow.confidence,ring_confidence=ring.confidence)
        self.log('cast','已发送校正及松开左键，等待浮标反馈',**self.aim_evidence)

    def begin_cast(self):
        self.stats['casts'] += 1
        self.aim_evidence = self.landing_evidence = None
        self.cast_effective = False
        self.cast_open = True
        self.cast_at = time.monotonic()
        self.enter('waiting')

    def tick(self):
        self.input.guard()
        try:
            state,can_throw,player = self.state()
            battle = player['stage']==2
            idle = state==0 and player['stage']==0
        except (OSError,ValueError,KeyError,StopIteration,struct.error) as exc:
            self.input.release()
            previous = self.phase
            if previous == 'aiming':
                self.begin_cast()
                self.log('cast','松开瞄准键后等待实际浮标反馈')
            self.recover('reference_retry',f'已释放按键，重新定位玩家和工具：{exc}',
                         'waiting' if previous=='aiming' else previous)
            self.await_binding(recovery=True)
            return
        self.read_failures = 0
        now = time.monotonic()
        age = now-self.phase_at
        if state!=self.last_game_state:
            self.last_game_state = state
            fields = {k:v for k,v in player.items() if (not isinstance(v,(list,tuple)) or all(map(math.isfinite,v)))
                      and (not isinstance(v,float) or math.isfinite(v))}
            self.log('game_state','读取浮标与玩家阶段',state=state,can_throw=can_throw,player=fields)
        self.cast_feedback(state)
        if self.cast_open and state==2 and self.landing_evidence is None:
            position = player['float_xyz']
            if all(map(math.isfinite,position)) and any(position):
                self.landing_evidence = position

        if self.phase=='hooked':
            if state==4 or player['stage']==4:
                self.stats['escapes'] += 1
                self.recover('escape','游戏已确认拉扯失败，等待浮标回收','returning')
            elif state==6 or player['stage']==5 or idle:
                self.input.release()
                self.enter('result')
            elif not battle:
                self.recover('state_inconsistent','收线期间浮标进入未确认状态')
            elif age>=policy.FISHING_HOOK_TIMEOUT_SECONDS:
                self.stats['timeouts'] += 1
                raise InterruptedError('收线超时，已停止')
            else:
                color = self.line.read(self.grab())
                action = self.tension.evaluate(color['dom'],self.input.held,time.monotonic())
                if action=='timeout':
                    raise InterruptedError('张力持续无法确认，已停止')
                if action=='release':
                    self.input.release()
                elif self.input.held or action=='press':
                    self.input.press()
                if color['dom']!=self.last_color:
                    self.last_color = color['dom']
                    self.log('line_sample','鱼线张力颜色变化',color=color)
            return
        if battle and self.phase!='result':
            self.stats['bites'] += 1
            if self.cast_open and not self.cast_effective:
                self.cast_effective = True
                self.stats['effective'] += 1
                self.log('cast_effective','玩家已进入拉扯阶段，确认抛竿生效')
            self.stats['cast_bites'] += int(self.cast_open)
            self.input.release()
            self.hooked_fish_id = player['fish_id']
            self.inventory_before = self.inventory()
            self.tension,self.line = TensionControl(),FishingLine()
            self.enter('hooked')
            self.log('hook','已确认咬钩，等待有效张力后收线')
            return
        if self.phase=='result':
            after = self.inventory() if self.inventory_before is not None else None
            fish_ids = after['fish_ids'] & {self.hooked_fish_id} if after else ()
            if confirmed_catch(self.inventory_before,after,fish_ids):
                self.stats['catches'] += 1
                self.stats['cast_catches'] += int(self.cast_open)
                self.consecutive_failures = 0
                sample = None
                if self.cast_open and self.aim_evidence and self.landing_evidence:
                    sample = {**self.aim_evidence,'float_world_xyz':self.landing_evidence,
                              'fish_id':self.hooked_fish_id,'pixel_error_verified':False}
                self.log('catch','已确认当前咬钩鱼类入包',fish_id=self.hooked_fish_id,
                         cast_number=self.casts,aim_evidence=sample)
            elif self.inventory_before is not None and age<policy.FISHING_RESULT_SECONDS:
                return
            else:
                self.stats['unconfirmed'] += 1
                self.recover('result_unknown','拉扯结束，实际钓获或逃脱未确认','returning')
                return
            self.close_cycle()
            self.target = FishTarget()
            self.enter('returning')
            return
        if self.phase=='returning':
            if idle:
                self.enter('idle')
            elif age>=policy.FISHING_RETRIEVE_SECONDS:
                raise InterruptedError('浮标未回收，已安全停止')
            return
        if self.phase=='aiming':
            if state!=0:
                self.input.release()
                self.begin_cast()
                self.cast_feedback(state)
                self.log('aim_state_changed','瞄准期间浮标状态发生变化，等待反馈')
            elif age>=policy.FISHING_AIM_LIMIT:
                kind = 'fish_lost' if now-self.target.last_at>policy.FISHING_LOCK_MAX_GAP else 'ring_unconfirmed'
                self.input.release()
                self.begin_cast()
                self.log(kind,'瞄准达到上限，已松手；可能已经抛竿，等待实际反馈')
                self.consecutive_failures += 1
            else:
                self.input.press()
                if age>=policy.FISHING_AIM_SETTLE and now>=self.aim_poll_at:
                    self.aim_poll_at = now+policy.FISHING_AIM_SAMPLE
                    self.observe_aim(now,True,can_throw)
            return
        if self.phase=='retrieve':
            if idle:
                self.input.release()
                self.enter('idle')
                self.log('retrieved','已确认浮标回收')
            elif state!=2 or age>=policy.FISHING_RETRIEVE_SECONDS:
                raise InterruptedError('浮标未成功回收或状态变化，停止自动操作')
            else:
                self.input.press()
            return
        if self.phase=='waiting':
            if idle and age>=policy.FISHING_CONFIRM_CAST_SECONDS:
                if self.cast_effective:
                    self.stats['unconfirmed'] += 1
                self.recover('cast_miss','浮标未持续入水或已消失，重新搜索')
            elif state==2 and age>=policy.FISHING_RECAST_SECONDS:
                self.recover('recast','长时间未咬钩，准备回收浮标','retrieve')
            elif state not in (0,2) and age>=policy.FISHING_FLIGHT_SECONDS:
                raise InterruptedError(f'浮标长时间未确认入水（状态 {state}）')
            elif state==0 and not can_throw and age>=policy.FISHING_FLIGHT_SECONDS:
                raise InterruptedError('浮标未入水且鱼竿未恢复空闲')
            return

        if state in (1,2):
            self.enter('waiting')
            self.log('existing_float','发现已有浮标，先等待游戏状态')
        elif idle:
            if self.consecutive_failures>=policy.FISHING_MAX_CONSECUTIVE_FAILURES:
                raise InterruptedError('连续抛竿失败达到上限')
            if now>=self.aim_poll_at:
                self.aim_poll_at = now+policy.FISHING_AIM_SAMPLE
                self.observe_aim(now,False)
            if self.phase=='idle' and age>=policy.FISHING_SEARCH_SECONDS:
                self.recover('fish_lost','搜索窗口内未锁定可用鱼影')
        elif age>=policy.FISHING_FLIGHT_SECONDS:
            raise InterruptedError(f'游戏状态未确认（状态 {state}），停止自动操作')

    def run(self):
        resources = Path(getattr(sys,'_MEIPASS',ROOT))
        def read_release(name):
            path = resources/name
            return path.read_text(encoding='utf-8').strip() if path.is_file() else None
        metadata = {'program_version':read_release('version.txt'),'program_commit':read_release('heartopia-build.txt'),
                    'window_size':self.size,'duration':self.duration,'catch_target':self.catch_target,
                    'profile':json.loads(self.profile.read_text(encoding='utf-8')).get('game_assembly_sha256'),
                    'position':None,'camera':None}
        try:
            metadata['position'] = self.scene.memory.read()
            metadata['camera'] = self.scene.memory.movement_basis()
        except (OSError,ValueError):
            pass
        self.archive = FishingArchive(self.out,metadata)
        self.input.start()
        self.enter('idle')
        outcome = 'finished'
        try:
            self.log('init','正在获取玩家、鱼竿和浮标')
            self.await_binding()
            self.enter('idle')
            while (self.duration is None or time.monotonic()-self.started<self.duration) and \
                    (self.catch_target is None or self.stats['catches']<self.catch_target):
                self.tick()
                time.sleep(.04)
        except (KeyboardInterrupt,InterruptedError) as exc:
            outcome = 'stopped'
            self.log('interrupt',str(exc) or '用户中断')
        except Exception as exc:
            outcome = 'error'
            self.log('error',f'{type(exc).__name__}：{exc}')
            raise
        finally:
            try:
                try:
                    self.input.close()
                except Exception:
                    self.device.release_all()
                    raise
            finally:
                if self.phase in ('hooked','result'):
                    self.stats['unconfirmed'] += 1
                self.close_cycle()
                self.scene.close()
                self.log('end','自动钓鱼已结束',**self.metrics())
                self.archive.finish({'outcome':outcome,**self.metrics(),'failures':dict(self.failures)})
                if self.archive.error and self.on_event is not None:
                    self.on_event(fishing_event('error','钓鱼归档异常：'+self.archive.error))


def main():
    parser = argparse.ArgumentParser(description='自动钓鱼实机运行器（游戏须已在前台）')
    parser.add_argument('--duration',type=float)
    parser.add_argument('--out')
    parser.add_argument('--catch-target',type=int)
    options = parser.parse_args()
    FishingRunner(duration=options.duration,out_dir=options.out,catch_target=options.catch_target).run()


if __name__=='__main__':
    main()
