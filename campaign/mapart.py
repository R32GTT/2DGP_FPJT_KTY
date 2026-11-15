"""전략 상태 위의 탑다운 외형. 지형·시설 그림은 충돌·전술 데이터를 생성하지 않는다."""

import math
import random

from .viewmodel import FACILITY_RADIUS_M, segment_distance


GROUND = "#354332"


def rectangle(canvas, camera, x0, y0, x1, y1, **options):
    return canvas.create_rectangle(*camera.screen(x0, y0), *camera.screen(x1, y1),
                                   tags="terrain", **options)


def landscape(canvas, state, camera, width, height):
    canvas.create_rectangle(0, 0, width, height, fill=GROUND, outline="", tags="terrain")
    if camera.scale < 0.03:
        return
    left, top = camera.world(0, 0)
    right, bottom = camera.world(width, height)
    nodes = [node.position for node in state.nodes.values()]
    left, right = max(left, min(p.x for p in nodes) - 800), min(right, max(p.x for p in nodes) + 800)
    top, bottom = max(top, min(p.y for p in nodes) - 800), min(bottom, max(p.y for p in nodes) + 800)
    cell = 200
    detail_count = 6 if camera.scale >= 0.5 else 2 if camera.scale >= 0.15 else 1
    for grid_x in range(math.floor(left / cell), math.ceil(right / cell)):
        for grid_y in range(math.floor(top / cell), math.ceil(bottom / cell)):
            # 보기용 난수 스트림은 캠페인 RNG와 완전히 분리한다.
            generator = random.Random(f"landscape:{state.seed}:{grid_x}:{grid_y}:{cell}")
            x0, y0 = grid_x * cell, grid_y * cell
            color = generator.choice(("#354332", "#374634", "#394834", "#334130"))
            rectangle(canvas, camera, x0, y0, x0 + cell, y0 + cell, fill=color, outline="")
            for index in range(6):
                x, y = x0 + generator.random() * cell, y0 + generator.random() * cell
                radius_m = generator.uniform(7, 14)
                tree_color = generator.choice(("#213d2e", "#284b35", "#31533a"))
                # 줌 단계가 바뀌어도 같은 나무의 좌표와 외형은 유지한다.
                if index >= detail_count:
                    continue
                if any(abs(x - state.nodes[f.node_id].position.x) < FACILITY_RADIUS_M + 20
                       and abs(y - state.nodes[f.node_id].position.y) < FACILITY_RADIUS_M + 20
                       for f in state.facilities.values()):
                    continue
                if any(segment_distance(x, y, state.nodes[r.start].position.x,
                                        state.nodes[r.start].position.y, state.nodes[r.end].position.x,
                                        state.nodes[r.end].position.y) < 23 for r in state.roads.values()):
                    continue
                sx, sy = camera.screen(x, y)
                radius = max(2, radius_m * camera.scale)
                canvas.create_oval(sx - radius + 3, sy - radius + 4, sx + radius + 3, sy + radius + 4,
                                   fill="#293629", outline="", tags="terrain")
                canvas.create_oval(sx - radius, sy - radius, sx + radius, sy + radius,
                                   fill=tree_color,
                                   outline="#3e5e40", tags="terrain")


def compound_ground(canvas, state, camera, facility):
    point = state.nodes[facility.node_id].position
    radius = FACILITY_RADIUS_M
    rectangle(canvas, camera, point.x - radius, point.y - radius, point.x + radius, point.y + radius,
              fill="#535b4b", outline="#6f7660")
    rectangle(canvas, camera, point.x - radius + 8, point.y - radius + 8,
              point.x + radius - 8, point.y + radius - 8, fill="#66675a", outline="")


