"""그림·회전된 몸통/무기·화면 효과. 모든 리소스는 시작 시 준비한다."""

from copy import copy
import importlib
import math
from time import perf_counter

import pico2d as pico

from .ai_config import RADIO_GRACE_SECONDS, RADIO_TRANSMIT_SECONDS
from .geometry import Rect
from .camera import CANVAS_SIZE
from .hud import HUD
from .lighting import Lighting, incident_light, smooth
from .map_images import MapImages
from .perception import INSPECT_THRESHOLD, NOTICE_THRESHOLD, SOUND_MEMORY_SECONDS
from .throwables import BURST_SECONDS
from .transport import TRUCK_SIZE
from .visual_assets import (
    actor_image, draw_polygon, prop_image, smoke_image, tree_canopy_image, tree_trunk_image, truck_image,
    vignette_image, weapon_image,
)
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
        self.backend.SDL_SetWindowResizable(self.backend.window, self.backend.SDL_TRUE)
        # SDL이 출력과 마우스 이벤트를 같은 논리 좌표로 변환한다.
        self.backend.SDL_RenderSetLogicalSize(self.backend.renderer, *CANVAS_SIZE)
        self.backend.SDL_ShowCursor(self.backend.SDL_DISABLE)
        self.images, self.missing = {}, []
        self.map_images = MapImages(self.backend)
        self.lighting = Lighting(self.backend)
        self.layers = {}
        spawn = world.data["player_spawn"]
        self.map_images.prepare(world.visible_layers(Rect(spawn[0] - 20, spawn[1] - 16, 40, 32)), budget=12)
        self.tree_canopy = self._image("assets/prototype/tree_canopy.png") or tree_canopy_image(self.backend)
        self.tree_trunk = self._image("assets/prototype/tree_trunk.png") or tree_trunk_image(self.backend)
        self.truck = truck_image(self.backend)
        self.props = {kind: prop_image(self.backend, kind) for kind in ("cover", "desk", "rack", "bed", "antenna")}
        self.sprites = {key: self._image(f"assets/prototype/{key}.png") or actor_image(self.backend, key == "player")
                        for key in ("player", "guard")}
        self.guns = {key: self._image(spec.image) or weapon_image(self.backend, key == "ar_b", spec.category)
                     for key, spec in load_weapons().items()}
        self.door = self._image("assets/prototype/door.png", "assets/generated/entities/door_door_wood_horizontal_64x32.png")
        self.vignette = self._image("assets/prototype/alert_vignette.png") or vignette_image(self.backend)
        self.smoke = smoke_image(self.backend)
        self.hud = HUD(self.backend)
        self.hud.prepare_map(world)
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

    def _draw_trees(self, view, colliders, player, sky, night_vision, debug):
        crown_size = 3.6
        crown_px = crown_size * view.scale
        viewport = view.viewport()
        ambient, direct = self.lighting.sky_colors(sky, night_vision)
        canopy_color = tuple(min(255, fill + sun) for fill, sun in zip(ambient, direct))
        self.backend.SDL_SetTextureColorMod(self.tree_canopy.texture, *canopy_color)
        self.backend.SDL_SetTextureColorMod(self.tree_trunk.texture, *ambient)
        trunks = []
        previous_crown_alpha = 1.0
        for tree in colliders:
            if tree.kind != "tree" or not tree.active:
                continue
            rect = tree.rect
            x, y = rect.x + rect.w * 0.5, rect.y + rect.h * 0.5
            crown = Rect(x, y, 0, 0).expanded(crown_size * 0.5)
            crown_in_view = crown.overlaps(viewport)
            trunk_in_view = rect.overlaps(viewport)
            if not (crown_in_view or trunk_in_view):
                continue
            px, py = view.pico(x, y)
            distance = math.hypot(x - player[0], y - player[1])
            fade = 1.0 - smooth((distance - 1.55) / 2.05)
            if crown_in_view:
                crown_alpha = 1.0 - 0.92 * fade
                if crown_alpha != previous_crown_alpha:
                    self.tree_canopy.opacify(crown_alpha)
                    previous_crown_alpha = crown_alpha
                self.tree_canopy.draw(px, py, crown_px, crown_px)
            if trunk_in_view and (fade >= 1 / 255 or debug):
                trunks.append((rect, px, py, fade))
        if previous_crown_alpha != 1:
            self.tree_canopy.opacify(1.0)
        # 수관을 먼저 모두 그리고, 가려져 있던 밑동만 위에 드러낸다.
        previous_trunk_alpha = 1.0
        for rect, px, py, fade in trunks:
            if fade >= 1 / 255:
                if fade != previous_trunk_alpha:
                    self.tree_trunk.opacify(fade)
                    previous_trunk_alpha = fade
                self.tree_trunk.draw(px, py, rect.w * view.scale, rect.h * view.scale)
            if debug:
                self._rect(view, rect, (148, 190, 124), filled=False)
        if previous_trunk_alpha != 1:
            self.tree_trunk.opacify(1.0)

    @staticmethod
    def _draw_navigation(navigation, view):
        keys = set(navigation.point_index.query(view.viewport(1)))
        drawn = set()
        for key in sorted(keys):
            point = navigation.nodes[key]
            x, y = view.pico(*point)
            for neighbor in navigation.edges[key]:
                edge = tuple(sorted((key, neighbor)))
                if edge not in drawn:
                    drawn.add(edge)
                    pico.draw_line(x, y, *view.pico(*navigation.nodes[neighbor]), 100, 139, 177)
        for key in keys:
            x, y = view.pico(*navigation.nodes[key])
            pico.draw_rectangle(x - 3, y - 3, x + 3, y + 3, 152, 198, 240, filled=True)

    @staticmethod
    def _draw_awareness(brain, x, y, time):
        radio_start = brain.radio.started_at
        level = brain.memory.recognition
        grace = RADIO_GRACE_SECONDS - (time - brain.contact_at) if brain.contact_at is not None else 0
        if radio_start is not None:
            color = (255, 112, 96)
            progress = min(1.0, (time - radio_start) / RADIO_TRANSMIT_SECONDS)
            pico.draw_rectangle(x - 3, y + 5, x + 3, y + 12, *color, filled=True)
            pico.draw_line(x + 2, y + 12, x + 2, y + 16, *color)
        elif brain.memory.visible or grace > 0:
            color = (255, 91, 80)
            progress = grace / RADIO_GRACE_SECONDS if grace > 0 else 1.0
            pico.draw_line(x, y + 6, x, y + 14, *color)
            pico.draw_rectangle(x - 1, y + 3, x + 1, y + 4, *color, filled=True)
        elif 0 < level < 1.0:
            progress = level
            color = ((248, 157, 76) if level >= INSPECT_THRESHOLD else
                     (253, 213, 109) if level >= NOTICE_THRESHOLD else (229, 235, 225))
        else:
            return
        pico.draw_rectangle(x - 16, y - 2, x + 16, y + 2, 29, 30, 29, filled=True)
        if progress > 0:
            pico.draw_rectangle(x - 15, y - 1, x - 15 + 30 * progress, y + 1, *color, filled=True)

    @staticmethod
    def _enemy_visibility(world, player, enemies, sky, lights, night_vision):
        origin, floor = player.xy, player.position.floor
        centers = world.geometry.sight_flags(origin, [actor.xy for actor in enemies], floor=floor)
        visible = [actor for actor, clear in zip(enemies, centers) if clear]
        blocked = [actor for actor, clear in zip(enemies, centers) if not clear]
        sides = []
        for actor in blocked:
            dx, dy = actor.xy[0] - origin[0], actor.xy[1] - origin[1]
            scale = actor.radius / (math.hypot(dx, dy) or 1)
            sides.extend(((actor.xy[0] - dy * scale, actor.xy[1] + dx * scale),
                          (actor.xy[0] + dy * scale, actor.xy[1] - dx * scale)))
        if sides:
            flags = world.geometry.sight_flags(origin, sides, floor=floor)
            visible.extend(actor for actor, left, right in zip(blocked, flags[::2], flags[1::2]) if left or right)

        result = {}
        ambient = sum(sky.ambient) / 765 * (1.9 if night_vision else 1.0)
        ambient_clear = max(3, 1 + 30 * min(1.0, ambient))
        for actor in visible:
            distance = math.dist(origin, actor.xy)
            alpha = 1.0
            if sky.daylight < 1 and distance > ambient_clear:
                visible_light, infrared = incident_light(world, actor.xy, sky, lights)
                brightness = visible_light * 1.9 + infrared if night_vision else visible_light
                limit = 3 + 30 * min(1.0, brightness)
                alpha = smooth((limit - distance) / 2)
            if alpha >= 1 / 255:
                result[actor.id] = alpha
        return result

    def _draw_throwables(self, simulation, view, floor):
        for flight in simulation.throwables.flights:
            if flight.floor != floor:
                continue
            x, y = flight.position
            body = Rect(x - 0.07, y - 0.07, 0.14, 0.14)
            if view.visible(body):
                self._rect(view, body, (120, 132, 94))
        for cloud in simulation.throwables.smoke:
            if cloud.floor != floor:
                continue
            if not view.visible(Rect(*cloud.position, 0, 0).expanded(cloud.radius)):
                continue
            diameter = 2 * cloud.radius * view.scale
            self.smoke.opacify(min(1.0, cloud.expires_at - simulation.time))
            self.smoke.draw(*view.pico(*cloud.position), diameter, diameter)
        for burst in simulation.throwable_bursts:
            if burst.floor != floor:
                continue
            progress = (simulation.time - burst.time) / BURST_SECONDS
            radius = (0.18 + 0.58 * progress) * burst.spec.effect.radius
            if not view.visible(Rect(*burst.position, 0, 0).expanded(radius)):
                continue
            x, y = burst.position
            points = [view.pico(x + math.cos(angle) * radius, y + math.sin(angle) * radius)
                      for angle in (index * math.tau / 12 for index in range(12))]
            color = (255, 251, 224) if burst.spec.effect.kind == "flash" else (248, 157, 76)
            draw_polygon(self.backend, points, (*color, round(208 * (1 - progress))))

    def render(self, simulation, camera, *, debug=False, paused=False, update_ms=0.0, mouse=None, map_open=False,
               inventory_view=None, intel_index=None, minimap_open=True, map_navigation=None):
        started = perf_counter()
        view = copy(camera)
        shake_x, shake_y = simulation.feedback.offset
        view.offset_x += shake_x
        view.offset_y += shake_y
        self.backend.SDL_SetRenderDrawColor(self.backend.renderer, 18, 28, 29, 255)
        self.backend.SDL_RenderClear(self.backend.renderer)
        pico.draw_rectangle(0, 0, view.width - 1, view.height - 1, 18, 28, 29, filled=True)
        if inventory_view is not None:
            self.hud.render(simulation, camera, paused=paused, debug=False,
                            update_ms=update_ms, draw_ms=self.last_draw_ms,
                            inventory_view=inventory_view, minimap_open=False,
                            weapon_images=self.guns)
            self.last_draw_ms = (perf_counter() - started) * 1000
            pico.update_canvas()
            return
        if map_open:
            self.hud.render(simulation, camera, paused=paused, debug=False,
                            update_ms=update_ms, draw_ms=self.last_draw_ms, map_open=True,
                            intel_index=intel_index, map_navigation=map_navigation)
            self.last_draw_ms = (perf_counter() - started) * 1000
            pico.update_canvas()
            return
        world = simulation.world
        visible_layers = world.visible_layers(view.viewport(1))
        self.layers = self.map_images.prepare(visible_layers)
        for layer in visible_layers:
            image = self.layers[layer.id]
            if image is not None:
                x, y = view.pico(layer.rect.x + layer.rect.w / 2, layer.rect.y + layer.rect.h / 2)
                size = layer.image_size or (layer.rect.w, layer.rect.h)
                image.rotate_draw(-math.radians(layer.rotation), x, y, size[0] * view.scale, size[1] * view.scale)
            elif layer.polygon:
                points = layer.triangles or layer.polygon
                draw_polygon(self.backend, [view.pico(*point) for point in points], layer.color,
                             range(len(points)) if layer.triangles else None)
            elif layer.role != "walls" and not layer.rotation % 90:
                self._rect(view, layer.rect, layer.color)
        colliders = world.visible_colliders(view.viewport(4))
        for collider in colliders:
            if collider.kind in {"building", "terrain", "tree"}:
                continue
            if collider.kind == "parked_truck":
                rect = collider.rect
                x, y = view.pico(rect.x + rect.w / 2, rect.y + rect.h / 2)
                width, height = (rect.h, rect.w) if collider.rotation % 180 else (rect.w, rect.h)
                self.truck.rotate_draw(-math.radians(collider.rotation), x, y,
                                       width * view.scale, height * view.scale)
                continue
            if collider.kind == "door":
                if world.data.get("training_facility"):
                    self._rect(view, collider.rect, (242, 190, 65), filled=collider.active)
                elif collider.active:
                    if self.door:
                        x, y = view.pico(collider.rect.x + collider.rect.w / 2, collider.rect.y + collider.rect.h / 2)
                        width, height = (collider.rect.h, collider.rect.w) if collider.rotation % 180 else (collider.rect.w, collider.rect.h)
                        self.door.rotate_draw(-math.radians(collider.rotation), x, y, width * view.scale, height * view.scale)
                    else:
                        self._rect(view, collider.rect, (140, 104, 62))
                else:
                    jamb = (Rect(collider.rect.x, collider.rect.y, collider.rect.w, 0.12) if collider.rotation % 180 else
                            Rect(collider.rect.x, collider.rect.y, 0.12, collider.rect.h))
                    self._rect(view, jamb, (78, 184, 132))
            elif collider.kind in self.props and (collider.material == "wood" or collider.kind != "cover"):
                rect = collider.rect
                x, y = view.pico(rect.x + rect.w / 2, rect.y + rect.h / 2)
                width, height = (rect.h, rect.w) if collider.rotation % 180 else (rect.w, rect.h)
                self.props[collider.kind].rotate_draw(-math.radians(collider.rotation), x, y,
                                                      width * view.scale, height * view.scale)
            elif collider.kind != "wall" or self.layers.get(collider.visual_layer or "walls") is None:
                rect = collider.rect
                self._rect(view, Rect(rect.x + 0.08, rect.y + 0.09, rect.w, rect.h), (20, 29, 28, 130))
                self._rect(view, rect, (126, 111, 77) if collider.material == "wood" else (139, 148, 133))
                self._rect(view, rect, (185, 184, 153), filled=False)
            if debug:
                self._rect(view, collider.rect, (233, 111, 77) if collider.active else (91, 213, 150), filled=False)
        if world.facilities:
            for facility in world.facilities:
                state = simulation.campaign.simulation.state.facilities.get(facility["campaign_id"])
                watch = facility.get("watch_post")
                if watch is not None:
                    (wx, wy), _ = watch
                    booth = Rect(wx - 1.1, wy - 1.1, 2.2, 2.2)
                    if view.visible(booth):
                        enabled = state.damage < 100 and not state.watch_disabled
                        self._rect(view, booth, (80, 108, 93) if enabled else (77, 68, 63))
                        self._rect(view, booth, (179, 193, 160) if enabled else (115, 88, 76), filled=False)
                        for dx in (-0.98, 0.78):
                            for dy in (-0.98, 0.78):
                                self._rect(view, Rect(wx + dx, wy + dy, 0.2, 0.2), (50, 58, 51))
                point = facility["terminal"]
                marker = Rect(point[0] - 0.35, point[1] - 0.35, 0.7, 0.7)
                if not view.visible(marker):
                    continue
                color = ((119, 191, 157) if facility["kind"] == "safehouse" else
                         (177, 85, 69) if state and (state.damage >= 100 or state.relay_disabled)
                         else (193, 166, 83))
                if facility["kind"] == "sam":
                    x, y = view.pico(*point)
                    self.props["antenna"].draw(x, y, 3.2 * view.scale, 2.7 * view.scale)
                self._rect(view, marker, color)
                self._rect(view, marker, (228, 219, 174), filled=False)
        elif world.data.get("interaction_markers", True):
            crate = Rect(world.crate[0] - 0.35, world.crate[1] - 0.35, 0.7, 0.7)
            self._rect(view, crate, (183, 156, 76))
            self._rect(view, crate, (228, 205, 137), filled=False)
        safe = Rect(world.safehouse[0] - 1.2, world.safehouse[1] - 1.2, 2.4, 2.4)
        if world.data.get("interaction_markers", True) and view.visible(safe):
            self._rect(view, safe, (47, 94, 82))
            self._rect(view, safe, (124, 206, 175), filled=False)
            self._rect(view, Rect(world.safehouse[0] - 0.1, world.safehouse[1] - 0.5, 0.2, 1), (206, 225, 202))
            self._rect(view, Rect(world.safehouse[0] - 0.5, world.safehouse[1] - 0.1, 1, 0.2), (206, 225, 202))
        for point, _ in simulation.dropped:
            self._rect(view, Rect(point[0] - 0.25, point[1] - 0.15, 0.5, 0.3), (218, 181, 106))
        for point, _ in simulation.inventory.ground:
            pile = Rect(point[0] - 0.28, point[1] - 0.22, 0.56, 0.44)
            if view.visible(pile):
                self._rect(view, pile, (139, 161, 124))
        if world.campaign_map:
            roads = simulation.campaign.simulation.state.roads
            for control in world.road_controls:
                marker = Rect(*control["position"], 0, 0).expanded(0.24)
                if view.visible(marker):
                    self._rect(view, marker, (219, 100, 69) if roads[control["road"]].blocked else (238, 197, 92))
                    self._rect(view, marker, (47, 52, 48), filled=False)
            for x, y, _ in simulation.campaign.parked(simulation.roster.active_nodes):
                if view.visible(Rect(x - 2.8, y - 1.2, 5.6, 2.4)):
                    self.truck.draw(*view.pico(x, y), 5.6 * view.scale, 2.4 * view.scale)
            for truck in simulation.transports.display_trucks:
                if view.visible(truck.bounds):
                    x, y = view.pico(truck.x, truck.y)
                    self.truck.rotate_draw(-truck.heading, x, y, TRUCK_SIZE[0] * view.scale, TRUCK_SIZE[1] * view.scale)
                    if truck.stopped:
                        for side in (-0.7, 0.7):
                            px, py = truck.world_point(2.45, side)
                            self._rect(view, Rect(px - 0.13, py - 0.13, 0.26, 0.26), (255, 148, 52))
                    elif debug:
                        for seat, side in enumerate((-0.25, 0.25)):
                            if seat < len(truck.crew) and truck.crew[seat].hp > 0:
                                px, py = truck.world_point(1.95, side)
                                self._rect(view, Rect(px - 0.09, py - 0.09, 0.18, 0.18),
                                           (255, 168, 92) if seat == 0 else (137, 201, 210))
        search_counts = None
        if debug:
            self._draw_navigation(world.navigation, view)
            if not map_open:
                search_counts = self.hud.draw_search_overlay(simulation, view)
        sky = simulation.sky
        player = simulation.player
        night_vision = player.night_vision and player.hp > 0
        floor = player.position.floor
        lights = tuple(simulation.light_sources(sky, floor, infrared=night_vision))
        candidates = []
        threat_candidates = []
        for actor in (*simulation.guards, player):
            brain = simulation.ai.brains.get(actor.id)
            if brain is not None and not debug:
                threat_candidates.append((actor, brain))
            if actor.position.floor != floor:
                continue
            body_angle = actor.facing + BODY_FORWARD_OFFSET
            body_bounds = self._rotated_bounds(actor.xy, BODY_SIZE_M, body_angle)
            weapon = actor.weapon if actor.hp > 0 else None
            center, _ = weapon_pose(actor)
            gun_bounds = self._rotated_bounds(center, weapon.spec.size_m, actor.facing) if weapon else body_bounds
            if view.visible(body_bounds) or view.visible(gun_bounds):
                candidates.append((actor, body_angle, weapon, center, gun_bounds, brain))
        flash_left = player.blinded_until - simulation.time
        seen_enemies = {} if flash_left > 0 else self._enemy_visibility(
            world, player, [actor for actor, *_ in candidates if actor.team != player.team], sky, lights, night_vision)
        awareness = []
        visible_enemies = []
        for actor, body_angle, weapon, center, gun_bounds, brain in candidates:
            alpha = 1.0 if actor.team == player.team else seen_enemies.get(actor.id, 0)
            if alpha == 0:
                continue
            x, y = view.pico(*actor.xy)
            image = self.sprites["player" if actor.team == "player" else "guard"]
            body_alpha = alpha if actor.hp > 0 else 0.35 * alpha
            if body_alpha < 1:
                image.opacify(body_alpha)
            image.rotate_draw(-body_angle, x, y, BODY_SIZE_M[0] * view.scale, BODY_SIZE_M[1] * view.scale)
            if body_alpha < 1:
                image.opacify(1.0)
            if actor.hp > 0 and simulation.presence:
                role = simulation.presence.roles.get(actor.id)
                if role is not None and role[1] == "commander_lost":
                    badge = max(1, round(view.scale * 0.022))
                    pico.draw_rectangle(x - badge, y - badge, x + badge, y + badge,
                                        242, 195, 92, round(alpha * 255), filled=True)
            if weapon:
                gun_x, gun_y = view.pico(*center)
                gun = self.guns[weapon.spec.id]
                if alpha < 1:
                    gun.opacify(alpha)
                gun.rotate_draw(-actor.facing, gun_x, gun_y,
                                weapon.spec.size_m[0] * view.scale, weapon.spec.size_m[1] * view.scale)
                if alpha < 1:
                    gun.opacify(1.0)
            if brain and not debug and actor.hp > 0 and alpha >= 0.5:
                visible_enemies.append((actor, brain))
            if (not map_open and brain and actor.hp > 0 and alpha >= 0.5
                    and (brain.radio.started_at is not None or brain.memory.recognition > 0)):
                awareness.append((brain, x, y + max(12, 0.45 * view.scale)))
            if debug:
                self._rect(view, Rect(actor.position.x - actor.radius, actor.position.y - actor.radius,
                                      actor.radius * 2, actor.radius * 2), (244, 211, 119), filled=False)
                self._rect(view, gun_bounds, (107, 187, 212), filled=False)
                if brain and actor.hp:
                    previous = (x, y)
                    for point in brain.motion.path:
                        current = view.pico(*point)
                        pico.draw_line(*previous, *current, 102, 215, 194)
                        px, py = current
                        pico.draw_rectangle(px - 2, py - 2, px + 2, py + 2, 102, 215, 194, filled=True)
                        previous = current
                    if brain.motion.goal is not None:
                        gx, gy = view.pico(*brain.motion.goal)
                        color = (231, 107, 82) if brain.motion.route_failed else (102, 215, 194)
                        pico.draw_rectangle(gx - 4, gy - 4, gx + 4, gy + 4, *color, filled=False)
                    if brain.memory.visible:
                        pico.draw_line(x, y, *view.pico(*brain.memory.last_seen), 212, 103, 75)
                    if (brain.memory.last_sound is not None
                            and simulation.time - brain.memory.sound_heard_at < SOUND_MEMORY_SECONDS):
                        sx, sy = view.pico(*brain.memory.last_sound)
                        pico.draw_line(sx - 5, sy, sx + 5, sy, 244, 184, 88)
                        pico.draw_line(sx, sy - 5, sx, sy + 5, 244, 184, 88)
        self.lighting.draw(world, view, lights, sky, night_vision=night_vision, floor=floor)
        # 지면 조명으로 그려진 그림자 위에 수관을 별도로 합성한다.
        self._draw_trees(view, colliders, player.xy, sky, night_vision, debug)
        self._draw_throwables(simulation, view, floor)
        for point in simulation.laser_targets(sky, view.viewport()):
            x, y = view.pico(*point)
            alpha = round(230 * sky.infrared)
            pico.draw_rectangle(x - 2, y - 2, x + 2, y + 2, 90, 225, 125, alpha // 2, filled=True)
            pico.draw_rectangle(x - 1, y - 1, x + 1, y + 1, 191, 255, 195, alpha, filled=True)
        for alarm in simulation.alarms.values():
            x, y = alarm.position
            beacon = Rect(x - 0.23, y - 0.23, 0.46, 0.46)
            if alarm.until > simulation.time and view.visible(beacon):
                pulse = 0.5 + 0.5 * math.sin(simulation.time * math.tau)
                self._rect(view, beacon, (255, int(48 + 78 * pulse), 54))
        if debug:
            for brain, x, y in awareness:
                self._draw_awareness(brain, x, y, simulation.time)
        for trace in simulation.combat.traces:
            color = (249, 225, 137) if trace.team == "player" else (230, 128, 92)
            pico.draw_line(*view.pico(*trace.start), *view.pico(*trace.end), *color)
        aim_point = None
        if mouse is not None and not map_open and player.hp > 0 and flash_left <= 0:
            # 흔들리는 화면에서도 실제 조준한 월드 지점에 조준점을 함께 그린다.
            aim = camera.world(*mouse)
            x, y = view.pico(*aim)
            aim_point = x, y
            weapon = simulation.player.weapon
            spread = weapon.spread_deg if weapon is not None else 0
            gap = max(4, math.dist(simulation.player.xy, aim) * view.scale * math.tan(math.radians(spread)))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                pico.draw_line(x + dx * gap, y + dy * gap, x + dx * (gap + 5), y + dy * (gap + 5),
                               226, 230, 202)
        if simulation.feedback.alpha > 0.001:
            self.vignette.opacify(simulation.feedback.alpha)
            self.vignette.draw(camera.width / 2, camera.height / 2, camera.width, camera.height)
        if flash_left > 0 and player.hp > 0:
            pico.draw_rectangle(0, 0, view.width - 1, view.height - 1, 250, 249, 233,
                                round(244 * min(1.0, flash_left / 0.45)), filled=True)
        self.hud.render(simulation, camera, paused=paused, debug=debug,
                        update_ms=update_ms, draw_ms=self.last_draw_ms, map_open=map_open,
                        cache_count=len(self.map_images.cache), search_counts=search_counts,
                        seen_enemies=seen_enemies, inventory_view=inventory_view, intel_index=intel_index,
                        minimap_open=minimap_open, world_view=view, aim_point=aim_point,
                        weapon_image=self.guns.get(player.weapon.spec.id) if player.weapon else None,
                        visible_enemies=visible_enemies, threat_candidates=threat_candidates,
                        map_navigation=map_navigation, weapon_images=self.guns)
        self.last_draw_ms = (perf_counter() - started) * 1000
        pico.update_canvas()

    def close(self):
        self.hud.close()
        self.lighting.close()
        self.layers.clear()
        self.map_images.close()
        self.sprites.clear()
        self.props.clear()
        self.guns.clear()
        self.door = self.vignette = self.tree_canopy = self.tree_trunk = self.truck = self.smoke = None
        self.images.clear()
        self.backend.SDL_ShowCursor(self.backend.SDL_ENABLE)
