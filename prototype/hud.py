"""캐시된 글자·단계 진행 막대·상태 카드. 게임 상태를 변경하지 않는다."""

import ctypes
from dataclasses import dataclass
import math
import os
from itertools import chain
from pathlib import Path
from time import perf_counter

import pico2d as pico

from campaign.clock import world_clock
from .geometry import Rect
from .inventory import BAG_LIMIT, PICKUP_REACH
from .intelligence import OPERATION_NAMES
from .map_batch import MapBatch, map_colliders, map_rectangles, map_roads
from .weapons import STAGE_NAMES
from .interactions import nearby_facility, nearby_road_control


AI_STATES = {"idle": "대기", "patrol": "순찰", "return": "복귀", "boarding": "집결", "regroup": "수색 후 집결",
             "alert": "조사", "search": "전투 수색", "combat": "교전",
             "combat_guard": "전투 경계", "retreat": "엄폐·후퇴", "radio": "무전 중"}
AI_ACTIONS = {"cover": "엄폐", "retreat": "후퇴", "pin": "제압됨", "reload": "재장전",
              "fire": "사격", "advance": "접근", "flank": "측면 이동",
              "peek": "피킹", "peek_return": "엄폐 복귀", "reposition": "사선 확보", "wait": "사격 대기"}
SOUND_RESPONSES = {"new": "총성 감지", "renewed": "총성 재조사", "same_area": "총성 감지 · 경로 유지",
                   "out_of_range": "총성 감지 범위 밖", "no_estimate": "총성 감지 · 위치 추정 실패",
                   "checked": "총성 조사 종료", "unreachable": "소리 위치에 접근할 수 없음"}
SEARCH_CONFIRMED = (96, 236, 186)
SEARCH_VISIBLE = (255, 108, 98)
SEARCH_OCCLUDED = (255, 215, 83)
SEARCH_OUTSIDE = (203, 210, 206)


@dataclass
class Label:
    style: tuple
    width: int
    height: int
    raster_size: int = 0
    image: pico.Image | None = None


