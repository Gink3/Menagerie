import queue
import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from capture_station.focus import measure
from capture_station.camera import Camera
from mini_scan.editing import adjust


class FocusTests(unittest.TestCase):
    def test_blur_reduces_score_and_plain_region_is_unknown(self):
        gray = ((np.indices((256,256)).sum(axis=0)//8 % 2)*255).astype(np.uint8)
        sharp = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        blurred = cv2.GaussianBlur(sharp, (15,15), 4)
        original,_ = measure(sharp, [0,0,1,1])
        soft,_ = measure(blurred, [0,0,1,1])
        self.assertGreater(original['score'],soft['score']*2)
        sharp[:128] = 100
        plain,_ = measure(sharp, [0,0,1,.5])
        self.assertIsNone(plain['score'])
        self.assertTrue(plain['low_texture'])

    def test_simulator_saves_actual_capture_settings(self):
        events = queue.Queue()
        with tempfile.TemporaryDirectory() as directory:
            camera = Camera('Simulator', (320,240),events)
            try:
                camera.capture('token',Path(directory)/'photo.png',metadata={'lighting':{'brightness':35}})
                while True:
                    event = events.get(timeout=5)
                    self.assertNotEqual(event['type'],'camera_error',event)
                    if event['type']=='photo_saved':
                        break
                self.assertEqual(event['capture']['lighting']['brightness'],35)
                self.assertTrue(event['capture']['simulated_camera'])
                self.assertEqual(event['capture']['width'],320)
                self.assertIn('roi', event['capture']['focus'])
            finally:
                camera.close()
                camera.thread.join(3)

    def test_edits_preserve_input_and_validate_limits(self):
        original = Image.new('RGB',(20,20),(40,40,40))
        edited = adjust(original,exposure=1)
        self.assertEqual(original.getpixel((0,0)),(40,40,40))
        self.assertEqual(edited.getpixel((0,0)),(80,80,80))
        with self.assertRaises(ValueError):
            adjust(original,exposure=float('nan'))
