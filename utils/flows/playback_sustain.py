"""Tool equip, repair and stamina workflows; preserves pause/resume state."""
from utils.core.tool_repair import ToolRepairCycle
from utils.core.stamina import usable_stamina
from utils.flows.playback_signals import _Paused


class SustainActions:
    def food_retry(self, message, index, total):
        self.release()
        self.food_failures += 1
        delay = min(5., 2. ** min(self.food_failures-1, 3))
        self.food_retry_at = self.clock.monotonic()+delay
        self.report('eating', f'{message} · {delay:g}秒后自动重试（第{self.food_failures}次） · F6暂停 · F9停止',
                    waypoint=index+1, waypoint_count=total, food_retry=self.food_failures)
        return True

    def food_retry_wait(self):
        self.release()
        self.navigation_waiting = True
        try:
            self.wait(min(.2, max(.01, self.food_retry_at-self.clock.monotonic())))
        finally:
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()
        return True

    def repair_pause(self, message, index, total, state='repair_paused'):
        self.release()
        pause_with_reason = getattr(self.controls, 'pause_with_reason', None)
        if pause_with_reason:
            pause_with_reason(message)
        else:
            self.controls.pause()
        self.report(state, message+' · 处理后切回游戏按 F6 继续 · F9停止',
                    waypoint=index+1, waypoint_count=total)
        raise _Paused()

    def repair_tap(self, key):
        try:
            self.set_keys({key})
            self.wait(.1)
        finally:
            self.release()

    def read_tool(self):
        self.last_tool_check = self.clock.monotonic()
        try:
            self.tool_status = self.reader.tools_snapshot()
        except (ValueError, OSError):
            self.tool_status = {'state': 'unavailable'}
        return self.tool_status

    def read_stamina(self):
        self.last_stamina_check = self.clock.monotonic()
        try:
            self.stamina_status = self.reader.stamina_snapshot()
        except (ValueError, OSError):
            self.stamina_status = {'state': 'unavailable'}
        return self.stamina_status

    def close_stamina_menu(self, index, total):
        if self.clock.monotonic() < self.food_retry_at:
            return self.food_retry_wait()
        self.release()
        self.navigation_waiting = True
        try:
            closed = self.food.close_menu(self.window, self.repair_tap, self.guard, self.wait, self.device)
            self.meal_menu_pending = False
            self.food_menu_checked = True
            if closed:
                self.report('playing', '已关闭背包，继续当前点位 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total)
            return bool(closed)
        except (ValueError, OSError) as exc:
            return self.food_retry(f'关闭背包未完成：{exc}', index, total)
        finally:
            self.release()
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()

    def maintain_stamina(self, index, total):
        if self.food is None:
            return False
        if self.clock.monotonic() < self.food_retry_at:
            return self.food_retry_wait()
        if self.clock.monotonic()-self.last_stamina_check >= 1.:
            self.read_stamina()
        status = self.stamina_status
        if not usable_stamina(status):
            self.release()
            self.navigation_waiting = True
            try:
                for _ in range(3):
                    self.wait(.1)
                    status = self.read_stamina()
                    if usable_stamina(status):
                        break
            finally:
                self.navigation_waiting = False
                self.last_tick = self.clock.monotonic()
        if not usable_stamina(status):
            return self.food_retry('暂时无法读取本地角色体力，等待读取恢复', index, total)
        if status['current'] < 30:
            self.meal_refilling = True
        if status['current'] >= 30 and not self.meal_refilling and not self.meal_pending and not self.meal_menu_pending:
            # Starting or resuming may leave a backpack from manual eating or
            # an earlier run open. Check once before movement, never per tick.
            if not self.food_menu_checked:
                return self.close_stamina_menu(index, total)
            return False
        self.release()
        self.navigation_waiting = True
        try:
            action = '正在补充体力至100' if status['current'] < 100 else '体力已达100，正在收起背包'
            self.report('eating', f'体力 {status["current"]} / {status["maximum"]}，{action} · F6暂停 · F9停止',
                        waypoint=index+1, waypoint_count=total)
            if self.clock.monotonic() < self.meal_settle_until:
                self.wait(self.meal_settle_until-self.clock.monotonic())
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('体力正在更新，请稍后继续')
            if not self.meal_pending and status['current'] < 100:
                self.meal_menu_pending = True
                inventory_reader = getattr(self.reader, 'food_inventory_snapshot', None)
                if callable(inventory_reader):
                    inventory = inventory_reader(check=self.guard)
                    names = sorted({item['name'] for item in inventory})
                    self.report('eating', f'背包可食用食物 {len(names)} 种，随机选择并搜索 · F6暂停 · F9停止',
                                waypoint=index+1, waypoint_count=total)
                    target = self.food.prepare(self.window, self.repair_tap, self.wait, self.guard, self.device,
                                               inventory=inventory, query_check=getattr(self.reader, 'food_search_query', None))
                    self.report('eating', f'正在食用{target["food"]}，体力达到100后继续路线 · F6暂停 · F9停止',
                                waypoint=index+1, waypoint_count=total, food_name=target['food'])
                else:  # Injected simulation devices keep their existing contract.
                    target = self.food.prepare(self.window, self.repair_tap, self.wait, self.guard, self.device)
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('食用前无法确认体力')
                if status['current'] < 100:
                    # Retain the attempted use across F6, focus loss and a
                    # partial mouse press. Never eat twice before verifying.
                    self.meal_pending = True
                    self.meal_before, self.meal_deadline = status['current'], self.clock.monotonic()+8.
                    try:
                        self.food.use(target, self.device, self.guard, self.wait)
                    finally:
                        self.release()
            status = self.read_stamina()
            while self.meal_pending and self.clock.monotonic() < self.meal_deadline \
                    and (not usable_stamina(status) or status['current'] <= self.meal_before):
                self.wait(.1)
                status = self.read_stamina()
            if not usable_stamina(status):
                raise ValueError('食用后暂时无法确认体力，请等待恢复后继续')
            if self.meal_pending and status['current'] <= self.meal_before:
                self.meal_pending = False
                raise ValueError('食用后体力未增加，请检查水果数量或食用动作')
            if self.meal_pending:
                self.meal_pending = False
                self.meal_settle_until = self.clock.monotonic()+2.
                self.wait(2.)  # Keep the confirmed eating animation out of movement/calibration.
                status = self.read_stamina()
                if not usable_stamina(status):
                    raise ValueError('体力正在更新，请稍后继续')
            if status['current'] >= 100:
                if self.meal_menu_pending:
                    self.food.close_menu(self.window, self.repair_tap, self.guard, self.wait, self.device)
                    self.meal_menu_pending = False
                    self.food_menu_checked = True
                self.meal_refilling = False
                self.food_failures, self.food_retry_at = 0, 0.
                self.report('playing', f'体力已达 {status["current"]}（目标100），继续当前点位 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total)
            return True
        except (ValueError, OSError) as exc:
            return self.food_retry(f'自动进食未完成：{exc}', index, total)
        finally:
            self.release()
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()

    def equip_axe_pick(self, index, total):
        self.release()
        self.navigation_waiting = True
        self.report('equipping', '正在拿出斧镐，确认持用后自动继续 · F6暂停 · F9停止',
                    waypoint=index+1, waypoint_count=total)
        try:
            if not self.equip_pending:
                self.wait(.15)
                tool = self.read_tool()
                if not ToolRepairCycle.usable(tool):
                    raise ValueError('暂时无法确认斧镐状态，请等待读取恢复')
                if not tool['equipped']:
                    self.equip_menu_pending = True
                    target = self.tool_selector.prepare(self.window, self.repair_tap, self.wait, self.guard)
                    # Opening the menu can race a manual selection. Clicking an
                    # already held tool can put it away, so re-read before input.
                    tool = self.read_tool()
                    if not ToolRepairCycle.usable(tool):
                        raise ValueError('选用前斧镐状态发生变化')
                    if not tool['equipped']:
                        self.equip_pending = True
                        self.equip_verify_until = self.clock.monotonic()+3.
                        try:
                            self.tool_selector.use(target, self.device, self.guard, self.wait)
                        finally:
                            self.release()
            # An interrupted click is never repeated on F6 resume. First
            # verify its result, even if the original deadline has elapsed.
            tool = self.read_tool()
            while self.equip_pending and self.clock.monotonic() < self.equip_verify_until \
                    and not (ToolRepairCycle.usable(tool) and tool['equipped']):
                self.wait(.1)
                tool = self.read_tool()
            if not ToolRepairCycle.usable(tool) or not tool['equipped']:
                self.equip_pending = False
                raise ValueError('选用后尚未确认手持斧镐，请检查 T 面板')
            if self.equip_menu_pending:
                self.tool_selector.close_menu(self.window, self.repair_tap, self.guard, self.wait)
                self.equip_menu_pending = False
            self.equip_pending = False
            tool = self.read_tool()
            if not ToolRepairCycle.usable(tool) or not tool['equipped']:
                raise ValueError('斧镐持用状态再次改变，请确认后继续')
            self.report('playing', '已手持斧镐，继续当前点位 · F6暂停 · F9停止',
                        waypoint=index+1, waypoint_count=total)
            return True
        except (ValueError, OSError) as exc:
            self.repair_pause(f'自动拿出斧镐未完成：{exc}', index, total, state='equip_paused')
        finally:
            self.release()
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()

    def maintain_tool(self, index, total):
        if self.repair_box is None and self.tool_selector is None:
            return False
        now = self.clock.monotonic()
        if now-self.last_tool_check >= 1.:
            self.read_tool()
        if self.tool_selector is not None:
            if self.tool_status.get('state') == 'locked':
                self.repair_pause('斧镐尚未解锁，暂时无法自动拿出', index, total, state='equip_paused')
            if not ToolRepairCycle.usable(self.tool_status):
                self.repair_pause('暂时无法确认斧镐持用状态，请等待读取恢复', index, total, state='equip_paused')
            if not self.tool_status['equipped'] or self.equip_pending or self.equip_menu_pending:
                return self.equip_axe_pick(index, total)
        if self.repair_box is None:
            return False
        outcome = self.repair_cycle.observe(self.tool_status, now)
        if self.repair_menu_pending and not self.repair_cycle.active:
            try:
                self.repair_box.close_menu(self.window, self.repair_tap, self.guard, self.wait)
                self.repair_menu_pending = False
            except (ValueError, OSError) as exc:
                self.repair_pause(f'关闭 T 面板失败：{exc}', index, total)
        if outcome == 'idle':
            return False
        failures = {'unavailable': '自动维修暂时读不到耐久，请等待游戏恢复',
                    'not_held': '斧镐耐久偏低，请先手持斧镐再自动维修',
                    'changed': '维修期间工具数据改变，请确认正在持用斧镐',
                    'failed': '维修后耐久未恢复到20%，请检查维修盒数量或手动维修'}
        if outcome in failures:
            self.repair_pause(failures[outcome], index, total)
        self.release()
        self.navigation_waiting = True
        try:
            if outcome == 'needed':
                self.report('repairing', '耐久低于20%，正在打开 T 面板使用维修盒 · F9停止',
                            waypoint=index+1, waypoint_count=total, repair_phase='opening')
                try:
                    target = self.repair_box.prepare(self.window, self.repair_tap, self.wait, self.guard)
                    self.repair_menu_pending = True
                    # Preparation takes time. Recheck the actual tool immediately
                    # before consuming anything, and retain the attempt on pause.
                    self.tool_status = self.reader.tools_snapshot()
                    if self.repair_cycle.observe(self.tool_status, self.clock.monotonic()) != 'needed':
                        return True
                    self.repair_cycle.mark_used(self.tool_status, self.clock.monotonic())
                    try:
                        self.repair_box.use(target, self.device, self.guard, self.wait)
                    finally:
                        # Wait a full effect duration AFTER releasing the click,
                        # including when F6 interrupts that press.
                        self.repair_cycle.started_at = self.clock.monotonic()
                except (ValueError, OSError) as exc:
                    self.repair_pause(f'维修盒使用未确认：{exc}', index, total)
                finally:
                    self.release()
            if outcome == 'completed':
                try:
                    self.repair_box.close_menu(self.window, self.repair_tap, self.guard, self.wait)
                    self.repair_menu_pending = False
                except (ValueError, OSError) as exc:
                    self.repair_pause(f'维修已完成，关闭 T 面板失败：{exc}', index, total)
                self.report('playing', '维修完成，继续当前点位 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total, repair_phase='completed')
                return True
            remaining = self.repair_cycle.remaining(self.clock.monotonic())
            if now-self.last_repair_report >= .5:
                self.report('repairing', f'维修盒恢复中 · 剩余 {remaining:.1f} 秒 · F6暂停 · F9停止',
                            waypoint=index+1, waypoint_count=total, repair_phase='waiting',
                            repair_remaining=remaining)
                self.last_repair_report = now
            self.wait(min(.1, max(.01, remaining)))
            return True
        finally:
            self.navigation_waiting = False
            self.last_tick = self.clock.monotonic()
