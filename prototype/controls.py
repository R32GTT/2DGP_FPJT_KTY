"""SDL 입력을 상태·일회성 명령으로 변환. 전투와 AI는 SDL을 참조하지 않는다."""

import ctypes
import sdl2


class Controls:
    def __init__(self):
        self.keys = set()
        self.mouse = (640, 360)
        self.firing = False
        self.running = True
        self.paused = False
        self.debug = False

    def poll(self, camera):
        actions = []
        event = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(event)):
            if event.type == sdl2.SDL_QUIT:
                self.running = False
            elif event.type == sdl2.SDL_WINDOWEVENT and event.window.event == sdl2.SDL_WINDOWEVENT_FOCUS_LOST:
                self.keys.clear()
                self.firing = False
                self.paused = True
            elif event.type == sdl2.SDL_KEYDOWN and not event.key.repeat:
                key = event.key.keysym.sym
                self.keys.add(key)
                if key == sdl2.SDLK_ESCAPE:
                    self.running = False
                elif key == sdl2.SDLK_p:
                    self.paused = not self.paused
                elif key == sdl2.SDLK_F1:
                    self.debug = not self.debug
                elif key in (sdl2.SDLK_f, sdl2.SDLK_e):
                    actions.append("door" if key == sdl2.SDLK_f else "supplies")
                elif key in (sdl2.SDLK_EQUALS, sdl2.SDLK_PLUS, sdl2.SDLK_KP_PLUS):
                    camera.set_zoom(camera.magnification * 1.2)
                elif key in (sdl2.SDLK_MINUS, sdl2.SDLK_KP_MINUS):
                    camera.set_zoom(camera.magnification / 1.2)
            elif event.type == sdl2.SDL_KEYUP:
                self.keys.discard(event.key.keysym.sym)
            elif event.type == sdl2.SDL_MOUSEMOTION:
                self.mouse = (event.motion.x, event.motion.y)
            elif event.type in (sdl2.SDL_MOUSEBUTTONDOWN, sdl2.SDL_MOUSEBUTTONUP):
                if event.button.button == sdl2.SDL_BUTTON_LEFT:
                    self.firing = event.type == sdl2.SDL_MOUSEBUTTONDOWN
            elif event.type == sdl2.SDL_MOUSEWHEEL:
                camera.set_zoom(camera.magnification * 1.2 ** event.wheel.y)
        return actions

    @property
    def direction(self):
        return (int(sdl2.SDLK_d in self.keys) - int(sdl2.SDLK_a in self.keys),
                int(sdl2.SDLK_s in self.keys) - int(sdl2.SDLK_w in self.keys))

    @property
    def sprint(self):
        return bool(self.keys & {sdl2.SDLK_LSHIFT, sdl2.SDLK_RSHIFT})
