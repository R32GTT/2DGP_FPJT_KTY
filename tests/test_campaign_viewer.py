import unittest

from campaign.clock import world_clock
from prototype.camera import PlayCamera


class CameraAndClockTests(unittest.TestCase):
    def test_player_camera_preserves_world_screen_inverse_at_each_zoom(self):
        camera = PlayCamera(width=1000, height=700)
        position = (23.0, -12.0)
        for zoom in (0.5, 1.0, 2.0, 4.0):
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

    def test_world_clock_keeps_days(self):
        self.assertEqual(world_clock(86400 + 3661), "D02  01:01:01")
