"""SDL 입력을 상태·일회성 명령으로 변환. 전투와 AI는 SDL을 참조하지 않는다."""

import ctypes
import math
import sdl2

from .map_navigation import MapNavigation, RESPAWN_SIDEBAR
from .settings import DEFAULT_BINDINGS, input_mods, load_settings


KEYPAD_ZOOM = {sdl2.SDLK_KP_PLUS: "zoom_in", sdl2.SDLK_KP_MINUS: "zoom_out"}


class Controls:
    def __init__(self, settings=None):
        self.settings = settings or load_settings()
        self.keys = set()
        self.buttons = set()
        self.mouse = (640, 360)
        self.firing = False
        self.aiming = False
        self.trigger_pending = False
        self.running = True
        self.paused = False
        self.pause_requested = False
        self.pause_page = "menu"
        self.debug = False
        self.map_open = False
        self.map_navigation = MapNavigation()
        self.map_press = None
        self.map_clicks = []
        self.minimap_open = True
        self.inventory_open = False
        self.inventory_events = []
        self.intel_open = False

    def poll(self, camera, *, respawn_selecting=False):
        actions = []
        self.inventory_events.clear()
        self.map_clicks.clear()
        event = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(event)):
            map_active = self.map_open or respawn_selecting
            if event.type == sdl2.SDL_QUIT:
                self.running = False
            elif event.type == sdl2.SDL_WINDOWEVENT and event.window.event == sdl2.SDL_WINDOWEVENT_FOCUS_LOST:
                self.keys.clear()
                self.buttons.clear()
                self.firing = False
                self.aiming = self.trigger_pending = False
                self.paused = True
                self.map_navigation.dragging = False
                self.map_press = None
            elif event.type == sdl2.SDL_KEYDOWN and not event.key.repeat:
                key = event.key.keysym.sym
                self.keys.add(key)
                command = self.settings.action_for_key(key, input_mods(event.key.keysym.mod))
                if command is None:
                    alias = KEYPAD_ZOOM.get(key)
                    if alias is not None and self.settings.bindings[alias] == DEFAULT_BINDINGS[alias]:
                        command = alias
                if key == sdl2.SDLK_ESCAPE:
                    self.pause_requested = True
                    self.pause_page = "menu"
                    self.firing = self.aiming = self.trigger_pending = False
                elif key == sdl2.SDLK_F1:
                    self.debug = not self.debug
                elif key in (sdl2.SDLK_F2, sdl2.SDLK_F3):
                    actions.append("restart_ai" if key == sdl2.SDLK_F2 else "next_ai")
                elif key == sdl2.SDLK_F4:
                    actions.append("restock_range")
                elif command in ("save", "load"):
                    self.pause_requested = True
                    self.pause_page = command
                    self.firing = self.aiming = self.trigger_pending = False
                elif command in ("quick_save", "quick_load"):
                    actions.append(command)
                elif command == "map":
                    if not respawn_selecting:
                        self.map_open = not self.map_open
                        self.map_press = None
                        self.map_navigation.dragging = False
                        self.firing = self.aiming = self.trigger_pending = False
                        self.inventory_open = self.intel_open = False
                elif key == sdl2.SDLK_HOME and map_active:
                    self.map_navigation.reset()
                elif command == "minimap":
                    self.minimap_open = not self.minimap_open
                elif command in ("intel", "collect_intel"):
                    actions.append(command)
                elif self.intel_open and key in (sdl2.SDLK_UP, sdl2.SDLK_DOWN):
                    actions.append("intel_previous" if key == sdl2.SDLK_UP else "intel_next")
                elif command == "inventory":
                    actions.append("inventory")
                elif self.inventory_open and key in (sdl2.SDLK_UP, sdl2.SDLK_DOWN, sdl2.SDLK_RETURN):
                    actions.append({sdl2.SDLK_UP: "item_previous", sdl2.SDLK_DOWN: "item_next",
                                    sdl2.SDLK_RETURN: "item_use"}[key])
                elif command in ("lamp", "night_vision", "ir_laser", "facility_alternate"):
                    actions.append(command)
                elif key in (sdl2.SDLK_LEFT, sdl2.SDLK_RIGHT):
                    actions.append("respawn_previous" if key == sdl2.SDLK_LEFT else "respawn_next")
                elif command and command.startswith("weapon_"):
                    actions.append(f"slot_{int(command[-1]) - 1}")
                elif command in ("door", "supplies", "reload", "cancel", "fire_mode",
                                 "respawn", "throw"):
                    actions.append(command)
                elif command == "fire" and not (map_active or self.inventory_open or self.intel_open):
                    self.trigger_pending = True
                elif command == "zoom_in":
                    if map_active:
                        self.map_navigation.zoom_at(1.2, self.mouse, (camera.width, camera.height),
                                                    sidebar=RESPAWN_SIDEBAR if respawn_selecting else 0)
                    else:
                        camera.set_zoom(camera.magnification * 1.2)
                elif command == "zoom_out":
                    if map_active:
                        self.map_navigation.zoom_at(1 / 1.2, self.mouse, (camera.width, camera.height),
                                                    sidebar=RESPAWN_SIDEBAR if respawn_selecting else 0)
                    else:
                        camera.set_zoom(camera.magnification / 1.2)
            elif event.type == sdl2.SDL_KEYUP:
                self.keys.discard(event.key.keysym.sym)
            elif event.type == sdl2.SDL_MOUSEMOTION:
                self.mouse = (event.motion.x, event.motion.y)
                if map_active and self.map_press is not None:
                    if not self.map_navigation.dragging and math.dist(self.mouse, self.map_press) >= 5:
                        self.map_navigation.dragging = True
                    if self.map_navigation.dragging:
                        self.map_navigation.move(event.motion.xrel, event.motion.yrel)
            elif event.type in (sdl2.SDL_MOUSEBUTTONDOWN, sdl2.SDL_MOUSEBUTTONUP):
                if event.type == sdl2.SDL_MOUSEBUTTONDOWN:
                    self.buttons.add(event.button.button)
                else:
                    self.buttons.discard(event.button.button)
                if map_active:
                    if event.button.button == sdl2.SDL_BUTTON_LEFT:
                        if event.type == sdl2.SDL_MOUSEBUTTONDOWN:
                            self.map_press = (event.button.x, event.button.y)
                            self.map_navigation.dragging = False
                        else:
                            if self.map_press is not None and not self.map_navigation.dragging:
                                self.map_clicks.append((event.button.x, event.button.y))
                            self.map_press = None
                            self.map_navigation.dragging = False
                        self.firing = False
                    elif event.button.button == sdl2.SDL_BUTTON_RIGHT:
                        self.aiming = False
                    continue
                if self.inventory_open:
                    if event.button.button == sdl2.SDL_BUTTON_LEFT:
                        self.inventory_events.append(("down" if event.type == sdl2.SDL_MOUSEBUTTONDOWN else "up",
                                                      (event.button.x, event.button.y),
                                                      bool(sdl2.SDL_GetModState() & sdl2.KMOD_SHIFT)))
                    continue
                if (self.settings.action_for_mouse(event.button.button) == "fire"
                        and event.type == sdl2.SDL_MOUSEBUTTONDOWN and not self.intel_open):
                    self.trigger_pending = True
            elif event.type == sdl2.SDL_MOUSEWHEEL:
                if map_active and event.wheel.y:
                    self.map_navigation.zoom_at(1.2 ** event.wheel.y, self.mouse,
                                                (camera.width, camera.height),
                                                sidebar=RESPAWN_SIDEBAR if respawn_selecting else 0)
                elif self.inventory_open and event.wheel.y:
                    self.inventory_events.append(("wheel", self.mouse, event.wheel.y))
                else:
                    camera.set_zoom(camera.magnification * 1.2 ** event.wheel.y)
        interface_open = self.map_open or self.inventory_open or self.intel_open or respawn_selecting
        self.firing = not interface_open and self.settings.held("fire", self.keys, self.buttons)
        self.aiming = not interface_open and self.settings.held("aim", self.keys, self.buttons)
        return actions

    @property
    def direction(self):
        return (int(self.settings.held("move_right", self.keys, self.buttons))
                - int(self.settings.held("move_left", self.keys, self.buttons)),
                int(self.settings.held("move_down", self.keys, self.buttons))
                - int(self.settings.held("move_up", self.keys, self.buttons)))

    @property
    def sprint(self):
        return self.settings.held("sprint", self.keys, self.buttons)

    @property
    def walking(self):
        return self.settings.held("walk", self.keys, self.buttons)

    def consume_trigger(self):
        pressed, self.trigger_pending = self.trigger_pending, False
        return pressed