def compound_buildings(canvas, state, camera, facility):
    point = state.nodes[facility.node_id].position
    radius = FACILITY_RADIUS_M
    incident = [road for road in state.roads.values() if facility.node_id in {road.start, road.end}]
    gates = {"left": [], "right": [], "top": [], "bottom": []}
    for road in incident:
        neighbor = state.nodes[road.end if road.start == facility.node_id else road.start].position
        dx, dy = neighbor.x - point.x, neighbor.y - point.y
        factor = radius / max(abs(dx), abs(dy))
        if abs(dx) >= abs(dy):
            gates["right" if dx > 0 else "left"].append(dy * factor)
        else:
            gates["bottom" if dy > 0 else "top"].append(dx * factor)
    for side, openings in gates.items():
        cursor = -radius
        for opening in sorted(openings) + [radius + 16]:
            end = min(radius, opening - 16)
            if end > cursor:
                if side in {"left", "right"}:
                    x = point.x + (-radius if side == "left" else radius)
                    start_point, end_point = (x, point.y + cursor), (x, point.y + end)
                else:
                    y = point.y + (-radius if side == "top" else radius)
                    start_point, end_point = (point.x + cursor, y), (point.x + end, y)
                canvas.create_line(*camera.screen(*start_point), *camera.screen(*end_point),
                                   fill="#a5a98b", width=2, dash=(5, 3), tags="terrain")
            cursor = opening + 16
    candidates = [(-65, -65), (65, -65), (-65, 65), (65, 65)]

    def clearance(offset):
        x, y = point.x + offset[0], point.y + offset[1]
        return min((segment_distance(x, y, state.nodes[road.start].position.x,
                                     state.nodes[road.start].position.y, state.nodes[road.end].position.x,
                                     state.nodes[road.end].position.y) for road in incident), default=100)

    # 그림이 실제 운송 경로를 덮지 않도록 도로에서 먼 구석에 건물을 배치한다.
    candidates.sort(key=clearance, reverse=True)
    warehouse = "logistics" in facility.capabilities
    for index, (dx, dy) in enumerate(candidates[:2 if warehouse else 1]):
        x, y = point.x + dx, point.y + dy
        half_w, half_h = (35, 23) if warehouse else (31, 27)
        rectangle(canvas, camera, x - half_w + 5, y - half_h + 7, x + half_w + 5, y + half_h + 7,
                  fill="#3b4039", outline="")
        color = "#8b6254" if facility.damage == 100 else "#929991" if warehouse else "#89937d"
        rectangle(canvas, camera, x - half_w, y - half_h, x + half_w, y + half_h,
                  fill=color, outline="#c1c3ad", width=2)
        ridge_y = camera.screen(x, y)[1]
        canvas.create_line(*camera.screen(x - half_w, y), *camera.screen(x + half_w, y),
                           fill="#59665c", width=2, tags="terrain")
        if warehouse:
            for offset in range(-25, 30, 10):
                sx, _ = camera.screen(x + offset, y)
                canvas.create_line(sx, ridge_y - half_h * camera.scale, sx, ridge_y + half_h * camera.scale,
                                   fill="#737d74", tags="terrain")
            rectangle(canvas, camera, x - 12, y + half_h - 4, x + 12, y + half_h,
                      fill="#404d45", outline="")
        elif "communication" in facility.capabilities:
            sx, sy = camera.screen(x, y)
            arm = 16 * camera.scale
            canvas.create_line(sx - arm, sy + arm, sx, sy - arm, sx + arm, sy + arm,
                               fill="#e3dfb1", width=2, tags="terrain")
            canvas.create_oval(sx - arm * 0.8, sy - arm * 1.8, sx + arm * 0.8, sy - arm * 0.2,
                               outline="#e3dfb1", width=2, tags="terrain")
        else:
            rectangle(canvas, camera, x - 9, y - 12, x + 9, y + 8, fill="#435443", outline="#b6bca4")
    # 화물 컨테이너는 전술 오브젝트가 아닌 시설 외형이다.
    if warehouse:
        dx, dy = candidates[-1]
        if clearance((dx, dy)) > 32:
            for offset, color in ((-12, "#7d7153"), (12, "#687981")):
                rectangle(canvas, camera, point.x + dx + offset - 8, point.y + dy - 18,
                          point.x + dx + offset + 8, point.y + dy + 18, fill=color, outline="#b0ae91")
