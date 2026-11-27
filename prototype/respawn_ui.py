"""복귀 지도에 표시하는 안전가옥 목록과 마우스 판정의 공통 배치."""

from .map_navigation import RESPAWN_SIDEBAR
from .simulation import RESPAWN_PAGE_SIZE


def panel_rect(size):
    width, _ = size
    return width - RESPAWN_SIDEBAR + 20, 48, width - 24, 400


def row_rect(size, index):
    left, _, right, _ = panel_rect(size)
    top = 134 + index * 42
    return left + 12, top, right - 12, top + 34


def page_rects(size):
    left, _, right, _ = panel_rect(size)
    return ((left + 12, 322, left + 64, 352),
            (right - 64, 322, right - 12, 352))


def visible_sites(simulation):
    start = simulation.respawn_page * RESPAWN_PAGE_SIZE
    return tuple(simulation.respawn_sites.values())[start:start + RESPAWN_PAGE_SIZE]


def contains(rect, point):
    return rect[0] <= point[0] < rect[2] and rect[1] <= point[1] < rect[3]


def hit_respawn_map(simulation, navigation, size, mouse):
    for index, site in enumerate(visible_sites(simulation)):
        if contains(row_rect(size, index), mouse):
            return "site", site["id"]
    if simulation.respawn_pages > 1:
        previous, following = page_rects(size)
        if contains(previous, mouse):
            return "page", -1
        if contains(following, mouse):
            return "page", 1

    projection = navigation.transform(simulation.world.bounds, size, sidebar=RESPAWN_SIDEBAR)
    left, top, width, height = projection.viewport
    if not left <= mouse[0] < left + width or not top <= mouse[1] < top + height:
        return None
    closest_id, closest_distance = None, 18 * 18
    for site in simulation.respawn_sites.values():
        x, y = projection.screen(*site["position"])
        if not left + 12 <= x <= left + width - 12 or not top + 12 <= y <= top + height - 12:
            continue
        distance = (x - mouse[0]) ** 2 + (y - mouse[1]) ** 2
        if distance <= closest_distance:
            closest_id, closest_distance = site["id"], distance
    return ("site", closest_id) if closest_id is not None else None
