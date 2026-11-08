"""Compact offline catalogue inside the existing cooking tab."""
import tkinter as tk
from tkinter import ttk, messagebox

from utils.core.cooking_catalog import load_catalog, material_status


class CookingCatalogPanel:
    def __init__(self, owner, parent):
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
        body = owner.frame(self.frame, card=True, border=False); body.pack(fill='both',expand=True,pady=3)
        self.list = ttk.Treeview(body, columns=('name',), show='headings', height=4, selectmode='browse')
        self.list.heading('name', text='料理列表'); self.list.column('name',width=150,minwidth=90)
        self.list.pack(side='left',fill='both',expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical',command=self.list.yview)
        scroll.pack(side='left',fill='y'); self.list.configure(yscrollcommand=scroll.set)
        self.detail = tk.Text(body, width=24,height=5,wrap='word',state='disabled',
                              font=('Microsoft YaHei UI',-12))
        self.detail.pack(side='left',fill='both',expand=True,padx=(5,0))
        actions = owner.frame(self.frame,card=True,border=False); actions.pack(fill='x')
        self.stock_button = owner.button(actions,'刷新背包材料',self.refresh_inventory)
        self.stock_button.pack(side='left',fill='x',expand=True,padx=(0,3))
        self.select_button = owner.button(actions,'确认料理并选菜',self.confirm,primary=True)
        self.select_button.pack(side='left',fill='x',expand=True)
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
            text=f"{recipe['name']}\n食谱 ID：{recipe['recipe_id']}\n产物 ID：{recipe['product_id'] or '待确认'}\n"
            for material in recipe['ingredients']:
                ids=material['allowed_item_ids']
                count='未读取' if self.inventory is None else str(sum(self.inventory['counts'].get(i,0) for i in ids))
                usable='未读取' if self.inventory is None else str(sum(self.inventory['selectable_counts'].get(i,0) for i in ids))
                label=f"组 {material['material_group_id']}" if material['item_id'] is None else str(material['item_id'])
                text+=f"{material['name']} ×{material['quantity']}（{label}）\n背包 {count} / 可用 {usable}\n"
            text+='材料'+('充足' if status['sufficient'] else '不足或尚未确认')+'；仅选菜，不开始制作。'
        self.detail.configure(state='normal');self.detail.delete('1.0','end');self.detail.insert('1.0',text);self.detail.configure(state='disabled')

    def refresh_inventory(self):
        pid=self.owner.view.status.get('pid')
        if not pid:
            messagebox.showerror('背包未读取','请先连接游戏',parent=self.owner.root);return
        self.inventory=None;self.show_detail()
        self.owner.cooking.read_inventory(pid)

    def apply_result(self,result):
        if 'inventory' in result:
            self.inventory=result['inventory'];self.show_detail()

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
        self.select_button.configure(state='disabled' if active else 'normal')
        self.stock_button.configure(state='disabled' if active else 'normal')
