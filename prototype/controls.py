"""SDL 입력을 상태·일회성 명령으로 변환. 전투와 AI는 SDL을 참조하지 않는다."""

import ctypes
import math
import sdl2

from .map_navigation import MapNavigation, RESPAWN_SIDEBAR


class Controls:
    def __init__(self):
        self.keys = set()
        self.mouse = (640, 360)
        self.firing = False
        self.aiming = False
        self.trigger_pending = False
        self.running = True
        self.paused = False
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
                self.firing = False
                self.aiming = self.trigger_pending = False
                self.paused = True
                self.map_navigation.dragging = False
                self.map_press = None
            elif event.type == sdl2.SDL_KEYDOWN and not event.key.repeat:
                key = event.key.keysym.sym
                self.keys.add(key)
                if key == sdl2.SDLK_ESCAPE:
                    if respawn_selecting:
                        actions.append("respawn_cancel")
                        self.map_press = None
                        self.map_navigation.dragging = False
                    elif self.inventory_open:
                        self.inventory_open = False
                    elif self.intel_open:
                        self.intel_open = self.map_open = False
                    elif self.map_open:
                        self.map_open = False
                        self.map_press = None
                        self.map_navigation.dragging = False
                    else:
                        self.running = False
                elif key == sdl2.SDLK_p:
                    self.paused = not self.paused
                elif key == sdl2.SDLK_F1:
                    self.debug = not self.debug
                elif key in (sdl2.SDLK_F2, sdl2.SDLK_F3):
                    actions.append("restart_ai" if key == sdl2.SDLK_F2 else "next_ai")
                elif key == sdl2.SDLK_F4:
                    actions.append("restock_range")
                elif key in (sdl2.SDLK_F5, sdl2.SDLK_F6, sdl2.SDLK_F8, sdl2.SDLK_F9):
                    actions.append({sdl2.SDLK_F5: "save", sdl2.SDLK_F6: "quick_save",
                                    sdl2.SDLK_F8: "quick_load", sdl2.SDLK_F9: "load"}[key])
                elif key == sdl2.SDLK_m:
                    if not respawn_selecting:
                        self.map_open = not self.map_open
                        self.map_press = None
                        self.map_navigation.dragging = False
                        self.firing = self.aiming = self.trigger_pending = False
                        self.inventory_open = self.intel_open = False
                elif key == sdl2.SDLK_HOME and map_active:
                    self.map_navigation.reset()
                elif key == sdl2.SDLK_h:
                    self.minimap_open = not self.minimap_open
                elif key == sdl2.SDLK_i:
                    actions.append("collect_intel" if event.key.keysym.mod & sdl2.KMOD_SHIFT else "intel")
                elif self.intel_open and key in (sdl2.SDLK_UP, sdl2.SDLK_DOWN):
                    actions.append("intel_previous" if key == sdl2.SDLK_UP else "intel_next")
                elif key == sdl2.SDLK_TAB:
                    actions.append("inventory")
                elif key == sdl2.SDLK_g:
                    actions.append("throw")
                elif self.inventory_open and key in (sdl2.SDLK_UP, sdl2.SDLK_DOWN, sdl2.SDLK_RETURN):
                    actions.append({sdl2.SDLK_UP: "item_previous", sdl2.SDLK_DOWN: "item_next",
                                    sdl2.SDLK_RETURN: "item_use"}[key])
                elif key in (sdl2.SDLK_l, sdl2.SDLK_n, sdl2.SDLK_k):
                    actions.append({sdl2.SDLK_l: "lamp", sdl2.SDLK_n: "night_vision", sdl2.SDLK_k: "ir_laser"}[key])
                elif key == sdl2.SDLK_e and event.key.keysym.mod & sdl2.KMOD_SHIFT:
                    actions.append("facility_alternate")
                elif key in (sdl2.SDLK_LEFT, sdl2.SDLK_RIGHT):
                    actions.append("respawn_previous" if key == sdl2.SDLK_LEFT else "respawn_next")
                elif sdl2.SDLK_1 <= key <= sdl2.SDLK_4:
                    actions.append(f"slot_{key - sdl2.SDLK_1}")
                elif key in (sdl2.SDLK_f, sdl2.SDLK_e, sdl2.SDLK_r, sdl2.SDLK_c,
                             sdl2.SDLK_v, sdl2.SDLK_RETURN):
                    actions.append({sdl2.SDLK_f: "door", sdl2.SDLK_e: "supplies", sdl2.SDLK_r: "reload",
                                    sdl2.SDLK_c: "cancel", sdl2.SDLK_v: "fire_mode", sdl2.SDLK_RETURN: "respawn"}[key])
                elif key in (sdl2.SDLK_EQUALS, sdl2.SDLK_PLUS, sdl2.SDLK_KP_PLUS):
                    if map_active:
                        self.map_navigation.zoom_at(1.2, self.mouse, (camera.width, camera.height),
                                                    sidebar=RESPAWN_SIDEBAR if respawn_selecting else 0)
                    else:
                        camera.set_zoom(camera.magnification * 1.2)
                elif key in (sdl2.SDLK_MINUS, sdl2.SDLK_KP_MINUS):
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
                if event.button.button == sdl2.SDL_BUTTON_LEFT:
                    self.firing = event.type == sdl2.SDL_MOUSEBUTTONDOWN
                    self.trigger_pending |= self.firing
                elif event.button.button == sdl2.SDL_BUTTON_RIGHT:
                    self.aiming = event.type == sdl2.SDL_MOUSEBUTTONDOWN
            elif event.type == sdl2.SDL_MOUSEWHEEL:
                if map_active and event.wheel.y:
                    self.map_navigation.zoom_at(1.2 ** event.wheel.y, self.mouse,
                                                (camera.width, camera.height),
                                                sidebar=RESPAWN_SIDEBAR if respawn_selecting else 0)
                elif self.inventory_open and event.wheel.y:
                    self.inventory_events.append(("wheel", self.mouse, event.wheel.y))
                else:
                    camera.set_zoom(camera.magnification * 1.2 ** event.wheel.y)
        return actions

    @property
    def direction(self):
        return (int(sdl2.SDLK_d in self.keys) - int(sdl2.SDLK_a in self.keys),
                int(sdl2.SDLK_s in self.keys) - int(sdl2.SDLK_w in self.keys))

    @property
    def sprint(self):
        return bool(self.keys & {sdl2.SDLK_LSHIFT, sdl2.SDLK_RSHIFT})

    @property
    def walking(self):
        return bool(self.keys & {sdl2.SDLK_LCTRL, sdl2.SDLK_RCTRL})

    def consume_trigger(self):
        pressed, self.trigger_pending = self.trigger_pending, False
        return pressed
