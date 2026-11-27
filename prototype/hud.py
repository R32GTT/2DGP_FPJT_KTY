"""캐시된 글자·단계 진행 막대·상태 카드. 게임 상태를 변경하지 않는다."""

import ctypes
from dataclasses import dataclass
import math
from itertools import chain
from time import perf_counter

import pico2d as pico

from campaign.clock import world_clock
from .geometry import Rect
from .inventory import BAG_LIMIT
from .intelligence import OPERATION_NAMES
from .map_batch import MapBatch, map_colliders, map_rectangles, map_roads
from .map_navigation import MapNavigation, RESPAWN_SIDEBAR
from .minimap_image import MINIMAP_SCALE, build_minimap_image
from .respawn_ui import page_rects, panel_rect, row_rect, visible_sites
from .weapons import STAGE_NAMES
from .hud_theme import (ACCENT, FONT_PATH, FONT_SIZES, FONT_WIDTH_SCALE, PANEL, PANEL_EDGE,
                        TEXT, THEME, vehicle_marker_color)
from .settings import DEFAULT_BINDINGS, binding_name


AI_STATES = {"idle": "대기", "patrol": "순찰", "return": "복귀", "boarding": "집결", "regroup": "수색 후 집결",
             "alert": "조사", "search": "전투 수색", "combat": "교전",
             "combat_guard": "전투 경계", "retreat": "엄폐·후퇴", "radio": "무전 중"}
AI_ACTIONS = {"cover": "엄폐", "retreat": "후퇴", "pin": "제압됨", "reload": "재장전",
              "fire": "사격", "advance": "접근", "flank": "측면 이동",
              "peek": "피킹", "peek_return": "엄폐 복귀", "reposition": "사선 확보", "wait": "사격 대기"}
SOUND_RESPONSES = {"new": "총성 감지", "renewed": "총성 재조사", "same_area": "총성 감지 · 경로 유지",
                   "out_of_range": "총성 감지 범위 밖", "no_estimate": "총성 감지 · 위치 추정 실패",
                   "checked": "총성 조사 종료", "unreachable": "소리 위치에 접근할 수 없음"}
SEARCH_CONFIRMED = THEME.white
SEARCH_VISIBLE = THEME.red
SEARCH_OCCLUDED = THEME.red_dim
SEARCH_OUTSIDE = THEME.muted


@dataclass
class Label:
    style: tuple
    width: int
    height: int
    raster_size: int = 0
    image: pico.Image | None = None
    opacity: int = 255


