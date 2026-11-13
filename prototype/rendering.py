"""PNG 레이어·동적 개체 표시. 리소스는 시작 시 로드하고 HUD 텍스처는 캐시한다."""

import importlib
import math
import os
from pathlib import Path
from time import perf_counter

import pico2d as pico

from .geometry import Rect
from .world import ROOT


class Renderer:
    def __init__(self, world):
        self.backend = importlib.import_module("pico2d.pico2d")
        self.backend.SDL_SetRenderDrawBlendMode(self.backend.renderer, self.backend.SDL_BLENDMODE_BLEND)
        self.backend.SDL_SetHint(b"SDL_RENDER_SCALE_QUALITY", b"0")
        self.images, self.missing, self.labels = {}, [], {}
        self.layers = {layer.id: self._image(layer.image, layer.fallback) for layer in world.layers}
        self.sprites = {key: self._image(f"assets/prototype/{key}.png") for key in ("player", "guard")}
        self.door = self._image("assets/prototype/door.png", "assets/generated/entities/door_door_wood_horizontal_64x32.png")
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
        self.font = pico.load_font(str(font_path), 16)
        self.hud_at = 0.0
        self.last_draw_ms = 0.0
        for name in self.missing:
            print(f"임시 표현 사용: {name}")

    def _image(self, name, fallback=""):
        for candidate in (name, fallback):
            if not candidate:
                continue
            path = ROOT / candidate
            if path.is_file():
                if candidate not in self.images:
                    self.images[candidate] = pico.load_image(str(path))
                return self.images[candidate]
        self.missing.append(name)
        return None

    @staticmethod
    def _rect(camera, rect, color, *, filled=True):
        left, top = camera.pico(rect.x, rect.y)
        right, bottom = camera.pico(rect.right, rect.bottom)
        pico.draw_rectangle(left, bottom, right, top, *color, filled=filled)

    def _label(self, key, text):
        if key in self.labels and self.labels[key][0] == text:
            return
        backend = self.backend
        surface = backend.TTF_RenderUTF8_Blended(self.font.font, text.encode("utf-8"), backend.SDL_Color(235, 238, 218))
        texture = backend.SDL_CreateTextureFromSurface(backend.renderer, surface)
        backend.SDL_FreeSurface(surface)
        if not texture:
            raise RuntimeError("HUD 텍스처 생성 실패")
        self.labels[key] = (text, pico.Image(texture))

    def render(self, simulation, camera, *, debug=False, paused=False, update_ms=0.0):
        started = perf_counter()
        pico.clear_canvas()
        pico.draw_rectangle(0, 0, camera.width - 1, camera.height - 1, 21, 30, 30, filled=True)
        world = simulation.world
        for layer in world.layers:
            if not camera.visible(layer.rect):
                continue
            image = self.layers[layer.id]
            if image is not None:
                x, y = camera.pico(layer.rect.x + layer.rect.w / 2, layer.rect.y + layer.rect.h / 2)
                image.draw(x, y, layer.rect.w * camera.scale, layer.rect.h * camera.scale)
            elif layer.id != "walls":
                self._rect(camera, layer.rect, layer.color)
        for collider in world.geometry.objects.values():
            if not camera.visible(collider.rect):
                continue
            if collider.kind == "door":
                if collider.active:
                    if self.door is not None:
                        rect = collider.rect
                        x, y = camera.pico(rect.x + rect.w / 2, rect.y + rect.h / 2)
                        self.door.draw(x, y, rect.w * camera.scale, rect.h * camera.scale)
                    else:
                        self._rect(camera, collider.rect, (121, 89, 49))
                else:
                    self._rect(camera, Rect(collider.rect.x, collider.rect.y, 0.12, 0.25), (70, 174, 105))
            elif collider.kind == "cover" or self.layers.get("walls") is None:
                self._rect(camera, collider.rect, (90, 97, 91))
            if debug:
                self._rect(camera, collider.rect, (238, 121, 75) if collider.active else (87, 207, 137), filled=False)
        self._rect(camera, Rect(world.crate[0] - 0.35, world.crate[1] - 0.35, 0.7, 0.7), (190, 163, 74))
        for actor in [*simulation.guards, simulation.player]:
            if not camera.visible(Rect(actor.position.x - 1, actor.position.y - 1, 2, 2)):
                continue
            image = self.sprites["player" if actor is simulation.player else "guard"]
            x, y = camera.pico(*actor.xy)
            if image is not None and actor.hp > 0:
                image.rotate_draw(-actor.facing, x, y, 0.75 * camera.scale, 0.375 * camera.scale)
            else:
                color = ((91, 199, 208) if actor is simulation.player else
                         (231, 109, 83) if actor.state == "chase" else (209, 185, 114)) if actor.hp else (74, 71, 67)
                self._rect(camera, Rect(actor.position.x - 0.24, actor.position.y - 0.18, 0.48, 0.36), color)
            if actor.hp:
                aim_x = actor.position.x + math.cos(actor.facing) * 0.65
                aim_y = actor.position.y + math.sin(actor.facing) * 0.65
                pico.draw_line(x, y, *camera.pico(aim_x, aim_y), 35, 40, 39)
            if debug:
                self._rect(camera, Rect(actor.position.x - actor.radius, actor.position.y - actor.radius,
                                        actor.radius * 2, actor.radius * 2), (245, 224, 130), filled=False)
                brain = simulation.ai.brains.get(actor.id)
                if brain is not None and actor.hp:
                    pico.draw_line(x, y, *camera.pico(*simulation.player.xy), 238 if brain.visible else 80, 100, 90)
        for bullet in simulation.projectiles.bullets:
            pico.draw_line(*camera.pico(bullet.x, bullet.y),
                           *camera.pico(bullet.x - bullet.vx * 0.007, bullet.y - bullet.vy * 0.007), 255, 234, 149)
        self._hud(simulation, camera, paused, debug, update_ms)
        self.last_draw_ms = (perf_counter() - started) * 1000
        pico.update_canvas()

    def _hud(self, simulation, camera, paused, debug, update_ms):
        now = perf_counter()
        if now >= self.hud_at:
            self.hud_at = now + 0.2
            state = simulation.campaign.simulation.state
            self._label("title", f"IMAGE MAP PROTOTYPE  |  줌 {camera.magnification:.0%}  |  {'일시정지' if paused else '실행 중'}")
            self._label("controls", "WASD 이동 · Shift 달리기 · 마우스 발사 · 휠/+/- 줌 · F 문 · E 보급품 · P 정지 · F1 판정 · Esc 종료")
            self._label("state", f"사격 {simulation.projectiles.shots} / 명중 {simulation.projectiles.hits} / 활성 탄환 {len(simulation.projectiles.bullets)}"
                        f"  |  실제 창고 {simulation.campaign.supplies} / 확보 {state.player_stock['supplies']}")
            self._label("timing", f"갱신 {update_ms:.2f}ms / 그리기 {self.last_draw_ms:.2f}ms  |  누적 벽 후보 검사 {simulation.world.geometry.candidate_tests}"
                        f"  |  {'판정 표시 ON' if debug else '판정 표시 OFF'}")
            self._label("message", simulation.message)
        pico.draw_rectangle(0, camera.height - 125, camera.width - 1, camera.height - 1, 19, 28, 30, 220, filled=True)
        for index, key in enumerate(("title", "controls", "state", "timing", "message")):
            if key in self.labels:
                image = self.labels[key][1]
                image.draw(12 + image.w / 2, camera.height - 17 - index * 23)

    def close(self):
        # SDL 렌더러를 닫기 전에 모든 텍스처 참조를 해제한다.
        self.labels.clear()
        self.layers.clear()
        self.sprites.clear()
        self.door = None
        self.images.clear()
        self.backend.TTF_CloseFont(self.font.font)
