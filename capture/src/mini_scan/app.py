"""Dark desktop workflow for RFID-led miniature capture and Menagerie upload."""

import json
import queue
import re
import shutil
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font, simpledialog
from PIL import Image, ImageTk
from tkinterdnd2 import TkinterDnD, DND_FILES

from capture_station.connection import Connection
from capture_station.lighting_ui import LightingPanel
from capture_station.focus_ui import FocusPanel
from capture_station.camera import Camera, devices
from .capture import Sequence
from .client import Client, APIError, remember, recalled, server_url
from .storage import DATA, Store, write_json

BG = "#242426"
PANEL = "#303034"
TEXT = "#eeeeef"
PURPLE = "#82449a"
ACCENT = "#b477cc"


def theme(root):
    for name in font.names(root):
        font.nametofont(name).configure(size=-22)
    root.configure(bg=BG)
    root.option_add("*Background", BG)
    root.option_add("*Foreground", TEXT)
    root.option_add("*Entry.Background", PANEL)
    root.option_add("*Text.Background", PANEL)
    root.option_add("*Listbox.Background", PANEL)
    root.option_add("*selectBackground", PURPLE)
    root.option_add("*insertBackground", TEXT)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".", background=BG, foreground=TEXT, fieldbackground=PANEL, font=("Sans", -22)
    )
    style.configure("TButton", background=PURPLE, padding=(14, 10))
    style.map("TButton", background=[("active", "#9756ae"), ("disabled", PANEL)])
    style.configure("TEntry", fieldbackground=PANEL, insertcolor=TEXT, padding=8)
    style.configure(
        "TCombobox", fieldbackground=PANEL, arrowcolor=TEXT, padding=8, arrowsize=24
    )
    style.configure("TCheckbutton", indicatorsize=24, padding=6)
    style.configure("TScrollbar", arrowsize=26, width=26)
    style.configure("Treeview", rowheight=38)
    root.option_add("*TCombobox*Listbox.font", ("Sans", -22))
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL)],
        foreground=[("readonly", TEXT)],
    )
    style.configure("Title.TLabel", font=("Sans", -38, "bold"), foreground=ACCENT)
    style.configure("TLabelframe", background=BG)
    style.configure("TLabelframe.Label", foreground=ACCENT)
    style.map("Treeview", background=[("selected", PURPLE)])


def size_window(window, width, height):
    width = min(width, max(640, window.winfo_screenwidth() - 80))
    height = min(height, max(480, window.winfo_screenheight() - 100))
    window.geometry(f"{width}x{height}")
    window.minsize(min(width, 1000), min(height, 700))


def scroll_panel(parent, width):
    """Keep enlarged controls reachable on smaller displays."""
    canvas = tk.Canvas(parent, highlightthickness=0, width=width)
    vertical = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    horizontal = ttk.Scrollbar(parent, orient="horizontal", command=canvas.xview)
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)
    canvas.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    content = ttk.Frame(canvas, padding=4)
    item = canvas.create_window((0, 0), window=content, anchor="nw")

    def resize(event=None):
        canvas.itemconfigure(
            item, width=max(content.winfo_reqwidth(), canvas.winfo_width())
        )
        canvas.configure(scrollregion=canvas.bbox("all"))

    content.bind("<Configure>", resize)
    canvas.bind("<Configure>", resize)

    # Scope wheel handling to this panel, without intercepting dropdowns or text editors.
    def wheel(event):
        widget = event.widget
        while widget is not None and widget != parent:
            if isinstance(widget, (ttk.Combobox, tk.Text, tk.Listbox)):
                return
            widget = getattr(widget, "master", None)
        if widget == parent:
            delta = -1 if getattr(event, "num", None) == 4 or event.delta > 0 else 1
            canvas.yview_scroll(delta * 3, "units")

    parent.winfo_toplevel().bind("<MouseWheel>", wheel, add="+")
    parent.winfo_toplevel().bind("<Button-4>", wheel, add="+")
    parent.winfo_toplevel().bind("<Button-5>", wheel, add="+")
    return content


def button(parent, text, command):
    widget = ttk.Button(parent, text=text, command=command)
    widget.pack(side="left", padx=7, pady=7)
    return widget


def row(parent):
    frame = ttk.Frame(parent)
    frame.pack(fill="x", pady=7)
    return frame


def field(parent, label, value="", width=30):
    container = row(parent)
    ttk.Label(container, text=label, width=23).pack(side="left")
    variable = tk.StringVar(value=str(value))
    ttk.Entry(container, textvariable=variable, width=width).pack(
        side="left", fill="x", expand=True
    )
    return variable


