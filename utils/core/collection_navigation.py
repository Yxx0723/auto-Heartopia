"""Saved destinations over the proven local controller, without game guidance."""
from utils.core.guidance_navigation import GuidanceNavigator


class CollectionNavigator(GuidanceNavigator):
    continuous_collection = True
    continuous_guidance = False

    def __init__(self,*args,native_map_collection=False,**kwargs):
        if type(native_map_collection) is not bool:
            raise ValueError('采集原生引导开关无效')
        self.native_map_collection=native_map_collection
        self.native_scene_provider=None
        super().__init__(*args,respect_water=True,**kwargs)

    def _shore_target(self,position,target,check):
        # A saved collection destination is not a disposable guidance lookahead.
        # Preserve its exact arrival and interaction semantics, including water.
        return None
