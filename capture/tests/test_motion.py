import unittest

from menagerie_pico.motion import Motion


class Hardware:
    def __init__(self):
        self.position = 0
        self.enabled = False
        self.sensor = lambda: self.position % 200 < 3

    def enable(self, enabled):
        self.enabled = enabled

    def step(self, direction, invert):
        self.position += direction

    def hall_active(self):
        return self.sensor()


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.hw = Hardware()
        self.events = []
        self.motor = Motion(self.hw, self.events.append)
        self.now = 0
        self.ident = 0
        self.command("CONFIG", steps_per_rev=200, speed=1000, hall=True)

    def command(self, cmd, **kwargs):
        self.ident += 1
        self.motor.handle(dict(id=self.ident, cmd=cmd, **kwargs), self.now)

    def finish(self):
        for _ in range(20000):
            self.now += 1000
            if self.now % 500000 == 0:
                self.motor.handle(dict(id=0, cmd="PING"), self.now)
            self.motor.tick(self.now)
            if self.motor.operation is None:
                return
        self.fail("Motion did not finish")

    def test_six_angles_complete_exact_revolution_and_reverse(self):
        self.command("ZERO")
        for direction in (60, -60):
            for _ in range(6):
                self.command("ROTATE", degrees=direction)
                self.finish()
            self.assertEqual(self.motor.position, 200 if direction == 60 else 0)

    def test_210_pulses_per_60_degree_interval(self):
        self.command("CONFIG", steps_per_rev=1260, speed=210, hall=True)
        self.command("ZERO")
        for index in range(1, 7):
            self.command("ROTATE", degrees=60)
            self.finish()
            self.assertEqual(self.motor.position, index * 210)

    def test_return_uses_nearest_equivalent_home(self):
        self.command("ZERO")
        self.command("JOG", steps=170)
        self.finish()
        self.command("RETURN")
        self.finish()
        self.assertEqual(self.motor.position, 200)
        self.assertEqual(self.motor.snapshot()["angle"], 0)

    def test_stop_interrupts_and_clears_reference(self):
        self.command("ZERO")
        self.command("JOG", steps=500)
        self.motor.tick(1000)
        self.command("STOP")
        stopped = self.hw.position
        self.motor.tick(100000)
        self.assertEqual(self.hw.position, stopped)
        self.assertFalse(self.motor.known)
        self.command("RETURN")
        self.assertEqual(self.events[-1]["type"], "error")

    def test_watchdog_releases_even_while_holding(self):
        self.command("ZERO")
        self.motor.tick(2000001)
        self.assertFalse(self.hw.enabled)
        self.assertFalse(self.motor.known)

    def test_homing_clears_active_sensor_then_finds_edge(self):
        self.command("HOME")
        self.assertEqual(self.motor.phase, "clear")
        self.finish()
        self.assertTrue(self.motor.known)
        self.assertEqual(self.motor.position, 0)
        self.assertLess(self.hw.position, -190)

    def test_missing_and_stuck_sensor_are_bounded(self):
        for active in (False, True):
            self.hw.sensor = lambda: active
            self.command("HOME")
            self.finish()
            self.assertFalse(self.motor.known)
            self.assertFalse(self.hw.enabled)
            self.assertEqual(self.events[-1]["type"], "error")

    def test_uncalibrated_rotation_rejected_but_jog_allowed(self):
        self.command("CONFIG", steps_per_rev=0, speed=200)
        self.command("ROTATE", degrees=60)
        self.assertEqual(self.events[-1]["type"], "error")
        self.command("JOG", steps=-10)
        self.finish()
        self.assertEqual(self.hw.position, -10)

    def test_busy_rejects_second_move(self):
        self.command("JOG", steps=10)
        self.command("JOG", steps=100)
        self.assertEqual(self.events[-1]["type"], "error")
        self.finish()
        self.assertEqual(self.hw.position, 10)

    def test_reconfigure_direction_invalidates_home(self):
        self.command("ZERO")
        self.command("CONFIG", steps_per_rev=200, speed=200, invert=True)
        self.assertFalse(self.motor.known)


if __name__ == "__main__":
    unittest.main()
