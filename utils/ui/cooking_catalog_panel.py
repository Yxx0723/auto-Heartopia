"""Compact offline catalogue inside the existing cooking tab."""
from collections import deque
from datetime import datetime
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from utils.core.cooking_catalog import load_catalog, material_status
from utils.drivers.cooking_unlocked_recipes import CookingUnlockedRecipes
from utils.ui import theme
from utils.ui.cooking_event_view import event_view
from utils.ui.cooking_log_text import (batch_status_text, log_line, step_summary,
                                       step_text)
from utils.ui.log_window import append_log_lines


class CookingCatalogPanel:
    def __init__(self, owner, parent, *, runtime_parent=None):
        self.owner = owner; self.inventory = None; self.recipe = None; self.active = None
        self.recipes = load_catalog()['recipes']
        self.by_id = {r['recipe_id']: r for r in self.recipes}
        # None 表示尚未读到游戏内的已解锁清单；读到之前保留全量列表。
        self.unlocked = None; self.unlocked_ids = None
        self.unlocked_thread = None; self.unlocked_result = None
        self.frame = owner.frame(parent, card=True, border=False)
        self.frame.pack(fill='both', expand=True, padx=8, pady=(4,2))
        bar = owner.frame(self.frame, card=True, border=False); bar.pack(fill='x')
        self.category = tk.StringVar(value='日常料理'); self.query = tk.StringVar()
        ttk.Combobox(bar, textvariable=self.category, values=('日常料理','近幽集','其它图鉴'),
                     state='readonly', width=10).pack(side='left')
        ttk.Entry(bar, textvariable=self.query, width=17).pack(side='left', fill='x', expand=True, padx=(5,0))
        owner.label(bar, '搜索名称 / ID', color='muted').pack(side='left', padx=3)
        body = owner.frame(self.frame, card=True, border=False)
        body.pack(fill='both',expand=True,pady=3)
        self.list = ttk.Treeview(body, columns=('name',), show='headings', height=4, selectmode='browse')
        self.list.heading('name', text='料理列表'); self.list.column('name',width=150,minwidth=90)
        self.list.pack(side='left',fill='both',expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical',command=self.list.yview)
        scroll.pack(side='left',fill='y'); self.list.configure(yscrollcommand=scroll.set)
        self.detail = tk.Text(body, width=24,height=5,wrap='word',state='disabled',
                              font=theme.font(12),bg=theme.COLORS['card'],
                              fg=theme.COLORS['text'],relief='flat',bd=0,
                              highlightthickness=0,padx=theme.px(6),pady=theme.px(3))
        self.detail.pack(side='left',fill='both',expand=True,padx=(5,0))
        actions = owner.frame(self.frame,card=True,border=False); actions.pack(fill='x')
        self.stock_button = owner.button(actions,'刷新背包材料',self.refresh_inventory)
        self.stock_button.pack(side='left',fill='x',expand=True,padx=(0,3))
        self.unlocked_button = owner.button(actions,'已解锁图鉴',self.show_unlocked)
        self.unlocked_button.pack(side='left',fill='x',expand=True,padx=(3,0))
        batch=owner.frame(self.frame,card=True,border=False);batch.pack(fill='x',pady=(3,0))
        owner.label(batch,'连续锅数（1～99）').pack(side='left')
        self.batch_target=tk.StringVar(value='1')
        self.batch_count=ttk.Spinbox(batch,from_=1,to=99,textvariable=self.batch_target,width=4)
        self.batch_count.pack(side='left',padx=4)
        self.batch_button=owner.button(batch,'开始',self.make_batch,primary=True)
        self.batch_button.pack(side='left',fill='x',expand=True,padx=(0,3))
        self.batch_stop_button=owner.button(batch,'停止',owner.cooking.stop)
        self.batch_stop_button.pack(side='left',fill='x',expand=True)
        self.batch_stop_button.configure(state='disabled')
        # The single log view belongs to the main window's shared bottom pane,
        # never to the cooking tab. Keep its event/state objects across tabs.
        self.runtime_page=owner.frame(runtime_parent if runtime_parent is not None else owner.root,
                                      card=True,border=False)
        self.runtime_page.pack(fill='both',expand=True,padx=8,pady=3)
        self.runtime_status=tk.StringVar(value='批次状态：空闲 · 当前阶段：—')
        # 批次进度与阶段已经写在日志行里，不再常驻占两行。
        self.batch_progress=tk.StringVar(value='目标 — · 当前 — · 已完成 0 · 成品 +0')
        log_area=owner.frame(self.runtime_page,card=True,border=False)
        log_area.pack(fill='both',expand=True)
        self.runtime_log=tk.Text(log_area,height=3,width=42,wrap='word',state='disabled',
                                 font=theme.font(11),bg=theme.COLORS['card'],
                                 fg=theme.COLORS['text'],relief='flat',bd=0,
                                 highlightthickness=0,padx=theme.px(6),pady=theme.px(3))
        # 错误 / 警告 / 正常三档用主题色区分，避免高饱和深色与浅色卡片冲突。
        for status,color in (('进行中',theme.COLORS['muted']),('已派发',theme.COLORS['accent']),
                             ('等待',theme.COLORS['muted']),
                             ('已确认',theme.COLORS['green']),('已完成',theme.COLORS['green']),
                             ('失败',theme.COLORS['red']),('未确认',theme.COLORS['amber'])):
            self.runtime_log.tag_configure(status,foreground=color)
        self.runtime_log.pack(side='left',fill='both',expand=True)
        log_scroll=ttk.Scrollbar(log_area,orient='vertical',command=self.runtime_log.yview)
        log_scroll.pack(side='right',fill='y');self.runtime_log.configure(yscrollcommand=log_scroll.set)
        self.last_success='尚无确认操作';self.last_batch={}
        self.last_log_line='';self.last_error_note=''
        # Same records feed the inline pane and the detached full-log window;
        # keeping them in memory means the window needs no file IO and no timer.
        self.log_buffer=deque(maxlen=2000)
        self.log_views=[]
        self.list.bind('<<TreeviewSelect>>',self.changed)
        self.category.trace_add('write',self.filter);self.query.trace_add('write',self.filter)
        self.filter()
        # 背包数量会随烹饪消耗、采集收获变化；烹饪页可见且空闲时静默重读。
        self.inventory_auto_ms = 5000
        self.detail.after(self.inventory_auto_ms, self._auto_inventory_tick)

    def _auto_inventory_tick(self):
        """定时同步食材数量，不写运行日志；窗口销毁后自动停止。"""
        try:
            result = self.unlocked_result
            if result is not None:
                self.unlocked_result = None
                self.unlocked = result['recipes']
                self.unlocked_ids = {r['recipe_id'] for r in result['recipes']}
                self.filter()
        except Exception:
            pass
        try:
            if self.detail.winfo_ismapped() and not self.active:
                if self.unlocked is None:
                    self._load_unlocked_ids()
                self.auto_read_inventory(silent=True)
        except Exception:
            pass
        try:
            self.detail.after(self.inventory_auto_ms, self._auto_inventory_tick)
        except Exception:
            pass

    def _load_unlocked_ids(self):
        """后台只读一次游戏内已解锁清单；读不到就等下个周期重试。"""
        if self.unlocked_thread and self.unlocked_thread.is_alive():
            return
        try:
            pid = int(self.owner.view.status.get('pid'))
        except (TypeError, ValueError):
            return
        if pid <= 0:
            return
        cooking = self.owner.cooking

        def read():
            try:
                memory = cooking.reader_factory(pid, cooking.profile)
                try:
                    self.unlocked_result = CookingUnlockedRecipes(memory).snapshot()
                finally:
                    memory.close()
            except Exception:
                self.unlocked_result = None

        self.unlocked_thread = threading.Thread(target=read, name='cooking-unlocked-read', daemon=True)
        self.unlocked_thread.start()

    def filter(self,*args):
        previous = self.recipe['recipe_id'] if self.recipe else None
        self.list.delete(*self.list.get_children()); query=self.query.get().strip().casefold()
        if self.unlocked is None:
            source = self.recipes
        else:
            source = []
            for item in self.unlocked:
                local = self.by_id.get(item['recipe_id'])
                if local is not None:
                    source.append(local)
                else:
                    # 游戏里已解锁，但本地配方数据尚未收录：只展示，不派发制作。
                    source.append({'recipe_id': item['recipe_id'], 'name': item['name'],
                                   'category': '其它图鉴', 'ingredients': [],
                                   'status': 'unconfirmed'})
        self.display_recipes = source
        for recipe in source:
            if recipe['category']==self.category.get() and (not query or query in recipe['name'].casefold() or query in str(recipe['recipe_id'])):
                self.list.insert('', 'end', iid=str(recipe['recipe_id']),values=(recipe['name'],))
        if previous is not None and self.list.exists(str(previous)):
            self.list.selection_set(str(previous))
        else:
            self.recipe=None;self.show_detail()

    def changed(self,event=None):
        selected=self.list.selection()
        self.recipe=next((r for r in getattr(self,'display_recipes',self.recipes)
                          if selected and str(r['recipe_id'])==selected[0]),None)
        self.show_detail()
        # 切料理只刷新数量，不再把「背包已读取」写进运行日志。
        self.auto_read_inventory(silent=True)
        self._log_selection()

    def _log_selection(self):
        """切料理时只记一句选了哪个料理，和烹饪日志同样的时间戳格式。"""
        if self.recipe is None:
            return
        stamp = datetime.now().strftime('%H:%M:%S')
        text = f"{stamp} [已选料理]：{self.recipe['name']}\n"
        line = (text, '已确认')
        try:
            append_log_lines(self.runtime_log, [line], 500)
            self.log_buffer.append(line)
            self.last_log_line = text
            self._broadcast_log([line])
        except Exception:
            pass

    def auto_read_inventory(self, *, silent=False):
        """选料理时重读背包；定时同步用 silent，不刷按钮文案和运行日志。"""
        if self.active:
            return
        raw=self.owner.view.status.get('pid')
        try:pid=int(raw)
        except (TypeError,ValueError):return
        if pid<=0:return
        if silent:
            self.owner.cooking.read_inventory(pid, silent=True)
            return
        if self.owner.cooking.read_inventory(pid):
            self.stock_button.configure(text='正在读取背包材料…')

    def show_detail(self):
        recipe=self.recipe
        text='请选择料理。背包未读取时不会把材料数量当作 0。'
        if recipe:
            if recipe.get('status') != 'confirmed':
                text = (f"{recipe['name']}\n\n"
                        '游戏图鉴已解锁，但本地未收录配方数据，暂不能自动制作。')
                self.detail.configure(state='normal');self.detail.delete('1.0','end')
                self.detail.insert('1.0',text);self.detail.configure(state='disabled')
                return
            status=material_status(recipe,self.inventory)
            text=f"{recipe['name']}\n\n"
            for material in recipe['ingredients']:
                ids=material['allowed_item_ids']
                count='未读取' if self.inventory is None else str(sum(self.inventory['counts'].get(i,0) for i in ids))
                usable='未读取' if self.inventory is None else str(sum(self.inventory['selectable_counts'].get(i,0) for i in ids))
                text += (
                    f"{material['name']} ×{material['quantity']}\n"
                    f"背包 {count} / 可用 {usable}\n"
                )
            text+='材料'+('充足' if status['sufficient'] else '不足或尚未确认')
        self.detail.configure(state='normal');self.detail.delete('1.0','end');self.detail.insert('1.0',text);self.detail.configure(state='disabled')

    def refresh_inventory(self):
        pid=self.owner.view.status.get('pid')
        try:pid=int(pid)
        except (TypeError,ValueError):pid=0
        if pid<=0:
            messagebox.showerror('背包未读取','请先连接游戏',parent=self.owner.root);return
        if self.owner.cooking.read_inventory(pid):
            self.stock_button.configure(text='正在读取背包材料…')

    def show_unlocked(self):
        """只读读取角色已解锁的料理图鉴，弹窗展示；不改游戏状态。"""
        raw = self.owner.view.status.get('pid')
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            messagebox.showerror('图鉴未读取', '请先连接游戏', parent=self.owner.root)
            return
        cooking = self.owner.cooking
        self.unlocked_button.configure(state='disabled')
        data = None
        try:
            memory = cooking.reader_factory(pid, cooking.profile)
            try:
                data = CookingUnlockedRecipes(memory).snapshot()
            finally:
                memory.close()
        except Exception as exc:
            messagebox.showerror('图鉴读取失败', str(exc), parent=self.owner.root)
            return
        finally:
            self.unlocked_button.configure(state='normal')
        self.unlocked = data['recipes']
        self.unlocked_ids = {r['recipe_id'] for r in data['recipes']}
        self.filter()
        self._show_unlocked_window(data)

    def _show_unlocked_window(self, data):
        window = tk.Toplevel(self.owner.root)
        window.title('已解锁料理图鉴 · %d 道' % data['count'])
        window.geometry('420x560')
        window.configure(bg=theme.COLORS['bg'])
        frame = ttk.Frame(window, padding=8)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=('name', 'id', 'new'), show='headings', height=18)
        tree.heading('name', text='料理'); tree.column('name', width=180, minwidth=120)
        tree.heading('id', text='ID'); tree.column('id', width=80, minwidth=70)
        tree.heading('new', text='新'); tree.column('new', width=50, minwidth=40)
        tree.pack(side='left', fill='both', expand=True)
        bar = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        bar.pack(side='left', fill='y'); tree.configure(yscrollcommand=bar.set)
        for row in data['recipes']:
            tree.insert('', 'end', values=(row['name'], row['recipe_id'],
                                          '新' if row['is_new'] else ''))
        ttk.Button(window, text='关闭', command=window.destroy).pack(pady=(0, 8))

    def apply_result(self,result):
        if result.get('state') == 'inventory_ready':
            self.stock_button.configure(text='刷新背包材料')
        elif result.get('state') == 'inventory_error' and not result.get('silent'):
            self.stock_button.configure(text='读取失败 · 点击重试')
        if result.get('state') in ('batch_finished', 'batch_stopped') and not result.get('silent'):
            # 批次结束后任务立即失活；稍等一拍再静默读回实际消耗量。
            try:
                self.detail.after(400, lambda: self.auto_read_inventory(silent=True))
            except Exception:
                pass
        if 'batch' in result:
            b=result['batch']
            self.last_batch=b
            self.batch_progress.set(f"目标 {b['target_pots']} · 当前 {b['current_pot']} · 已完成 {b['completed_pots']} · 成品 +{b['total_products']}")
            batch_state=batch_status_text(b,result.get('state'))
            phase=step_summary(b,result.get('state'))
            self.runtime_status.set('批次状态：'+batch_state+' · 当前阶段：'+(phase or '—'))
            if result.get('state')=='batch_wait':
                self.last_success='尚无确认操作'
        if 'inventory' in result:
            self.inventory=result['inventory'];self.show_detail()

    def apply_log_events(self,events):
        """Called only by the existing Tk tick; display failures stay local."""
        lines=[]
        for event in events:
            if event.get('silent'):
                continue  # 定时静默同步只更新食材详情，不写运行日志
            try:
                row=event_view(event);batch=event.get('batch') or self.last_batch
                if event.get('state')=='batch_wait':
                    self.last_success='尚无确认操作'
                if row['pot']=='—':row['pot']=batch.get('current_pot','—')
                if row['status'] in ('已确认','已完成'):
                    self.last_success=row['event']
                if row['status'] in ('失败','未确认') and event.get('state') in ('batch_stopped','error','ERROR','stopped','STOPPED'):
                    failure=batch.get('failure_diagnostic') or {}
                    context=batch.get('diagnostic_context') or {}
                    last=step_text(failure.get('last_successful_step') or context.get('last_successful_step') or '') or self.last_success
                    pot=batch.get('current_pot',row['pot'])
                    note=f"{row['time']} [停止]" + (f' 第 {pot} 锅' if pot not in ('—',0,'') else '') + f' · 最后成功的一步：{last}\n'
                    note = None if note == self.last_error_note else note
                text=log_line(row)
                if text == self.last_log_line:
                    continue  # 同一秒内重复上报同一条，界面上只说一次
                self.last_log_line=text
                lines.append((text,row['status']))
                if note:  # 「最后成功的一步」跟在出事那条后面
                    self.last_error_note=note; lines.append((note,'失败'))
            except Exception:
                # Tk or formatting errors must not propagate into the task or
                # stop the main tick. Full original events remain in the file.
                pass
        if lines:
            try:
                append_log_lines(self.runtime_log, lines, 500)
            except Exception:pass
            self.log_buffer.extend(lines)
            self._broadcast_log(lines)

    def log_lines(self):
        """Snapshot of the retained records, oldest first."""
        return list(self.log_buffer)

    def add_log_view(self, view):
        """Register a detached view; it is filled with the retained history."""
        if view not in self.log_views:
            self.log_views.append(view)
        try:
            view.set_lines(self.log_lines())
        except Exception:
            pass

    def remove_log_view(self, view):
        try:
            self.log_views.remove(view)
        except ValueError:
            pass

    def _broadcast_log(self, lines):
        for view in list(self.log_views):
            try:
                if view.alive():
                    view.append(lines)
                else:
                    self.remove_log_view(view)
            except Exception:
                self.remove_log_view(view)

    def confirm(self):
        if self.recipe is None:
            messagebox.showerror('未选择料理','请先选择料理，再确认',parent=self.owner.root);return
        try:
            self.owner.cooking.start_selection(self.owner.view.status.get('pid'),self.recipe)
            self.owner.cooking_status={'state':'selection_wait','message':'已选择 '+self.recipe['name']+'；站到灶台旁后切回游戏按 F6'}
            self.owner.render()
        except (OSError,ValueError) as exc:
            messagebox.showerror('无法选菜',str(exc),parent=self.owner.root)

    def set_active(self,active):
        if self.active == active:
            return
        self.active = active
        self.stock_button.configure(state='disabled' if active else 'normal')
        self.batch_button.configure(state='disabled' if active else 'normal')
        self.batch_stop_button.configure(state='normal' if self.owner.cooking.active else 'disabled')
        self.batch_count.configure(state='disabled' if active else 'normal')

    def make_batch(self):
        if self.recipe is None:
            messagebox.showerror('未选择料理','请先选择本批次唯一料理',parent=self.owner.root);return
        try:
            value=self.batch_target.get().strip()
            if not value.isdecimal() or not 1<=int(value)<=99:
                raise ValueError('连续制作锅数必须为 1～99 的整数')
            self.owner.cooking.start_batch(self.owner.view.status.get('pid'),self.recipe,int(value))
            self.batch_progress.set(f'目标 {int(value)} · 当前 0 · 已完成 0 · 成品 +0')
            self.owner.cooking_status={'state':'batch_wait','message':'连续制作待开始：切回游戏按 F6；F9停止'}
            self.owner.render()
        except (ValueError,OSError) as exc:
            messagebox.showerror('无法连续制作',str(exc),parent=self.owner.root)

    def make_one_pot(self):
        if self.recipe is None:
            messagebox.showerror('未选择料理','请先完成 P1 料理选择并保持游戏食谱面板打开',parent=self.owner.root);return
        try:
            self.owner.cooking.start_pot(self.owner.view.status.get('pid'),self.recipe)
            self.owner.cooking_status={'state':'single_pot_wait','message':'单锅待开始：切回游戏按 F6；F9停止'}
            self.owner.render()
        except (ValueError,OSError) as exc:
            messagebox.showerror('无法制作单锅',str(exc),parent=self.owner.root)
