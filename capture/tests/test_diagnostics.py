import unittest
from unittest.mock import patch

from menagerie_pico.diagnostics import HallMonitor
from menagerie_pico.pn532 import Reader
from tools.flash_pico import command, choose_port, FILES
from test_rfid import FakeBus


class SensorTests(unittest.TestCase):
    def test_unplugged_high_is_not_a_pass(self):
        monitor = HallMonitor(lambda: False)
        monitor.tick(10000)
        self.assertEqual(monitor.snapshot()['raw'], 1)
        self.assertFalse(monitor.snapshot()['toggled'])

    def test_magnet_changes_are_counted_and_reset(self):
        active = [False]
        monitor = HallMonitor(lambda: active[0])
        active[0] = True
        monitor.tick(10000)
        self.assertEqual(monitor.snapshot()['raw'], 0)
        self.assertTrue(monitor.snapshot()['toggled'])
        active[0] = False
        monitor.tick(20000)
        self.assertEqual(monitor.transitions, 2)
        monitor.reset()
        self.assertEqual(monitor.transitions, 0)
        self.assertFalse(monitor.snapshot()['toggled'])

    def test_rfid_test_clears_old_success_and_queries_firmware_again(self):
        bus = FakeBus()
        reader = Reader(bus, lambda event: None)
        for now in range(0, 500000, 5000):
            reader.tick(now)
        self.assertEqual(reader.status, 'ready')
        reader.restart()
        self.assertIsNone(reader.version)
        self.assertIsNone(reader.tag)
        self.assertEqual(reader.status, 'starting')
        bus.respond = False
        for now in range(500000, 1800000, 5000):
            reader.tick(now)
        self.assertEqual(reader.status, 'error')


class UploadTests(unittest.TestCase):
    def test_upload_includes_all_dependencies_and_verifies_before_reset(self):
        args = command('/dev/test')
        self.assertEqual(args[3:5], ['connect', '/dev/test'])
        self.assertEqual(FILES[-1], 'main.py')
        self.assertIn('lighting.py', FILES)
        self.assertEqual(len(FILES), 6)
        self.assertIn('Pin(22, Pin.OUT, value=0)', args[6])
        for name in FILES:
            self.assertIn(':' + name, args)
        self.assertEqual(args[-1], 'reset')
        self.assertIn('hashlib.sha256', args[-3])
        self.assertIn('import motion, pn532, rfid_config, diagnostics', args[-3])
        self.assertIn('sys.modules.pop', args[-3])
        self.assertIn('lighting.Lighting', args[-3])

    def test_ambiguous_devices_are_not_selected(self):
        from types import SimpleNamespace
        port = SimpleNamespace(vid=0x2E8A, pid=5, device='/dev/ttyACM0')
        with patch('serial.tools.list_ports.comports', return_value=[port, port]):
            with self.assertRaises(ValueError):
                choose_port()
        self.assertEqual(choose_port('/dev/explicit'), '/dev/explicit')
