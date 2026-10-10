import json
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, messagebox
import uuid

from .connection import Connection
from .capture_ui import CapturePanel
from .diagnostics_ui import DiagnosticsPanel
from .lighting_ui import LightingPanel


DATA = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "menagerie_capture"


def scrollable_tab(notebook, title):
    tab = ttk.Frame(notebook)
    notebook.add(tab, text=title)
    canvas = tk.Canvas(tab, highlightthickness=0)
    vertical = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
    horizontal = ttk.Scrollbar(tab, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    tab.rowconfigure(0, weight=1)
    tab.columnconfigure(0, weight=1)
    content = ttk.Frame(canvas, padding=8)
    window = canvas.create_window(0, 0, window=content, anchor="nw")

    def resize(event=None):
        canvas.itemconfigure(window, width=max(canvas.winfo_width(), content.winfo_reqwidth()),
                             height=max(canvas.winfo_height(), content.winfo_reqheight()))
        canvas.configure(scrollregion=canvas.bbox("all"))

    canvas.bind("<Configure>", resize)
    content.bind("<Configure>", resize)
    return content


class App:
    def __init__(self, root):
        self.root = root
        root.title("Menagerie Capture")
        root.minsize(800, 800)
        root.geometry(f"{min(1150, root.winfo_screenwidth() - 80)}x{min(1000, root.winfo_screenheight() - 100)}")
        self.capture = None
        self.connection = None
        self.events = queue.Queue()
        self.ident = 0
        self.pending = set()
        self.ready = False
        self.connection_error = None
        self.state = {}
        try:
            self.settings = json.loads((DATA / "settings.json").read_text())
            if not isinstance(self.settings, dict):
                self.settings = {}
        except (OSError, ValueError):
            self.settings = {}
        self.port = tk.StringVar(value="Simulator")
        self.steps = tk.StringVar(value=str(self.settings.get("steps_per_rev", 0)))
        self.speed = tk.StringVar(value=str(self.settings.get("speed", 200)))
        self.jog = tk.StringVar(value="200")
        self.invert = tk.BooleanVar(value=self.settings.get("invert", False))
        self.hall = tk.BooleanVar(value=self.settings.get("hall", False))
        self.miniature = tk.StringVar(value=self.settings.get("miniature", ""))
        self.identity_kind = self.settings.get("identity_kind", "uuid")
        self.last_rfid_uid = None
        self.rfid_tag = self.settings.get("rfid_tag")
        self.rfid_status = tk.StringVar(value="RFID: connect the Pico to check the reader")
        self.status = tk.StringVar(value="Disconnected. Start with Simulator or select your Pico.")
        self.position = tk.StringVar(value="Position: unreferenced")
        self.controls = []
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Menagerie Capture", font=("", 20, "bold")).pack(anchor="w")
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        outer = scrollable_tab(notebook, "Motor and miniature")
        camera_tab = scrollable_tab(notebook, "Camera and capture")
        diagnostics_tab = scrollable_tab(notebook, "Sensor tests")
        ttk.Label(outer, text="Motor bench • Clockwise is viewed from above the turntable").pack(anchor="w", pady=(0, 12))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        self.ports = ttk.Combobox(row, textvariable=self.port, width=46)
        self.ports.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Refresh", command=self.refresh_ports).pack(side="left", padx=6)
        self.connect_button = ttk.Button(row, text="Connect", command=self.connect)
        self.connect_button.pack(side="left")
        setup = ttk.LabelFrame(outer, text="Calibration and speed", padding=10)
        setup.pack(fill="x", pady=12)
        ttk.Label(setup, text="STEP pulses per full turntable revolution").grid(row=0, column=0, sticky="w")
        ttk.Entry(setup, textvariable=self.steps, width=12).grid(row=0, column=1, padx=8)
        ttk.Label(setup, text="0 = not calibrated").grid(row=0, column=2, sticky="w")
        ttk.Label(setup, text="Speed (STEP pulses/second, 20–1000)").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Spinbox(setup, from_=20, to=1000, increment=20, textvariable=self.speed, width=12).grid(row=1, column=1)
        ttk.Checkbutton(setup, text="Reverse motor direction", variable=self.invert).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(setup, text="Hall sensor installed (active-low, GP26)", variable=self.hall).grid(row=3, column=0, sticky="w")
        self.button(setup, "Apply settings", self.configure).grid(row=2, column=1, columnspan=2, rowspan=2, padx=8)
        ttk.Label(setup, text="Jog a known pulse count and measure rotation. Pulses/turn = pulses × 360 ÷ measured degrees.\nCalibrate the final turntable, including belt reduction. Start at low speed.", wraplength=700).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        movement = ttk.LabelFrame(outer, text="Turntable", padding=10)
        movement.pack(fill="x")
        self.ccw = self.button(movement, "↶  60° counterclockwise", lambda: self.send("ROTATE", degrees=-60))
        self.ccw.grid(row=0, column=0, padx=5, pady=5)
        self.cw = self.button(movement, "60° clockwise  ↷", lambda: self.send("ROTATE", degrees=60))
        self.cw.grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(movement, text="STOP", command=lambda: self.send("STOP")).grid(row=0, column=2, padx=12)
        self.button(movement, "Jog counterclockwise", lambda: self.jog_motor(-1)).grid(row=1, column=0, pady=5)
        self.button(movement, "Jog clockwise", lambda: self.jog_motor(1)).grid(row=1, column=1)
        ttk.Spinbox(movement, from_=1, to=100000, textvariable=self.jog, width=10).grid(row=1, column=2)
        ttk.Label(movement, text="Jog pulses").grid(row=1, column=3)
        self.button(movement, "Set current position as home", lambda: self.send("ZERO")).grid(row=2, column=0, pady=10)
        self.return_button = self.button(movement, "Return home", lambda: self.send("RETURN"))
        self.return_button.grid(row=2, column=1)
        self.home_button = self.button(movement, "Find Hall home", lambda: self.send("HOME"))
        self.home_button.grid(row=2, column=2, columnspan=2)
        ttk.Button(movement, text="Release motor", command=lambda: self.send("RELEASE")).grid(row=3, column=0)
        ttk.Label(movement, textvariable=self.position).grid(row=3, column=1, columnspan=3, sticky="w", padx=8)
        identity = ttk.LabelFrame(outer, text="Miniature identifier (saved locally)", padding=10)
        identity.pack(fill="x", pady=12)
        ttk.Entry(identity, textvariable=self.miniature, width=42).pack(side="left", fill="x", expand=True)
        ttk.Button(identity, text="Use RFID UID", command=self.use_rfid).pack(side="left", padx=5)
        ttk.Button(identity, text="New UUID", command=self.new_uuid).pack(side="left")
        reader_row = ttk.Frame(outer)
        reader_row.pack(fill="x", pady=(0, 6))
        self.button(reader_row, "Scan RFID again", lambda: self.send("RFID_SCAN")).pack(side="left", padx=(0, 8))
        ttk.Label(reader_row, textvariable=self.rfid_status, wraplength=650).pack(side="left", fill="x", expand=True)
        ttk.Label(outer, textvariable=self.status, wraplength=720).pack(anchor="w", pady=5)
        self.log = tk.Text(outer, height=6, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        self.capture = CapturePanel(camera_tab, self, DATA)
        self.diagnostics = DiagnosticsPanel(diagnostics_tab, self)
        self.lighting = LightingPanel(camera_tab, self.send,
            lambda: self.ready and self.connection is not None,
            lambda: bool(self.state.get("busy") or (self.capture and self.capture.active)),
            self.settings, self.save)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if not self.miniature.get():
            self.new_uuid()
        self.refresh_ports()
        self.update_controls()
        root.after(50, self.poll)

    def button(self, parent, title, action):
        button = ttk.Button(parent, text=title, command=action)
        self.controls.append(button)
        return button

    def refresh_ports(self):
        try:
            from serial.tools.list_ports import comports
            stable = sorted(str(p) for p in Path("/dev/serial/by-id").glob("*"))
            self.ports["values"] = ["Simulator"] + stable + [p.device for p in comports()]
        except ImportError:
            self.ports["values"] = ["Simulator"]

    def connect(self):
        if self.connection:
            self.capture.cancel()
            self.connection.close()
            self.ready = False
            self.connect_button.configure(state="disabled")
        else:
            self.connection_error = None
            self.connection = Connection(self.port.get(), self.events)
            self.connect_button.configure(text="Disconnect")
            self.ports.configure(state="disabled")
            self.status.set("Opening serial port and waiting for capture firmware…")
        self.update_controls()

    def send(self, command, _capture=False, **args):
        if self.capture and self.capture.active and not _capture:
            if command in ("STOP", "RELEASE"):
                self.capture.cancel(stop=False)
            else:
                return
        if not self.connection:
            return
        self.ident += 1
        self.pending.add(self.ident)
        self.connection.send(dict(id=self.ident, cmd=command, **args))
        self.update_controls()
        return self.ident

    def configure(self):
        try:
            steps, speed = int(self.steps.get()), int(self.speed.get())
            if not 0 <= steps <= 1000000 or not 20 <= speed <= 1000:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Settings", "Use 0–1000000 pulses per turn and 20–1000 pulses per second.")
            return
        values = dict(steps_per_rev=steps, speed=speed, invert=self.invert.get(), hall=self.hall.get())
        self.settings.update(values)
        self.save()
        self.send("CONFIG", **values)

    def jog_motor(self, direction):
        try:
            steps = int(self.jog.get())
            if not 1 <= steps <= 100000:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Jog", "Enter 1–100000 pulses.")
            return
        self.send("JOG", steps=direction * steps)

    def new_uuid(self):
        self.rfid_tag = None
        self.miniature.set(str(uuid.uuid4()))
        self.identity_kind = "uuid"
        self.save()

    def use_rfid(self):
        value = self.miniature.get().strip().upper().replace(":", "").replace(" ", "").replace("-", "")
        if len(value) not in (8, 14, 20) or any(c not in "0123456789ABCDEF" for c in value):
            messagebox.showerror("RFID UID", "Enter a 4, 7, or 10 byte hexadecimal UID.")
            return
        self.miniature.set(value)
        self.rfid_tag = None
        self.identity_kind = "rfid_uid"
        self.save()

    def accept_rfid(self, tag):
        uid = tag.get("uid", "")
        if not isinstance(uid, str) or len(uid) not in (8, 14, 20) or any(c not in "0123456789abcdefABCDEF" for c in uid):
            self.rfid_status.set("RFID: invalid UID received; scan again")
            return
        uid = uid.upper()
        self.last_rfid_uid = uid
        if self.capture and self.capture.active:
            self.rfid_status.set("Tag ignored during capture; scan again after capture finishes")
            return
        self.miniature.set(uid)
        self.identity_kind = "rfid_uid"
        self.rfid_tag = {key: tag[key] for key in ("uid", "uid_length", "atqa", "sak", "technology") if key in tag}
        self.rfid_tag["uid"] = uid
        self.save()
        self.rfid_status.set("RFID tag: " + uid)

    def save(self):
        self.settings.update(miniature=self.miniature.get(), identity_kind=self.identity_kind, rfid_tag=self.rfid_tag)
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            temporary = DATA / "settings.tmp"
            temporary.write_text(json.dumps(self.settings, indent=2) + "\n")
            temporary.replace(DATA / "settings.json")
        except OSError as error:
            messagebox.showerror("Save settings", str(error))

    def update_controls(self):
        idle = self.ready and not self.pending and not self.state.get("busy") and not (self.capture and self.capture.active)
        for button in self.controls:
            button.configure(state="normal" if idle else "disabled")
        calibrated = bool(self.state.get("steps_per_rev"))
        for button in (self.cw, self.ccw):
            button.configure(state="normal" if idle and calibrated else "disabled")
        self.return_button.configure(state="normal" if idle and calibrated and self.state.get("known") else "disabled")
        self.home_button.configure(state="normal" if idle and calibrated and self.settings.get("hall") else "disabled")

    def poll(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event.get("type")
            self.diagnostics.on_event(event)
            if kind == "connected":
                self.last_rfid_uid = None
                self.rfid_status.set("RFID: waiting for reader status (updated firmware required)")
                # A new host session must not inherit another session's reference/move.
                self.send("RELEASE")
                self.configure()
            elif kind == "disconnected":
                self.connection = None
                self.ready = False
                self.pending.clear()
                self.state = {}
                self.position.set("Position: unreferenced")
                self.connect_button.configure(text="Connect", state="normal")
                self.ports.configure(state="normal")
                self.rfid_status.set("RFID: Pico disconnected")
            elif kind == "rfid" and event.get("uid"):
                self.accept_rfid(event)
            elif kind in ("rfid_status", "rfid_error"):
                self.rfid_status.set(event.get("message", "RFID status unavailable"))
            reader = event.get("rfid")
            if isinstance(reader, dict):
                if reader.get("status") != "ready":
                    self.rfid_status.set(reader.get("message", "RFID unavailable"))
                elif reader.get("tag"):
                    tag = reader["tag"]
                    if tag.get("uid") != self.last_rfid_uid:
                        self.accept_rfid(tag)
                elif self.last_rfid_uid is None:
                    self.rfid_status.set(reader.get("message", "RFID ready"))
            if "state" in event:
                self.ready = True
                self.state = event["state"]
                angle = self.state.get("angle")
                position = "unreferenced" if angle is None else "{:.2f}°".format(angle)
                self.position.set("{} • {} pulses • {}".format(position, self.state["position"], "holding" if self.state["enabled"] else "released"))
            if kind in ("done", "error"):
                self.pending.discard(event.get("id"))
            if self.capture:
                self.capture.on_motor_event(event)
            self.lighting.on_event(event)
            if kind != "status":
                message = event.get("message") or {"connected": "Connected; applying settings…", "disconnected": "Disconnected", "accepted": "Moving…", "done": "Ready"}.get(kind, kind)
                if kind == "connection_error":
                    self.connection_error = message
                elif kind == "disconnected" and self.connection_error:
                    message = "Disconnected — " + self.connection_error
                self.status.set(message)
                self.log.configure(state="normal")
                self.log.insert("end", message + "\n")
                if int(self.log.index("end-1c").split(".")[0]) > 200:
                    self.log.delete("1.0", "2.0")
                self.log.see("end")
                self.log.configure(state="disabled")
            self.update_controls()
        self.lighting.update()
        self.root.after(50, self.poll)

    def close(self):
        self.lighting.cancel_change()
        self.capture.shutdown()
        if self.connection:
            self.connection.close()
            self.root.after(50, self.finish_close)
        else:
            self.finish_close()

    def finish_close(self):
        if (self.connection and self.connection.thread.is_alive()) or (self.capture.camera and self.capture.camera.thread.is_alive()):
            self.root.after(50, self.finish_close)
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
