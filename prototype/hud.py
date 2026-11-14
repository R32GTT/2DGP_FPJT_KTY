"""캐시된 글자·단계 진행 막대·상태 카드. 게임 상태를 변경하지 않는다."""

import os
import math
from pathlib import Path
from time import perf_counter

import pico2d as pico

from campaign.viewmodel import world_clock
from .weapons import STAGE_NAMES
from .interactions import nearby_facility


class HUD:
    def __init__(self, backend):
        self.backend = backend
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
        self.fonts = {size: pico.load_font(str(path), size) for size in (13, 16, 24)}
        self.labels = {}
        self.next_update = 0.0

    def label(self, key, text, size=16, color=(223, 230, 218)):
        if not text:
            self.labels.pop(key, None)
            return
        if key in self.labels and self.labels[key][0] == text:
            return
        backend = self.backend
        surface = backend.TTF_RenderUTF8_Blended(self.fonts[size].font, text.encode("utf-8"), backend.SDL_Color(*color))
        texture = backend.SDL_CreateTextureFromSurface(backend.renderer, surface)
        backend.SDL_FreeSurface(surface)
        if not texture:
            raise RuntimeError("HUD 텍스처 생성 실패")
        self.labels[key] = text, pico.Image(texture)

    def text(self, key, x, y):
        image = self.labels[key][1]
        image.draw(x + image.w / 2, y)

    @staticmethod
    def panel(x, y, w, h):
        pico.draw_rectangle(x, y, x + w, y + h, 17, 28, 31, 238, filled=True)
        pico.draw_rectangle(x, y, x + w, y + h, 67, 86, 82, 255)

    def render(self, simulation, camera, *, paused, debug, update_ms, draw_ms, map_open=False, cache_count=0):
        player, weapon = simulation.player, simulation.player.weapon
        if perf_counter() >= self.next_update:
            self.next_update = perf_counter() + 0.2
            self.label("brand", "REVENANT / FIELD PROTOTYPE", 16, (118, 215, 191))
            self.label("clock", f"{world_clock(simulation.campaign.simulation.state.world_time)}  |  {camera.magnification:.0%}", 13)
            self.label("vitals", f"HP {player.hp:03d}   ARMOR {player.armor:02d}", 16)
            self.label("weapon", weapon.spec.name if weapon else "UNARMED", 24)
            self.label("ammo", f"{weapon.magazine:02d}+{int(weapon.chambered)} / {weapon.reserve:03d}" if weapon else "-- / ---", 24)
            mode = "AUTO" if weapon and weapon.automatic else "SEMI"
            self.label("mode", f"{mode}  ·  1/2 교체  ·  V 모드  ·  R 재장전 / C 취소", 13)
            self.label("message", simulation.message, 13)
            self.label("help", "WASD 이동  Shift 달리기  Ctrl 걷기  우클릭 조준  F 문  E 사용  M 지도  P 정지  F1 정보", 13)
            self.label("timing", f"update {update_ms:.2f}ms / draw {draw_ms:.2f}ms | cache {cache_count}/24 | 근거리 AI {getattr(simulation.ai, 'near_count', 0)}", 13)
            self.label("stock", f"DEPOT {simulation.campaign.supplies}  /  확보 {simulation.campaign.simulation.state.player_stock['supplies']}", 13)
            facility_here = nearby_facility(simulation.world, simulation.player.xy)
            self.label("prompt", f"[E] {facility_here['name']} 사용" if facility_here else "", 16, (241, 211, 135))
            self.label("map_title", simulation.world.data.get("name", "지역 지도"), 24, (118, 215, 191))
            self.label("map_help", "M 닫기 · 지도에서도 세계 시간은 진행됨 · F1 개발 정보", 13)
            self.label("map_size", f"{simulation.world.bounds.w:g} × {simulation.world.bounds.h:g} m", 13)
            for facility in simulation.world.facilities:
                self.label("site/" + facility["id"], facility["name"], 13)
                campaign = simulation.campaign.simulation.state.facilities.get(facility["campaign_id"])
                status = f"피해 {campaign.damage}%" if campaign else "복귀 거점"
                self.label("site_info/" + facility["id"], f"{facility['name']} · {status}", 13)
            for index, (key, title) in enumerate(STAGE_NAMES.items()):
                duration = weapon.spec.reload_stages[index].duration if weapon else 0
                self.label(key, f"{index + 1} {title} {duration:g}s", 13)
            if weapon and (weapon.reloading or weapon.stage > 0):
                phase = STAGE_NAMES[weapon.spec.reload_stages[weapon.stage].id]
                status = "진행" if weapon.reloading else "중단 / R 재개"
                self.label("reload", f"{phase} — {status} · 남은 {weapon.remaining_reload:.2f}s", 13, (241, 196, 112))
            else:
                self.label("reload", "완료 단계 보존 · 취소한 단계는 처음부터 재개", 13, (136, 159, 152))
        self.label("paused", "PAUSED — P 키로 재개" if paused else "", 24)
        width, height = camera.width, camera.height
        self.panel(12, height - 67, width - 24, 55)
        self.text("brand", 26, height - 30)
        self.text("clock", 26, height - 50)
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
                progress = weapon.stage_elapsed / weapon.spec.reload_stages[index].duration
            pico.draw_rectangle(x, 57, x + 116, 62, 43, 56, 52, filled=True)
            if progress:
                pico.draw_rectangle(x, 57, x + 116 * progress, 62, *color, filled=True)
        self.text("reload", 26, 35)
        self.panel(434, 12, width - 650, 70)
        self.text("message", 447, 60)
        self.text("stock", 447, 35)
        if debug:
            self.panel(12, height - 120, 650, 40)
            self.text("timing", 24, height - 100)
        else:
            self.text("help", 24, height - 88)
        if paused:
            self.panel(width / 2 - 190, height / 2 - 27, 380, 54)
            self.text("paused", width / 2 - 160, height / 2)
        if "prompt" in self.labels:
            self.panel(width / 2 - 150, 168, 300, 36)
            self.text("prompt", width / 2 - 132, 186)
        self._minimap(simulation, camera, debug)
        if map_open:
            self._region_map(simulation, camera, debug)

    def _minimap(self, simulation, camera, debug=False):
        x0, y0, width, height = camera.width - 196, 12, 180, 126
        self.panel(x0, y0, width, height)
        bounds = simulation.world.bounds
        scale = min((width - 16) / bounds.w, (height - 16) / bounds.h)

        def point(x, y):
            return x0 + 8 + (x - bounds.x) * scale, y0 + height - 8 - (y - bounds.y) * scale

        world = simulation.world
        if world.facilities:
            for road in world.roads:
                pico.draw_line(*point(*road["start"]), *point(*road["end"]), 82, 99, 90)
            for facility in world.facilities:
                x, y, w, h = facility["rect"]
                a, b = point(x, y), point(x + w, y + h)
                pico.draw_rectangle(a[0], b[1], b[0], a[1], 116, 133, 108, filled=True)
        else:
            for collider in world.geometry.objects.values():
                a, b = point(collider.rect.x, collider.rect.y), point(collider.rect.right, collider.rect.bottom)
                pico.draw_rectangle(a[0], b[1], b[0], a[1], 93, 111, 94, filled=True)
        for actor in [*simulation.guards, simulation.player]:
            if actor.hp > 0 and (actor.team == "player" or debug or math.dist(actor.xy, simulation.player.xy) < 20):
                x, y = point(*actor.xy)
                color = (108, 213, 197) if actor.team == "player" else (218, 131, 100)
                pico.draw_rectangle(x - 2, y - 2, x + 2, y + 2, *color, filled=True)
        if world.region_layout:
            for convoy in simulation.campaign.convoys(simulation.time):
                if not bounds.x <= convoy.x <= bounds.right or not bounds.y <= convoy.y <= bounds.bottom:
                    continue
                x, y = point(convoy.x, convoy.y)
                pico.draw_rectangle(x - 2, y - 1, x + 2, y + 1, 221, 186, 100, filled=True)

    def _region_map(self, simulation, camera, debug):
        self.panel(60, 166, camera.width - 120, camera.height - 246)
        self.text("map_title", 84, camera.height - 111)
        self.text("map_help", 84, camera.height - 140)
        world = simulation.world
        scale = min((camera.width - 480) / world.bounds.w, (camera.height - 370) / world.bounds.h)
        left = 84 + (camera.width - 480 - world.bounds.w * scale) / 2
        top = camera.height - 176

        def point(x, y):
            return left + (x - world.bounds.x) * scale, top - (y - world.bounds.y) * scale

        right, bottom = point(world.bounds.right, world.bounds.bottom)
        pico.draw_rectangle(left, bottom, right, top, 34, 49, 43, filled=True)
        for road in world.roads:
            pico.draw_line(*point(*road["start"]), *point(*road["end"]), 153, 155, 123)
        for index, facility in enumerate(world.facilities):
            x, y, w, h = facility["rect"]
            a, b = point(x, y), point(x + w, y + h)
            color = (94, 175, 150) if facility["kind"] == "safehouse" else (162, 166, 128)
            pico.draw_rectangle(a[0], b[1], b[0], a[1], *color, filled=True)
            self.text("site/" + facility["id"], a[0], b[1] - 13)
            self.text("site_info/" + facility["id"], camera.width - 350, camera.height - 200 - index * 34)
        for actor in [*simulation.guards, simulation.player]:
            if actor.hp <= 0 or (actor.team != "player" and not debug):
                continue
            x, y = point(*actor.xy)
            color = (112, 230, 205) if actor.team == "player" else (226, 131, 92)
            pico.draw_rectangle(x - 3, y - 3, x + 3, y + 3, *color, filled=True)
        for convoy in simulation.campaign.convoys(simulation.time):
            if not world.bounds.x <= convoy.x <= world.bounds.right or not world.bounds.y <= convoy.y <= world.bounds.bottom:
                continue
            x, y = point(convoy.x, convoy.y)
            pico.draw_rectangle(x - 3, y - 2, x + 3, y + 2, 221, 186, 100, filled=True)
        self.text("map_size", camera.width - 350, 245)

    def close(self):
        self.labels.clear()
        for font in self.fonts.values():
            self.backend.TTF_CloseFont(font.font)
        self.fonts.clear()
