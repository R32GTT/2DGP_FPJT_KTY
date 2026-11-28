import unittest

from campaign.clock import world_clock
from prototype.camera import PlayCamera


class CameraAndClockTests(unittest.TestCase):
    def test_player_camera_preserves_world_screen_inverse_at_each_zoom(self):
        camera = PlayCamera(width=1000, height=700)
        position = (23.0, -12.0)
        for zoom in (0.5, 0.55, 1.0, 2.0, 4.0):
            with self.subTest(zoom=zoom):
                camera.set_zoom(zoom)
                camera.follow(position, (750, 200))
                self.assertEqual(camera.magnification, zoom)
                for point in (position, (0, 0), (-17.5, 43.25)):
                    screen = camera.screen(*point)
                    restored = camera.world(*screen)
                    self.assertAlmostEqual(restored[0], point[0])
                    self.assertAlmostEqual(restored[1], point[1])
                    pico = camera.pico(*point)
                    self.assertEqual(pico, (screen[0], camera.height - screen[1]))
                viewport = camera.viewport()
                self.assertAlmostEqual(viewport.w, camera.width / camera.scale)
                self.assertAlmostEqual(viewport.h, camera.height / camera.scale)
                self.assertTrue(camera.visible(viewport))

    def test_field_scale_and_zoom_limits(self):
        camera = PlayCamera()
        self.assertEqual(camera.scale, 96)
        self.assertEqual(camera.magnification, 1.0)
        self.assertAlmostEqual(camera.viewport().w, 1280 / 96)
        self.assertAlmostEqual(camera.viewport().h, 7.5)
        camera.set_zoom(0.1)
        self.assertEqual(camera.magnification, 0.5)
        camera.set_zoom(5)
        self.assertEqual(camera.magnification, 4.0)

    def test_mouse_look_reaches_its_full_range_in_normal_and_aim_modes(self):
        camera = PlayCamera(width=1600, height=1080, zoom=0.5)
        position = (23.0, -12.0)
        mouse = (camera.width, camera.height)
        for aiming, expected in ((False, (4.8, 8.4)), (True, (14.4, 14.4))):
            with self.subTest(aiming=aiming):
                camera.follow(position, mouse, aiming=aiming)
                self.assertAlmostEqual(camera.look_x, expected[0])
                self.assertAlmostEqual(camera.look_y, expected[1])
                self.assertAlmostEqual(camera.world(camera.width / 2, camera.height / 2)[0] - position[0], expected[0])
                self.assertAlmostEqual(camera.world(camera.width / 2, camera.height / 2)[1] - position[1], expected[1])

        camera = PlayCamera()
        camera.follow((0, 0), (camera.width, camera.height))
        self.assertAlmostEqual(camera.look_x, 3.2)
        self.assertAlmostEqual(camera.look_y, 8.4)
        camera.follow((0, 0), (camera.width, camera.height), aiming=True, look_multiplier=1.5)
        self.assertAlmostEqual(camera.world(camera.width / 2, camera.height / 2)[0], 21.6)
        self.assertAlmostEqual(camera.world(camera.width / 2, camera.height / 2)[1], 21.6)
        camera.set_zoom(4)
        camera.recenter((0, 0))
        self.assertAlmostEqual(camera.world(camera.width / 2, camera.height / 2)[0], 21.6)
        self.assertAlmostEqual(camera.world(camera.width / 2, camera.height / 2)[1], 21.6)

    def test_aimed_view_can_show_a_guard_at_the_sight_range(self):
        camera = PlayCamera()
        middle_x, middle_y = camera.width / 2, camera.height / 2
        for mouse, point in (((camera.width, middle_y), (16, 0)), ((0, middle_y), (-16, 0)),
                             ((middle_x, camera.height), (0, 16)), ((middle_x, 0), (0, -16))):
            with self.subTest(point=point):
                camera.follow((0, 0), mouse, aiming=True)
                x, y = camera.screen(*point)
                self.assertTrue(0 <= x <= camera.width and 0 <= y <= camera.height)

    def test_world_clock_keeps_days(self):
        self.assertEqual(world_clock(86400 + 3661), "D02  01:01:01")
