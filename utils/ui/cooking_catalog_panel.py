"""Compact offline catalogue inside the existing cooking tab."""
from collections import deque
import tkinter as tk
from tkinter import ttk, messagebox

from utils.core.cooking_catalog import load_catalog, material_status
from utils.ui import theme
from utils.ui.cooking_event_view import event_view
from utils.ui.log_window import append_log_lines


class CookingCatalogPanel:
    def __init__(self, owner, parent, *, runtime_parent=None):
        self.owner = owner; self.inventory = None; self.recipe = None; self.active = None
        self.recipes = load_catalog()['recipes']
        self.frame = owner.frame(parent, card=True, border=False)
        self.frame.pack(fill='both', expand=True, padx=8, pady=(4,2))
        bar = owner.frame(self.frame, card=True, border=False); bar.pack(fill='x')
        self.category = tk.StringVar(value='日常料理'); self.query = tk.StringVar()
        ttk.Combobox(bar, textvariable=self.category, values=('日常料理','近幽集'),
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
        owner.label(self.runtime_page,'',textvariable=self.runtime_status,wraplength=390,
                    justify='left',anchor='w').pack(fill='x')
        self.batch_progress=tk.StringVar(value='目标 — · 当前 — · 已完成 0 · 成品 +0')
        owner.label(self.runtime_page,'',textvariable=self.batch_progress,anchor='w').pack(fill='x')
        self.progress=ttk.Progressbar(self.runtime_page,mode='determinate',maximum=1,value=0)
        self.progress.pack(fill='x',pady=(1,2))
        self.runtime_error=tk.StringVar()
        self.runtime_error_label=owner.label(self.runtime_page,'',textvariable=self.runtime_error,
                    color='red',wraplength=390,justify='left',font=theme.font(11),
                    height=2,anchor='nw')
        log_area=owner.frame(self.runtime_page,card=True,border=False);log_area.pack(fill='both',expand=True)
        self.runtime_log=tk.Text(log_area,height=3,width=42,wrap='word',state='disabled',
                                 font=theme.font(11),bg=theme.COLORS['card'],
                                 fg=theme.COLORS['text'],relief='flat',bd=0,
                                 highlightthickness=0,padx=theme.px(6),pady=theme.px(3))
        # 错误 / 警告 / 正常三档用主题色区分，避免高饱和深色与浅色卡片冲突。
        for status,color in (('进行中',theme.COLORS['muted']),('已派发',theme.COLORS['accent']),
                             ('已确认',theme.COLORS['green']),('已完成',theme.COLORS['green']),
                             ('失败',theme.COLORS['red']),('未确认',theme.COLORS['amber'])):
            self.runtime_log.tag_configure(status,foreground=color)
        self.runtime_log.pack(side='left',fill='both',expand=True)
        log_scroll=ttk.Scrollbar(log_area,orient='vertical',command=self.runtime_log.yview)
        log_scroll.pack(side='right',fill='y');self.runtime_log.configure(yscrollcommand=log_scroll.set)
        def show_error(*_):
            try:
                if self.runtime_error.get():
                    self.runtime_error_label.pack(fill='x',pady=(2,3),before=log_area)
                else:self.runtime_error_label.pack_forget()
            except Exception:pass
        self.runtime_error.trace_add('write',show_error)
        self.last_success='尚无确认操作';self.last_batch={}
        # Same records feed the inline pane and the detached full-log window;
        # keeping them in memory means the window needs no file IO and no timer.
        self.log_buffer=deque(maxlen=2000)
        self.log_views=[]
        self.list.bind('<<TreeviewSelect>>',self.changed)
        self.category.trace_add('write',self.filter);self.query.trace_add('write',self.filter)
        self.filter()

    def filter(self,*args):
        previous = self.recipe['recipe_id'] if self.recipe else None
        self.list.delete(*self.list.get_children()); query=self.query.get().strip().casefold()
        for recipe in self.recipes:
            if recipe['category']==self.category.get() and (not query or query in recipe['name'].casefold() or query in str(recipe['recipe_id'])):
                self.list.insert('', 'end', iid=str(recipe['recipe_id']),values=(recipe['name'],))
        if previous is not None and self.list.exists(str(previous)):
            self.list.selection_set(str(previous))
        else:
            self.recipe=None;self.show_detail()

    def changed(self,event=None):
        selected=self.list.selection()
        self.recipe=next((r for r in self.recipes if selected and str(r['recipe_id'])==selected[0]),None)
        self.show_detail()

    def show_detail(self):
        recipe=self.recipe
        text='请选择料理。背包未读取时不会把材料数量当作 0。'
        if recipe:
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
        if not pid:
            messagebox.showerror('背包未读取','请先连接游戏',parent=self.owner.root);return
        if self.owner.cooking.read_inventory(pid):
            self.stock_button.configure(text='正在读取背包材料…')

    def apply_result(self,result):
        if result.get('state') == 'inventory_ready':
            self.stock_button.configure(text='刷新背包材料')
        elif result.get('state') == 'inventory_error':
            self.stock_button.configure(text='读取失败 · 点击重试')
        if 'batch' in result:
            b=result['batch']
            self.last_batch=b
            self.batch_progress.set(f"目标 {b['target_pots']} · 当前 {b['current_pot']} · 已完成 {b['completed_pots']} · 成品 +{b['total_products']}")
            self.progress.configure(maximum=max(1,b['target_pots']),value=b['completed_pots'])
            batch_state='已完成' if b.get('state')=='batch_finished' else '失败 / 已停止' if b.get('state')=='batch_stopped' else '待开始' if result.get('state')=='batch_wait' else '进行中'
            self.runtime_status.set('批次状态：'+batch_state+' · 当前阶段：'
                +str((b.get('diagnostic_context') or {}).get('current_step') or result.get('state','—'))
                +' / '+str(result.get('state','—')))
            if result.get('state')=='batch_wait':
                self.runtime_error.set('');self.last_success='尚无确认操作'
        if 'inventory' in result:
            self.inventory=result['inventory'];self.show_detail()

    def apply_log_events(self,events):
        """Called only by the existing Tk tick; display failures stay local."""
        lines=[]
        for event in events:
            try:
                row=event_view(event);batch=event.get('batch') or self.last_batch
                if event.get('state')=='batch_wait':
                    self.runtime_error.set('');self.last_success='尚无确认操作'
                if row['pot']=='—':row['pot']=batch.get('current_pot','—')
                if row['status'] in ('已确认','已完成'):
                    self.last_success=row['event']+'：'+row['result']
                if row['status'] in ('失败','未确认') and event.get('state') in ('batch_stopped','error','unconfirmed','stopped','STOPPED'):
                    failure=batch.get('failure_diagnostic') or {}
                    context=batch.get('diagnostic_context') or {}
                    phase=failure.get('current_step') or context.get('current_step') or row['stage']
                    last=failure.get('last_successful_step') or context.get('last_successful_step') or self.last_success
                    self.runtime_error.set(f"第 {batch.get('current_pot',row['pot'])} 锅 · 出错阶段：{phase}\n错误：{row['result']}\n最后成功：{last}\n停止原因：{batch.get('message',row['result'])}")
                line=f"● {row['time']} | 第 {row['pot']} 锅 | {row['stage']} | {row['event']} | {row['status']} | {row['result']}\n"
                lines.append((line,row['status']))
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
