"""도로 폭과 차량 회전 궤적을 합치되 포장재 경계와 오목한 외곽을 보존한다."""

import numpy as np
from shapely import LineString, Polygon, box, constrained_delaunay_triangles, union_all


def polygons(shape):
    if shape.geom_type == 'Polygon':
        yield shape
    elif hasattr(shape, 'geoms'):
        for child in shape.geoms:
            yield from polygons(child)


def build_junctions(nodes, segments, bounds=None):
    from .road_generation import VEHICLE_MARGIN, VEHICLE_SIZE

    incident = {}
    for segment in segments:
        for key in segment['nodes']:
            incident.setdefault(key, []).append(segment)
    half_length, half_width = VEHICLE_SIZE[0] / 2 + VEHICLE_MARGIN, VEHICLE_SIZE[1] / 2 + VEHICLE_MARGIN
    junctions = []
    for key, adjacent in sorted(incident.items()):
        if len(adjacent) < 2:
            continue
        center = np.array(nodes[key])
        arms, parts, swept = [], {}, []
        for segment in adjacent:
            other = next(other for other in segment['nodes'] if other != key)
            vector = np.subtract(nodes[other], center)
            length = np.linalg.norm(vector)
            direction = vector / length
            start = center + direction * min(half_length, max(0, length - half_length))
            parts.setdefault(segment['material'], []).append(LineString([center, start]).buffer(segment['width'] / 2,
                                                                                              cap_style='flat'))
            arms.append((start, direction, segment))
        if len(arms) == 2 and np.dot(arms[0][1], arms[1][1]) < -.999:
            continue
        for index, (start, start_direction, first) in enumerate(arms):
            for end, end_direction, second in arms[index + 1:]:
                if np.dot(start_direction, end_direction) > .866:
                    continue
                material = min((first, second), key=lambda segment: (segment['width'], segment['material']))['material']
                if first['material'] == second['material']:
                    parts[material].append(LineString([start, center, end]).buffer(min(first['width'], second['width']) / 2,
                                                                                 cap_style='flat', join_style='bevel'))
                previous = None
                for t in np.linspace(0, 1, 7):
                    position = (1 - t) ** 2 * start + 2 * t * (1 - t) * center + t ** 2 * end
                    tangent = (1 - t) * (center - start) + t * (end - center)
                    if np.linalg.norm(tangent) < 1e-8:
                        tangent = -(1 - t) * start_direction + t * end_direction
                    if np.linalg.norm(tangent) < 1e-8:
                        tangent = end_direction
                    tangent = tangent / np.linalg.norm(tangent)
                    normal = np.array([-tangent[1], tangent[0]])
                    pose = Polygon([position + tangent * a * half_length + normal * b * half_width
                                    for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
                    sweep = union_all([previous, pose]).convex_hull if previous is not None else pose
                    parts[material].append(sweep)
                    swept.append(sweep)
                    previous = pose
        if not swept:
            continue
        parts = {material: [union_all(group)] for material, group in parts.items()}
        parts = {material: [group[0].union(group[0].simplify(.3, preserve_topology=True))]
                 for material, group in parts.items()}
        surface = union_all([part for group in parts.values() for part in group])
        if bounds:
            surface = surface.intersection(box(bounds.x, bounds.y, bounds.right, bounds.bottom))
        surface = union_all(list(polygons(surface)))
        patches, covered = [], Polygon()
        for material in sorted(parts, key=lambda value: value != 'asphalt'):
            shape = union_all(parts[material]).intersection(surface).difference(covered)
            covered = union_all([covered, shape])
            for polygon in polygons(shape):
                if polygon.area < 1e-8:
                    continue
                patches.append({'material': material, 'polygon': list(map(list, polygon.exterior.coords))[:-1],
                                'triangles': [list(point) for triangle in constrained_delaunay_triangles(polygon).geoms
                                              for point in list(triangle.exterior.coords)[:3]]})
        junctions.append({'node': key, 'polygon': list(map(list, surface.exterior.coords))[:-1],
                          'vehicle_polygon': list(map(list, union_all(swept).exterior.coords))[:-1] if swept else [],
                          'surfaces': patches})
    return junctions
