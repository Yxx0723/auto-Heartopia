"""One target's whole attempt. Counter changes are observations, bag gains confirm.

Bag IDs below were read from owned StorageBackpack and TableBagitem names on
2026-10-24, not inferred from resource/collectable IDs. No game input here.
"""
from uuid import uuid4

from utils.core import special_verdict as verdict

ITEM_IDS = {'fluorite': 40026, 'roaming_oak': 40006}


def stock(sample, identity):
    if not isinstance(sample, dict) or not isinstance(sample.get('scope'), str) or not sample['scope']:
        return None
    counts = sample.get('counts')
    if not isinstance(counts, dict):
        return None
    count = counts.get(identity, 0)
    return count if type(count) is int and 0 <= count <= 100000000 else None


class HarvestEvidence:
    def __init__(self, category, target, inventory):
        self.category = category
        self.identity = uuid4().hex
        self.item_id = ITEM_IDS.get(category)
        self.initial = verdict.snapshot(target)
        self.after = self.initial
        self.counter_outcome = verdict.UNKNOWN
        self.before_count = stock(inventory, self.item_id) if self.item_id else None
        self.after_count = self.before_count
        self.confirmed_count = None
        self.scope = inventory.get('scope') if self.before_count is not None else None
        self.invalid = False
        self.pressed = False
        self.confirmed = False

    def observe(self, target, inventory):
        if not self.pressed:
            return
        if target != 'unknown':
            after = verdict.snapshot(target) if target else None
            outcome = verdict.verdict(self.initial, after)
            # A recycled object cannot be attributed to the old attempt.
            if after and self.initial and any(self.initial.get(k) is not None and
                    self.initial[k] != after.get(k) for k in ('entity', 'component')):
                self.invalid = True
            self.counter_outcome = verdict.strongest([self.counter_outcome, outcome])
            if outcome != verdict.UNKNOWN:
                self.after = after
        count = stock(inventory, self.item_id)
        if count is not None and self.scope is not None:
            if inventory['scope'] != self.scope:
                self.invalid = True
            elif not self.invalid:
                self.after_count = count
                if count > self.before_count:
                    self.confirmed = True
                    if self.confirmed_count is None:
                        self.confirmed_count = count

    def fields(self):
        confirmed = self.confirmed and not self.invalid
        proof_count = self.confirmed_count if confirmed else self.after_count
        return {'resource_category': self.category, 'resource_evidence_id': self.identity, 'resource_item_id': self.item_id,
                'resource_confirmed': confirmed,
                'resource_confirmation_source': 'owned_backpack_delta' if confirmed else None,
                'resource_inventory_before': self.before_count,
                'resource_inventory_after': proof_count,
                'resource_inventory_last': self.after_count,
                'resource_inventory_delta': (proof_count-self.before_count
                    if self.scope is not None and not self.invalid else None),
                'resource_inventory_scope': self.scope,
                'resource_evidence_invalid': self.invalid,
                'resource_counter_outcome': self.counter_outcome,
                'resource_available_before': (self.initial or {}).get('available'),
                'resource_available_after': (self.after or {}).get('available')}
