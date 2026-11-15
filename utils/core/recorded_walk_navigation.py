"""Validate recorded walking segments without planning a substitute route."""
import math

from utils.core import thresholds as policy
from utils.core.coordinate_navigation import LocalNavigator, NavigationWait
from utils.core.scene_read import navigation_quality_valid


class RecordedWalkNavigator(LocalNavigator):
    def __init__(self, scene_provider, clock):
        super().__init__(scene_provider, clock=clock)
        self.walk_context = None

    def require_scene(self):
        scene = self.scene_provider()
        if scene and self.walk_context is not None and scene.get('cache_context') is not None \
                and scene['cache_context'] != self.walk_context:
            raise ValueError('场景已改变，录制路线回放停止')
        if not scene or scene.get('state') != 'ready' or scene.get('cache_context') is None \
                or not isinstance(scene.get('quality'), dict) \
                or not navigation_quality_valid(scene, self.clock()):
            raise NavigationWait('等待当前场景的完整碰撞数据', reason='scene')
        self.walk_context = scene['cache_context']
        return scene

    def next_target(self, position, target, check=lambda: None, before_plan=lambda: None):
        check()
        scene = self.require_scene()
        if math.dist(position, target) > 1.5 + policy.COORD_REACH_RADIUS:
            raise ValueError('角色已离开当前录制路段，已停止回放')
        if scene is not self.scene or self.obstacles is None:
            before_plan()
        self._refresh_obstacles(position, target)
        if self.require_scene() is not self.scene:
            raise NavigationWait('场景快照已更新，重新核对本路段', reason='scene')
        if abs(position[1] - target[1]) > .75 or not self.obstacles.clear(
                (position[0], position[2]), (target[0], target[2])):
            raise ValueError('录制路段受阻或高度不一致，已停止回放')
        self.path = [tuple(target)]
        self.last_decision = {'kind': 'recorded_walk'}
        return target

    def blocked(self, position, target, **kwargs):
        raise ValueError('录制路线移动无进展，已停止回放')
