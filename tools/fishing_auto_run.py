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


def find_game():
    games = []
    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip()=='心动小镇':
            games.append(hwnd)
    win32gui.EnumWindows(visit,None)
    if len(games)!=1:
        raise RuntimeError('未找到唯一的游戏窗口')
    return games[0]


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
        try:
            self.bind_rod()
        except BaseException:
            self.scene.close()
            raise
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
        self.meta = meta = self.scene.meta
        self.entity = self.identity()
        components = meta.dictionary(meta.ref(self.entity,'_components'),max_items=128)
        players = [p for p in components if meta.obj(p)['name']=='LocalPlayerComponent']
        if len(players)!=1:
            raise ValueError('玩家对象未确认')
        self.lpc = players[0]
        self.player_binding = player_fishing_status(meta,self.lpc,self.verified_images)
        self.rod = meta.ref(self.lpc,'_interactTool')
        if not self.rod or meta.obj(self.rod)['name']!='HandHoldFishingRod':
            raise ValueError('当前手持工具不是鱼竿，请先拿出鱼竿')
        self.rod_info = meta.obj(self.rod)
        self.float_component = meta.ref(self.rod,'floatComponent')
        if not self.float_component:
            raise ValueError('鱼竿的浮标引用尚未就绪')
        self.float_info = meta.obj(self.float_component)
        self.identity_at = time.monotonic()

    def read_field(self, pointer, info, name, fmt):
        offset = info['fields'][name]['offset']
        size = struct.calcsize(fmt)
        if not 16 <= offset <= info['size']-size:
            raise ValueError(f'钓鱼字段范围无效：{name}')
        return struct.unpack(fmt,self.meta.data(pointer+offset,size))[0]

    def state(self):
        if time.monotonic()-self.identity_at >= .5:
            if self.identity()!=self.entity:
                raise ValueError('玩家对象引用已变化')
            self.identity_at = time.monotonic()
        if self.meta.ref(self.lpc,'_interactTool')!=self.rod or \
                self.meta.ref(self.rod,'floatComponent')!=self.float_component:
            raise ValueError('鱼竿或浮标引用已变化')
        state = self.read_field(self.float_component,self.float_info,'<State>k__BackingField','<i')
        ready = self.read_field(self.rod,self.rod_info,'CanThrow','<B')
        if ready not in (0,1):
            raise ValueError('抛竿状态未确认')
        player = read_player_fishing(self.meta,self.player_binding)
        if player['stage'] not in range(6):
            raise ValueError('玩家钓鱼阶段未确认')
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
        except (OSError,ValueError,KeyError,StopIteration) as exc:
            self.input.release()
            self.read_failures += 1
            if self.read_failures>=policy.FISHING_MAX_READ_FAILURES:
                raise InterruptedError(f'连续无法读取鱼竿或浮标状态：{exc}') from exc
            self.recover('reference_retry',f'游戏状态读失败，尝试重新绑定：{exc}')
            try:
                self.bind_rod()
            except (OSError,ValueError,KeyError,StopIteration):
                pass
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
            self.log('init','自动钓鱼启动，钓获按当前咬钩鱼种的背包增量确认')
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
