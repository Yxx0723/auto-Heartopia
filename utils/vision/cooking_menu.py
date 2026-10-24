"""User-calibrated single-stove templates. No fixed desktop coordinates."""
import base64
import json
from pathlib import Path

import cv2
import numpy as np

from utils.core.cooking import Observation


REQUIRED = ('menu', 'recipe', 'materials', 'start', 'cooking', 'stir', 'collect')
DEFAULT_REQUIRED = ('menu', 'start', 'cooking', 'stir', 'collect', 'interact')


class CookingMenu:
    def __init__(self, profile, default_mode=False):
        self.default_mode = default_mode
        if isinstance(profile, (str, Path)):
            if Path(profile).stat().st_size > 8000000:
                raise ValueError('烹饪识别配置过大')
            profile = json.loads(Path(profile).read_text('utf-8'))
        if profile.get('schema') != 'cooking-profile-v1':
            raise ValueError('烹饪识别配置版本无效')
        self.profile = profile
        self.size = tuple(profile.get('client_size', ()))
        if len(self.size) != 2 or any(type(v) is not int or not 64 <= v <= 16384 for v in self.size):
            raise ValueError('烹饪标定窗口尺寸无效')
        self.templates = {}
        self.variants = {}
        names = DEFAULT_REQUIRED if default_mode else REQUIRED + (('interact',) if 'interact' in profile.get('templates', {}) else ())
        for name in names:
            item = profile.get('templates', {}).get(name)
            if not isinstance(item, dict):
                raise ValueError('尚未完成烹饪画面标定：'+name)
            rect = item.get('rect', ())
            if len(rect) != 4 or any(type(v) is not int for v in rect):
                raise ValueError('烹饪标定区域无效')
            x, y, w, h = rect
            if min(w, h) < 8 or x < 0 or y < 0 or x+w > self.size[0] or y+h > self.size[1]:
                raise ValueError('烹饪标定区域超出客户区')
            try:
                raw = base64.b64decode(item['png'], validate=True)
                template = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            except (ValueError, KeyError) as exc:
                raise ValueError('烹饪标定图片无效') from exc
            if template is None or template.shape[:2] != (h, w) or template.std() < 8:
                raise ValueError('烹饪标定图片空白或尺寸不符')
            self.templates[name] = (rect, template)
            variants = item.get('variants', [])
            if not isinstance(variants, list) or len(variants) > 4:
                raise ValueError('烹饪模板变体数量无效')
            self.variants[name] = []
            for variant in variants:
                try:
                    image = cv2.imdecode(np.frombuffer(base64.b64decode(variant['png'], validate=True), np.uint8), cv2.IMREAD_COLOR)
                except (KeyError, ValueError) as exc:
                    raise ValueError('烹饪模板变体无效') from exc
                if image is None or min(image.shape[:2]) < 8 or max(image.shape[:2]) > 128 or image.std() < 8:
                    raise ValueError('烹饪模板变体尺寸无效')
                self.variants[name].append((tuple(variant['rect']), image))

    def match(self, frame, name):
        results = [p for rect, template in (self.templates[name], *self.variants[name])
                   if (p := self._match_one(frame, name, rect, template)) is not None]
        if any(isinstance(p, str) for p in results):
            return None
        if not results:
            return None
        if any(abs(p[0]-results[0][0])+abs(p[1]-results[0][1]) > 20 for p in results[1:]):
            return None
        return results[0]

    def _match_one(self, frame, name, rect, template):
        x, y, w, h = rect
        if self.profile['templates'][name].get('glyph'):
            # World-space prompts move when the cooking camera rotates. Search
            # only the central game region, and reject multiple matches.
            left, top = self.size[0]//4, self.size[1]//4
            region = frame[top:self.size[1]*3//4, left:self.size[0]*3//4]
            low, high = region.min(axis=2), region.max(axis=2)
            mask = ((low > 170) & (high > 220) & (high.astype(float)-low < 90)).astype(np.uint8)*255
            region = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        else:
            pad = max(5, round(min(self.size)*.025))
            left, top = max(0, x-pad), max(0, y-pad)
            region = frame[top:min(self.size[1], y+h+pad), left:min(self.size[0], x+w+pad)]
        scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, best, _, (px, py) = cv2.minMaxLoc(scores)
        if not np.isfinite(best) or best < .94:
            return None
        # Reject two spatially distinct matches, rather than choosing an arbitrary stove.
        scores[max(0, py-h//2):py+h//2+1, max(0, px-w//2):px+w//2+1] = -1
        if cv2.minMaxLoc(scores)[1] >= .94:
            return 'ambiguous'
        return left+px+w//2, top+py+h//2

    def observe(self, picture):
        if tuple(picture.size) != self.size:
            raise ValueError('烹饪窗口尺寸已变化，请重新标定或恢复原尺寸')
        frame = cv2.cvtColor(np.asarray(picture.convert('RGB')), cv2.COLOR_RGB2BGR)
        menu, start = (self.match(frame, n) for n in ('menu', 'start'))
        if menu:
            if self.default_mode:
                # Correlation alone also matches a dimmed button behind a modal.
                if start:
                    x, y = start
                    patch = frame[max(0, y-12):y+12, max(0, x-60):x+60].astype(float)
                    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
                    if ((g > 150) & (b > 140) & (g-r > 50)).mean() > .15:
                        return Observation('menu', start, key='enter')
                return Observation()
            recipe, materials = (self.match(frame, n) for n in ('recipe', 'materials'))
            return Observation('menu', start, key='enter') if recipe and materials and start else Observation()
        stir, collect, cooking = (self.match(frame, n) for n in ('stir', 'collect', 'cooking'))
        if stir and collect:
            return Observation()  # Ambiguous state, no input.
        if collect:
            return Observation('done', collect, key='q')
        if stir:
            return Observation('stir', stir)
        if cooking:
            return Observation('cooking')
        if 'interact' in self.templates:
            entry = self.match(frame, 'interact')
            if entry:
                return Observation('interact', entry, key='q')
        return Observation()
