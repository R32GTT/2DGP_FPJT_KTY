"""그림·회전된 몸통/무기·화면 효과. 모든 리소스는 시작 시 준비한다."""

from copy import copy
import importlib
import math
from time import perf_counter

import pico2d as pico

from .geometry import Rect
from .hud import HUD
from .visual_assets import actor_image, layer_image, vignette_image, weapon_image
from .weapons import load_weapons, weapon_pose
from .world import ROOT


# 몸통 원본은 위를 바라본다. 가로는 어깨 폭, 세로는 앞뒤 두께다.
BODY_SIZE_M = (0.75, 0.375)
BODY_FORWARD_OFFSET = math.pi / 2


class Renderer:
    def __init__(self, world):
        self.backend = importlib.import_module("pico2d.pico2d")
        self.backend.SDL_SetWindowTitle(self.backend.window, b"REVENANT : L.O.S.T / Field Prototype")
        self.backend.SDL_SetRenderDrawBlendMode(self.backend.renderer, self.backend.SDL_BLENDMODE_BLEND)
        self.backend.SDL_SetHint(b"SDL_RENDER_SCALE_QUALITY", b"0")
        self.backend.SDL_ShowCursor(self.backend.SDL_DISABLE)
        self.images, self.missing = {}, []
        self.layers = {layer.id: self._image(layer.image, layer.fallback) or layer_image(self.backend, layer)
                       for layer in world.layers}
        self.sprites = {key: self._image(f"assets/prototype/{key}.png") or actor_image(self.backend, key == "player")
                        for key in ("player", "guard")}
        self.guns = {key: self._image(spec.image) or weapon_image(self.backend, key == "ar_b")
                     for key, spec in load_weapons().items()}
        self.door = self._image("assets/prototype/door.png", "assets/generated/entities/door_door_wood_horizontal_64x32.png")
        self.vignette = self._image("assets/prototype/alert_vignette.png") or vignette_image(self.backend)
        self.hud = HUD(self.backend)
        self.last_draw_ms = 0.0
        for name in self.missing:
            print(f"임시 그래픽 사용: {name}")

    def _image(self, name, fallback=""):
        if not name and not fallback:
            return None
        for candidate in (name, fallback):
            if candidate and (ROOT / candidate).is_file():
                if candidate not in self.images:
                    self.images[candidate] = pico.load_image(str(ROOT / candidate))
                return self.images[candidate]
        if name:
            self.missing.append(name)
        return None

    @staticmethod
    def _rect(camera, rect, color, *, filled=True):
        left, top = camera.pico(rect.x, rect.y)
        right, bottom = camera.pico(rect.right, rect.bottom)
        pico.draw_rectangle(left, bottom, right, top, *color, filled=filled)

    @staticmethod
    def _rotated_bounds(center, size, facing):
        cosine, sine = abs(math.cos(facing)), abs(math.sin(facing))
        width, height = size[0] * cosine + size[1] * sine, size[0] * sine + size[1] * cosine
        return Rect(center[0] - width / 2, center[1] - height / 2, width, height)

    def render(self, simulation, camera, *, debug=False, paused=False, update_ms=0.0, mouse=None):
        started = perf_counter()
        view = copy(camera)
        shake_x, shake_y = simulation.feedback.offset
        view.offset_x += shake_x
        view.offset_y += shake_y
        pico.clear_canvas()
        pico.draw_rectangle(0, 0, view.width - 1, view.height - 1, 18, 28, 29, filled=True)
        world = simulation.world
        for layer in world.layers:
            if not view.visible(layer.rect):
                continue
            image = self.layers[layer.id]
            if image is not None:
                x, y = view.pico(layer.rect.x + layer.rect.w / 2, layer.rect.y + layer.rect.h / 2)
                size = layer.image_size or (layer.rect.w, layer.rect.h)
                image.rotate_draw(-math.radians(layer.rotation), x, y, size[0] * view.scale, size[1] * view.scale)
        for collider in world.geometry.objects.values():
            if not view.visible(collider.rect):
                continue
            if collider.kind == "door":
                if collider.active:
                    if self.door:
                        x, y = view.pico(collider.rect.x + collider.rect.w / 2, collider.rect.y + collider.rect.h / 2)
                        self.door.draw(x, y, collider.rect.w * view.scale, collider.rect.h * view.scale)
                    else:
                        self._rect(view, collider.rect, (140, 104, 62))
                else:
                    self._rect(view, Rect(collider.rect.x, collider.rect.y, 0.12, 0.25), (78, 184, 132))
            elif collider.kind != "wall" or self.layers.get(collider.visual_layer or "walls") is None:
                rect = collider.rect
                self._rect(view, Rect(rect.x + 0.08, rect.y + 0.09, rect.w, rect.h), (20, 29, 28, 130))
                self._rect(view, rect, (126, 111, 77) if collider.material == "wood" else (139, 148, 133))
                self._rect(view, rect, (185, 184, 153), filled=False)
            if debug:
                self._rect(view, collider.rect, (233, 111, 77) if collider.active else (91, 213, 150), filled=False)
        crate = Rect(world.crate[0] - 0.35, world.crate[1] - 0.35, 0.7, 0.7)
        self._rect(view, crate, (183, 156, 76))
        self._rect(view, crate, (228, 205, 137), filled=False)
        safe = Rect(world.safehouse[0] - 1.2, world.safehouse[1] - 1.2, 2.4, 2.4)
        if view.visible(safe):
            self._rect(view, safe, (47, 94, 82))
            self._rect(view, safe, (124, 206, 175), filled=False)
            self._rect(view, Rect(world.safehouse[0] - 0.1, world.safehouse[1] - 0.5, 0.2, 1), (206, 225, 202))
            self._rect(view, Rect(world.safehouse[0] - 0.5, world.safehouse[1] - 0.1, 1, 0.2), (206, 225, 202))
        for point, _ in simulation.dropped:
            self._rect(view, Rect(point[0] - 0.25, point[1] - 0.15, 0.5, 0.3), (218, 181, 106))
        for actor in [*simulation.guards, simulation.player]:
            body_angle = actor.facing + BODY_FORWARD_OFFSET
            body_bounds = self._rotated_bounds(actor.xy, BODY_SIZE_M, body_angle)
            weapon = actor.weapon
            center, muzzle = weapon_pose(actor)
            gun_bounds = self._rotated_bounds(center, weapon.spec.size_m, actor.facing) if weapon else body_bounds
            if not (view.visible(body_bounds) or view.visible(gun_bounds)):
                continue
            x, y = view.pico(*actor.xy)
            image = self.sprites["player" if actor.team == "player" else "guard"]
            if actor.hp <= 0:
                image.opacify(0.35)
            image.rotate_draw(-body_angle, x, y, BODY_SIZE_M[0] * view.scale, BODY_SIZE_M[1] * view.scale)
            image.opacify(1.0)
            if actor.hp > 0 and weapon:
                gun_x, gun_y = view.pico(*center)
                gun = self.guns[weapon.spec.id]
                gun.rotate_draw(-actor.facing, gun_x, gun_y,
                                weapon.spec.size_m[0] * view.scale, weapon.spec.size_m[1] * view.scale)
            if debug:
                self._rect(view, Rect(actor.position.x - actor.radius, actor.position.y - actor.radius,
                                      actor.radius * 2, actor.radius * 2), (244, 211, 119), filled=False)
                self._rect(view, gun_bounds, (107, 187, 212), filled=False)
                brain = simulation.ai.brains.get(actor.id)
                if brain and actor.hp and brain.memory.last_seen:
                    pico.draw_line(x, y, *view.pico(*brain.memory.last_seen), 212, 103, 75)
        for trace in simulation.combat.traces:
            color = (249, 225, 137) if trace.team == "player" else (230, 128, 92)
            pico.draw_line(*view.pico(*trace.start), *view.pico(*trace.end), *color)
        if mouse is not None and simulation.player.hp > 0:
            # 흔들리는 화면에서도 실제 조준한 월드 지점에 조준점을 함께 그린다.
            x, y = view.pico(*camera.world(*mouse))
            for dx, dy in ((-9, 0), (9, 0), (0, -9), (0, 9)):
                pico.draw_line(x + dx * 0.5, y + dy * 0.5, x + dx, y + dy, 226, 230, 202)
        if simulation.feedback.alpha > 0.001:
            self.vignette.opacify(simulation.feedback.alpha)
            self.vignette.draw(camera.width / 2, camera.height / 2, camera.width, camera.height)
        self.hud.render(simulation, camera, paused=paused, debug=debug,
                        update_ms=update_ms, draw_ms=self.last_draw_ms)
        self.last_draw_ms = (perf_counter() - started) * 1000
        pico.update_canvas()

    def close(self):
        self.hud.close()
        self.layers.clear()
        self.sprites.clear()
        self.guns.clear()
        self.door = self.vignette = None
        self.images.clear()
        self.backend.SDL_ShowCursor(self.backend.SDL_ENABLE)
