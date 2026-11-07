"""Open the unfiltered backpack, select only slot one and use its Eat action."""
from utils.vision.food_menu import FruitMenu, BACKPACK_KEY


class FirstSlotFoodMenu(FruitMenu):
    direct_first_slot = True

    def ready_first(self, picture, cell):
        frame=self.frame(picture)
        bag=self.bag_in(frame)
        if bag is None or bag.get('search_results') or not self.selected(frame,cell):
            return None
        buttons=self.matches(frame,self.eat[:,38:74],self.scales(frame),minimum=.88)
        buttons=[b for b in buttons if self.is_food_button(frame,b,[cell])]
        if not buttons:return None
        first=buttons[0]
        if any(abs(b['x']-first['x'])>8 or abs(b['y']-first['y'])>8 for b in buttons):
            raise ValueError('第一格食用按钮位置未确认')
        return dict(first,food='背包第一格',cell=cell)

    def prepare(self,window,tap,wait,check,device):
        self.phase='open_backpack'
        check();picture=window.snapshot()
        # Dismiss an old search left open by the previous implementation.
        dialog=self.search_dialog(picture)
        if dialog is not None:
            self.use(dialog['close'],device,check,wait);wait(.3)
            picture=window.snapshot()
        end=self.end_search_target(self.frame(picture))
        if end is not None:
            self.use(end,device,check,wait);wait(.3)
            picture=window.snapshot()
        bag=self.locate(picture)
        if bag is None:
            tap(BACKPACK_KEY);wait(.5);check()
            bag=self.locate(window.snapshot())
        if bag is None or bag.get('search_results'):
            raise ValueError('未确认完整背包，未点击第一格')
        cell={'x':bag['x']-211*bag['scale'],'y':bag['y']+46*bag['scale'],
              'scale':bag['scale']}
        self.phase='select_first_slot'
        self.use(cell,device,check,wait)
        self.phase='confirm_first_slot_eat'
        for _ in range(6):
            wait(.15);check()
            target=self.ready_first(window.snapshot(),cell)
            if target is not None:return target
        raise ValueError('背包第一格没有确认到食用按钮；不会点击其他格子')

    def confirm(self,target,window,wait,check):
        check()
        current=self.ready_first(window.snapshot(),target['cell'])
        if current is None:raise ValueError('第一格食用按钮已变化，未食用')
        return current
