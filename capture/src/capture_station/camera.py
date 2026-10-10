"""Camera ownership, frame acquisition and image encoding stay off the Tk thread."""
from pathlib import Path
import queue
import threading
import time

import cv2
import numpy as np
from PIL import Image
from datetime import datetime, timezone
from .focus import measure


def devices():
    found = {'Simulator': 'Simulator'}
    stable = {p.resolve(): str(p) for p in Path('/dev/v4l/by-id').glob('*-index0')}
    for entry in sorted(Path('/sys/class/video4linux').glob('video*')):
        if (entry / 'index').read_text().strip() != '0':
            continue
        device = Path('/dev') / entry.name
        label = f"{(entry / 'name').read_text().strip()} ({device})"
        found[label] = stable.get(device, str(device))
    return found


class Camera:
    def __init__(self, device, size, events):
        self.device, self.size, self.events = device, size, events
        self.requests = queue.Queue()
        self.closing = threading.Event()
        self.lock = threading.Lock()
        self.preview = None
        self.focus = None
        self.focus_roi = (0.4, 0.4, 0.6, 0.6)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def capture(self, token, path, metadata=None):
        self.requests.put((token, Path(path), metadata or {}))

    def set_focus_roi(self, roi):
        with self.lock:
            self.focus_roi = tuple(roi)
            self.focus = None

    def get_focus(self):
        with self.lock:
            return self.focus

    def close(self):
        self.closing.set()

    def get_preview(self):
        with self.lock:
            return self.preview

    def run(self):
        cap = None
        try:
            simulated = self.device == 'Simulator'
            if not simulated:
                cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
                if not cap.isOpened():
                    raise RuntimeError('Cannot open camera. Close other camera applications and check the selected device.')
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
                cap.set(cv2.CAP_PROP_FPS, 15)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            pending = None
            discard = 0
            announced = False
            number = 0
            next_focus = 0
            while not self.closing.is_set():
                if pending is None:
                    try:
                        pending = self.requests.get_nowait()
                        # Consume fresh frames after the request, never save the preview cache.
                        discard = 3
                    except queue.Empty:
                        pass
                if simulated:
                    frame = np.zeros((self.size[1], self.size[0], 3), dtype=np.uint8)
                    frame[:] = (65, 45, 30)
                    cv2.putText(frame, 'SIMULATED CAMERA', (30, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (220, 220, 220), 2)
                    cv2.putText(frame, f'Frame {number}', (30, 150), cv2.FONT_HERSHEY_SIMPLEX, 1, (180, 220, 180), 2)
                    self.closing.wait(.067)
                else:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        raise RuntimeError('Camera stopped delivering frames. Reconnect the camera.')
                if self.closing.is_set():
                    break
                number += 1
                height, width = frame.shape[:2]
                if not announced:
                    self.events.put({'type': 'camera_ready', 'width': width, 'height': height, 'device': self.device})
                    announced = True
                scale = min(700 / width, 420 / height, 1)
                small = cv2.resize(frame, (int(width * scale), int(height * scale)))
                preview = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
                with self.lock:
                    self.preview = (number, preview)
                if time.monotonic() >= next_focus:
                    with self.lock:
                        roi = self.focus_roi
                    data, crop = measure(frame, roi)
                    focus_crop = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                    with self.lock:
                        self.focus = (number, data, focus_crop)
                    next_focus = time.monotonic() + .15
                if pending is not None:
                    if discard:
                        discard -= 1
                    else:
                        token, path, metadata = pending
                        with self.lock:
                            roi = self.focus_roi
                        focus, _ = measure(frame, roi)
                        metadata = dict(metadata, version=1, captured_at=datetime.now(timezone.utc).isoformat(),
                                        camera=str(self.device), width=width, height=height,
                                        simulated_camera=simulated, focus=focus)
                        temporary = path.with_suffix('.tmp.png')
                        try:
                            if not cv2.imwrite(str(temporary), frame):
                                raise OSError('Image encoder failed')
                            temporary.replace(path)
                        except Exception:
                            temporary.unlink(missing_ok=True)
                            raise
                        self.events.put({'type': 'photo_saved', 'token': token, 'path': str(path), 'width': width, 'height': height, 'capture': metadata})
                        pending = None
        except Exception as error:
            self.events.put({'type': 'camera_error', 'message': str(error)})
        finally:
            if cap is not None:
                cap.release()
            self.events.put({'type': 'camera_closed'})
