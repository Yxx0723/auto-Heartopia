"""Actual stamina units, rather than a percentage threshold."""


def stamina_status(current, base, boosted):
    # The verified local-player record can exceed its nominal maximum (109/100
    # observed after restart). Keep that actual value; an overflow is not a
    # failed read and must not pause movement or trigger additional food.
    if any(type(v) is not int for v in (current, base, boosted)) \
            or not 0 < base <= boosted <= 10000 or not 0 <= current <= 10000:
        raise ValueError('体力数值无效')
    return {'state': 'ready', 'current': current, 'maximum': boosted,
            'base_maximum': base, 'percent': current/boosted*100, 'low': current < 30}


def usable_stamina(status):
    return (isinstance(status, dict) and status.get('state') == 'ready'
            and type(status.get('current')) is int and type(status.get('maximum')) is int
            and 0 <= status['current'] <= 10000 and 0 < status['maximum'] <= 10000)