class HUD:
    def __init__(self, backend):
        self.backend = backend
        self.font_path = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf")
        self.fonts = {size: pico.load_font(self.font_path, size) for size in (13, 16, 24)}
        self.scaled_fonts = {}
        self.text_scale = 1.0
        self.labels = {}
        self.next_update = 0.0
        self.next_debug_update = 0.0
        self.message_simulation = None
        self.last_message = ""
        self.message_until = 0.0
        self.ai_rows = 0
        self._map_world = None
        self._map_batches = {}

    def label(self, key, text, size=16, color=(223, 230, 218)):
        if not text:
            self.labels.pop(key, None)
            return
        style = text, size, color
        cached = self.labels.get(key)
        if cached is not None and cached.style == style:
            return
        width, height = ctypes.c_int(), ctypes.c_int()
        if self.backend.TTF_SizeUTF8(self.fonts[size].font, text.encode("utf-8"),
                                     ctypes.byref(width), ctypes.byref(height)) < 0:
            raise RuntimeError("HUD 글자 크기 측정 실패")
        self.labels[key] = Label(style, width.value, height.value)

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

    def text(self, key, x, y):
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
            label.image, label.raster_size = pico.Image(texture), raster_size
        destination = backend.SDL_FRect(x, pico.get_canvas_height() - y - label.height / 2,
                                        label.width, label.height)
        backend.SDL_RenderCopyF(backend.renderer, label.image.texture, None, destination)

    @staticmethod
    def panel(x, y, w, h):
        pico.draw_rectangle(x, y, x + w, y + h, 17, 28, 31, 238, filled=True)
        pico.draw_rectangle(x, y, x + w, y + h, 67, 86, 82, 255)

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
            self.label(f"ai_id/{index}", f"{actor.id}   {state}", 13, (118, 215, 191))
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
            self.label(f"ai_sound/{index}", sound or "최근 총성 반응 없음", 13, (240, 187, 104))
        self.label("ai_hint", simulation.ai_hint, 13)

    def _update_debug(self, simulation, camera, now, update_ms, draw_ms, cache_count):
        if now < self.next_debug_update:
            return
        self.next_debug_update = now + 0.2
        player, weapon = simulation.player, simulation.player.weapon
        self.label("brand", "REVENANT / FIELD PROTOTYPE", 16, (118, 215, 191))
        self.label("clock", f"{world_clock(simulation.lighting_time)}  |  {camera.magnification:.0%}", 13)
        if simulation.world.territories:
            territory = simulation.world.territory_at(player.xy)
            name = simulation.world.data["territories"].get(territory, {}).get("name", "지역 사이 연결로")
            self.label("campaign_location", name, 16, (118, 215, 191))
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
            self.label("reload", f"{phase} — {status} · 남은 {weapon.remaining_reload:.2f}s", 13, (241, 196, 112))
        else:
            self.label("reload", "완료 단계 보존 · 취소한 단계는 처음부터 재개", 13, (136, 159, 152))

    def _draw_debug(self, simulation, camera, search_counts):
        player, weapon = simulation.player, simulation.player.weapon
        width, height = camera.width, camera.height
        self.panel(12, height - 67, width - 24, 55)
        self.text("brand", 26, height - 30)
        self.text("clock", 26, height - 50)
        if simulation.world.territories:
            self.text("campaign_location", 304, height - 41)
        self.text("vitals", width - 245, height - 29)
        pico.draw_rectangle(width - 245, height - 53, width - 30, height - 47, 45, 57, 55, filled=True)
        pico.draw_rectangle(width - 245, height - 53, width - 245 + 215 * player.hp / 100,
                             height - 47, 99, 186, 154, filled=True)
        self.panel(12, 12, 410, 142)
        self.text("weapon", 26, 129)
        self.text("ammo", 240, 129)
        self.text("mode", 26, 100)
        for index, key in enumerate(STAGE_NAMES):
            x = 26 + index * 128
            self.text(key, x, 77)
            color, progress = (67, 84, 79), 0
            if weapon and index < weapon.stage:
                color, progress = (96, 182, 151), 1
            elif weapon and index == weapon.stage and (weapon.reloading or weapon.stage):
                color = (227, 184, 104)
                progress = weapon.stage_elapsed / weapon.reload_duration(index)
            pico.draw_rectangle(x, 57, x + 116, 62, 43, 56, 52, filled=True)
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
        self.label("ai_legend", "파랑  맵 노드 · 청록  현재 경로 · 주황 +  소리 추정", 13)
        legend_y = height - (156 if "ai_hint" in self.labels else 136)
        self.text("ai_legend", 24, legend_y)
        if search_counts is not None:
            checked, total, blocked = search_counts
            counts = f"화면 안 수색 지점 · 확인 {checked}/{total} · 미확인 {total - checked} · 가림 {blocked}"
            legend = "청록 확인 완료 · 빨강 테두리 보임 · 노랑 X 가림 · 회색 테두리 시야 밖"
            self.label("search_counts", counts, 13)
            self.label("search_legend", legend, 13)
            self.text("search_counts", 24, legend_y - 20)
            self.text("search_legend", 24, legend_y - 38)

    def _draw_gameplay(self, simulation):
        player, weapon = simulation.player, simulation.player.weapon
        day, time = world_clock(simulation.lighting_time).split()
        self.label("game_clock", time if simulation.equipment_range else f"{time}  ·  {int(day[1:])}일차", 16)
        self.label("game_vitals", f"HP {player.hp:.0f}   ·   방어 {player.armor}", 16)

        left, base, width = 12, 12, 236
        show_reload = weapon is not None and (weapon.reloading or weapon.stage > 0)
        bottom = base + 32 if show_reload else base
        x = left + 12
        inventory = simulation.inventory
        throwable = inventory.active_throwable
        top = bottom + (156 if throwable else 134)
        pico.draw_rectangle(left, base, left + width, top, 15, 24, 27, 218, filled=True)
        if throwable:
            self.label("game_throwable", f"G  {inventory.item(throwable)[0]} ×{inventory.carried[throwable]}", 13)
            self.text("game_throwable", x, bottom + 138)
        lamp = {"off": "꺼짐", "white": "백색", "infrared": "IR"}[player.lamp]
        if player.lamp == "infrared" and not simulation.sky.infrared:
            lamp = "IR 주간"
        optics = f"N 야투 {'켜짐' if player.night_vision else '꺼짐'} · L {lamp} · K 레이저 {'켜짐' if player.ir_laser else '꺼짐'}"
        self.label("optics", optics, 13)
        self.text("optics", x, bottom + 117)
        self.text("game_clock", x, bottom + 94)
        self.text("game_vitals", x, bottom + 68)
        pico.draw_rectangle(x, bottom + 52, x + width - 24, bottom + 55, 57, 68, 64, filled=True)
        if player.hp > 0:
            health = min(1, player.hp / 100)
            color = (227, 122, 106) if player.hp <= 25 else (118, 215, 191)
            pico.draw_rectangle(x, bottom + 52, x + (width - 24) * health, bottom + 55, *color, filled=True)

        self.label("game_weapon", weapon.spec.name if weapon else "비무장", 16)
        self.text("game_weapon", x, bottom + 33)
        if weapon:
            self.label("game_mode", "AUTO" if weapon.automatic else "SEMI", 13, (168, 187, 180))
            self.label("game_ammo", f"{weapon.magazine:02d}+{int(weapon.chambered)} / {weapon.reserve:03d}", 24)
            self.text("game_mode", x, bottom + 15)
            self.text("game_ammo", x + 72, bottom + 25)
        if show_reload:
            self._draw_gameplay_reload(weapon, x, base, width - 24)

    def _draw_gameplay_reload(self, weapon, x, y, width):
        stages = weapon.spec.reload_stages
        phase = STAGE_NAMES[stages[weapon.stage].id]
        status = "재장전" if weapon.reloading else "R 재개"
        active_color = (241, 196, 112)
        self.label("game_reload", f"{phase}  ·  {status}", 13, active_color)
        self.text("game_reload", x, y + 21)
        gap = 4
        segment = (width - gap * (len(stages) - 1)) / len(stages)
        for index, stage in enumerate(stages):
            left = x + index * (segment + gap)
            track = (57, 68, 64)
            if index == weapon.stage:
                track = (108, 88, 63)
            elif index < weapon.stage:
                track = (118, 215, 191)
            pico.draw_rectangle(left, y + 8, left + segment, y + 11, *track, filled=True)
            if index == weapon.stage and weapon.stage_elapsed > 0:
                progress = weapon.stage_elapsed / weapon.reload_duration(index)
                pico.draw_rectangle(left, y + 8, left + segment * progress, y + 11, *active_color, filled=True)

    def _track_message(self, simulation, now):
        if simulation is not self.message_simulation:
            self.message_simulation = simulation
            self.last_message = simulation.message
            self.message_until = now + 3.0
        elif simulation.message != self.last_message:
            self.last_message = simulation.message
            self.message_until = now + 3.0

    def _draw_gameplay_notices(self, simulation, center, now, *, paused, map_open):
        prompt = not paused and not map_open and simulation.player.hp > 0 and "prompt" in self.labels
        if prompt:
            self._draw_notice("prompt", center, 29)

        if simulation.respawn_target is not None:
            message = "안전가옥으로 복귀 중"
        elif simulation.respawn_selecting:
            return
        elif simulation.player.hp <= 0:
            message = ("전투 불능  ·  Enter로 시험장 초기화" if simulation.equipment_range
                       else "전투 불능  ·  Enter로 안전가옥 복귀")
        elif now < self.message_until:
            message = self.last_message
        else:
            return
        self.label("game_notice", message, 16)
        if message:
            self._draw_notice("game_notice", center, 69 if prompt else 29)

    def _draw_notice(self, key, center, y):
        half_width = self.labels[key].width / 2
        pico.draw_rectangle(center - half_width - 12, y - 15, center + half_width + 12, y + 15,
                            15, 24, 27, 218, filled=True)
        self.text(key, center - half_width, y)

    def _draw_respawn_options(self, simulation, camera, point):
        for number, site in enumerate(simulation.respawn_sites.values(), 1):
            x, y = point(*site["position"])
            marker = f"respawn_marker/{number}"
            self.label(marker, str(number), 16, (17, 28, 31))
            half_width = self.labels[marker].width / 2
            pico.draw_rectangle(x - 10, y - 10, x + 10, y + 10, 118, 215, 191, filled=True)
            self.text(marker, x - half_width, y)
            stored = simulation.safehouse_stash.get(site["id"])
            loadout = f"  ·  보관 {stored.spec.name}" if stored else ""
            key = f"respawn_option/{number}"
            self.label(key, f"[{number}] {site['name']}{loadout}", 16)
            self.text(key, camera.width - 350, camera.height - 168 - number * 32)

    def render(self, simulation, camera, *, paused, debug, update_ms, draw_ms, map_open=False,
               cache_count=0, search_counts=None, seen_enemies=(), inventory_view=None, intel_index=None):
        self._update_text_scale(camera)
        now = perf_counter()
        self._track_message(simulation, now)
        if now >= self.next_update:
            self.next_update = now + 0.2
            facility_here = nearby_facility(simulation.world, simulation.player.xy)
            prompt = f"[E] {facility_here['name']} 사용" if facility_here else ""
            if simulation.equipment_range and math.dist(simulation.player.xy, simulation.world.crate) <= PICKUP_REACH:
                prompt = "[Tab] 시험장 보급함"
            if facility_here and facility_here["kind"] == "safehouse":
                stored = simulation.safehouse_stash.get(facility_here["id"])
                action = "무기 보관" if simulation.player.weapon else "보관함 비어 있음"
                if stored:
                    action = f"{stored.spec.name} {'교체' if simulation.player.weapon else '회수'}"
                prompt = f"[Shift+E] {action}"
                if simulation.kit_available and not simulation.player.loadout:
                    prompt = f"[E] 예비 키트 · {prompt}"
            elif facility_here and facility_here["kind"] == "warehouse":
                damage = simulation.campaign.simulation.state.facilities[facility_here["campaign_id"]].damage
                prompt = (f"[E] 물자 확보 · [Shift+E] 파괴 공작 ({damage}%)" if damage < 100
                          else "물류창고 무력화")
            if (simulation.presence and facility_here and facility_here["kind"] in {"warehouse", "hq"}
                    and simulation.campaign.simulation.state.facilities[facility_here["campaign_id"]].damage < 100):
                prompt += " · [Shift+I] 작전 기록"
            control = nearby_road_control(simulation.world, simulation.player.xy)
            if control:
                road = simulation.campaign.simulation.state.roads[control["road"]]
                prompt = f"[E] {'수송로 개방' if road.blocked else '수송로 차단'}"
            self.label("prompt", prompt, 16, (241, 211, 135))
            map_title = ("장비 시험장" if simulation.equipment_range else
                         simulation.stack_title or simulation.world.data.get("name", "지역 지도"))
            map_help = ("P 시작·재개 · F2 다시 보기 · F3 다음 배치 · M 닫기" if simulation.stack_drill
                        else "M 닫기 · 지도에서도 세계 시간은 진행됨 · F1 개발 정보")
            if simulation.presence:
                map_help = "M 닫기 · I 정보 노트 · 지도에서도 세계 시간 진행"
            self.label("map_title", map_title, 24, (118, 215, 191))
            self.label("map_help", map_help, 13)
            self.label("map_size", f"{simulation.world.bounds.w:.0f} × {simulation.world.bounds.h:.0f} m", 13)
            for facility in simulation.world.facilities:
                self.label("site/" + facility["id"], facility["name"], 13)
                campaign = simulation.campaign.simulation.state.facilities.get(facility["campaign_id"])
                status = f"피해 {campaign.damage}%" if campaign else "복귀 거점"
                if campaign and (simulation.world.data.get("generation") or
                                 simulation.world.territories and facility["kind"] == "warehouse"):
                    status += f" · 보급 {campaign.inventory['supplies']}"
                self.label("site_info/" + facility["id"], f"{facility['name']} · {status}", 13)
        self.label("paused", "PAUSED — P 키로 재개" if paused else "", 24)
        width, height = camera.width, camera.height
        if debug:
            self._update_debug(simulation, camera, now, update_ms, draw_ms, cache_count)
            self._draw_debug(simulation, camera, search_counts)
        else:
            self._draw_gameplay(simulation)
            self._draw_gameplay_notices(simulation, width / 2, now, paused=paused, map_open=map_open)
        if paused and (simulation.ai_scenario or simulation.world.data.get("training_facility")):
            self.panel(width / 2 - 106, height - 63, 212, 45)
            self.text("paused", width / 2 - 92, height - 40)
        elif paused:
            self.panel(width / 2 - 190, height / 2 - 27, 380, 54)
            self.text("paused", width / 2 - 160, height / 2)
        if debug and "prompt" in self.labels:
            self.panel(width / 2 - 150, 168, 300, 36)
            self.text("prompt", width / 2 - 132, 186)
        if debug or not map_open:
            self._minimap(simulation, camera, debug, seen_enemies)
        if map_open:
            self._region_map(simulation, camera, debug, selecting=simulation.respawn_selecting and not paused,
                             intel_index=intel_index)
        territory = simulation.world.territory_at(simulation.player.xy)
        alarm = simulation.alarms.get(territory)
        if territory not in simulation.campaign.simulation.state.regions:
            alarm = max((entry for entry in simulation.alarms.values()
                         if entry.near(simulation.player.xy)), key=lambda entry: entry.until, default=None)
        if alarm is not None and alarm.until > simulation.time:
            self.label("region_alarm", f"지역 경보  ·  {math.ceil(alarm.until - simulation.time)}초", 16, (255, 133, 107))
            self._draw_notice("region_alarm", width / 2, height - (80 if paused else 28))
        if inventory_view is not None:
            self._draw_inventory(camera, simulation.inventory.bag_weight, *inventory_view)

    def _draw_inventory(self, camera, weight, rows, selected):
        left, width, top = camera.width / 2 - 226, 452, camera.height - 103
        visible = 11
        start = min(max(0, selected - visible + 1), max(0, len(rows) - visible))
        self.panel(left, top - 372, width, 372)
        self.label("inventory_title", f"장비  ·  가방 {weight:.1f}/{BAG_LIMIT:g} kg", 16)
        self.text("inventory_title", left + 18, top - 22)
        for line, (text, _) in enumerate(rows[start:start + visible]):
            y = top - 54 - line * 26
            if start + line == selected:
                pico.draw_rectangle(left + 8, y - 11, left + width - 8, y + 11, 49, 76, 72, filled=True)
            key = f"inventory_row_{line}"
            self.label(key, text, 13)
            self.text(key, left + 18, y)
        self.label("inventory_keys", "↑↓ / 휠 선택    Enter 사용    Tab 닫기", 13)
        self.text("inventory_keys", left + 18, top - 351)

    def _minimap(self, simulation, camera, debug=False, seen_enemies=()):
        width, height = 180, 126
        x0, y0 = (camera.width - 196, 12) if debug else (12, camera.height - height - 12)
        self.panel(x0, y0, width, height)
        bounds = simulation.world.bounds
        scale = min((width - 16) / bounds.w, (height - 16) / bounds.h)

        def point(x, y):
            return x0 + 8 + (x - bounds.x) * scale, y0 + height - 8 - (y - bounds.y) * scale

        world = simulation.world
        canvas_height = pico.get_canvas_height()
        layout = x0, y0, scale, canvas_height
        if world.facilities:
            facilities = ((Rect(*facility["rect"]), (116, 133, 108)) for facility in world.facilities)
            terrain = chain(self._field_terrain(world), facilities)
            self._draw_map_batch("mini terrain", world, layout, map_rectangles(terrain, point, canvas_height))
        else:
            terrain = ((collider.rect, (93, 111, 94)) for collider in world.geometry.objects.values())
            self._draw_map_batch("mini obstacles", world, (*layout, world.revision),
                                 map_rectangles(terrain, point, canvas_height))
        self._draw_map_batch("mini roads", world, layout, map_roads(world, point, scale, canvas_height, ()))
        for actor in [*simulation.guards, simulation.player]:
            if actor.hp > 0 and (actor.team == "player" or debug or actor.id in seen_enemies):
                x, y = point(*actor.xy)
                color = (108, 213, 197) if actor.team == "player" else (218, 131, 100)
                pico.draw_rectangle(x - 2, y - 2, x + 2, y + 2, *color, filled=True)
        if world.campaign_map and (debug or not simulation.presence):
            for convoy in simulation.campaign.convoys(simulation.time):
                if not bounds.x <= convoy.x <= bounds.right or not bounds.y <= convoy.y <= bounds.bottom:
                    continue
                x, y = point(convoy.x, convoy.y)
                pico.draw_rectangle(x - 2, y - 1, x + 2, y + 1, 221, 186, 100, filled=True)

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
        return ((layer.rect, layer.color) for layer in world.map_layers if layer.role in {"water", "roof"})

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
                                    14, 22, 24, 190, filled=True)
                if sample in checked:
                    pico.draw_rectangle(*box, *SEARCH_CONFIRMED, filled=True)
                elif sample in blocked:
                    pico.draw_line(box[0], box[1], box[2], box[3], *SEARCH_OCCLUDED)
                    pico.draw_line(box[0], box[3], box[2], box[1], *SEARCH_OCCLUDED)
                else:
                    color = SEARCH_VISIBLE if sample in visible else SEARCH_OUTSIDE
                    pico.draw_rectangle(*box, *color)
        return (checked_count, total, blocked_count) if total else None

    def _region_map(self, simulation, camera, debug, *, selecting=False, intel_index=None):
        self.panel(60, 166, camera.width - 120, camera.height - 246)
        if selecting:
            self.label("respawn_title", "복귀할 안전가옥 선택", 24, (118, 215, 191))
            self.label("respawn_help", "숫자 키로 선택 · C 닫기", 13)
        self.text("respawn_title" if selecting else "map_title", 84, camera.height - 111)
        self.text("respawn_help" if selecting else "map_help", 84, camera.height - 140)
        world = simulation.world
        generated = bool(world.data.get("generation"))
        notes_open = intel_index is not None and generated and not selecting
        columns = 2 if not selecting and len(world.facilities) > 10 else 1
        plan_width = camera.width - (560 if notes_open else 168 if not world.facilities else 750 if columns == 2 else 480)
        scale = min(plan_width / world.bounds.w, (camera.height - 370) / world.bounds.h)
        left = 84 + (plan_width - world.bounds.w * scale) / 2
        top = camera.height - 176

        def point(x, y):
            return left + (x - world.bounds.x) * scale, top - (y - world.bounds.y) * scale

        right, bottom = point(world.bounds.right, world.bounds.bottom)
        pico.draw_rectangle(left, bottom, right, top, 34, 49, 43, filled=True)
        canvas_height = pico.get_canvas_height()
        layout = left, top, scale, canvas_height
        self._draw_map_batch("full field", world, layout, map_rectangles(self._field_terrain(world), point, canvas_height))
        state = simulation.campaign.simulation.state
        controlled_roads = {control["road"] for control in world.road_controls}
        for control in world.road_controls:
            x, y = point(*control["position"])
            pico.draw_rectangle(x - 3, y - 3, x + 3, y + 3, 241, 211, 135, filled=True)
        if not world.facilities or generated:
            ground = ((layer.rect, layer.color) for layer in world.map_layers
                      if not layer.role.startswith("road") and layer.role != "pavement")
            self._draw_map_batch("full terrain", world, layout, map_rectangles(ground, point, canvas_height))
            self._draw_map_batch("full obstacles", world, (*layout, world.revision),
                                 map_colliders(world.geometry.objects.values(), point, canvas_height))
        blocked = frozenset(key for key in controlled_roads if state.roads[key].blocked)
        self._draw_map_batch("full roads", world, (*layout, blocked), map_roads(world, point, scale, canvas_height, blocked))
        for key, rect in world.territories.items():
            a, b = point(rect.x, rect.y), point(rect.right, rect.bottom)
            pico.draw_rectangle(a[0], b[1], b[0], a[1], 90, 122, 110)
            if len(world.territories) > 2:
                self.label('territory/' + key, world.data['territories'][key]['name'], 13, (118, 215, 191))
                self.text('territory/' + key, a[0] + 3, a[1] + 9)
        for site in world.data.get("planned_facilities", ()):
            x, y, w, h = site["rect"]
            if not selecting and len(world.territories) <= 2:
                self.label("planned/" + site["id"], site["name"] + (" · 예약" if site["placeholder"] else ""), 13, (226, 221, 176))
                self.text("planned/" + site["id"], *point(x, y + h))
            gate, approach = site["connector"]["position"], site["connector"]["approach"]
            pico.draw_line(*point(*gate), *point(*approach), 112, 230, 205)
        rows = math.ceil(len(world.facilities) / columns)
        row_step = min(34, (camera.height - 476) / max(1, rows - 1))
        for index, facility in enumerate(world.facilities):
            x, y, w, h = facility["rect"]
            a, b = point(x, y), point(x + w, y + h)
            color = (94, 175, 150) if facility["kind"] == "safehouse" else (162, 166, 128)
            if not generated:
                pico.draw_rectangle(a[0], b[1], b[0], a[1], *color, filled=True)
                self.text("site/" + facility["id"], a[0], b[1] - 13)
            if not selecting and not notes_open:
                self.text("site_info/" + facility["id"], camera.width - (650 if columns == 2 else 350) + index // rows * 315,
                          camera.height - 200 - index % rows * row_step)
        for actor in [*simulation.guards, simulation.player]:
            if actor.hp <= 0 or (actor.team != "player" and not debug):
                continue
            x, y = point(*actor.xy)
            color = (112, 230, 205) if actor.team == "player" else (226, 131, 92)
            pico.draw_rectangle(x - 3, y - 3, x + 3, y + 3, *color, filled=True)
            if debug and actor.team != "player":
                pico.draw_line(x, y, x + 10 * math.cos(actor.facing), y - 10 * math.sin(actor.facing), 246, 246, 236)
        if debug or not generated:
            for convoy in simulation.campaign.convoys(simulation.time):
                if not world.bounds.x <= convoy.x <= world.bounds.right or not world.bounds.y <= convoy.y <= world.bounds.bottom:
                    continue
                x, y = point(convoy.x, convoy.y)
                pico.draw_rectangle(x - 3, y - 2, x + 3, y + 2, 221, 186, 100, filled=True)
        if notes_open:
            self._draw_operation_notes(simulation, camera, intel_index, point)
        else:
            self.text("map_size", camera.width - (230 if not world.facilities else 350), 245)
        if selecting:
            self._draw_respawn_options(simulation, camera, point)

    def _draw_operation_notes(self, simulation, camera, index, point):
        x, top = camera.width - 440, camera.height - 180
        self.label("intel_title", "정보 노트  ·  ↑↓ 선택  /  I 닫기", 16, (118, 215, 191))
        self.text("intel_title", x, top)
        notes = sorted(simulation.operation_notes.values(),
                       key=lambda note: (-max(note.recorded_at or 0, note.observed_at or 0), note.operation_id))
        if not notes:
            self.label("intel_empty", "창고·HQ 단말 또는 보이는 수송대에서 Shift+I", 13)
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
                pico.draw_rectangle(x - 8, y - 12, x + 344, y + 10, 55, 81, 73, filled=True)
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
                pico.draw_line(*point(*start), *point(*end), 250, 201, 105)
        if note.observed_position is not None and bounds.segment_entry(note.observed_position, note.observed_position) is not None:
            x, y = point(*note.observed_position)
            pico.draw_rectangle(x - 4, y - 4, x + 4, y + 4, 118, 215, 191)

    def close(self):
        self._map_batches.clear()
        self._map_world = None
        self.labels.clear()
        for font in chain(self.fonts.values(), self.scaled_fonts.values()):
            self.backend.TTF_CloseFont(font.font)
        self.fonts.clear()
        self.scaled_fonts.clear()
