import json
import os
import pty
import queue
import select
import time
import unittest

from capture_station.connection import Connection


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        self.events = queue.Queue()
        self.connection = Connection(os.ttyname(self.slave), self.events)
        deadline = time.monotonic() + 2
        data = b""
        while b"PING" not in data and time.monotonic() < deadline:
            if select.select([self.master], [], [], .1)[0]:
                data += os.read(self.master, 4096)
        self.assertIn(b"PING", data)

    def tearDown(self):
        self.connection.close()
        self.connection.thread.join(2)
        os.close(self.master)
        os.close(self.slave)

    def test_connection_requires_firmware_response(self):
        self.assertTrue(self.events.empty())
        response = json.dumps(dict(id=0, type="status", state=dict(
            busy=False, position=0, enabled=False, known=False, steps_per_rev=0
        ))).encode() + b"\n"
        os.write(self.master, response[:10])
        time.sleep(.02)
        self.assertTrue(self.events.empty())
        os.write(self.master, response[10:])
        self.assertEqual(self.events.get(timeout=2)["type"], "connected")
        self.assertEqual(self.events.get(timeout=2)["type"], "status")

    def test_close_sends_light_off_before_releasing_motor(self):
        self.connection.close()
        self.connection.thread.join(2)
        data = b""
        end = time.monotonic() + 1
        while b"RELEASE" not in data and time.monotonic() < end:
            if select.select([self.master], [], [], .1)[0]:
                data += os.read(self.master, 4096)
        requests = [json.loads(line) for line in data.splitlines()]
        light = next(i for i, r in enumerate(requests) if r.get("cmd") == "LIGHT")
        release = next(i for i, r in enumerate(requests) if r.get("cmd") == "RELEASE")
        self.assertFalse(requests[light]["enabled"])
        self.assertLess(light, release)

    def test_silent_device_has_specific_install_instructions(self):
        error = self.events.get(timeout=4)
        self.assertEqual(error["type"], "connection_error")
        self.assertIn("sent no data", error["message"])
        self.assertIn("firmware/pico/motion.py", error["message"])

    def test_repl_text_is_preserved_even_without_newline(self):
        os.write(self.master, b"MicroPython\r\n>>> ")
        error = self.events.get(timeout=4)
        self.assertEqual(error["type"], "connection_error")
        self.assertIn("MicroPython", error["message"])
        self.assertIn(">>>", error["message"])


if __name__ == "__main__":
    unittest.main()
