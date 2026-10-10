import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from capture_station.sessions import Session


class SessionTests(unittest.TestCase):
    def test_paths_cannot_escape_capture_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                Session(directory, '../../other', 'uuid', {}, 'single')

    def test_failed_session_keeps_saved_images_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(directory, 'test-id', 'uuid', {'width': 640}, 'six_angles')
            image = session.photo_path(60)
            image.write_bytes(b'test')
            session.add(dict(path=str(image), width=640, height=480), 60, {'position': 10})
            session.finish('failed', 'Camera unplugged')
            data = json.loads((session.path / 'session.json').read_text())
            self.assertEqual(data['status'], 'failed')
            self.assertEqual(data['images'][0]['angle_degrees'], 60)
            self.assertTrue(image.exists())
            self.assertFalse(Path(data['images'][0]['original_path']).is_absolute())


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Desktop integration requires a display')
class CaptureIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from capture_station import app
        self.temporary = tempfile.TemporaryDirectory()
        self.patch = patch.object(app, 'DATA', Path(self.temporary.name))
        self.patch.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.app = app.App(self.root)
        self.panel = self.app.capture
        self.panel.device.set('Simulator')
        self.panel.resolution.set('640x480')
        self.panel.settle.set('.2')
        self.panel.toggle_camera()
        self.wait(lambda: self.panel.camera_ready)

    def wait(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if self.errors:
                raise AssertionError(self.errors)
            if predicate():
                return
            time.sleep(.01)
        self.fail(f'Timed out: {self.panel.message.get()}')

    def connect_motor(self):
        self.app.steps.set('60')
        self.app.speed.set('1000')
        self.app.connect()
        self.wait(lambda: self.app.ready and not self.app.pending)
        self.app.send('ZERO')
        self.wait(lambda: not self.app.pending)

    def tearDown(self):
        self.panel.shutdown()
        if self.panel.camera:
            self.panel.camera.thread.join(3)
        if self.app.connection:
            self.app.connection.close()
            self.app.connection.thread.join(3)
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.patch.stop()
        self.temporary.cleanup()

    def test_lighting_toggle_dim_and_capture_lock_without_calibration(self):
        self.app.connect()
        light = self.app.lighting
        self.wait(lambda: light.initialized and light.pending is None)
        self.assertEqual(self.app.state['steps_per_rev'], 0)
        light.toggle.invoke()
        self.wait(lambda: light.pending is None and light.reported['enabled'])
        light.brightness.set(35)
        light.changed('35')
        self.wait(lambda: light.pending is None and light.reported['brightness'] == 35)
        self.assertEqual(light.reported['duty_u16'], round(35 * 65535 / 100))
        self.panel.settle.set('1')
        self.panel.start(False)
        self.wait(lambda: light.toggle.instate(['disabled']))
        self.assertEqual(light.reported['brightness'], 35)
        self.wait(lambda: not self.panel.active)
        self.wait(lambda: not light.toggle.instate(['disabled']))
        light.toggle.invoke()
        self.wait(lambda: light.pending is None and not light.reported['enabled'])
        self.assertEqual(self.app.settings['light_brightness'], 35)

    def test_single_capture_without_motor(self):
        self.panel.start(False)
        self.wait(lambda: not self.panel.active)
        self.assertEqual(self.panel.session.data['status'], 'complete')
        self.assertEqual(len(self.panel.session.data['images']), 1)
        from PIL import Image
        with Image.open(self.panel.session.path / self.panel.session.data['images'][0]['original_path']) as image:
            self.assertEqual(image.size, (640, 480))

    def test_rfid_selects_miniature_and_capture_keeps_original_identity(self):
        self.app.events.put(dict(type='rfid', uid='04AABBCC112233', uid_length=7, atqa='0044', sak='00', technology='ISO14443A'))
        self.wait(lambda: self.app.identity_kind == 'rfid_uid')
        self.assertEqual(self.app.miniature.get(), '04AABBCC112233')
        self.panel.settle.set('.5')
        self.panel.start(False)
        self.app.events.put(dict(type='rfid', uid='DEADBEEF'))
        self.app.events.put(dict(type='rfid_error', message='PN532 disconnected'))
        self.wait(lambda: not self.panel.active)
        self.assertEqual(self.panel.session.data['status'], 'complete')
        self.assertEqual(self.app.miniature.get(), '04AABBCC112233')
        self.assertEqual(self.panel.session.data['miniature_identifier'], '04AABBCC112233')
        self.assertEqual(self.panel.session.data['rfid_tag']['atqa'], '0044')

    def test_invalid_serial_uid_does_not_replace_miniature(self):
        original = self.app.miniature.get()
        self.app.accept_rfid({'uid': '../BAD!!'})
        self.assertEqual(self.app.miniature.get(), original)

    def test_sensor_test_buttons_work_without_motor_calibration(self):
        self.app.connect()
        self.wait(lambda: self.app.ready and not self.app.pending)
        self.app.diagnostics.test_hall()
        self.wait(lambda: not self.app.pending)
        self.assertIn('SIMULATED', self.app.diagnostics.hall.get())
        self.assertIn('do not test physical', self.app.diagnostics.hall_result.get())
        self.app.diagnostics.test_reader()
        self.wait(lambda: not self.app.pending)
        self.assertIn('no physical RFID', self.app.diagnostics.reader.get())

    def test_diagnostics_display_live_hall_and_reader_results(self):
        diagnostics = self.app.diagnostics
        diagnostics.on_event({'sensors': {'hall': dict(raw=1, active=False, transitions=0, toggled=False)}})
        self.assertIn('Not verified', diagnostics.hall_result.get())
        diagnostics.on_event({'sensors': {'hall': dict(raw=0, active=True, transitions=1, toggled=True)}})
        self.assertIn('both observed', diagnostics.hall_result.get())
        diagnostics.on_event({'rfid': dict(status='ready', firmware='1.6')})
        self.assertIn('SPI communication verified', diagnostics.reader.get())
        diagnostics.on_event(dict(type='rfid', uid='DEADBEEF', uid_length=4, atqa='0004', sak='00'))
        self.assertIn('DEADBEEF', diagnostics.tag.get())
        diagnostics.on_event(dict(type='disconnected'))
        self.assertIn('disconnected', diagnostics.reader.get())

    def test_six_angles_and_return_home(self):
        self.connect_motor()
        self.panel.start(True)
        self.wait(lambda: not self.panel.active)
        data = self.panel.session.data
        self.assertEqual(data['status'], 'complete', data)
        self.assertEqual([photo['angle_degrees'] for photo in data['images']], [0, 60, 120, 180, 240, 300])
        self.assertEqual([photo['motor_position']['position'] for photo in data['images']], [0, 10, 20, 30, 40, 50])
        self.assertEqual(self.app.state['angle'], 0)
        self.assertEqual(len(list(self.panel.session.path.glob('*.png'))), 6)

    def test_stop_during_settle_cancels_future_photos(self):
        self.connect_motor()
        self.panel.settle.set('5')
        self.panel.start(True)
        self.wait(lambda: self.panel.stage == 'settling')
        self.app.send('STOP')
        self.wait(lambda: not self.app.pending)
        self.assertFalse(self.panel.active)
        self.assertEqual(self.panel.session.data['status'], 'cancelled')
        self.assertEqual(self.panel.session.data['images'], [])

    def test_camera_failure_aborts_sequence(self):
        self.connect_motor()
        self.panel.start(True)
        self.panel.events.put({'type': 'camera_error', 'message': 'Camera disconnected'})
        self.wait(lambda: not self.panel.active)
        self.assertEqual(self.panel.session.data['status'], 'failed')
        self.assertIn('Camera disconnected', self.panel.session.data['reason'])
