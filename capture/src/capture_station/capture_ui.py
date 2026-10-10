"""Desktop camera preview and asynchronous capture sequencing."""
from pathlib import Path
import queue
import subprocess
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import uuid

from PIL import Image, ImageTk
from .camera import Camera, devices
from .sessions import Session
from .focus_ui import FocusPanel


class CapturePanel:
    def __init__(self, parent, app, data):
        self.app = app
        self.root = app.root
        self.camera = None
        self.camera_ready = False
        self.camera_info = {}
        self.events = queue.Queue()
        self.active = False
        self.session = None
        self.waiting_motor = None
        self.photo_token = None
        self.stage = ''
        self.preview_number = None
        self.closing = False
        self.device = tk.StringVar()
        self.resolution = tk.StringVar(value=app.settings.get('resolution', '3264x2448'))
        self.output = tk.StringVar(value=app.settings.get('capture_directory', str(data / 'captures')))
        self.settle = tk.StringVar(value=str(app.settings.get('settle_seconds', 1.0)))
        self.message = tk.StringVar(value='Select a camera and open the preview. Focus the lens manually.')
        identity_row = ttk.Frame(parent)
        identity_row.pack(fill='x', pady=(0, 6))
        ttk.Label(identity_row, text='Miniature: ').pack(side='left')
        ttk.Label(identity_row, textvariable=app.miniature).pack(side='left')
        top = ttk.Frame(parent)
        top.pack(fill='x')
        self.open_button = ttk.Button(top, text='Open camera', command=self.toggle_camera)
        self.open_button.pack(side='right')
        self.refresh_button = ttk.Button(top, text='Refresh', command=self.refresh)
        self.refresh_button.pack(side='right', padx=5)
        self.selector = ttk.Combobox(top, textvariable=self.device, state='readonly', width=46)
        self.selector.pack(side='left', fill='x', expand=True)
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=7)
        ttk.Label(row, text='Requested size').pack(side='left')
        self.size_box = ttk.Combobox(row, textvariable=self.resolution, width=13, state='readonly', values=['3264x2448', '2592x1944', '2048x1536', '1600x1200', '1280x960', '1024x768', '640x480'])
        self.size_box.pack(side='left', padx=5)
        ttk.Label(row, text='Settle seconds').pack(side='left', padx=(15, 5))
        self.settle_box = ttk.Spinbox(row, from_=0.2, to=30, increment=.2, textvariable=self.settle, width=6)
        self.settle_box.pack(side='left')
        self.actual = ttk.Label(parent, text='Camera closed')
        self.actual.pack(anchor='w')
        self.preview = ttk.Label(parent, text='Camera preview', anchor='center')
        self.preview.pack(fill='both', expand=True)
        self.focus = FocusPanel(parent, self.preview, lambda: self.camera,
                                lambda: app.lighting.capture_settings() if hasattr(app, 'lighting') else None)
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=6)
        ttk.Label(row, text='Save to').pack(side='left')
        self.output_entry = ttk.Entry(row, textvariable=self.output)
        self.output_entry.pack(side='left', fill='x', expand=True, padx=5)
        self.browse_button = ttk.Button(row, text='Browse', command=self.browse)
        self.browse_button.pack(side='left')
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=5)
        self.single_button = ttk.Button(row, text='Capture photo', command=lambda: self.start(False))
        self.single_button.pack(side='left')
        self.sequence_button = ttk.Button(row, text='Capture six angles', command=lambda: self.start(True))
        self.sequence_button.pack(side='left', padx=6)
        self.cancel_button = ttk.Button(row, text='Cancel capture / Stop', command=self.cancel)
        self.cancel_button.pack(side='left')
        ttk.Button(parent, text='Open session folder', command=self.open_folder).pack(anchor='w', pady=4)
        ttk.Label(parent, textvariable=self.message, wraplength=720).pack(anchor='w', pady=4)
        self.photos = ttk.Treeview(parent, columns=('angle', 'size', 'file'), show='headings', height=4)
        for key, label, width in [('angle', 'Angle', 80), ('size', 'Resolution', 120), ('file', 'Saved original (double-click to review)', 450)]:
            self.photos.heading(key, text=label)
            self.photos.column(key, width=width)
        self.photos.pack(fill='x')
        self.photos.bind('<Double-1>', self.review)
        self.refresh()
        self.root.after(70, self.poll)

    def refresh(self):
        self.device_map = devices()
        self.selector['values'] = list(self.device_map)
        if self.device.get() not in self.device_map:
            self.device.set(next((name for name in self.device_map if 'HD USB' in name), 'Simulator'))

    def browse(self):
        chosen = filedialog.askdirectory(initialdir=str(Path(self.output.get()).expanduser()))
        if chosen:
            self.output.set(chosen)

    def toggle_camera(self):
        if self.camera:
            self.cancel()
            self.camera_ready = False
            self.camera.close()
            self.open_button.configure(state='disabled')
        else:
            try:
                width, height = map(int, self.resolution.get().split('x'))
                device = self.device_map[self.device.get()]
            except (ValueError, KeyError):
                return
            self.camera = Camera(device, (width, height), self.events)
            self.preview_number = None
            self.open_button.configure(text='Close camera')
            self.message.set('Opening camera…')
        self.update_controls()

    def start(self, sequence):
        if self.active or not self.camera_ready:
            return
        if self.app.pending or self.app.state.get('busy'):
            self.message.set('Wait for the motor to finish before capturing.')
            return
        if sequence and not (self.app.ready and self.app.state.get('steps_per_rev') and self.app.state.get('known')):
            self.message.set('Calibrate the motor and establish home on the Motor tab first.')
            return
        try:
            delay = float(self.settle.get())
            if not .2 <= delay <= 30:
                raise ValueError('Settle time must be between 0.2 and 30 seconds.')
            if not self.output.get().strip():
                raise ValueError('Choose a folder for captures.')
            identifier = self.app.miniature.get().strip()
            if self.app.identity_kind == 'uuid':
                try:
                    uuid.UUID(identifier)
                except ValueError:
                    raise ValueError('Choose New UUID or enter a tag and click Use RFID UID on the Motor tab.')
            self.session = Session(self.output.get(), self.app.miniature.get(), self.app.identity_kind,
                                   dict(self.camera_info), 'six_angles' if sequence else 'single')
            self.session.data['motor'] = dict(self.app.state)
            tag = self.app.rfid_tag
            if self.app.identity_kind == 'rfid_uid' and isinstance(tag, dict) and tag.get('uid') == self.app.miniature.get():
                self.session.data['rfid_tag'] = dict(tag)
            self.session.data['settle_seconds'] = delay
            self.session.data['simulated_motor'] = self.app.port.get() == 'Simulator'
            self.session.write()
        except (ValueError, OSError) as error:
            self.message.set(str(error))
            return
        self.app.settings.update(capture_directory=self.output.get(), resolution=self.resolution.get(), settle_seconds=delay)
        self.app.save()
        self.delay = delay
        self.sequence = sequence
        self.angle = 0 if sequence else None
        self.index = 0
        self.active = True
        self.photos.delete(*self.photos.get_children())
        if sequence:
            self.stage = 'homing'
            self.move('HOME' if self.app.settings.get('hall') else 'RETURN')
        else:
            self.begin_settle()
        self.update_controls()
        self.app.update_controls()

    def move(self, command, **args):
        self.waiting_motor = self.app.send(command, _capture=True, **args)
        speed = max(20, self.app.state.get('speed', 200))
        self.deadline = time.monotonic() + max(125, self.app.state.get('steps_per_rev', 0) / speed * 2 + 10)
        self.message.set('Returning home…' if command in ('RETURN', 'HOME') else f'Moving to {self.angle}°…')

    def on_motor_event(self, event):
        if not self.active:
            return
        kind = event.get('type')
        if kind in ('error', 'connection_error', 'disconnected'):
            self.fail(event.get('message') or 'Motor disconnected')
        elif kind == 'done' and event.get('id') == self.waiting_motor:
            self.waiting_motor = None
            if self.stage == 'returning':
                self.complete()
            else:
                self.begin_settle()

    def begin_settle(self):
        self.stage = 'settling'
        self.deadline = time.monotonic() + self.delay
        self.message.set('Waiting for vibration to settle…')

    def take_photo(self):
        self.stage = 'photographing'
        self.photo_token = uuid.uuid4().hex
        self.photo_position = dict(self.app.state)
        self.deadline = time.monotonic() + 20
        self.camera.capture(self.photo_token, self.session.photo_path(self.angle),
                            metadata={"lighting": self.app.lighting.capture_settings()})
        self.message.set(f'Capturing {self.angle}°…' if self.sequence else 'Capturing photo…')

    def photo_saved(self, event):
        if not self.active or event['token'] != self.photo_token:
            return
        self.photo_token = None
        self.session.add(event, self.angle, self.photo_position)
        self.photos.insert('', 'end', iid=event['path'], values=(f'{self.angle}°' if self.angle is not None else 'Manual', f"{event['width']} × {event['height']}", Path(event['path']).name))
        if not self.sequence:
            self.complete()
            return
        self.index += 1
        if self.index == 6:
            self.stage = 'returning'
            self.move('RETURN')
        else:
            self.angle = self.index * 60
            self.stage = 'moving'
            self.move('ROTATE', degrees=60)

    def complete(self):
        try:
            self.session.finish('complete')
        except OSError as error:
            self.fail(str(error))
            return
        self.active = False
        self.message.set(f'Saved {len(self.session.data["images"])} photo(s) to {self.session.path}')
        self.app.update_controls()

    def cancel(self, stop=True):
        if not self.active:
            return
        self.end('cancelled', 'Capture cancelled. Saved photos are retained.', stop)

    def fail(self, reason):
        self.end('failed', reason, True)

    def end(self, status, reason, stop):
        self.active = False
        self.waiting_motor = self.photo_token = None
        try:
            self.session.finish(status, reason)
        except OSError as error:
            reason += f' (Could not update manifest: {error})'
        if stop and self.app.connection:
            self.app.send('STOP')
        self.message.set(reason)
        self.app.update_controls()

    def update_controls(self):
        idle = not self.active
        motor_idle = not self.app.pending and not self.app.state.get('busy')
        self.single_button.configure(state='normal' if idle and self.camera_ready and motor_idle else 'disabled')
        sequence_ready = idle and self.camera_ready and motor_idle and self.app.ready and self.app.state.get('known') and self.app.state.get('steps_per_rev')
        self.sequence_button.configure(state='normal' if sequence_ready else 'disabled')
        self.cancel_button.configure(state='normal' if self.active else 'disabled')
        for widget in (self.output_entry, self.browse_button, self.settle_box):
            widget.configure(state='normal' if idle else 'disabled')
        self.selector.configure(state='readonly' if self.camera is None else 'disabled')
        self.size_box.configure(state='readonly' if self.camera is None else 'disabled')
        self.refresh_button.configure(state='normal' if self.camera is None else 'disabled')

    def poll(self):
        if self.closing:
            return
        try:
            for _ in range(30):
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                kind = event['type']
                if kind == 'camera_ready':
                    self.camera_ready = True
                    self.camera_info = event
                    self.actual.configure(text=f"Actual: {event['width']} × {event['height']}")
                    self.message.set('Camera ready. Use Capture photo, or establish motor home for six angles.')
                elif kind == 'camera_error':
                    self.camera_ready = False
                    if self.active:
                        self.fail(event['message'])
                    else:
                        self.message.set(event['message'])
                elif kind == 'camera_closed':
                    self.camera = None
                    self.camera_ready = False
                    self.open_button.configure(text='Open camera', state='normal')
                    self.actual.configure(text='Camera closed')
                    self.preview.configure(image='', text='Camera closed')
                    self.preview.image = None
                elif kind == 'photo_saved':
                    self.photo_saved(event)
            if self.camera:
                preview = self.camera.get_preview()
                if preview and preview[0] != self.preview_number:
                    self.preview_number = preview[0]
                    image = ImageTk.PhotoImage(self.focus.decorate(preview[1]))
                    self.preview.configure(image=image, text='')
                    self.preview.image = image
            if self.active and time.monotonic() >= self.deadline:
                if self.stage == 'settling':
                    self.take_photo()
                else:
                    self.fail('Capture timed out waiting for the camera or motor.')
        except (OSError, ValueError) as error:
            if self.active:
                self.fail(str(error))
            else:
                self.message.set(str(error))
        self.focus.update()
        self.update_controls()
        self.root.after(70, self.poll)

    def review(self, event=None):
        selected = self.photos.selection()
        if not selected:
            return
        with Image.open(selected[0]) as original:
            image = original.copy()
        image.thumbnail((1000, 750))
        window = tk.Toplevel(self.root)
        window.title(Path(selected[0]).name)
        photo = ImageTk.PhotoImage(image)
        label = ttk.Label(window, image=photo)
        label.image = photo
        label.pack()

    def open_folder(self):
        directory = self.session.path if self.session else Path(self.output.get()).expanduser()
        if directory.is_dir():
            subprocess.Popen(['xdg-open', str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def shutdown(self):
        self.cancel()
        self.closing = True
        if self.camera:
            self.camera.close()