class HUD:
    def __init__(self, backend, settings=None):
        self.backend = backend
        self.bindings = settings.bindings if settings is not None else DEFAULT_BINDINGS
        self.font_path = str(FONT_PATH)
        self.type = FONT_SIZES
        self.fonts = {size: pico.load_font(self.font_path, size)
                      for size in set(FONT_SIZES.values()) | {13, 16, 24}}
        self.scaled_fonts = {}
        self.text_scale = 1.0
        self.labels = {}
        self.next_update = 0.0
        self.next_debug_update = 0.0
        self.ai_rows = 0
        self._map_world = None
        self._map_batches = {}
        self._map_image = None
        self._mini_image = None
        self._mini_world = None
        self._mini_revision = -1
        self._shown_weapon = None
        self._weapon_card_until = 0.0
        self._active_threats = set()
        self._threat_exits = {}
        self._threat_simulation = None

    def _draw_map_background(self, world, left, top, width, height):
        if world.map_background_path is None:
            return False
        if self._map_world is not world:
            self.prepare_map(world)
        self._map_image.draw(left + width / 2, top - height / 2, width, height)
        return True

    def prepare_map(self, world):
        if world.map_background_path is not None and self._map_world is not world:
            world.map_background_future.result()
            self._map_world = world
            self._map_batches.clear()
            self._map_image = pico.load_image(str(world.map_background_path))
        if world.map_background_path is None:
            self._prepare_minimap_image(world)

    def _prepare_minimap_image(self, world):
        if self._mini_world is world and self._mini_revision == world.revision:
            return
        image = build_minimap_image(world)
        width, height = image.size
        pixels = ctypes.create_string_buffer(image.tobytes())
        surface = self.backend.SDL_CreateRGBSurfaceWithFormatFrom(
            pixels, width, height, 32, width * 4, self.backend.SDL_PIXELFORMAT_RGBA32)
        if not surface:
            raise RuntimeError("미니맵 표면 생성 실패")
        try:
            texture = self.backend.SDL_CreateTextureFromSurface(self.backend.renderer, surface)
        finally:
            self.backend.SDL_FreeSurface(surface)
        if not texture:
            raise RuntimeError("미니맵 텍스처 생성 실패")
        self.backend.SDL_SetTextureScaleMode(texture, self.backend.SDL_ScaleModeNearest)
        self._mini_image = pico.Image(texture)
        self._mini_world = world
        self._mini_revision = world.revision

    def label(self, key, text, size=16, color=TEXT):
        if not text:
            self.labels.pop(key, None)
            return
        style = text, size, color
        cached = self.labels.get(key)
        if cached is not None and cached.style == style:
            return
        if size not in self.fonts:
            self.fonts[size] = pico.load_font(self.font_path, size)
        width, height = ctypes.c_int(), ctypes.c_int()
        if self.backend.TTF_SizeUTF8(self.fonts[size].font, text.encode("utf-8"),
                                     ctypes.byref(width), ctypes.byref(height)) < 0:
            raise RuntimeError("HUD 글자 크기 측정 실패")
        self.labels[key] = Label(style, round(width.value * FONT_WIDTH_SCALE), height.value)

    def _update_text_scale(self, camera):
        width, height = ctypes.c_int(), ctypes.c_int()
        if self.backend.SDL_GetRendererOutputSize(self.backend.renderer,
                                                   ctypes.byref(width), ctypes.byref(height)) < 0:
            return
        if width.value <= 0 or height.value <= 0:
            return
        scale = min(width.value / camera.width, height.value / camera.height)
        # 작은 창 크기 변화마다 새 글꼴과 텍스처를 만들지 않는다.
        bucket = max(1.0, math.ceil(scale * 4) / 4)
        if bucket != self.text_scale:
            for font in self.scaled_fonts.values():
                self.backend.TTF_CloseFont(font.font)
            self.scaled_fonts.clear()
            self.text_scale = bucket

    def text(self, key, x, y, *, opacity=255):
        backend = self.backend
        label = self.labels[key]
        text, size, color = label.style
        raster_size = math.ceil(size * self.text_scale)
        if label.raster_size != raster_size:
            font = self.fonts.get(raster_size) or self.scaled_fonts.get(raster_size)
            if font is None:
                font = self.scaled_fonts[raster_size] = pico.load_font(self.font_path, raster_size)
            surface = backend.TTF_RenderUTF8_Blended(font.font, text.encode("utf-8"), backend.SDL_Color(*color))
            if not surface:
                raise RuntimeError("HUD 글자 그리기 실패")
            try:
                texture = backend.SDL_CreateTextureFromSurface(backend.renderer, surface)
            finally:
                backend.SDL_FreeSurface(surface)
            if not texture:
                raise RuntimeError("HUD 텍스처 생성 실패")
            backend.SDL_SetTextureScaleMode(texture, backend.SDL_ScaleModeLinear)
            label.image, label.raster_size, label.opacity = pico.Image(texture), raster_size, 255

        if opacity != label.opacity:
            backend.SDL_SetTextureAlphaMod(label.image.texture, opacity)
            label.opacity = opacity
        destination = backend.SDL_FRect(x, pico.get_canvas_height() - y - label.height / 2,
                                        label.width, label.height)
        backend.SDL_RenderCopyF(backend.renderer, label.image.texture, None, destination)

    @staticmethod
    def panel(x, y, w, h):
        pico.draw_rectangle(x, y, x + w, y + h, *PANEL, THEME.panel_alpha, filled=True)
        pico.draw_rectangle(x, y, x + w, y + h, *PANEL_EDGE, THEME.edge_alpha)

    def _update_ai(self, simulation):
        nearby = sorted((actor for actor in simulation.guards if actor.hp > 0),
                        key=lambda actor: math.dist(actor.xy, simulation.player.xy))
        self.ai_rows = min(4, len(nearby))
        for index, actor in enumerate(nearby[:self.ai_rows]):
            brain = simulation.ai.brains[actor.id]
            memory = brain.memory
            time = simulation.time
            cues = [(memory.seen_at, "목격", memory.last_seen),
                    (memory.sound_at, "소리", memory.last_sound),
                    (memory.reported_at, "보고", memory.reported)]
            stamp, kind, point = max(cues, key=lambda cue: cue[0] if cue[2] is not None else -math.inf)
            cue = f"{kind} {point[0]:.0f}, {point[1]:.0f} · {time - stamp:.1f}초 전" if point else "단서 없음"
            action = AI_ACTIONS.get(brain.combat.action, "") if actor.state in {"combat", "retreat"} else ""
            state = " / ".join(part for part in (AI_STATES.get(actor.state, actor.state), action) if part)
            if time < brain.combat.combat_alert_until:
                state += f" · 경계 {math.ceil(brain.combat.combat_alert_until - time)}초"
            elif memory.recognition > 0:
                state += f" · 인식 {memory.recognition:.0%}"
            route = "목표 없음"
            if brain.search.task.scanning:
                route = "바라보는 방향 확인"
            elif (actor.state == "retreat" and brain.combat.action == "cover" and brain.combat.action_goal is not None
                  and math.dist(actor.xy, brain.combat.action_goal) <= 0.3):
                x, y = brain.combat.action_goal
                route = f"{x:.1f}, {y:.1f} · 엄폐 도착"
            elif brain.motion.pending_goal is not None:
                x, y = brain.motion.pending_goal
                route = f"{x:.1f}, {y:.1f} · 경로 계산 대기"
            elif brain.motion.door_stack is not None:
                route = f"문 옆 집결 · {brain.motion.door_wait}"
            elif brain.motion.door_wait:
                route = f"문 통과 차례 대기 · {brain.motion.door_wait}"
            elif brain.motion.blocked_by:
                route = f"병력 통과 대기 · {brain.motion.blocked_by}"
            elif brain.motion.goal is not None:
                if brain.motion.path_revision != simulation.world.revision:
                    status = "장애물 변경 · 재계산 대기"
                elif brain.motion.route_failed:
                    status = "도달 경로 없음"
                elif brain.motion.path:
                    status = f"경유점 {len(brain.motion.path)}개"
                else:
                    status = "도착"
                route = f"{brain.motion.goal[0]:.1f}, {brain.motion.goal[1]:.1f} · {status}"
                if brain.motion.goal == brain.search.cover:
                    route += " · 엄폐 이동"
            self.label(f"ai_id/{index}", f"{actor.id}   {state}", 13, ACCENT)
            self.label(f"ai_cue/{index}", f"{cue}   의심 {memory.suspicion:.2f} · 제압 {brain.combat.suppression:.2f}", 13)
            self.label(f"ai_route/{index}", route, 13)
            sound = SOUND_RESPONSES.get(brain.search.sound_response, "") if time - brain.search.sound_response_at < 5 else ""
            if brain.search.task.sweep is not None and actor.state in {"search", "combat_guard"}:
                sweep = brain.search.task.sweep
                sound = f"실내 수색 {len(sweep.checked)}/{len(sweep.area.points)} 지점 확인"
            if brain.look.reason:
                focus = brain.look.point
                location = f"{focus[0]:.1f}, {focus[1]:.1f} · " if focus is not None else ""
                sound = f"시선 {location}{brain.look.reason}"
            self.label(f"ai_sound/{index}", sound or "최근 총성 반응 없음", 13, THEME.muted)
        self.label("ai_hint", simulation.ai_hint, 13)

    def _update_debug(self, simulation, camera, now, update_ms, draw_ms, cache_count):
        if now < self.next_debug_update:
            return
        self.next_debug_update = now + 0.2
        player, weapon = simulation.player, simulation.player.weapon
        self.label("brand", "REVENANT / FIELD PROTOTYPE", 16, ACCENT)
        self.label("clock", f"{world_clock(simulation.lighting_time)}  |  {camera.magnification:.0%}", 13)
        if simulation.world.territories:
            territory = simulation.world.territory_at(player.xy)
            name = simulation.world.data["territories"].get(territory, {}).get("name", "지역 사이 연결로")
            self.label("campaign_location", name, 16, ACCENT)
        self.label("vitals", f"HP {player.hp:03.0f}   ARMOR {player.armor:02d}", 16)
        self.label("weapon", weapon.spec.name if weapon else "UNARMED", 24)
        self.label("ammo", f"{weapon.magazine:02d}+{int(weapon.chambered)} / {weapon.reserve:03d}" if weapon else "-- / ---", 24)
        mode = "AUTO" if weapon and weapon.automatic else "SEMI"
        self.label("mode", f"{mode}  ·  1/2/3 교체  ·  V 모드  ·  R 재장전 / C 취소", 13)
        self.label("message", simulation.message, 13)
        self.label("timing", f"update {update_ms:.2f}ms / draw {draw_ms:.2f}ms | cache {cache_count}/24 | 근거리 AI {getattr(simulation.ai, 'near_count', 0)}", 13)
        self.label("stock", f"DEPOT {simulation.campaign.supplies}  /  확보 {simulation.campaign.simulation.state.player_stock['supplies']}", 13)
        self._update_ai(simulation)
        for index, (key, title) in enumerate(STAGE_NAMES.items()):
            duration = weapon.reload_duration(index) if weapon else 0
            self.label(key, f"{index + 1} {title} {duration:g}s", 13)
        if weapon and (weapon.reloading or weapon.stage > 0):
            phase = STAGE_NAMES[weapon.spec.reload_stages[weapon.stage].id]
            status = "진행" if weapon.reloading else "중단 / R 재개"
            self.label("reload", f"{phase} — {status} · 남은 {weapon.remaining_reload:.2f}s", 13, ACCENT)
        else:
            self.label("reload", "완료 단계 보존 · 취소한 단계는 처음부터 재개", 13, THEME.muted)

    def _draw_debug(self, simulation, camera, search_counts):
        player, weapon = simulation.player, simulation.player.weapon
        width, height = camera.width, camera.height
        self.panel(12, height - 67, width - 24, 55)
        self.text("brand", 26, height - 30)
        self.text("clock", 26, height - 50)
        if simulation.world.territories:
            self.text("campaign_location", 304, height - 41)
        self.text("vitals", width - 245, height - 29)
        pico.draw_rectangle(width - 245, height - 53, width - 30, height - 47,
                            *PANEL, THEME.track_alpha, filled=True)
        pico.draw_rectangle(width - 245, height - 53, width - 245 + 215 * player.hp / 100,
                             height - 47, *ACCENT, filled=True)
        self.panel(12, 12, 410, 142)
        self.text("weapon", 26, 129)
        self.text("ammo", 240, 129)
        self.text("mode", 26, 100)
        for index, key in enumerate(STAGE_NAMES):
            x = 26 + index * 128
            self.text(key, x, 77)
            color, progress = ACCENT, 0
            if weapon and index < weapon.stage:
                color, progress = THEME.white, 1
            elif weapon and index == weapon.stage and (weapon.reloading or weapon.stage):
                progress = weapon.stage_elapsed / weapon.reload_duration(index)
            pico.draw_rectangle(x, 57, x + 116, 62, *PANEL, THEME.track_alpha, filled=True)
            if progress:
                pico.draw_rectangle(x, 57, x + 116 * progress, 62, *color, filled=True)
        self.text("reload", 26, 35)
        self.panel(434, 12, width - 650, 70)
        self.text("message", 447, 60)
        self.text("stock", 447, 35)
        self.panel(12, height - 120, 650, 40)
        self.text("timing", 24, height - 100)
        if self.ai_rows:
            top = height - 80
            left = width - 365
            self.panel(left, top - self.ai_rows * 80 - 10, 350, self.ai_rows * 80 + 10)
            for index in range(self.ai_rows):
                y = top - 15 - index * 80
                for row, key in enumerate(("ai_id", "ai_cue", "ai_route", "ai_sound")):
                    self.text(f"{key}/{index}", left + 12, y - row * 18)
        if "ai_hint" in self.labels:
            self.text("ai_hint", 24, height - 136)
        self.label("ai_legend", "흰색  맵 노드 · 빨강  현재 경로 · 빨강 +  소리 추정", 13)
        legend_y = height - (156 if "ai_hint" in self.labels else 136)
        self.text("ai_legend", 24, legend_y)
        if search_counts is not None:
            checked, total, blocked = search_counts
            counts = f"화면 안 수색 지점 · 확인 {checked}/{total} · 미확인 {total - checked} · 가림 {blocked}"
            legend = "흰색 확인 완료 · 빨강 테두리 보임 · 어두운 빨강 X 가림 · 흐린 흰색 시야 밖"
            self.label("search_counts", counts, 13)
            self.label("search_legend", legend, 13)
            self.text("search_counts", 24, legend_y - 20)
            self.text("search_legend", 24, legend_y - 38)

    def _draw_weapon_card(self, simulation, now, weapon_image):
        player, weapon = simulation.player, simulation.player.weapon
        key = (id(simulation), player.active_weapon)
        if key != self._shown_weapon:
            self._shown_weapon = key
            self._weapon_card_until = now + 2.6
        if now >= self._weapon_card_until:
            return
        x, y, width, height = 16, 16, 256, 80
        pico.draw_rectangle(x, y, x + width, y + height, *PANEL, THEME.card_alpha, filled=True)
        pico.draw_rectangle(x, y, x + width, y + height, *PANEL_EDGE, THEME.edge_alpha)
        pico.draw_rectangle(x, y + height - 3, x + 45, y + height, *ACCENT, filled=True)
        pico.draw_rectangle(x + 12, y + 13, x + 98, y + 65,
                            *PANEL, THEME.track_alpha, filled=True)
        if weapon and weapon_image:
            weapon_image.draw(x + 55, y + 39, 74, 26)
        self.label("game_weapon", weapon.spec.name if weapon else "비무장", self.type["weapon"], TEXT)
        self.text("game_weapon", x + 111, y + 52)
        muzzle = weapon.part("muzzle") if weapon else None
        suppressed = muzzle is not None and muzzle.id == "suppressor"
        self.label("game_suppressed", "SUPPRESSED" if suppressed else "", self.type["detail"], ACCENT)
        if suppressed:
            self.text("game_suppressed", x + 111, y + 26)

    def _draw_player_health(self, player, view):
        x, y = view.pico(*player.xy)
        y += max(34, 0.58 * view.scale)
        if not -100 < x < view.width + 100 or y > view.height + 20:
            return
        width = 94
        self.label("game_health", f"{max(0, player.hp):.0f}/100", self.type["detail"], TEXT)
        self.text("game_health", x - self.labels["game_health"].width / 2, y + 20)
        pico.draw_rectangle(x - width / 2 - 2, y - 7, x + width / 2 + 2, y + 2,
                            *PANEL, THEME.card_alpha, filled=True)
        pico.draw_rectangle(x - width / 2, y - 5, x + width / 2, y,
                            *PANEL_EDGE, THEME.edge_alpha)
        if player.hp > 0:
            pico.draw_rectangle(x - width / 2, y - 5, x - width / 2 + width * min(1.0, player.hp / 100), y,
                                *ACCENT, filled=True)

    def _draw_ammo(self, simulation, aim_point, view):
        weapon = simulation.player.weapon
        if weapon is None or aim_point is None:
            return
        x = max(56, min(view.width - 56, aim_point[0]))
        y = max(68, min(view.height - 9, aim_point[1]))
        available = weapon.reserve + simulation.inventory.carried.get(f"ammo:{weapon.spec.ammo_type}", 0)
        self.label("game_ammo", f"{weapon.magazine + int(weapon.chambered):02d}/{available:02d}",
                   self.type["value"], TEXT)
        text_width = self.labels["game_ammo"].width
        self.text("game_ammo", x - text_width / 2, y - 30)
        self._draw_reload_ribbon(weapon, x - 42, y - 50, 84)

    @staticmethod
    def _draw_reload_ribbon(weapon, x, y, width):
        stages = weapon.spec.reload_stages
        gap = 3
        segment = (width - gap * (len(stages) - 1)) / len(stages)
        colors = (THEME.white, THEME.red_dim, THEME.red)
        for index in range(len(stages)):
            left = x + index * (segment + gap)
            pico.draw_rectangle(left, y, left + segment, y + 4,
                                *PANEL, THEME.track_alpha, filled=True)
            if weapon.reloading or weapon.stage:
                progress = (1.0 if index < weapon.stage else
                            weapon.stage_elapsed / weapon.reload_duration(index)
                            if weapon.reloading and index == weapon.stage else 0.0)
                if progress:
                    pico.draw_rectangle(left, y, left + segment * progress, y + 4, *colors[index], filled=True)

    def _draw_enemy_awareness(self, visible_enemies, view):
        for actor, brain in visible_enemies:
            x, y = view.pico(*actor.xy)
            y += max(30, 0.52 * view.scale)
            if y > view.height + 10:
                continue
            level = max(0.0, min(1.0, max(brain.memory.recognition, brain.memory.suspicion)))
            pico.draw_rectangle(x - 34, y, x + 34, y + 5,
                                *PANEL, THEME.track_alpha, filled=True)
            if level:
                pico.draw_rectangle(x - 33, y + 1, x - 33 + 66 * level, y + 4,
                                    *ACCENT, round(120 + 135 * level), filled=True)

    def _draw_gameplay_alerts(self, simulation, camera, *, map_open, paused):
        if simulation.alarms:
            territory = simulation.world.territory_at(simulation.player.xy)
            alarm = simulation.alarms.get(territory)
            if territory not in simulation.campaign.simulation.state.regions:
                alarm = max((entry for entry in simulation.alarms.values()
                             if entry.near(simulation.player.xy)), key=lambda entry: entry.until, default=None)
            if alarm is not None and alarm.until > simulation.time:
                self.label("region_alarm", f"지역 경보  ·  {math.ceil(alarm.until - simulation.time)}초", 16,
                           ACCENT)
                x = (camera.width - self.labels["region_alarm"].width) / 2
                self.text("region_alarm", x, camera.height - (52 if paused else 27))
        if map_open or simulation.player.hp > 0 or simulation.respawn_selecting:
            return
        if simulation.respawn_target is not None:
            message = "안전가옥 복귀 중"
        else:
            respawn_key = binding_name(self.bindings["respawn"])
            message = (f"{respawn_key}  ·  재시작" if simulation.equipment_range
                       else f"{respawn_key}  ·  안전가옥 선택")
        self.label("respawn_notice", message, 20, TEXT)
        self.text("respawn_notice", (camera.width - self.labels["respawn_notice"].width) / 2,
                  camera.height / 2 - 75)

    def _draw_threats(self, simulation, view, threat_candidates):
        if simulation is not self._threat_simulation:
            self._threat_simulation = simulation
            self._active_threats.clear()
            self._threat_exits.clear()
        player_x, player_y = view.pico(*simulation.player.xy)
        shapes, active = [], set()
        ring = 0

        def triangle(angle, radius, length, half_width, color):
            ux, uy = math.cos(angle), math.sin(angle)
            tx, ty = -uy, ux
            points = ((player_x + ux * (radius + length), player_y + uy * (radius + length)),
                      (player_x + ux * radius + tx * half_width, player_y + uy * radius + ty * half_width),
                      (player_x + ux * radius - tx * half_width, player_y + uy * radius - ty * half_width))
            shapes.append((tuple((px, view.height - py) for px, py in points), color))

        for actor, brain in threat_candidates:
            if actor.hp <= 0 or actor.position.floor != simulation.player.position.floor:
                continue
            memory = brain.memory
            enemy_x, enemy_y = view.pico(*actor.xy)
            angle = math.atan2(enemy_y - player_y, enemy_x - player_x)
            detected = memory.recognition >= 1.0 or simulation.time < brain.combat.combat_alert_until
            if detected:
                if actor.id in self._active_threats and actor.id not in self._threat_exits:
                    self._threat_exits[actor.id] = (simulation.time, angle, ring % 3)
                continue
            if not memory.exposed:
                continue
            active.add(actor.id)
            level = max(0.0, min(1.0, memory.recognition))
            radius = 47 + (ring % 3) * 11
            ring += 1
            color = ACCENT
            arc_alpha = round(220 * max(0.0, 1.0 - level / 0.55))
            if arc_alpha:
                for step in range(4):
                    start = angle - 0.43 + step * 0.215
                    end = start + 0.215
                    points = tuple((player_x + math.cos(direction) * distance,
                                    view.height - player_y - math.sin(direction) * distance)
                                   for direction, distance in ((start, radius), (end, radius),
                                                               (end, radius + 3), (start, radius + 3)))
                    shapes.append(((points[0], points[1], points[2], points[0], points[2], points[3]),
                                   (*color, arc_alpha)))
            triangle(angle, radius - 2, 8 + 42 * level, 3 + 13 * level,
                     (*color, round(230 * min(1.0, level / 0.4))))
        self._active_threats = active
        for actor_id, (started, angle, ring_index) in tuple(self._threat_exits.items()):
            elapsed = simulation.time - started
            if elapsed >= 1.0:
                del self._threat_exits[actor_id]
                continue
            opacity = (0.4 + 0.6 * abs(math.cos(elapsed * math.tau * 2)) if elapsed < 0.5
                       else 2 * (1 - elapsed))
            triangle(angle, 47 + ring_index * 11, 50, 16, (*ACCENT, round(235 * opacity)))
        if shapes:
            MapBatch(self.backend, shapes).draw(self.backend)

    def _draw_respawn_options(self, simulation, camera, projection):
        size = camera.width, camera.height
        left, top, map_width, map_height = projection.viewport

        def box(rect, color, *, filled=False):
            x0, y0, x1, y1 = rect
            pico.draw_rectangle(x0, camera.height - y1, x1, camera.height - y0, *color, filled=filled)

        selected = simulation.respawn_sites.get(simulation.respawn_selected_id)
        for site in simulation.respawn_sites.values():
            x, screen_y = projection.screen(*site["position"])
            if not left + 12 <= x <= left + map_width - 12 or not top + 12 <= screen_y <= top + map_height - 12:
                continue
            y = camera.height - screen_y
            active = site is selected
            radius = 13 if active else 10
            pico.draw_rectangle(x - radius, y - radius, x + radius, y + radius,
                                *PANEL_EDGE, THEME.edge_alpha)
            pico.draw_rectangle(x - radius + 2, y - radius + 2, x + radius - 2, y + radius - 2,
                                *PANEL, THEME.card_alpha, filled=True)
            pico.draw_rectangle(x - 5, y - 5, x + 5, y + 5,
                                *(ACCENT if active else TEXT), filled=True)
            if active:
                self.label("respawn_selected_name", site["name"], 16, ACCENT)
                label_width = self.labels["respawn_selected_name"].width + 16
                label_x = max(left + 4, min(left + map_width - label_width - 4, x + 18))
                label_y = max(top + 4, min(top + map_height - 30, screen_y - 33))
                box((label_x, label_y, label_x + label_width, label_y + 26),
                    (*PANEL, THEME.label_alpha), filled=True)
                box((label_x, label_y, label_x + label_width, label_y + 26),
                    (*PANEL_EDGE, THEME.edge_alpha))
                self.text("respawn_selected_name", label_x + 8, camera.height - label_y - 13)

        panel = panel_rect(size)
        box(panel, (*PANEL, THEME.card_alpha), filled=True)
        box(panel, (*PANEL_EDGE, THEME.edge_alpha))
        self.label("respawn_title", "복귀 위치", 24, ACCENT)
        self.text("respawn_title", panel[0] + 16, camera.height - 79)
        self.label("respawn_help", "마커 또는 목록 클릭", 13, TEXT)
        self.text("respawn_help", panel[0] + 16, camera.height - 107)
        for index, site in enumerate(visible_sites(simulation)):
            rect = row_rect(size, index)
            active = site is selected
            if active:
                box(rect, (*THEME.red_dim, THEME.hover_alpha), filled=True)
            box(rect, (*(ACCENT if active else PANEL_EDGE), THEME.edge_alpha))
            key = f"respawn_option/{index}"
            self.label(key, site["name"], 16, ACCENT if active else TEXT)
            self.text(key, rect[0] + 10, camera.height - rect[1] - 17)
        if simulation.respawn_pages > 1:
            previous, following = page_rects(size)
            for key, rect, glyph in (("respawn_previous", previous, "‹"),
                                     ("respawn_next", following, "›")):
                box(rect, (*PANEL_EDGE, THEME.edge_alpha))
                self.label(key, glyph, 20, TEXT)
                self.text(key, (rect[0] + rect[2] - self.labels[key].width) / 2,
                          camera.height - (rect[1] + rect[3]) / 2)
            self.label("respawn_page", f"{simulation.respawn_page + 1}/{simulation.respawn_pages}", 16)
            self.text("respawn_page", (previous[2] + following[0] - self.labels["respawn_page"].width) / 2,
                      camera.height - 337)
        respawn_key = binding_name(self.bindings["respawn"])
        self.label("respawn_confirm", f"선택 후 {respawn_key} 확정 · ESC 취소", 13,
                   ACCENT if selected else THEME.muted)
        self.text("respawn_confirm", panel[0] + 16, camera.height - 374)

    def render(self, simulation, camera, *, paused, debug, update_ms, draw_ms, map_open=False,
               cache_count=0, search_counts=None, seen_enemies=(), inventory_view=None, intel_index=None,
               minimap_open=True, world_view=None, aim_point=None, weapon_image=None,
               visible_enemies=(), threat_candidates=(), map_navigation=None, weapon_images=None):
        self._update_text_scale(camera)
        now = perf_counter()
        if map_open and now >= self.next_update:
            self.next_update = now + 0.2
            for facility in simulation.world.facilities:
                self.label("poi_name/" + facility["id"], facility["name"], 16, TEXT)
                campaign = simulation.campaign.simulation.state.facilities.get(facility["campaign_id"])
                status = (f"피해 {campaign.damage}%  보급 {campaign.inventory['supplies']}"
                          if campaign else "복귀 거점")
                self.label("poi_status/" + facility["id"], status, 13, THEME.muted)
        width, height = camera.width, camera.height
        if debug:
            self._update_debug(simulation, camera, now, update_ms, draw_ms, cache_count)
            self._draw_debug(simulation, camera, search_counts)
        else:
            if not map_open and inventory_view is None:
                if simulation.time < simulation.player.hurt_until:
                    self._draw_player_health(simulation.player, world_view or camera)
                self._draw_enemy_awareness(visible_enemies, world_view or camera)
                if simulation.player.hp > 0:
                    self._draw_threats(simulation, world_view or camera, threat_candidates)
                    self._draw_ammo(simulation, aim_point, world_view or camera)
                self._draw_weapon_card(simulation, now, weapon_image)
        if paused:
            pico.draw_rectangle(width - 43, height - 39, width - 38, height - 21,
                                *ACCENT, filled=True)
            pico.draw_rectangle(width - 33, height - 39, width - 28, height - 21,
                                *ACCENT, filled=True)
        if (debug or minimap_open) and not map_open and inventory_view is None:
            self._minimap(simulation, camera, debug, seen_enemies)
        if map_open:
            self._region_map(simulation, camera, debug, selecting=simulation.respawn_selecting and not paused,
                             intel_index=intel_index, navigation=map_navigation or MapNavigation())
        if not debug:
            self._draw_gameplay_alerts(simulation, camera, map_open=map_open, paused=paused)
        if inventory_view is not None:
            self._draw_inventory(camera, *inventory_view, weapon_images or {})

    def _draw_inventory(self, camera, layout, mouse, press, weapon_images):
        red = ACCENT
        height = camera.height

        def box(rect, color, *, filled=False):
            left, top, right, bottom = rect
            pico.draw_rectangle(left, height - bottom, right, height - top, *color, filled=filled)

        pico.draw_rectangle(0, 0, camera.width, height,
                            *PANEL, THEME.modal_alpha, filled=True)
        box(layout.panel, (*PANEL, THEME.panel_alpha), filled=True)
        box(layout.panel, (*PANEL_EDGE, THEME.edge_alpha))
        for section in (layout.bag_box, layout.floor_box):
            box(section, (*PANEL, THEME.panel_alpha), filled=True)
            box(section, (*PANEL_EDGE, THEME.edge_alpha))
        self.label("inventory_bag_title", f"BACKPACK {layout.weight:.1f}KG / {BAG_LIMIT:g}KG", 16, red)
        self.label("inventory_floor_title", "FLOOR", 16, red)
        self.text("inventory_bag_title", layout.bag_box[0] + 5, height - layout.bag_box[1] + 18)
        self.text("inventory_floor_title", layout.floor_box[0] + 5, height - layout.floor_box[1] + 18)
        for entry in layout.slots:
            if entry.zone == "attached":
                key = f"inventory_attached_{entry.key[0]}_{entry.key[1]}"
                self.label(key, entry.name, 13, red)
                self.text(key, entry.rect[0], height - entry.rect[1] - 10)
                continue
            highlighted = entry.active or entry.contains(*mouse)
            box(entry.rect, (*ACCENT, THEME.edge_alpha) if highlighted
                else (*PANEL_EDGE, THEME.edge_alpha))
            image = weapon_images.get(entry.weapon_id)
            if image is not None:
                left, top, right, bottom = entry.rect
                image.draw((left + right) / 2, height - (top + bottom) / 2,
                           (right - left) * 0.83, (bottom - top) * 0.75)
        for column, entries in (("bag", layout.bag), ("floor", layout.floor)):
            for index, entry in enumerate(entries):
                if entry.contains(*mouse) or press and press[0] == entry:
                    box(entry.rect, (*ACCENT, THEME.hover_alpha), filled=True)
                key = f"inventory_{column}_{index}"
                self.label(key, entry.name, 16, red)
                self.text(key, entry.rect[0] + 5, height - entry.rect[1] - 12)
                for part_index, detail in enumerate(entry.details):
                    detail_key = f"{key}_part_{part_index}"
                    self.label(detail_key, detail, 13, red)
                    self.text(detail_key, entry.rect[0] + 5, height - entry.rect[1] - 31 - part_index * 20)
        if press is not None and math.dist(mouse, press[1]) >= 6:
            source = press[0]
            if source is not None:
                image = weapon_images.get(source.weapon_id)
                if image is not None:
                    image.draw(mouse[0] + 34, height - mouse[1] - 14, 68, 28)
                elif source.name:
                    self.label("inventory_drag", source.name, 16, red)
                    self.text("inventory_drag", mouse[0] + 12, height - mouse[1] - 12)

    def _minimap(self, simulation, camera, debug=False, seen_enemies=()):
        width, height = 202, 146
        x0, y0 = ((camera.width - width - 16, 16) if debug else
                  (16, camera.height - height - 16))
        pico.draw_rectangle(x0, y0, x0 + width, y0 + height,
                            *PANEL, THEME.panel_alpha, filled=True)
        left, bottom, map_width, map_height = x0 + 4, y0 + 4, width - 8, height - 8
        pico.draw_rectangle(left, bottom, left + map_width, bottom + map_height,
                            *PANEL, THEME.panel_alpha, filled=True)
        player = simulation.player
        world = simulation.world
        scale = MINIMAP_SCALE
        center_x, center_y = player.xy
        bounds = Rect(center_x - map_width / (2 * scale), center_y - map_height / (2 * scale),
                      map_width / scale, map_height / scale)

        def point(x, y):
            return (left + map_width / 2 + (x - center_x) * scale,
                    bottom + map_height / 2 - (y - center_y) * scale)

        clip = self.backend.SDL_Rect(round(left), round(camera.height - bottom - map_height),
                                     round(map_width), round(map_height))
        self.backend.SDL_RenderSetClipRect(self.backend.renderer, ctypes.byref(clip))
        try:
            if world.map_background_path is not None:
                self._draw_map_background(world, *point(world.bounds.x, world.bounds.y),
                                          world.bounds.w * scale, world.bounds.h * scale)
            else:
                self._prepare_minimap_image(world)
                self._mini_image.draw(*point(world.bounds.x + world.bounds.w / 2,
                                             world.bounds.y + world.bounds.h / 2),
                                      world.bounds.w * scale, world.bounds.h * scale)
            for actor in simulation.guards:
                if actor.hp > 0 and (debug or actor.id in seen_enemies) and bounds.segment_entry(actor.xy, actor.xy) is not None:
                    x, y = point(*actor.xy)
                    pico.draw_rectangle(x - 2, y - 2, x + 2, y + 2, *ACCENT, filled=True)
            operations = simulation.campaign.simulation.state.operations
            for convoy in simulation.campaign.convoys(simulation.time, bounds=bounds):
                if convoy.vehicles <= 0 or bounds.segment_entry((convoy.x, convoy.y),
                                                                 (convoy.x, convoy.y)) is None:
                    continue
                x, y = point(convoy.x, convoy.y)
                color = vehicle_marker_color(operations[convoy.id].kind)
                pico.draw_rectangle(x - 4, y - 3, x + 4, y + 3, *PANEL_EDGE, filled=True)
                pico.draw_rectangle(x - 3, y - 2, x + 3, y + 2, *color, filled=True)
            x, y = point(*player.xy)
            pico.draw_rectangle(x - 6, y - 6, x + 6, y + 6, *PANEL_EDGE, filled=True)
            pico.draw_rectangle(x - 4, y - 4, x + 4, y + 4, *THEME.player_blue, filled=True)
        finally:
            self.backend.SDL_RenderSetClipRect(self.backend.renderer, None)
        pico.draw_rectangle(left, bottom, left + map_width, bottom + map_height,
                            *PANEL_EDGE, THEME.edge_alpha)

    def _draw_map_batch(self, name, world, layout, triangles):
        if self._map_world is not world:
            self._map_world = world
            self._map_batches.clear()
        entry = self._map_batches.get(name)
        if entry is None or entry[0] != layout:
            entry = self._map_batches[name] = layout, MapBatch(self.backend, triangles)
        entry[1].draw(self.backend)

    @staticmethod
    def _field_terrain(world):
        return ((layer.rect, layer.color) for layer in world.overview_landmarks)

    def draw_search_overlay(self, simulation, view):
        world = simulation.world
        sweeps = {}
        for incident in simulation.ai.incidents.values():
            sweep = incident.indoor
            if sweep is not None:
                previous = sweeps.get(sweep.area.id)
                if previous is None or sweep.evidence_at >= previous.evidence_at:
                    sweeps[sweep.area.id] = sweep
        checked_count = total = blocked_count = 0
        bounds = view.viewport()
        radius = max(5, min(10, round(view.scale * 0.18)))
        for area in world.search_areas:
            sweep = sweeps.get(area.id)
            if sweep is None and not world.data.get("training_facility"):
                continue
            samples = tuple((x, y) for x, y in area.points
                            if bounds.x <= x <= bounds.right and bounds.y <= y <= bounds.bottom)
            if not samples:
                continue
            checked, visible, blocked = (), (), ()
            if sweep is not None:
                checked = sweep.checked.intersection(samples)
                observers = []
                for actor in simulation.guards:
                    brain = simulation.ai.brains[actor.id]
                    active = brain.look.sample is not None and brain.look.sample[0] is sweep
                    observing = brain.search.task.sweep is sweep and actor.state in {"search", "regroup", "combat_guard"}
                    if actor.hp > 0 and (active or observing):
                        stationary = (brain.search.task.sample_stationary if brain.search.task.scan.sample_at == simulation.time
                                      else brain.search.task.scanning)
                        observers.append((actor.xy, actor.facing, stationary))
                visible, blocked = sweep.pending_view(observers, world.geometry, samples=samples)
            checked_count += len(checked)
            blocked_count += len(blocked)
            total += len(samples)
            for sample in samples:
                x, y = view.pico(*sample)
                box = (x - radius, y - radius, x + radius, y + radius)
                pico.draw_rectangle(x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2,
                                    *PANEL, THEME.label_alpha, filled=True)
                if sample in checked:
                    pico.draw_rectangle(*box, *SEARCH_CONFIRMED, filled=True)
                elif sample in blocked:
                    pico.draw_line(box[0], box[1], box[2], box[3], *SEARCH_OCCLUDED)
                    pico.draw_line(box[0], box[3], box[2], box[1], *SEARCH_OCCLUDED)
                else:
                    color = SEARCH_VISIBLE if sample in visible else SEARCH_OUTSIDE
                    pico.draw_rectangle(*box, *color)
        return (checked_count, total, blocked_count) if total else None

    def _region_map(self, simulation, camera, debug, *, selecting=False, intel_index=None, navigation):
        width, height = camera.width, camera.height
        world = simulation.world
        bounds = world.bounds
        projection = navigation.transform(bounds, (width, height),
                                          sidebar=RESPAWN_SIDEBAR if selecting else 0)
        viewport, scale = projection.viewport, projection.scale

        def point(x, y):
            screen_x, screen_y = projection.screen(x, y)
            return screen_x, height - screen_y

        pico.draw_rectangle(0, 0, width, height,
                            *PANEL, THEME.modal_alpha, filled=True)
        left, bottom, area_width, area_height = viewport
        pico.draw_rectangle(left, bottom, left + area_width, bottom + area_height,
                            *PANEL, THEME.panel_alpha, filled=True)
        pico.draw_rectangle(left, bottom, left + area_width, bottom + area_height,
                            *PANEL_EDGE, THEME.edge_alpha)
        clip = self.backend.SDL_Rect(left, height - bottom - area_height, area_width, area_height)
        self.backend.SDL_RenderSetClipRect(self.backend.renderer, ctypes.byref(clip))
        try:
            map_left, map_top = point(bounds.x, bounds.y)
            map_width, map_height = bounds.w * scale, bounds.h * scale
            if world.map_background_path is not None:
                self._draw_map_background(world, map_left, map_top, map_width, map_height)
            else:
                self._prepare_minimap_image(world)
                self._mini_image.draw(map_left + map_width / 2, map_top - map_height / 2,
                                      map_width, map_height)
            for key, rect in world.territories.items():
                a, b = point(rect.x, rect.y), point(rect.right, rect.bottom)
                pico.draw_rectangle(a[0], b[1], b[0], a[1], *PANEL_EDGE, THEME.edge_alpha)
            state = simulation.campaign.simulation.state
            for control in world.road_controls:
                road = state.roads[control["road"]]
                x, y = point(*control["position"])
                color = ACCENT if road.blocked else TEXT
                pico.draw_rectangle(x - 3, y - 3, x + 3, y + 3, *color, filled=True)
            if not selecting:
                placed_labels = []
                for facility in world.facilities:
                    x, y, w, h = facility["rect"]
                    px, py = point(x + w / 2, y + h / 2)
                    if not left - 160 <= px <= left + area_width + 160 or not bottom - 30 <= py <= bottom + area_height + 30:
                        continue
                    color = TEXT if facility["kind"] == "safehouse" else ACCENT
                    pico.draw_rectangle(px - 4, py - 4, px + 4, py + 4, *color, filled=True)
                    name = "poi_name/" + facility["id"]
                    status = "poi_status/" + facility["id"]
                    label_width = max(self.labels[name].width, self.labels[status].width) + 18
                    candidates = ((px + 10, py - 18), (px - label_width - 10, py - 18),
                                  (px - label_width / 2, py + 12),
                                  (px - label_width / 2, py - 54))
                    choices = []
                    for preference, (candidate_x, candidate_y) in enumerate(candidates):
                        label_x = max(left + 4, min(left + area_width - label_width - 4, candidate_x))
                        label_y = max(bottom + 4, min(bottom + area_height - 46, candidate_y))
                        overlap = sum(max(0, min(label_x + label_width, right) - max(label_x, other_x)) *
                                      max(0, min(label_y + 42, top) - max(label_y, other_y))
                                      for other_x, other_y, right, top in placed_labels)
                        choices.append((overlap, preference, label_x, label_y))
                    _, _, label_x, label_y = min(choices)
                    placed_labels.append((label_x, label_y, label_x + label_width, label_y + 42))
                    pico.draw_rectangle(label_x, label_y, label_x + label_width, label_y + 42,
                                        *PANEL, THEME.label_alpha, filled=True)
                    self.text(name, label_x + 9, label_y + 28)
                    self.text(status, label_x + 9, label_y + 10)
            actors = (simulation.player, *simulation.guards) if debug else (simulation.player,)
            for actor in actors:
                if actor.hp <= 0:
                    continue
                x, y = point(*actor.xy)
                if actor.team == "player":
                    pico.draw_rectangle(x - 7, y - 7, x + 7, y + 7, *PANEL_EDGE, filled=True)
                    pico.draw_rectangle(x - 5, y - 5, x + 5, y + 5, *THEME.player_blue, filled=True)
                else:
                    pico.draw_rectangle(x - 4, y - 4, x + 4, y + 4, *ACCENT, filled=True)
            for convoy in simulation.campaign.convoys(simulation.time, bounds=bounds):
                if convoy.vehicles <= 0 or bounds.segment_entry((convoy.x, convoy.y),
                                                                 (convoy.x, convoy.y)) is None:
                    continue
                x, y = point(convoy.x, convoy.y)
                operation = state.operations[convoy.id]
                color = vehicle_marker_color(operation.kind)
                pico.draw_rectangle(x - 5, y - 4, x + 5, y + 4, *PANEL_EDGE, filled=True)
                pico.draw_rectangle(x - 4, y - 3, x + 4, y + 3, *color, filled=True)
        finally:
            self.backend.SDL_RenderSetClipRect(self.backend.renderer, None)
        if selecting:
            self._draw_respawn_options(simulation, camera, projection)
        elif intel_index is not None and world.data.get("generation"):
            self._draw_operation_notes(simulation, camera, intel_index, point)

    def _draw_operation_notes(self, simulation, camera, index, point):
        x, top = camera.width - 440, camera.height - 180
        self.label("intel_title", "정보 노트  ·  ↑↓ 선택  /  ESC 닫기", 16, ACCENT)
        self.text("intel_title", x, top)
        notes = sorted(simulation.operation_notes.values(),
                       key=lambda note: (-max(note.recorded_at or 0, note.observed_at or 0), note.operation_id))
        if not notes:
            intel_key = binding_name(self.bindings["collect_intel"])
            self.label("intel_empty", f"창고·HQ 단말 또는 보이는 수송대에서 {intel_key}", 13)
            self.text("intel_empty", x, top - 38)
            return
        state = simulation.campaign.simulation.state
        index = min(index, len(notes) - 1)
        first = min(max(0, index - 3), max(0, len(notes) - 5))
        for offset, note in enumerate(notes[first:first + 5], first):
            op = state.operations[note.operation_id]
            kind = OPERATION_NAMES[op.kind] if note.source else "미확인 차량"
            y = top - 34 - (offset - first) * 26
            key = f"intel_row/{offset - first}"
            if offset == index:
                pico.draw_rectangle(x - 8, y - 12, x + 344, y + 10,
                                    *ACCENT, THEME.hover_alpha, filled=True)
            self.label(key, f"{offset + 1}. {kind}", 16)
            self.text(key, x, y)

        note = notes[index]
        op = state.operations[note.operation_id]
        details = []
        if note.source:
            names = {site["campaign_id"]: site["name"] for site in simulation.world.facilities}
            names.update({state.source.id: "외부 보급원", "external_front": "외부 전선"})
            details.extend((f"출발지  {names[op.origin_id]}", f"목적지  {names[op.destination_id]}"))
            if note.departure_at is not None:
                details.append(f"{'출발 확인' if note.departed else '출발 예정'}  {world_clock(note.departure_at)}")
            if note.quantity is not None:
                detail = f"야간 장비 {note.quantity}세트" if op.kind == "equipment" else f"병력 {note.quantity}명"
                details.append(detail)
            details.append(f"{names[note.source]} 기록  {world_clock(note.recorded_at)}")
        if note.observed_at is not None:
            details.append(f"마지막 현장 관측  {world_clock(note.observed_at)}")
        for index, detail in enumerate(details):
            key = f"intel_detail/{index}"
            self.label(key, detail, 13)
            self.text(key, x, top - 173 - 22 * index)

        bounds = simulation.world.bounds
        route = [(state.nodes[key].position.x, state.nodes[key].position.y) for key in note.route]
        for start, end in zip(route, route[1:]):
            if all(bounds.segment_entry(node, node) is not None for node in (start, end)):
                pico.draw_line(*point(*start), *point(*end), *ACCENT)
        if note.observed_position is not None and bounds.segment_entry(note.observed_position, note.observed_position) is not None:
            x, y = point(*note.observed_position)
            pico.draw_rectangle(x - 4, y - 4, x + 4, y + 4, *ACCENT)

    def close(self):
        self._map_batches.clear()
        self._map_world = None
        self._map_image = None
        self._mini_image = None
        self._mini_world = None
        self.labels.clear()
        for font in chain(self.fonts.values(), self.scaled_fonts.values()):
            self.backend.TTF_CloseFont(font.font)
        self.fonts.clear()
        self.scaled_fonts.clear()
