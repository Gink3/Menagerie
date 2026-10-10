import os
import queue
import time
import unittest

from capture_station.connection import Connection
from menagerie_pico.lighting import Lighting


class LightingTests(unittest.TestCase):
    def setUp(self):
        self.levels = []
        self.light = Lighting(self.levels.append)

    def test_start_off_dim_and_toggle_preserves_brightness(self):
        self.assertEqual(self.levels, [0])
        self.light.handle({"enabled": True, "brightness": 25}, 0)
        self.assertEqual(self.levels[-1], 16384)
        self.light.handle({"enabled": False}, 1)
        self.assertEqual(self.levels[-1], 0)
        self.assertEqual(self.light.snapshot()["brightness"], 25)
        self.light.handle({"enabled": True}, 2)
        self.assertEqual(self.levels[-1], 16384)
        self.light.handle({"enabled": True, "brightness": 100}, 3)
        self.assertEqual(self.levels[-1], 65535)
        self.light.handle({"enabled": True, "brightness": 0}, 4)
        self.assertEqual(self.levels[-1], 0)

    def test_independent_watchdog_and_no_automatic_reenable(self):
        self.light.handle({"enabled": True, "brightness": 50}, 0)
        self.light.touch(1000000)
        self.assertFalse(self.light.tick(2500000))
        self.assertTrue(self.light.enabled)
        self.assertTrue(self.light.tick(3000001))
        self.assertEqual(self.levels[-1], 0)
        self.light.touch(4000000)
        self.assertFalse(self.light.enabled)
        self.assertEqual(self.light.brightness, 50)

    def test_invalid_or_busy_commands_do_not_change_output(self):
        self.light.handle({"enabled": True, "brightness": 50}, 0)
        before = self.light.snapshot()
        for request in (
            {"enabled": 1},
            {"enabled": "true"},
            {"enabled": True, "brightness": -1},
            {"enabled": True, "brightness": 101},
            {"enabled": True, "brightness": 1.5},
            {"enabled": True, "brightness": True},
        ):
            with self.assertRaises(ValueError):
                self.light.handle(request, 1)
            self.assertEqual(self.light.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "motion"):
            self.light.handle({"enabled": True, "brightness": 40}, 2, busy=True)
        self.assertEqual(self.light.snapshot(), before)
        # Shutdown can always switch off, including during motor motion.
        self.light.handle({"enabled": False}, 3, busy=True)
        self.assertFalse(self.light.enabled)
        self.assertEqual(self.levels[-1], 0)


class LightingConnectionTests(unittest.TestCase):
    def test_simulator_reports_acknowledged_output(self):
        events = queue.Queue()
        connection = Connection("Simulator", events)
        try:
            connection.send(
                {"id": 99, "cmd": "LIGHT", "enabled": True, "brightness": 37}
            )
            end = time.monotonic() + 3
            response = None
            while time.monotonic() < end:
                event = events.get(timeout=3)
                if event.get("id") == 99:
                    response = event
                    break
            self.assertIsNotNone(response)
            self.assertEqual(response["type"], "done")
            self.assertEqual(response["lighting"]["brightness"], 37)
            self.assertTrue(response["lighting"]["enabled"])
            self.assertTrue(response["lighting"]["simulated"])
        finally:
            connection.close()
            connection.thread.join(2)


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs desktop display")
class LightingUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from capture_station.lighting_ui import LightingPanel

        self.root = tk.Tk()
        self.root.withdraw()
        self.commands = []
        self.settings = {"light_brightness": 42}
        self.busy = False
        self.available = True

        def send(command, **args):
            self.commands.append((command, args))
            return len(self.commands)

        self.panel = LightingPanel(
            self.root,
            send,
            lambda: self.available,
            lambda: self.busy,
            self.settings,
            lambda: None,
        )
        self.addCleanup(self.root.destroy)

    def report(self, enabled=False, brightness=100, ident=0, kind="status"):
        self.panel.on_event(
            {
                "id": ident,
                "type": kind,
                "state": {},
                "lighting": {
                    "enabled": enabled,
                    "brightness": brightness,
                    "simulated": True,
                },
            }
        )

    def test_remembers_only_brightness_then_allows_toggle(self):
        self.assertTrue(self.panel.toggle.instate(["disabled"]))
        self.report(enabled=True)
        self.assertEqual(
            self.commands[-1], ("LIGHT", {"enabled": False, "brightness": 42})
        )
        self.report(brightness=42, ident=1, kind="done")
        self.panel.toggle.invoke()
        self.assertEqual(
            self.commands[-1], ("LIGHT", {"enabled": True, "brightness": 42})
        )
        self.report(enabled=True, brightness=42, ident=2, kind="done")
        self.assertIn("Simulator", self.panel.status.get())
        self.assertEqual(self.settings["light_brightness"], 42)

    def test_capture_lock_cancels_queued_slider_change(self):
        self.report()
        self.report(brightness=42, ident=1, kind="done")
        self.panel.brightness.set(60)
        self.panel.changed("60")
        self.assertIsNotNone(self.panel.timer)
        self.busy = True
        self.panel.update()
        self.assertIsNone(self.panel.timer)
        self.assertTrue(self.panel.slider.instate(["disabled"]))
        self.assertEqual(self.panel.brightness.get(), 42)
        self.panel.apply()
        self.assertEqual(len(self.commands), 1)
        self.busy = False
        self.panel.update()
        self.assertFalse(self.panel.slider.instate(["disabled"]))

    def test_older_firmware_and_disconnect_never_show_light_on(self):
        self.panel.on_event({"type": "status", "state": {}})
        self.assertIn("Update", self.panel.status.get())
        self.assertTrue(self.panel.toggle.instate(["disabled"]))
        self.assertEqual(self.commands, [])
        self.report()
        self.report(enabled=True, brightness=42, ident=1, kind="done")
        self.available = False
        self.panel.on_event({"type": "disconnected"})
        self.assertFalse(self.panel.enabled.get())
        self.assertTrue(self.panel.slider.instate(["disabled"]))
        self.available = True
        self.report(enabled=True, brightness=42)
        self.assertFalse(self.commands[-1][1]["enabled"])
