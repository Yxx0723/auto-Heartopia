"""Read recipe IDs and live native card rectangles; no screen recognition."""
import struct

from utils.core.ui_projection import rect_to_client
from utils.drivers.cooking_menu_state import CookingMenuStateReader
from utils.drivers.item_name_table import ItemNameTable


class CookingSelectionReader(CookingMenuStateReader):
    def _available(self, panel):
        m = self.ui.meta
        obj = m.ref(panel, '_allRecipes'); info = m.obj(obj)
        size = m.integer(obj+m.field(info['address'], '_size')['offset'])
        version = m.integer(obj+m.field(info['address'], '_version')['offset'])
        array = m.ref(obj, '_items'); ai, capacity = m.array(array, 2048)
        element = m.cls(ai['element'])
        if (element['name'], element['namespace']) != ('CookingRecipe', 'XDTGameSystem.GameplaySystem.Cooking') \
                or not 0 <= size <= capacity:
            raise ValueError('可选食谱列表未确认')
        stride = element['size']-16
        id_at = m.field(element['address'], 'staticId')['offset']-16
        raw = m.data(array+32, size*stride) if size else b''
        ids = [struct.unpack_from('<i', raw, i*stride+id_at)[0] for i in range(size)]
        if version != m.integer(obj+m.field(info['address'], '_version')['offset']) \
                or size != m.integer(obj+m.field(info['address'], '_size')['offset']) \
                or m.ref(obj, '_items') != array or (size and m.data(array+32, len(raw)) != raw):
            raise ValueError('可选食谱列表正在变化')
        return ids

    def selection(self, client_size):
        state = self.snapshot()
        if not state['open']:
            raise ValueError('请先打开灶台食谱面板')
        m = self.ui.meta; stack = self.ui.ui_views()
        panels = [p for p in stack[3] if m.obj(p)['name'] == 'CookPanel']
        if len(panels) != 1:
            raise ValueError('食谱面板未确认')
        panel = panels[0]; nodes = m.ref(panel, 'ui'); ids = self._available(panel)
        scroll = m.ref(nodes, 'recipes_scroll')
        if m.ref(scroll, '_ownerUIView') != panel:
            raise ValueError('料理列表不属于当前灶台面板')
        # Reuse the existing typed Dictionary<int, reference> decoder.
        handles, stable = ItemNameTable._pairs(self.ui, m.ref(scroll, '_handleToData'), 2048, lambda: None)
        native = self.layout.native
        scroll_target = self.layout.resolve(m.ref(scroll, '_scrollSystem'), 'ScrollSystem', 'XDTGUI.UI')
        scroll_rect = native.ref(scroll_target, '_scrollRect')
        viewport = native.ref(scroll_rect, 'm_Viewport')
        canvas = m.ref(stack[1], '<CanvasRect>k__BackingField')
        canvas_rect = self.layout.rect(canvas)['rect']; canvas_chain = self.layout.transforms(canvas)
        viewport_rect = rect_to_client(self.layout.native_rect(viewport)['rect'],
            self.layout.native_transforms(viewport), canvas_rect, canvas_chain, client_size)
        cards = []
        card_addresses = {}
        for card in m.dictionary(m.ref(scroll, '_rootToComponent'), max_items=256):
            if (m.obj(card)['name'], m.obj(card)['namespace']) != ('NewCardWidget', 'XDTGame.UI.Widget'):
                continue
            if m.ref(card, '_scrollSystem') != scroll:
                raise ValueError('料理卡片归属未确认')
            handle = m.integer(card+m.field(m.obj(card)['address'], '_handle')['offset'])
            data = handles.get(handle)
            if not data or m.obj(data)['name'] != 'CookRecipeScrollData':
                continue
            f = m.field(m.obj(data)['address'], 'data')
            typ = m.cls(f['type']); identity = m.integer(data+f['offset']+m.field(typ['address'], 'staticId')['offset']-16)
            if identity not in ids:
                continue
            if self.ui.payload(card, '_isLock', 2, 1) != b'\0':
                continue
            ui = m.ref(card, 'ui'); root = m.ref(ui, 'root_t'); component = m.ref(ui, 'card_btn')
            try:
                rect = self.ui._button_rect(self.layout, (*stack[:3], panel, nodes), component, client_size)
            except ValueError:
                continue  # Recycled/off-client cards are never click targets.
            l,t,r,b = rect; vl,vt,vr,vb = viewport_rect
            if vl <= l < r <= vr and vt <= t < b <= vb:
                cards.append({'recipe_id': identity, 'rect': rect, 'handle': handle})
                card_addresses[handle] = {'card_address': card, 'button_address': component}
            if m.ref(card, 'ui') != ui or m.ref(ui, 'root_t') != root \
                    or m.ref(ui, 'card_btn') != component or m.integer(card+m.field(m.obj(card)['address'], '_handle')['offset']) != handle:
                raise ValueError('料理卡片正在回收或更新')
        selected = self.recipe.snapshot()
        after = self.snapshot()
        if not stable() or self.ui.ui_views() != stack or self._available(panel) != ids \
                or after != state or selected['scope'] != state['scope']:
            raise ValueError('料理列表或灶台在读取期间变化')
        return {**state, 'available_ids': ids, 'cards': cards, 'viewport': viewport_rect,
                'selected': selected['selected'], 'signature': selected['signature'],
                'selection_evidence': {'panel_address': panel, 'ui_manager': stack[1],
                    'panel_active': True, 'panel_focused': True, 'card_addresses': card_addresses}}
