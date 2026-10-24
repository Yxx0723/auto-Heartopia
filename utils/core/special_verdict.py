"""Turn two read-only samples of a dynamic resource into an honest verdict.

A press of F is not a collection. This module compares the state that the
scene reader returned before and after one press and names what is actually
observable:

* ``gathered``  a parsed counter went down, so the game registered the action;
* ``gone``      the target left the scene entirely;
* ``cooling``   the cold-end timestamp jumped forward, which is how a started
                cooldown looks - probable, still not a confirmed drop;
* ``changed``   some other field of the collectable struct moved;
* ``unchanged`` nothing moved at all;
* ``unknown``   the scene was not readable, so no claim is made either way.

Only ``gathered`` is treated as confirmed. Disappearance may be unloading.
Nothing here writes
to the game, and a digest match is never used to claim a drop.
"""

GATHERED, GONE, COOLING = 'gathered', 'gone', 'cooling'
CHANGED, UNCHANGED, UNKNOWN = 'changed', 'unchanged', 'unknown'

CONFIRMED = (GATHERED,)
# A cooldown start ends the press loop but is reported as probable, not proven.
SETTLED = CONFIRMED + (COOLING, GONE)

# One press can be sampled several times while the game settles. The strongest
# observation wins so a later quiet sample cannot erase a real change.
_RANK = {GONE: 5, GATHERED: 5, COOLING: 4, CHANGED: 3, UNCHANGED: 2, UNKNOWN: 1}


def strongest(outcomes):
    """Reduce several samples of one press to the most informative one."""
    return max(outcomes, key=lambda outcome: _RANK.get(outcome, 0), default=UNKNOWN)


def snapshot(target):
    """Copy the verdict-relevant fields out of one scene sample."""
    if not target:
        return None
    return {'available': target.get('resource_available'),
            'cold_end': target.get('resource_cold_end_ms'),
            'digest': target.get('resource_state_digest'),
            'entity': target.get('resource_entity'), 'component': target.get('resource_component')}


def verdict(before, after):
    """Classify one press. ``after`` is ``None`` when the target has vanished."""
    if after is None:
        return GONE
    if before and any(before.get(k) is not None and before.get(k) != after.get(k)
                      for k in ('entity', 'component')):
        return UNKNOWN  # A replacement/pool slot is not the previous target's drop.
    if not before or type(before.get('available')) is not int \
            or type(after.get('available')) is not int:
        return UNKNOWN
    # A moved counter proves the press landed even if other fields are unread.
    if after['available'] < before['available']:
        return GATHERED
    if type(before.get('cold_end')) is int and type(after.get('cold_end')) is int \
            and after['cold_end'] > before['cold_end'] and after['cold_end'] > 0:
        return COOLING
    # A missing digest on either side is an unread field, not a change.
    if before.get('digest') and after.get('digest') and after['digest'] != before['digest']:
        return CHANGED
    return UNCHANGED


def conclusive(outcome):
    """True when pressing again cannot add information: stop the loop."""
    return outcome in SETTLED


def confirmed(outcome):
    return outcome in CONFIRMED
