"""캠페인의 실제 좌표를 그리는 RTS형 전략 지도 위젯."""

import math
import tkinter as tk

from .mapart import GROUND, compound_buildings, compound_ground, landscape
from .viewmodel import FACILITY_RADIUS_M, Camera, pick


BACKGROUND = "#101b24"
TEXT = "#dce8ed"
MUTED = "#8196a5"
ACCENT = "#6de0c5"
GOLD = "#f2c56f"
RED = "#ed797e"
BLUE = "#81b3ec"
FONT = "Malgun Gothic"


class StrategicMap(tk.Canvas):
    def __init__(self, master, state, *, on_select):
        super().__init__(master, background=GROUND, highlightthickness=0)
        self.state = state
        self.on_select = on_select
        self.camera = Camera()
        self.minimap_camera = Camera()
        self.selection = None
        self.markers = {}
        self.dirty = True
        self.fitted = False
        self.drag = None
        self.minimap_box = None
        self.bind("<Configure>", self._resize)
        self.bind("<Button-1>", self._click)
        self.bind("<Double-Button-1>", self._focus_click)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda event: self.zoom(1.2, event.x, event.y))
        self.bind("<Button-5>", lambda event: self.zoom(1 / 1.2, event.x, event.y))
        for button in (2, 3):
            self.bind(f"<ButtonPress-{button}>", self._drag_start)
            self.bind(f"<B{button}-Motion>", self._drag_move)
        for key, dx, dy in (("Left", 40, 0), ("Right", -40, 0), ("Up", 0, 40), ("Down", 0, -40),
                            ("a", 40, 0), ("d", -40, 0), ("w", 0, 40), ("s", 0, -40)):
            self.bind(f"<KeyPress-{key}>", lambda event, x=dx, y=dy: self.pan(x, y))

    def _resize(self, event):
        if not self.fitted and event.width > 100 and event.height > 100:
            self.focus_region()
            self.fitted = True
        self.dirty = True

    def fit(self):
        self.camera.fit(self.state, self.winfo_width(), self.winfo_height())
        self.dirty = True

    def focus_region(self):
        if not self.state.regions:
            self.fit()
            return
        region = max(self.state.regions.values(), key=lambda item: item.importance)
        points = [self.state.nodes[self.state.facilities[key].node_id].position for key in region.facility_ids]
        if not points:
            self.fit()
            return
        x0, x1 = min(p.x for p in points) - 220, max(p.x for p in points) + 220
        y0, y1 = min(p.y for p in points) - 220, max(p.y for p in points) + 220
        scale = min(self.winfo_width() / (x1 - x0), self.winfo_height() / (y1 - y0), 1.2)
        self.focus_world((x0 + x1) / 2, (y0 + y1) / 2, scale=scale)

    def focus_world(self, x, y, *, scale=None):
        self.camera.scale = scale if scale is not None else max(self.camera.scale, 0.8)
        self.camera.offset_x = self.winfo_width() / 2 - x * self.camera.scale
        self.camera.offset_y = self.winfo_height() / 2 - y * self.camera.scale
        self.dirty = True

    def _focus_click(self, event):
        self.focus_world(*self.camera.world(event.x, event.y))

    def pan(self, dx, dy):
        self.camera.offset_x += dx
        self.camera.offset_y += dy
        self.dirty = True

    def zoom(self, factor, x, y):
        self.camera.zoom(factor, x, y)
        self.dirty = True

    def _wheel(self, event):
        self.zoom(1.2 if event.delta > 0 else 1 / 1.2, event.x, event.y)

    def _drag_start(self, event):
        self.focus_set()
        self.drag = (event.x, event.y)

    def _drag_move(self, event):
        if self.drag is not None:
            self.pan(event.x - self.drag[0], event.y - self.drag[1])
            self.drag = (event.x, event.y)

    def _click(self, event):
        self.focus_set()
        if self.minimap_box is not None:
            x0, y0, x1, y1 = self.minimap_box
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                x, y = self.minimap_camera.world(event.x - x0, event.y - y0)
                self.camera.offset_x = self.winfo_width() / 2 - x * self.camera.scale
                self.camera.offset_y = self.winfo_height() / 2 - y * self.camera.scale
                self.dirty = True
                return
        self.on_select(pick(self.state, self.camera, self.markers, event.x, event.y))

    def render(self, markers):
        self.markers = markers
        if self.dirty:
            self._terrain()
            self.dirty = False
        self.delete("dynamic")
        self._selected_route()
        self._counts()
        width, height = self.winfo_width(), self.winfo_height()
        for vehicle in self.state.vehicles.values():
            if vehicle.status == "destroyed" and vehicle.position is not None:
                x, y = self.camera.screen(vehicle.position.x, vehicle.position.y)
                if -20 <= x <= width + 20 and -20 <= y <= height + 20:
                    self.create_text(x, y, text="×", fill=RED, font=(FONT, 16), tags="dynamic")
        for marker in markers.values():
            x, y = self.camera.screen(marker.x, marker.y)
            if not -30 <= x <= width + 30 or not -30 <= y <= height + 30:
                continue
            color = RED if marker.waiting else (BLUE if marker.phase == "returning"
                                               else GOLD if marker.kind == "export" else ACCENT)
            if self.selection == ("operation", marker.id):
                self.create_oval(x - 17, y - 17, x + 17, y + 17,
                                 outline="white", width=2, tags="dynamic")
            operation = self.state.operations[marker.id]
            vehicle_ids = [key for key in operation.vehicle_ids if self.state.vehicles[key].status != "destroyed"]
            for index, vehicle_id in enumerate(vehicle_ids):
                # 복수 차량은 같은 추상 수송대 위치의 표시 슬롯으로만 펼친다.
                self._truck(x, y + index * 11, marker.heading, color, tag="dynamic")
            if not vehicle_ids:
                self.create_oval(x - 5, y - 5, x + 5, y + 5, fill=color, outline=TEXT, tags="dynamic")
            label = f"#{marker.id.removeprefix('operation_')}  ×{marker.vehicles}"
            self.create_text(x, y - 19, text=label, fill=color, font=(FONT, 8), tags="dynamic")
        self._minimap()
        self.create_text(14, height - 31, anchor="sw", text="보급 ▶   출하 ▶   귀환 ▶   대기 ▶",
                         fill=MUTED, font=(FONT, 9), tags="dynamic")
        for offset, color in ((0, ACCENT), (64, GOLD), (128, BLUE), (192, RED)):
            self.create_rectangle(15 + offset, height - 17, 31 + offset, height - 14,
                                  fill=color, outline="", tags="dynamic")
        self.create_text(15, 14, anchor="nw", text="STRATEGIC MAP  /  후방 군수 작전망",
                         fill=MUTED, font=(FONT, 10), tags="dynamic")

    def _terrain(self):
        self.delete("terrain")
        width, height = self.winfo_width(), self.winfo_height()
        landscape(self, self.state, self.camera, width, height)
        left, top = self.camera.world(0, 0)
        right, bottom = self.camera.world(width, height)
        spacing = 10 ** math.ceil(math.log10(70 / self.camera.scale))
        for x in range(math.floor(left / spacing), math.ceil(right / spacing) + 1):
            sx, _ = self.camera.screen(x * spacing, 0)
            self.create_line(sx, 0, sx, height, fill="#3c4a37", tags="terrain")
        for y in range(math.floor(top / spacing), math.ceil(bottom / spacing) + 1):
            _, sy = self.camera.screen(0, y * spacing)
            self.create_line(0, sy, width, sy, fill="#3c4a37", tags="terrain")
        for region in self.state.regions.values():
            points = [self.state.nodes[self.state.facilities[fid].node_id].position
                      for fid in region.facility_ids]
            if not points:
                continue
            x0, y0 = self.camera.screen(min(p.x for p in points) - 300, min(p.y for p in points) - 320)
            x1, y1 = self.camera.screen(max(p.x for p in points) + 300, max(p.y for p in points) + 420)
            self.create_rectangle(x0, y0, x1, y1, outline="#6d805e", dash=(6, 8), tags="terrain")
            self.create_text(x0 + 8, y0 + 7, anchor="nw", text=f"REGION {region.id.upper()}",
                             fill="#a2b48a", font=(FONT, 9, "bold"), tags="terrain")
        detailed = self.camera.scale >= 0.35
        if detailed:
            for facility in self.state.facilities.values():
                point = self.state.nodes[facility.node_id].position
                x, y = self.camera.screen(point.x, point.y)
                radius = FACILITY_RADIUS_M * self.camera.scale
                if -radius <= x <= width + radius and -radius <= y <= height + radius:
                    compound_ground(self, self.state, self.camera, facility)
        for road in self.state.roads.values():
            start, end = self.state.nodes[road.start].position, self.state.nodes[road.end].position
            coordinates = (*self.camera.screen(start.x, start.y), *self.camera.screen(end.x, end.y))
            road_width = max(4, 13 * self.camera.scale)
            self.create_line(*coordinates, fill="#8a8870", width=road_width + 4, tags="terrain")
            self.create_line(*coordinates, fill=RED if road.blocked else "#4b5050", width=road_width,
                             dash=(7, 5) if road.blocked else (), tags="terrain")
            if detailed and not road.blocked:
                self.create_line(*coordinates, fill="#aaa78e", width=1, dash=(12, 14), tags="terrain")
            if road.blocked:
                x, y = self.camera.screen((start.x + end.x) / 2, (start.y + end.y) / 2)
                self.create_text(x, y - 12, text="차단", fill=RED, font=(FONT, 8), tags="terrain")
        facility_nodes = {facility.node_id for facility in self.state.facilities.values()}
        for node in self.state.nodes.values():
            if node.id in facility_nodes:
                continue
            x, y = self.camera.screen(node.position.x, node.position.y)
            external = node.id in {self.state.source.node_id, self.state.demand.node_id}
            color = GOLD if node.id == self.state.demand.node_id else BLUE
            radius = 10 if external else 4
            self.create_polygon(x, y - radius, x + radius, y, x, y + radius, x - radius, y,
                                fill=color if external else "#526875", outline=BACKGROUND, tags="terrain")
            label = ("외부 공급원" if node.id == self.state.source.node_id else
                     "외부 전선" if node.id == self.state.demand.node_id else node.id)
            self.create_text(x, y + 22, text=label, fill=TEXT, font=(FONT, 9), tags="terrain")
        for facility in self.state.facilities.values():
            point = self.state.nodes[facility.node_id].position
            x, y = self.camera.screen(point.x, point.y)
            if not -100 <= x <= width + 100 or not -100 <= y <= height + 100:
                continue
            if detailed:
                compound_buildings(self, self.state, self.camera, facility)
            color = RED if facility.damage == 100 else GOLD if "logistics" in facility.capabilities else BLUE
            self.create_rectangle(x - 13, y - 12, x + 13, y + 12, fill=BACKGROUND,
                                  outline=color, width=2, tags="terrain")
            symbol = "HQ" if "command" in facility.capabilities else "WH" if "logistics" in facility.capabilities else "C"
            self.create_text(x, y, text="×" if facility.damage == 100 else symbol,
                             fill=color, font=(FONT, 8, "bold"), tags="terrain")
            label = "본부" if symbol == "HQ" else "창고" if symbol == "WH" else "통신"
            self.create_text(x, y + 25, text=f"{facility.region_id.upper()} {label}",
                             fill=TEXT, font=(FONT, 8), tags="terrain")
        # 정적 배경은 선택·수송대·미니맵보다 아래에 유지한다.
        self.tag_lower("terrain")

    def _counts(self):
        for facility in self.state.facilities.values():
            point = self.state.nodes[facility.node_id].position
            x, y = self.camera.screen(point.x, point.y)
            if not -100 <= x <= self.winfo_width() + 100 or not -100 <= y <= self.winfo_height() + 100:
                continue
            soldiers = sum(unit.soldiers for unit in self.state.units.values() if unit.node_id == facility.node_id)
            vehicles = sum(vehicle.status != "destroyed" and vehicle.node_id == facility.node_id
                           for vehicle in self.state.vehicles.values())
            self.create_text(x, y + 41, text=f"병 {soldiers} · 차 {vehicles}",
                             fill=MUTED, font=(FONT, 8), tags="dynamic")
            if self.camera.scale >= 0.35:
                parked = [vehicle for vehicle in self.state.vehicles.values()
                          if vehicle.node_id == facility.node_id and vehicle.status != "destroyed"]
                for index, vehicle in enumerate(parked):
                    self._truck(x - 22 + index * 16, y - 24, 0, "#bdc0a0", tag="dynamic")
                groups = [unit for unit in self.state.units.values()
                          if unit.node_id == facility.node_id and unit.soldiers]
                for index, unit in enumerate(groups):
                    sx, sy = x - 22 + index * 14, y + 61
                    self.create_rectangle(sx - 4, sy - 4, sx + 4, sy + 4, fill="#b1bd8c",
                                          outline="#2a352a", tags="dynamic")
                    self.create_text(sx, sy + 12, text=str(unit.soldiers), fill=TEXT,
                                     font=(FONT, 7), tags="dynamic")
            if self.selection == ("facility", facility.id):
                radius = max(19, FACILITY_RADIUS_M * self.camera.scale)
                self.create_rectangle(x - radius, y - radius, x + radius, y + radius,
                                      outline="white", width=2, tags="dynamic")

    def _truck(self, x, y, heading, color, *, tag):
        cosine, sine = math.cos(heading), math.sin(heading)

        def polygon(points, fill):
            vertices = []
            for dx, dy in points:
                vertices.extend((x + dx * cosine - dy * sine, y + dx * sine + dy * cosine))
            self.create_polygon(vertices, fill=fill, outline="#18231c", tags=tag)

        polygon(((-8, -5), (8, -5), (8, 5), (-8, 5)), color)
        polygon(((3, -4), (7, -4), (7, 4), (3, 4)), "#d5d8b8")
        polygon(((-7, -3), (1, -3), (1, 3), (-7, 3)), "#526355")

    def _selected_route(self):
        if self.selection is None:
            return
        kind, key = self.selection
        if kind == "operation" and key in self.state.operations:
            nodes = self.state.operations[key].route
            color = GOLD
        elif kind == "road":
            road = self.state.roads[key]
            nodes, color = [road.start, road.end], "white"
        else:
            return
        coordinates = []
        for node_id in nodes:
            point = self.state.nodes[node_id].position
            coordinates.extend(self.camera.screen(point.x, point.y))
        if len(coordinates) >= 4:
            self.create_line(*coordinates, fill=color, width=2, dash=(6, 4),
                             arrow=tk.LAST, tags="dynamic")

    def _minimap(self):
        width, height = 195, 110
        x0, y0 = self.winfo_width() - width - 14, self.winfo_height() - height - 14
        self.minimap_box = (x0, y0, x0 + width, y0 + height)
        self.minimap_camera.fit(self.state, width, height, padding=14)
        self.create_rectangle(*self.minimap_box, fill="#0b141d", outline="#48606c", tags="dynamic")
        for road in self.state.roads.values():
            start, end = self.state.nodes[road.start].position, self.state.nodes[road.end].position
            ax, ay = self.minimap_camera.screen(start.x, start.y)
            bx, by = self.minimap_camera.screen(end.x, end.y)
            self.create_line(x0 + ax, y0 + ay, x0 + bx, y0 + by,
                             fill=RED if road.blocked else "#456575", tags="dynamic")
        for marker in self.markers.values():
            x, y = self.minimap_camera.screen(marker.x, marker.y)
            self.create_oval(x0 + x - 2, y0 + y - 2, x0 + x + 2, y0 + y + 2,
                             fill=GOLD if marker.kind == "export" else ACCENT, outline="", tags="dynamic")
        left, top = self.minimap_camera.screen(*self.camera.world(0, 0))
        right, bottom = self.minimap_camera.screen(*self.camera.world(self.winfo_width(), self.winfo_height()))
        clip = lambda value, maximum: max(1, min(maximum - 1, value))
        self.create_rectangle(x0 + clip(left, width), y0 + clip(top, height),
                              x0 + clip(right, width), y0 + clip(bottom, height),
                              outline=TEXT, tags="dynamic")