class App:
    def __init__(self, root, store=None):
        self.root = root
        theme(root)
        root.title("Mini Scan")
        size_window(root, 1560, 1040)
        self.store = store or Store()
        self.events = queue.Queue()
        self.connection = None
        self.camera = None
        self.connected = False
        self.camera_ready = False
        self.state = {}
        self.counter = 0
        self.sequence = None
        self.client = None
        self.user = None
        self.collections = []
        self.dialogs = {}
        self.pending_tags = set()
        self.seen = set()
        self.busy_network = False
        self.offline = False
        self.config_pending = None
        self.configured = False
        self.device_settings = None
        try:
            self.settings = json.loads((self.store.root / "settings.json").read_text())
        except (OSError, ValueError):
            self.settings = {}
        try:
            calibration = json.loads(
                (DATA.parent / "menagerie_capture/settings.json").read_text()
            )
        except (OSError, ValueError):
            calibration = {}
        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="MINI SCAN", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer, text="Connect · Choose collection · Scan tag · Capture · Upload"
        ).pack(anchor="w", pady=(0, 12))
        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)
        controls = ttk.Frame(panes)
        preview_panel = ttk.Frame(panes, padding=12)
        panes.add(controls, weight=3)
        panes.add(preview_panel, weight=2)
        outer = scroll_panel(controls, 940)
        ttk.Label(preview_panel, text="Camera preview", style="Title.TLabel").pack(
            anchor="w"
        )
        account = ttk.LabelFrame(outer, text="Menagerie", padding=10)
        account.pack(fill="x")
        self.url = field(
            account, "Server URL", self.settings.get("server", "http://localhost:8000")
        )
        keyrow = row(account)
        ttk.Label(keyrow, text="API key", width=23).pack(side="left")
        self.key = tk.StringVar()
        ttk.Entry(keyrow, textvariable=self.key, show="•").pack(
            side="left", fill="x", expand=True
        )
        try:
            self.key.set(recalled(self.url.get()))
        except Exception:
            pass
        self.remember = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            account, text="Remember key in system keyring", variable=self.remember
        ).pack(anchor="w")
        buttons = row(account)
        button(buttons, "Connect / refresh collections", self.connect_api)
        button(buttons, "Load saved key", self.load_key)
        button(buttons, "Work offline", self.use_offline)
        self.account_status = tk.StringVar(value="Not connected")
        ttk.Label(account, textvariable=self.account_status).pack(anchor="w")
        collections = row(outer)
        ttk.Label(collections, text="Collection", width=23).pack(side="left")
        self.collection = ttk.Combobox(collections, state="readonly")
        self.collection.pack(side="left", fill="x", expand=True)
        self.collection.bind("<<ComboboxSelected>>", lambda e: self.check_collection())
        manual_row = row(outer)
        button(manual_row, "New item without RFID", self.manual_item)
        button(manual_row, "Find existing UUID", self.find_uuid)
        device = ttk.LabelFrame(outer, text="Capture station", padding=10)
        device.pack(fill="x", pady=8)
        portrow = row(device)
        ttk.Label(portrow, text="Pico", width=23).pack(side="left")
        self.port = ttk.Combobox(portrow)
        self.port.pack(side="left", fill="x", expand=True)
        portactions = row(device)
        button(portactions, "Refresh", self.refresh_devices)
        button(portactions, "Connect", self.connect_device)
        button(portactions, "Disconnect", self.disconnect_device)
        self.steps = field(
            device,
            "Pulses per revolution",
            self.settings.get("steps_per_rev", calibration.get("steps_per_rev", 0)),
        )
        self.sector_pulses = field(device, "Pulses per 60° interval", "210")
        button(row(device), "Use interval × 6", self.use_sector_pulses)
        ttk.Label(device, text="210 pulses between magnets = 1260 per revolution. Click Apply motor settings after editing.", wraplength=700).pack(anchor="w")
        self.speed = field(
            device,
            "STEP pulses/second",
            self.settings.get("speed", calibration.get("speed", 200)),
        )
        self.invert = tk.BooleanVar(
            value=self.settings.get("invert", calibration.get("invert", False))
        )
        ttk.Checkbutton(
            device, text="Reverse motor direction", variable=self.invert
        ).pack(anchor="w")
        button(row(device), "Apply motor settings", self.apply_motor_settings)
        self.jog_pulses = field(device, "Calibration jog pulses", "210")
        jog_actions = row(device)
        button(jog_actions, "Jog counterclockwise", lambda: self.jog_motor(-1))
        button(jog_actions, "Jog clockwise", lambda: self.jog_motor(1))
        ttk.Label(device, text="Jog between adjacent magnets to measure interval pulses. Six identical magnets make any magnet a home reference; they do not identify a unique zero.", wraplength=700).pack(anchor="w")
        camrow = row(device)
        ttk.Label(camrow, text="Camera", width=23).pack(side="left")
        self.camera_choice = ttk.Combobox(camrow, state="readonly")
        self.camera_choice.pack(side="left", fill="x", expand=True)
        button(camrow, "Open camera", self.open_camera)
        actions = row(device)
        button(actions, "STOP", self.stop)
        button(actions, "Release motor", self.release)
        self.device_status = tk.StringVar(value="Disconnected")
        ttk.Label(device, textvariable=self.device_status, wraplength=970).pack(
            anchor="w"
        )
        self.lighting = LightingPanel(
            outer,
            self.send,
            lambda: self.connected and self.configured,
            lambda: bool(
                self.state.get("busy") or (self.sequence and self.sequence.active)
            ),
            self.settings,
            self.save_settings,
        )
        lower = row(outer)
        button(lower, "Local drafts / storage", self.storage_window)
        button(lower, "Rescan tag", self.rescan)
        self.status = tk.StringVar(
            value="Connect to Menagerie and choose a collection."
        )
        ttk.Label(outer, textvariable=self.status, wraplength=980).pack(
            anchor="w", pady=8
        )
        self.preview = ttk.Label(preview_panel)
        self.preview.pack(fill="both", expand=True)
        self.focus = FocusPanel(preview_panel, self.preview, lambda: self.camera, self.lighting.capture_settings)
        self.refresh_devices()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(50, self.poll)

    def async_call(self, work, done):
        def run():
            try:
                result = work()
                error = None
            except Exception as exc:
                result = None
                error = exc
            self.events.put(
                {"type": "callback", "callback": done, "result": result, "error": error}
            )

        threading.Thread(target=run, daemon=True).start()

    def fail(self, error):
        messagebox.showerror("Mini Scan", str(error), parent=self.root)

    def save_settings(self):
        self.settings.update(server=self.url.get())
        write_json(self.store.root / "settings.json", self.settings)

    def load_key(self):
        try:
            self.key.set(recalled(self.url.get()))
        except Exception as error:
            self.fail(error)

    def connect_api(self):
        if self.busy_network:
            return
        if self.dialogs or self.pending_tags:
            self.fail(
                "Close item dialogs before changing or refreshing the account. Drafts remain saved."
            )
            return
        try:
            client = Client(self.url.get(), self.key.get())
        except Exception as error:
            self.fail(error)
            return
        self.busy_network = True
        self.client = None
        self.user = None
        self.collections = []
        self.collection.set("")
        self.seen.clear()
        self.account_status.set("Connecting…")

        def done(result, error):
            self.busy_network = False
            if error:
                self.account_status.set("Connection failed")
                self.fail(error)
                return
            self.client = client
            self.user, self.collections = result
            self.offline = False
            self.store.cache_account(client.url, self.user, self.collections)
            self.account_status.set("Connected as " + self.user["username"])
            self.collection["values"] = [
                f"{c['name']} (#{c['id']})" for c in self.collections
            ]
            if self.collections:
                self.collection.current(0)
                self.check_collection()
            else:
                self.status.set(
                    "No collections. Create a collection in Menagerie, then refresh."
                )
            self.url.set(client.url)
            self.save_settings()
            if self.remember.get():
                try:
                    remember(client.url, client.key)
                except Exception as exc:
                    messagebox.showwarning(
                        "Key not saved",
                        "Connected, but the system keyring could not save the key. "
                        + str(exc),
                        parent=self.root,
                    )

        self.async_call(client.connect, done)

    def use_offline(self):
        if self.dialogs or self.pending_tags or self.busy_network:
            self.fail(
                "Close item dialogs and finish pending requests before switching accounts."
            )
            return
        try:
            url = server_url(self.url.get())
            accounts = self.store.cached_accounts(url)
            if not accounts:
                raise ValueError(
                    "Connect successfully once to cache an account and collection definitions."
                )
        except ValueError as error:
            self.fail(error)
            return
        window = tk.Toplevel(self.root)
        window.title("Choose cached account")
        choice = ttk.Combobox(
            window,
            values=[f"{a['user']['username']} (#{a['user']['id']})" for a in accounts],
            state="readonly",
            width=45,
        )
        choice.pack(padx=12, pady=12)
        choice.current(0)

        def select():
            account = accounts[choice.current()]
            self.client = Client(url, self.key.get() or "offline")
            self.user, self.collections = account["user"], account["collections"]
            self.offline = True
            self.seen.clear()
            self.collection["values"] = [
                f"{c['name']} (#{c['id']})" for c in self.collections
            ]
            if self.collections:
                self.collection.current(0)
            self.account_status.set(
                "Offline · "
                + self.user["username"]
                + " · cached collection definitions"
            )
            self.status.set(
                "Capture drafts locally. Reconnect with this account before uploading."
            )
            window.destroy()

        button(window, "Use cached account", select)

    def selected_collection(self):
        index = self.collection.current()
        return self.collections[index] if 0 <= index < len(self.collections) else None

    def check_collection(self):
        collection = self.selected_collection()
        if not collection:
            return False
        if collection.get("issues"):
            messagebox.showerror(
                "Collection needs edits",
                "\n".join(collection["issues"])
                + "\n\nEdit the collection in Menagerie, then refresh collections.",
                parent=self.root,
            )
            webbrowser.open(self.client.url + collection["edit_url"])
            return False
        self.status.set("Scan an RFID tag or choose New item without RFID.")
        return True

    def refresh_devices(self):
        from serial.tools.list_ports import comports

        ports = sorted(str(p) for p in Path("/dev/serial/by-id").glob("*")) or [
            p.device for p in comports() if p.vid is not None
        ]
        self.port["values"] = ports + ["Simulator"]
        if not self.port.get() and ports:
            self.port.set(ports[0])
        self.cameras = devices()
        self.camera_choice["values"] = list(self.cameras)
        if not self.camera_choice.get():
            preferred = next(
                (n for n in self.cameras if "HD USB" in n),
                next((n for n in self.cameras if n != "Simulator"), "Simulator"),
            )
            self.camera_choice.set(preferred)

    def use_sector_pulses(self):
        try:
            pulses = int(self.sector_pulses.get())
            if not 1 <= pulses <= 166666:
                raise ValueError("Enter 1–166666 pulses per 60° interval.")
            self.steps.set(str(pulses * 6))
        except ValueError as error:
            self.fail(error)

    def motor_idle(self):
        if not self.connected:
            raise ValueError("Connect the Pico first.")
        if self.config_pending is not None or self.state.get("busy") or (self.sequence and self.sequence.active):
            raise ValueError("Wait for motor configuration or capture to finish, or press STOP.")

    def apply_motor_settings(self):
        try:
            self.motor_idle()
            steps, speed = int(self.steps.get()), int(self.speed.get())
            if not 0 <= steps <= 1000000 or not 20 <= speed <= 1000:
                raise ValueError("Use 0–1000000 pulses/revolution and 20–1000 pulses/second.")
        except ValueError as error:
            self.fail(error)
            return
        self.device_settings = dict(steps_per_rev=steps, speed=speed, invert=self.invert.get(), hall=True)
        self.configured = False
        self.config_pending = self.send("CONFIG", **self.device_settings)
        self.device_status.set("Applying motor settings…")

    def jog_motor(self, direction):
        try:
            self.motor_idle()
            pulses = int(self.jog_pulses.get())
            if not 1 <= pulses <= 100000:
                raise ValueError("Enter 1–100000 jog pulses.")
        except ValueError as error:
            self.fail(error)
            return
        self.send("JOG", steps=direction * pulses)

    def connect_device(self):
        if self.connection:
            self.fail("Disconnect the current Pico first.")
            return
        try:
            steps = int(self.steps.get())
            speed = int(self.speed.get())
            if not 0 <= steps <= 1000000 or not 20 <= speed <= 1000:
                raise ValueError(
                    "Use 0–1000000 pulses/revolution and 20–1000 pulses/second."
                )
            if not self.port.get():
                raise ValueError("Select a Pico port.")
            self.device_settings = dict(
                steps_per_rev=steps, speed=speed, invert=self.invert.get(), hall=True
            )
        except ValueError as error:
            self.fail(error)
            return
        self.configured = False
        self.settings.update(self.device_settings)
        self.save_settings()
        self.connection = Connection(self.port.get(), self.events)
        self.device_status.set("Connecting…")

    def disconnect_device(self):
        if self.sequence and self.sequence.active:
            self.sequence.cancel("Disconnected; home position is no longer confirmed.")
        if self.connection:
            self.connection.close()

    def send(self, command, **kwargs):
        self.counter += 1
        if self.connection:
            self.connection.send(dict(id=self.counter, cmd=command, **kwargs))
        return self.counter

    def open_camera(self):
        if self.sequence and self.sequence.active:
            self.fail("Finish or stop capture first.")
            return
        if self.camera:
            self.camera.close()
            self.camera.thread.join(timeout=2)
            if self.camera.thread.is_alive():
                self.fail("Camera is still closing. Retry shortly.")
                return
            # Old camera events are drained by poll before a replacement starts.
            self.camera = None
            self.camera_ready = False
            self.root.after(150, self.open_camera)
            return
        choice = self.camera_choice.get()
        if choice not in self.cameras:
            self.fail("Select a camera.")
            return
        self.camera_ready = False
        self.camera = Camera(self.cameras[choice], (3264, 2448), self.events)

    def stop(self):
        self.lighting.cancel_change()
        if self.sequence and self.sequence.active:
            self.sequence.cancel()
        else:
            self.send("STOP")

    def release(self):
        self.stop()
        self.send("RELEASE")

    def rescan(self):
        self.seen.clear()
        self.send("RFID_SCAN")

    def find_uuid(self):
        if not self.client or not self.user or self.offline:
            self.fail("Connect to Menagerie first to look up an existing item.")
            return
        value = simpledialog.askstring(
            "Find existing item", "Item UUID", parent=self.root
        )
        if not value:
            return
        import uuid

        try:
            value = str(uuid.UUID(value.strip()))
        except ValueError:
            self.fail("Enter a valid UUID.")
            return
        client = self.client
        self.pending_tags.add(value)

        def done(result, error):
            self.pending_tags.discard(value)
            if error:
                self.fail(error)
            else:
                self.recapture_item(result["item"])

        self.async_call(lambda: client.request("GET", "/items/by-uuid/" + value), done)

    def manual_item(self):
        if not self.client or not self.user or not self.selected_collection():
            self.fail("Connect to Menagerie and choose a collection first.")
            return
        if self.check_collection():
            self.open_draft(
                self.store.create(
                    self.client.url, self.user["id"], self.selected_collection()
                )
            )

    def scan(self, uid):
        uid = str(uid).upper()
        if not re.fullmatch(r"(?:[0-9A-F]{8}|[0-9A-F]{14}|[0-9A-F]{20})", uid):
            return
        if uid in self.dialogs:
            self.dialogs[uid].window.lift()
            return
        if uid in self.pending_tags or uid in self.seen:
            return
        if not self.client or not self.user or not self.selected_collection():
            self.status.set(
                "Tag detected. Connect to Menagerie and choose a collection, then rescan."
            )
            return
        collection = self.selected_collection()
        client = self.client
        user = self.user["id"]
        draft = self.store.for_tag(client.url, user, uid)
        if draft and draft.data["status"] != "uploaded":
            self.open_draft(draft)
            return
        if self.offline:
            if not self.check_collection():
                return
            draft = self.store.create(client.url, user, collection, uid)
            draft.data["lookup_pending"] = True
            draft.save()
            self.open_draft(draft)
            return
        self.pending_tags.add(uid)

        def done(result, error):
            self.pending_tags.discard(uid)
            if error:
                self.seen.add(uid)
                self.fail(error)
                return
            if result:
                self.seen.add(uid)
                self.recapture_item(result["item"])
                return
            if collection.get("issues"):
                self.fail("Collection needs edits: " + "\n".join(collection["issues"]))
                return
            self.open_draft(self.store.create(client.url, user, collection, uid))

        self.async_call(lambda: client.lookup(uid), done)

    def recapture_item(self, item):
        collection = next(
            (c for c in self.collections if c["id"] == item["collection_id"]), None
        )
        if not collection:
            self.fail("Refresh collections to load this item's collection.")
            return
        self.open_draft(
            self.store.recapture(self.client.url, self.user["id"], collection, item)
        )

    def open_draft(self, draft):
        uid = draft.key
        if uid in self.dialogs:
            self.dialogs[uid].window.lift()
            return
        if draft.data["status"] == "sending":
            draft.data["status"] = "uncertain"
            draft.save()
        if draft.data["status"] == "draft":
            current = next(
                (c for c in self.collections if c["id"] == draft.data["collection_id"]),
                None,
            )
            if current:
                draft.data["schema"] = current
                valid_ids = {str(f["id"]) for f in current["fields"]}
                removed = {
                    k: v for k, v in draft.data["values"].items() if k not in valid_ids
                }
                if removed:
                    draft.data.setdefault("retired_values", {}).update(removed)
                    draft.data["values"] = {
                        k: v for k, v in draft.data["values"].items() if k in valid_ids
                    }
                draft.save()
        self.seen.add(uid)
        self.dialogs[uid] = ItemDialog(self, draft)

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event.get("type")
                if kind == "callback":
                    try:
                        event["callback"](event["result"], event["error"])
                    except Exception as error:
                        self.fail(error)
                    continue
                if "state" in event:
                    self.state = event["state"]
                if kind == "connected":
                    self.connected = True
                    self.send("RELEASE")
                    self.config_pending = self.send("CONFIG", **self.device_settings)
                    self.device_status.set("Connected; applying calibration…")
                if event.get("id") == self.config_pending and kind in ("done", "error"):
                    self.configured = kind == "done"
                    self.config_pending = None
                    if self.configured:
                        self.settings.update(self.device_settings)
                        self.save_settings()
                        self.send("RFID_SCAN")
                    self.device_status.set(
                        "Pico ready · Hall homing enabled"
                        if self.configured
                        else event.get("message", "Configuration failed")
                    )
                if kind in ("connection_error", "disconnected"):
                    self.connected = False
                    self.configured = False
                    self.connection = None
                    self.device_status.set(event.get("message", "Pico disconnected"))
                if kind == "camera_ready":
                    self.camera_ready = True
                    self.status.set(
                        f"Camera ready: {event['width']} × {event['height']}"
                    )
                if kind in ("camera_error", "camera_closed"):
                    self.camera_ready = False
                    if kind == "camera_error":
                        self.status.set(event.get("message", "Camera error"))
                if self.sequence:
                    self.sequence.event(event)
                self.lighting.on_event(event)
                if kind == "rfid":
                    self.scan(event.get("uid", ""))
                # Snapshot tags are not new scans; explicit rescan produces an RFID event.
        except queue.Empty:
            pass
        if self.sequence:
            self.sequence.tick()
        if self.camera:
            preview = self.camera.get_preview()
            if preview and getattr(self, "frame_number", None) != preview[0]:
                self.frame_number = preview[0]
                frame = preview[1].copy()
                frame.thumbnail((max(100, self.preview.winfo_width()), 600))
                self.preview_image = ImageTk.PhotoImage(self.focus.decorate(frame))
                self.preview.configure(image=self.preview_image)
        self.lighting.update()
        self.focus.update()
        self.poll_id = self.root.after(50, self.poll)

    def storage_window(self):
        window = tk.Toplevel(self.root)
        window.title("Local drafts and storage")
        size_window(window, 1280, 700)
        usage = tk.StringVar()
        ttk.Label(window, textvariable=usage).pack(anchor="w", padx=12, pady=8)
        limit = field(window, "Cache limit (GiB)", self.settings.get("cache_gib", 5))
        listing = tk.Listbox(window)
        listing.pack(fill="both", expand=True, padx=12)
        drafts = []

        def refresh():
            nonlocal drafts
            drafts = (
                self.store.drafts(self.client.url, self.user["id"])
                if self.client and self.user
                else []
            )
            listing.delete(0, "end")
            for d in drafts:
                listing.insert(
                    "end",
                    f"{d.data['status']:10} {d.key} · {d.data['name'] or 'Unnamed'}",
                )
            usage.set(
                f"Total local storage: {self.store.usage() / 1024**3:.2f} GiB. Unsent drafts are never automatically deleted."
            )

        def selected():
            return drafts[listing.curselection()[0]] if listing.curselection() else None

        def open_selected():
            d = selected()
            if d:
                self.open_draft(d)

        def delete_selected():
            d = selected()
            if not d:
                return
            if d.key in self.dialogs:
                self.fail("Close this item dialog first.")
                return
            if messagebox.askyesno(
                "Delete local copy",
                "Permanently delete this local draft and its photos? This does not delete anything in Menagerie.",
                parent=window,
            ):
                shutil.rmtree(d.path)
                self.seen.discard(d.key)
                refresh()

        def prune():
            try:
                value = float(limit.get())
                if not 0.1 <= value <= 10000:
                    raise ValueError("Choose a cache limit between 0.1 and 10000 GiB.")
                self.settings["cache_gib"] = value
                self.save_settings()
                self.store.prune_uploaded(
                    int(value * 1024**3), [d.draft.path for d in self.dialogs.values()]
                )
                refresh()
            except ValueError as error:
                self.fail(error)

        actions = row(window)
        button(actions, "Open / retry", open_selected)
        button(actions, "Delete local copy", delete_selected)
        button(actions, "Save limit / prune uploaded copies", prune)
        refresh()

    def close(self):
        if any(d.uploading for d in self.dialogs.values()):
            self.fail("Wait for the current upload to finish.")
            return
        self.lighting.cancel_change()
        self.stop()
        for dialog in list(self.dialogs.values()):
            dialog.close()
        if self.connection:
            self.connection.close()
        if self.camera:
            self.camera.close()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


