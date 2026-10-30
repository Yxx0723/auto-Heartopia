"""Owned navigation-only geometry, prepared before publication, not a TTL cache."""
from utils.core.coordinate_navigation import scene_geometry_signature


class _FrozenDict(dict):
    def _readonly(self, *args, **kwargs):
        raise TypeError('Published navigation geometry is read-only')

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly

    def __deepcopy__(self, memo):
        return self  # All nested geometry containers are recursively frozen.


def _freeze(value):
    if isinstance(value, dict):
        return _FrozenDict({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError('Unsupported navigation geometry value')


class PreparedNavigationScene(_FrozenDict):
    def __init__(self, source):
        super().__init__(_freeze(source))
        self._geometry = tuple(self.get(k) for k in
                               ('colliders', 'navigation_exclusions', 'navigation_reference'))
        self._signature = scene_geometry_signature(self)

    def prepared_signature(self):
        # Replacing a field on this outer mapping revokes the prepared result.
        if all(self.get(k) is original for k, original in zip(
                ('colliders', 'navigation_exclusions', 'navigation_reference'), self._geometry)):
            return self._signature
        return None
