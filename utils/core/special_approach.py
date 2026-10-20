"""Resource interaction standpoints stay outside the resource's solid center."""
import math
from utils.core import thresholds as policy
from utils.core.coordinate_route import distance


def in_interaction_range(position, resource):
    return distance(position, resource) <= policy.COORD_SPECIAL_INTERACTION_RADIUS \
        and abs(position[1]-resource[1]) <= policy.COORD_MAX_HEIGHT_ERROR


def interaction_standpoint(position, resource, obstacles):
    if in_interaction_range(position, resource):
        return list(position)
    angle = math.atan2(position[2]-resource[2], position[0]-resource[0])
    candidates = []
    for radius in (.9, 1.05, 1.2):
        for turn in range(24):
            theta = angle+turn*math.tau/24
            point = [resource[0]+radius*math.cos(theta), resource[1], resource[2]+radius*math.sin(theta)]
            obstacles.set_goal(point)
            if obstacles.clear((point[0], point[2]), (point[0], point[2])):
                candidates.append(point)
    if not candidates:
        raise ValueError('资源附近暂未找到可站立的交互位置')
    return min(candidates, key=lambda point: distance(position, point))