class ItemDialog:
    def __init__(self, app, draft):
        self.app, self.draft = app, draft
        self.uploading = False
        self.capture_active = False
        self.save_timer = None
        self.window = tk.Toplevel(app.root)
        self.window.title("Mini Scan · " + draft.key)
        size_window(self.window, 1560, 1040)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.mutable = draft.data["status"] not in (
            "sending",
            "uncertain",
            "conflict",
            "uploaded",
        )
        ttk.Label(
            self.window,
            text=f"{draft.data['schema']['name']} · {'RFID ' + draft.data['rfid_uid'] if draft.data['rfid_uid'] else 'Manual item'}",
            style="Title.TLabel",
        ).pack(anchor="w", padx=14, pady=10)
        body = ttk.Panedwindow(self.window, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=1)
        canvas = tk.Canvas(left, highlightthickness=0, width=400)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        form = ttk.Frame(canvas)
        form_id = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(form_id, width=e.width)
        )
        self.variables = {}
        self.texts = {}
        ttk.Label(form, text="UUID (generated automatically)").pack(
            anchor="w", pady=(8, 2)
        )
        self.uuid_value = tk.StringVar(value=draft.data["item_uuid"])
        ttk.Entry(form, textvariable=self.uuid_value, state="readonly").pack(fill="x")
        if draft.data.get("target_item_id"):
            self.image_mode = tk.StringVar(value=draft.data.get("image_mode", "append"))
            ttk.Label(form, text="Existing item: " + draft.data["name"]).pack(
                anchor="w"
            )
            ttk.Label(form, text="Existing item photos").pack(anchor="w")
            mode = ttk.Combobox(
                form,
                textvariable=self.image_mode,
                values=["append", "replace"],
                state="readonly",
            )
            mode.pack(fill="x")
            if not self.mutable:
                mode.state(["disabled"])
            self.image_mode.trace_add("write", self.changed)
            ttk.Label(
                form, text="Only photos are uploaded; edit item details in Menagerie."
            ).pack(anchor="w")
        definitions = [
            ("name", "Name", "text"),
            ("description", "Description", "multiline"),
            ("tags", "Tags (comma separated)", "text"),
        ]
        definitions += [
            (
                str(f["id"]),
                f["name"] + (" *" if f.get("required") else ""),
                f["field_type"],
            )
            for f in draft.data["schema"]["fields"] if not f.get("capture_key")
        ]
        if draft.data.get("target_item_id"):
            definitions = []
        for key, label, kind in definitions:
            value = (
                draft.data.get(key, "")
                if key in ("name", "description", "tags")
                else draft.data["values"].get(key, "")
            )
            ttk.Label(form, text=label).pack(anchor="w", pady=(8, 2))
            if kind == "multiline":
                widget = tk.Text(form, height=4, wrap="word")
                widget.insert("1.0", value)
                widget.pack(fill="x")
                self.texts[key] = widget
                widget.bind("<<Modified>>", self.text_changed)
                if not self.mutable:
                    widget.configure(state="disabled")
            elif kind == "dropdown":
                var = tk.StringVar(value=value)
                self.variables[key] = var
                definition = next(
                    f for f in draft.data["schema"]["fields"] if str(f["id"]) == key
                )
                widget = ttk.Combobox(
                    form,
                    textvariable=var,
                    values=[""] + definition.get("options", []),
                    state="readonly",
                )
                widget.pack(fill="x")
                if not self.mutable:
                    widget.state(["disabled"])
                var.trace_add("write", self.changed)
            elif kind == "checkbox":
                var = tk.BooleanVar(value=value in ("true", True))
                self.variables[key] = var
                widget = ttk.Checkbutton(form, text=label, variable=var)
                widget.pack(anchor="w")
                if not self.mutable:
                    widget.state(["disabled"])
                var.trace_add("write", self.changed)
            else:
                var = tk.StringVar(value=value)
                self.variables[key] = var
                widget = ttk.Entry(form, textvariable=var)
                widget.pack(fill="x")
                if not self.mutable:
                    widget.state(["readonly"])
                if kind in ("date", "timestamp"):
                    ttk.Label(
                        form,
                        text="YYYY-MM-DD"
                        + ("THH:MM:SS" if kind == "timestamp" else ""),
                    ).pack(anchor="w")
                var.trace_add("write", self.changed)
        ttk.Label(right, text="Final photos · display order from top to bottom").pack(
            anchor="w"
        )
        self.images = tk.Listbox(right, height=9, exportselection=False)
        self.images.pack(fill="x")
        self.images.bind("<<ListboxSelect>>", lambda e: self.show_image())
        self.images.drop_target_register(DND_FILES)
        self.images.dnd_bind("<<Drop>>", self.drop)
        ttk.Label(right, text="Drop image files here, or use Add images.").pack(
            anchor="w"
        )
        tools = row(right)
        self.edit_buttons = [
            button(tools, "Add images", self.add_files),
            button(tools, "Include / exclude", self.toggle),
        ]
        tools = row(right)
        self.edit_buttons += [
            button(tools, "↑", lambda: self.move(-1)),
            button(tools, "↓", lambda: self.move(1)),
            button(tools, "Edit photo", self.edit_image),
            button(tools, "Delete", self.delete_image),
            button(tools, "Restore original", self.restore_original),
        ]
        self.image_preview = ttk.Label(right)
        self.image_preview.pack(fill="both", expand=True)
        footer = row(self.window)
        self.capture_button = button(footer, "Capture 6 photos", self.capture)
        self.retake_angle = ttk.Combobox(
            footer,
            values=["current", "0", "60", "120", "180", "240", "300"],
            state="readonly",
            width=7,
        )
        self.retake_angle.set("current")
        self.retake_angle.pack(side="left", padx=4)
        self.single_button = button(
            footer, "Capture one view", lambda: self.capture(self.retake_angle.get())
        )
        footer = row(self.window)
        self.upload_button = button(
            footer,
            "Retry upload"
            if draft.data["status"] == "uncertain"
            else "Resend / verify upload"
            if draft.data["status"] == "uploaded"
            else "Upload to Menagerie",
            self.upload,
        )
        footer = row(self.window)
        if draft.data["status"] == "conflict":
            button(footer, "Start as new untagged item", self.recover_conflict)
            if draft.data.get("conflict", {}).get("item"):
                button(footer, "Add photos to existing item", self.resolve_existing)
            conflict_url = draft.data.get("conflict", {}).get("url")
            if conflict_url:
                button(
                    footer,
                    "Open existing item",
                    lambda: webbrowser.open(app.client.url + conflict_url),
                )
        footer = row(self.window)
        if draft.data["status"] == "uploaded" and draft.data.get("result"):
            button(footer, "Capture more photos", lambda: self.start_recapture())
        button(footer, "Close / keep draft", self.close)
        if draft.data.get("target_item_id") and not draft.data.get("result"):
            button(
                footer,
                "Edit item in Menagerie",
                lambda: webbrowser.open(
                    app.client.url + f"/items/{draft.data['target_item_id']}/edit"
                ),
            )
        if draft.data.get("result"):
            button(
                footer,
                "Open in Menagerie",
                lambda: webbrowser.open(app.client.url + draft.data["result"]["url"]),
            )
        self.message = tk.StringVar(
            value="Draft saved locally."
            if self.mutable
            else "Submission frozen. Retry sends exactly the same data."
            if draft.data["status"] == "uncertain"
            else draft.data.get("conflict", {}).get(
                "error", "Upload conflict. Start a new untagged item to recover."
            )
            if draft.data["status"] == "conflict"
            else "Uploaded. Local copy retained."
        )
        ttk.Label(self.window, textvariable=self.message, wraplength=1020).pack(
            fill="x", padx=14, pady=8
        )
        self.refresh()
        self.lock_controls()

    def lock_controls(self):
        editable = self.mutable and not self.uploading and not self.capture_active
        for b in self.edit_buttons + [self.capture_button, self.single_button]:
            b.state(["!disabled"] if editable else ["disabled"])
        self.upload_button.state(
            ["disabled"]
            if self.uploading
            or self.capture_active
            or self.draft.data["status"] == "conflict"
            else ["!disabled"]
        )

    def text_changed(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self.changed()

    def changed(self, *args):
        if not self.mutable or self.uploading:
            return
        if self.save_timer:
            self.window.after_cancel(self.save_timer)
        self.save_timer = self.window.after(350, self.save)

    def save(self):
        if self.save_timer:
            self.window.after_cancel(self.save_timer)
        self.save_timer = None
        if not self.mutable or self.uploading:
            return
        if hasattr(self, "image_mode"):
            self.draft.data["image_mode"] = self.image_mode.get()
        for key, var in self.variables.items():
            value = var.get()
            value = ("true" if value else "false") if isinstance(value, bool) else value
            if key in ("name", "description", "tags"):
                self.draft.data[key] = value
            else:
                self.draft.data["values"][key] = value
        for key, widget in self.texts.items():
            value = widget.get("1.0", "end-1c")
            if key == "description":
                self.draft.data[key] = value
            else:
                self.draft.data["values"][key] = value
        self.draft.save()

    def selection(self):
        return self.images.curselection()[0] if self.images.curselection() else None

    def refresh(self, index=0):
        self.images.delete(0, "end")
        for n, image in enumerate(self.draft.data["images"]):
            self.images.insert(
                "end",
                f"{n + 1}. {'✓ Upload' if image['selected'] else '— Excluded'} · {image['file'][:12]}",
            )
        if self.draft.data["images"]:
            self.images.selection_set(min(index, len(self.draft.data["images"]) - 1))
        self.show_image()

    def show_image(self):
        index = self.selection()
        if index is None:
            self.image_preview.configure(image="")
            return
        with Image.open(
            self.draft.path / self.draft.data["images"][index]["file"]
        ) as image:
            image.thumbnail((620, 420))
            self.photo = ImageTk.PhotoImage(image)
            self.image_preview.configure(image=self.photo)

    def add_files(self):
        self.import_files(
            filedialog.askopenfilenames(
                parent=self.window,
                filetypes=[
                    ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
                    ("All files", "*"),
                ],
            )
        )

    def drop(self, event):
        self.import_files(self.window.tk.splitlist(event.data))
        return event.action

    def import_files(self, files):
        if not self.mutable or self.uploading or self.capture_active:
            return
        try:
            for path in files:
                self.draft.add_image(path)
        except Exception as error:
            self.app.fail(error)
        self.refresh()

    def toggle(self):
        index = self.selection()
        if index is not None:
            self.draft.mutable()
            image = self.draft.data["images"][index]
            image["selected"] = not image["selected"]
            self.draft.save()
            self.refresh(index)

    def move(self, delta):
        index = self.selection()
        if index is not None and 0 <= index + delta < len(self.draft.data["images"]):
            self.draft.mutable()
            images = self.draft.data["images"]
            images[index], images[index + delta] = images[index + delta], images[index]
            self.draft.save()
            self.refresh(index + delta)

    def delete_image(self):
        index = self.selection()
        if index is not None and messagebox.askyesno(
            "Delete image", "Delete this local image?", parent=self.window
        ):
            self.draft.delete_image(index)
            self.refresh()

    def edit_image(self):
        index = self.selection()
        if index is not None:
            ImageEditor(self, index)

    def restore_original(self):
        index = self.selection()
        if index is not None:
            try:
                self.draft.restore_original(index)
                self.refresh(index)
            except Exception as error:
                self.app.fail(error)

    def capture(self, angle=None):
        app = self.app
        if app.sequence and app.sequence.active:
            app.fail("Another item is being captured.")
            return
        if not app.camera_ready or (
            angle != "current" and (not app.connected or not app.configured)
        ):
            app.fail("Connect the Pico and open the camera first.")
            return
        self.save()

        def complete(ok, message):
            self.capture_active = False
            self.message.set(message)
            self.refresh()
            self.lock_controls()

        app.sequence = Sequence(
            app.send,
            app.camera,
            self.draft,
            self.message.set,
            complete,
            angle=None
            if angle is None
            else "current"
            if angle == "current"
            else int(angle),
        )
        try:
            app.sequence.start(app.state)
        except Exception as error:
            app.fail(error)
            return
        self.capture_active = True
        self.lock_controls()

    def start_recapture(self):
        item = dict(
            id=self.draft.data["result"]["item_id"],
            collection_id=self.draft.data["collection_id"],
            item_uuid=self.draft.data["item_uuid"],
            rfid_uid=self.draft.data["rfid_uid"],
            name=self.draft.data["name"],
        )
        self.close()
        self.app.recapture_item(item)

    def resolve_existing(self):
        item = self.draft.data["conflict"]["item"]
        collection = next(
            (c for c in self.app.collections if c["id"] == item["collection_id"]), None
        )
        if not collection:
            self.app.fail("Refresh collections first.")
            return
        self.close()
        self.draft.data.update(
            status="draft",
            request_id=None,
            conflict=None,
            target_item_id=item["id"],
            collection_id=collection["id"],
            schema=collection,
            item_uuid=item["item_uuid"],
            rfid_uid=item["rfid_uid"],
            name=item["name"],
            image_mode="append",
        )
        self.draft.save()
        self.app.open_draft(self.draft)

    def recover_conflict(self):
        if messagebox.askyesno(
            "Start a new item",
            "Keep these photos and fields, generate a new UUID, and remove the RFID association? "
            "Any existing Menagerie item remains unchanged.",
            parent=self.window,
        ):
            self.close()
            self.draft.recover_conflict()
            self.app.open_draft(self.draft)

    def upload(self):
        if self.app.offline:
            self.app.fail(
                "Close this dialog and reconnect with this account before uploading. Your draft is saved."
            )
            return
        if self.mutable and self.draft.data.get("target_item_id"):
            self.draft.data["image_mode"] = self.image_mode.get()
            if self.image_mode.get() == "replace" and not messagebox.askyesno(
                "Replace existing photos",
                "Replace all photos currently saved on this item with the selected photos?",
                parent=self.window,
            ):
                return
        if self.uploading or self.capture_active:
            return
        self.save()
        try:
            self.draft.prepare()
        except Exception as error:
            self.app.fail(error)
            return
        self.uploading = True
        self.mutable = False
        self.lock_controls()
        self.message.set("Uploading finalized images…")

        # Disable form widgets as well as image actions while the request is in flight.
        def freeze(widget):
            for child in widget.winfo_children():
                if isinstance(
                    child, (ttk.Entry, ttk.Combobox, ttk.Checkbutton, tk.Text)
                ):
                    child.configure(state="disabled")
                freeze(child)

        freeze(self.window)

        def done(result, error):
            self.uploading = False
            if error:
                # Only an explicit validation/auth rejection permits changing the payload.
                editable = isinstance(error, APIError) and error.status in (
                    400,
                    401,
                    403,
                    404,
                    413,
                    422,
                )
                if isinstance(error, APIError) and (
                    error.status == 409
                    or (error.status == 404 and self.draft.data.get("target_item_id"))
                ):
                    self.draft.data.update(
                        status="conflict",
                        conflict={**error.details, "error": str(error)},
                    )
                    self.draft.save()
                    self.app.fail(error)
                    self.close()
                    self.app.open_draft(self.draft)
                    return
                self.draft.data["status"] = "draft" if editable else "uncertain"
                if editable:
                    self.draft.data["request_id"] = None
                self.draft.save()
                self.message.set(str(error))
                self.lock_controls()
                if editable:
                    self.app.fail(error)
                    if error.details.get("edit_url"):
                        webbrowser.open(self.app.client.url + error.details["edit_url"])
                    self.close()
                    self.app.open_draft(self.draft)
                else:
                    self.upload_button.configure(text="Retry upload")
                return
            self.draft.data.update(status="uploaded", result=result)
            self.draft.save()
            self.message.set("Uploaded successfully. Local copy retained.")
            self.upload_button.configure(text="Resend / verify upload")
            self.lock_controls()
            button(
                self.upload_button.master,
                "Open in Menagerie",
                lambda: webbrowser.open(self.app.client.url + result["url"]),
            )
            limit = float(self.app.settings.get("cache_gib", 5)) * 1024**3
            self.app.store.prune_uploaded(
                int(limit), [d.draft.path for d in self.app.dialogs.values()]
            )

        self.app.async_call(lambda: self.app.client.upload(self.draft), done)

    def close(self):
        if self.uploading:
            self.app.fail("Wait for upload to finish.")
            return
        if self.capture_active:
            self.app.fail("Stop capture before closing this item.")
            return
        if self.save_timer:
            self.window.after_cancel(self.save_timer)
            self.save_timer = None
        self.save()
        self.app.dialogs.pop(self.draft.key, None)
        self.window.destroy()


class ImageEditor:
    def __init__(self, dialog, index):
        self.dialog, self.index = dialog, index
        self.window = tk.Toplevel(dialog.window)
        self.window.title("Photo editor")
        self.window.transient(dialog.window)
        self.window.grab_set()
        self.image = Image.open(
            dialog.draft.path / dialog.draft.data["images"][index]["file"]
        )
        self.image.load()
        preview = self.image.copy()
        preview.thumbnail(
            (
                min(1100, self.window.winfo_screenwidth() - 100),
                min(650, max(200, self.window.winfo_screenheight() - 400)),
            )
        )
        self.base_preview = preview.copy()
        self.scale = self.image.width / preview.width
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas = tk.Canvas(
            self.window,
            width=preview.width,
            height=preview.height,
            highlightthickness=0,
        )
        self.canvas.pack(padx=10, pady=10)
        self.preview_item = self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        ttk.Label(
            self.window,
            text="Drag a rectangle to crop. Resize dimensions apply after cropping.",
        ).pack()
        crop_tools = row(self.window)
        button(crop_tools, "Auto crop · plain background", self.auto_crop)
        ttk.Label(crop_tools, text="Tolerance").pack(side="left", padx=(12, 4))
        self.background_tolerance = tk.StringVar(value="30")
        ttk.Spinbox(crop_tools, from_=5, to=80, width=4, textvariable=self.background_tolerance).pack(side="left")
        ttk.Label(crop_tools, text="Padding %").pack(side="left", padx=(12, 4))
        self.crop_padding = tk.StringVar(value="10")
        ttk.Spinbox(crop_tools, from_=0, to=50, width=4, textvariable=self.crop_padding).pack(side="left")
        button(crop_tools, "Reset crop", self.reset_crop)
        self.crop_message = tk.StringVar(value="Auto crop suggests a rectangle. Review it, then choose Apply.")
        ttk.Label(self.window, textvariable=self.crop_message, wraplength=900).pack()
        self.box = (0, 0, self.image.width, self.image.height)
        self.rectangle = None
        self.width = field(self.window, "Output width", self.image.width)
        self.height = field(self.window, "Output height", self.image.height)
        self.canvas.bind("<ButtonPress-1>", self.start)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.finish)
        self.adjustments = {}
        for key, label, low, high, value in [("exposure", "Exposure (stops)", -2, 2, 0),
                                            ("contrast", "Contrast", .5, 1.5, 1),
                                            ("color", "Color saturation", 0, 2, 1),
                                            ("sharpness", "Sharpen", 0, 2, 1)]:
            line = row(self.window)
            ttk.Label(line, text=label, width=20).pack(side="left")
            var = tk.DoubleVar(value=value)
            self.adjustments[key] = var
            ttk.Scale(line, from_=low, to=high, variable=var, command=self.render).pack(side="left", fill="x", expand=True)
        self.compare = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.window, text="Compare with photo before these adjustments", variable=self.compare, command=self.render).pack(anchor="w")
        ttk.Label(self.window, text="Sharpening improves appearance; it cannot recover missed focus. Originals and capture scores are preserved.", wraplength=900).pack()
        actions = row(self.window)
        button(actions, "Apply", self.apply)
        button(actions, "Cancel", self.window.destroy)

    def set_crop(self, box):
        self.box = box
        if self.rectangle:
            self.canvas.delete(self.rectangle)
        sx = self.photo.width() / self.image.width
        sy = self.photo.height() / self.image.height
        self.rectangle = self.canvas.create_rectangle(
            box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy,
            outline=ACCENT, width=2)
        self.width.set(box[2] - box[0])
        self.height.set(box[3] - box[1])

    def auto_crop(self):
        from .editing import plain_background_crop
        try:
            box = plain_background_crop(self.image, float(self.background_tolerance.get()),
                                        float(self.crop_padding.get()) / 100)
        except ValueError as error:
            self.crop_message.set(str(error))
            return
        self.set_crop(box)
        self.crop_message.set("Suggested crop ready. Check the base and thin details; drag to adjust, or choose Apply.")

    def reset_crop(self):
        self.set_crop((0, 0, self.image.width, self.image.height))
        self.crop_message.set("Full photo selected. No crop has been applied.")

    def render(self, value=None):
        from .editing import adjust
        image = self.base_preview if self.compare.get() else adjust(self.base_preview, **{k: v.get() for k, v in self.adjustments.items()})
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.itemconfigure(self.preview_item, image=self.photo)

    def start(self, event):
        self.origin = (max(0, event.x), max(0, event.y))
        if self.rectangle:
            self.canvas.delete(self.rectangle)
        self.rectangle = self.canvas.create_rectangle(
            *self.origin, *self.origin, outline=ACCENT, width=2
        )

    def drag(self, event):
        x = max(0, min(event.x, self.photo.width()))
        y = max(0, min(event.y, self.photo.height()))
        self.canvas.coords(self.rectangle, *self.origin, x, y)

    def finish(self, event):
        if not self.rectangle:
            return
        x1, y1, x2, y2 = self.canvas.coords(self.rectangle)
        if abs(x2 - x1) < 2 or abs(y2 - y1) < 2:
            return
        sx = self.image.width / self.photo.width()
        sy = self.image.height / self.photo.height()
        self.box = (
            int(min(x1, x2) * sx),
            int(min(y1, y2) * sy),
            min(self.image.width, round(max(x1, x2) * sx)),
            min(self.image.height, round(max(y1, y2) * sy)),
        )
        self.width.set(self.box[2] - self.box[0])
        self.height.set(self.box[3] - self.box[1])

    def apply(self):
        try:
            self.dialog.draft.edit(
                self.index, self.box, (int(self.width.get()), int(self.height.get())),
                adjustments={k: v.get() for k, v in self.adjustments.items()}
            )
        except Exception as error:
            self.dialog.app.fail(error)
            return
        self.dialog.refresh(self.index)
        self.image.close()
        self.window.destroy()


def main():
    root = TkinterDnD.Tk()
    App(root)
    root.mainloop()
