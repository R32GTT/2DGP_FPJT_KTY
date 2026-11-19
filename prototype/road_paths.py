"""연결점은 고정하고 짧은 꺾임만 검증 가능한 곡선으로 다듬는다."""

import math
from shapely import LineString


def smooth_path(points, valid, locked=()):
    points = [tuple(point) for point in points]
    result = [points[0]]
    for previous, center, following in zip(points, points[1:], points[2:]):
        before, after = math.dist(previous, center), math.dist(center, following)
        if min(before, after) < 1e-6 or center in locked:
            result.append(center)
            continue
        incoming = [(center[i] - previous[i]) / before for i in (0, 1)]
        outgoing = [(following[i] - center[i]) / after for i in (0, 1)]
        if sum(a * b for a, b in zip(incoming, outgoing)) > .995:
            result.append(center)
            continue
        distance = min(6, before * .3, after * .3)
        start = [center[i] - incoming[i] * distance for i in (0, 1)]
        end = [center[i] + outgoing[i] * distance for i in (0, 1)]
        curve = [tuple((1 - t) ** 2 * start[i] + 2 * (1 - t) * t * center[i] + t ** 2 * end[i]
                       for i in (0, 1)) for t in (0, .25, .5, .75, 1)]
        result.extend(curve if valid(LineString(curve)) else [center])
    result.append(points[-1])
    return [point for index, point in enumerate(result) if index == 0 or math.dist(point, result[index - 1]) > 1e-6]
