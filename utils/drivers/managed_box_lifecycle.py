"""Read-only proof of a detached box, never a missing-transform guess."""
import hashlib
import struct

from utils.core import thresholds as policy
from utils.core.scene_read import SceneReadIssue


# SHA256 of the validated runtime XDTBaseService PE, inspected 2026-10-29.
# PhysicObject.UnSpawn clears group/hierarchy without clearing _enabled;
# DynamicCollision.Detach clears native backing. Unknown builds fail closed.
PHYSICS_IMAGE_SHA256 = '0bbdb950abbd0733b36a3cb1b0c95c0e7c9841e21792390501d2353d341e8468'


class DetachedBoxGuard:
    def __init__(self, meta, check, images):
        self.meta, self.check, self.images = meta, check, images
        self.image_attempts, self.proofs = {}, {}
        self.stats = {'provisional':0, 'confirmed':0, 'changed':0, 'unconfirmed':0}

    def image_verified(self, info):
        self.check()
        try:
            m = self.meta
            image = m.pointer(info['address'] + 0x40)
            if m.text(m.pointer(image + 0x30)) != 'XDTBaseService':
                return False
            raw, size = struct.unpack('<QI', m.data(image + 0x10, 12))
            if not 0 < size <= policy.COORD_SCENE_MAX_BYTES:
                return False
            key = (m.domain, image, raw, size)
            if self.images.get(key) is True:
                return True
            if key not in self.image_attempts:
                self.image_attempts[key] = hashlib.sha256(m.data(raw, size)).hexdigest() == PHYSICS_IMAGE_SHA256
                self.check()
                if self.image_attempts[key]:
                    self.images.clear()  # One immutable image; never cache heap object state.
                    self.images[key] = True
            return self.image_attempts[key]
        except (OSError, ValueError) as exc:
            if isinstance(exc, SceneReadIssue) and exc.kind in ('cancelled','scope_changed'):
                raise
            return False

    def detached(self, collider, info, stream):
        self.check()
        try:
            m = self.meta
            if m.obj(collider)['address'] != info['address']:
                return False
            fields = [m.field(info['address'], name) for name in ('_stream','_group','_hierarchy','_collider')]
            if any(f['code'] not in (18,21,28,29) or f['flags'] & 16
                   or not 16 <= f['offset'] <= info['size']-8 for f in fields):
                return False
            offsets = [f['offset'] for f in fields]
            if len(set(offsets)) != len(offsets):
                return False
            lo, hi = min(offsets), max(offsets)+8
            expected = (stream,0,0,0)
            for _ in range(2):
                raw = m.data(collider+lo,hi-lo)
                if tuple(struct.unpack_from('<Q',raw,offset-lo)[0] for offset in offsets) != expected:
                    return False
                self.check()
            return m.obj(collider)['address'] == info['address']
        except (OSError, ValueError) as exc:
            if isinstance(exc, SceneReadIssue) and exc.kind in ('cancelled','scope_changed'):
                raise
            return False

    def consider(self, collider, info, stream):
        if (info.get('name'),info.get('namespace')) == ('DynamicBoxCollider','XDTGame.Core.Physics') \
                and self.image_verified(info) and self.detached(collider,info,stream):
            self.proofs[collider] = (info,stream)
            self.stats['provisional'] += 1
            return True
        self.stats['unconfirmed'] += 1
        return False

    def revalidate(self):
        changed = sum(not self.detached(collider,info,stream)
                      for collider,(info,stream) in self.proofs.items())
        self.stats['changed'] = changed
        self.stats['confirmed'] = len(self.proofs)-changed
        return changed
