import unittest
from unittest.mock import Mock

from mini_scan.app import App


class MotorSettingsTests(unittest.TestCase):
    def test_210_pulses_between_magnets_converts_to_full_turn(self):
        app = Mock()
        app.sector_pulses.get.return_value = '210'
        App.use_sector_pulses(app)
        app.steps.set.assert_called_once_with('1260')
        app.fail.assert_not_called()

    def test_apply_sends_updated_calibration_without_reconnecting(self):
        app = Mock()
        app.steps.get.return_value = '1260'
        app.speed.get.return_value = '210'
        app.invert.get.return_value = False
        app.send.return_value = 42
        App.apply_motor_settings(app)
        app.send.assert_called_once_with('CONFIG', steps_per_rev=1260,
                                         speed=210, invert=False, hall=True)
        self.assertEqual(app.config_pending, 42)
        self.assertFalse(app.configured)

    def test_busy_motor_rejects_configuration(self):
        app = Mock(connected=True, config_pending=None, state={'busy': True}, sequence=None)
        with self.assertRaises(ValueError):
            App.motor_idle(app)
