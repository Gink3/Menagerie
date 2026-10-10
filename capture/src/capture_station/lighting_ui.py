"""Shared, acknowledged lighting controls for both desktop applications."""

import time
import tkinter as tk
from tkinter import ttk


class LightingPanel:
    def __init__(self, parent, send, available, busy, settings, save):
        self.send, self.available, self.busy = send, available, busy
        self.settings, self.save = settings, save
        self.frame = ttk.LabelFrame(parent, text="Lighting", padding=10)
        self.frame.pack(fill="x", pady=8)
        self.enabled = tk.BooleanVar(value=False)
        try:
            brightness = max(0, min(100, int(settings.get("light_brightness", 100))))
        except (ValueError, TypeError):
            brightness = 100
        self.brightness = tk.DoubleVar(value=brightness)
        self.percent = tk.StringVar(value=f"{brightness}%")
        self.toggle = ttk.Checkbutton(
            self.frame, text="Light on", variable=self.enabled, command=self.apply
        )
        self.toggle.pack(anchor="w")
        controls = ttk.Frame(self.frame)
        controls.pack(fill="x")
        ttk.Label(controls, text="Brightness").pack(side="left")
        self.slider = ttk.Scale(
            controls, from_=0, to=100, variable=self.brightness, command=self.changed
        )
        self.slider.pack(side="left", fill="x", expand=True, padx=10)
        ttk.Label(controls, textvariable=self.percent, width=5).pack(side="left")
        self.status = tk.StringVar(value="Connect the Pico to control the light.")
        ttk.Label(self.frame, textvariable=self.status, wraplength=700).pack(anchor="w")
        self.pending = None
        self.timer = None
        self.supported = None
        self.initialized = False
        self.reported = None
        self.update()

    def capture_settings(self):
        if not self.available() or not self.initialized or self.pending is not None or not self.reported:
            return None
        return {k: self.reported[k] for k in ("enabled", "brightness", "gpio", "frequency", "duty_u16", "simulated") if k in self.reported}

    def cancel_change(self):
        if self.timer is not None:
            self.frame.after_cancel(self.timer)
            self.timer = None

    def changed(self, value):
        self.percent.set(f"{round(float(value))}%")
        self.cancel_change()
        if (
            self.available()
            and self.supported
            and self.initialized
            and not self.busy()
            and self.pending is None
        ):
            self.timer = self.frame.after(150, self.apply)

    def apply(self):
        self.cancel_change()
        if (
            not self.available()
            or not self.supported
            or self.busy()
            or self.pending is not None
        ):
            self.sync()
            return
        brightness = round(self.brightness.get())
        ident = self.send("LIGHT", enabled=self.enabled.get(), brightness=brightness)
        if ident is not None:
            self.pending = ident
            self.sent_at = time.monotonic()
            self.settings["light_brightness"] = brightness
            self.save()
            self.status.set("Applying light settings…")
        self.update()

    def sync(self):
        if self.reported and self.initialized:
            self.enabled.set(self.reported["enabled"])
            self.brightness.set(self.reported["brightness"])
            self.percent.set(f"{self.reported['brightness']}%")

    def on_event(self, event):
        kind = event.get("type")
        if kind in ("connected", "disconnected", "connection_error"):
            self.cancel_change()
            self.pending = None
            self.supported = None
            self.initialized = False
            self.reported = None
            self.enabled.set(False)
        light = event.get("lighting")
        if isinstance(light, dict):
            self.supported = True
            self.reported = light
        elif "state" in event and self.supported is None:
            self.supported = False
        if (
            self.pending is not None
            and event.get("id") == self.pending
            and kind in ("done", "error")
        ):
            self.pending = None
            self.sync()
            if kind == "error":
                self.status.set(event.get("message", "Light command rejected."))
                self.update(show_status=False)
                return
        if self.pending is None and self.timer is None:
            self.sync()
        self.update()

    def update(self, show_status=True):
        available = self.available()
        busy = self.busy()
        if busy or not available:
            self.cancel_change()
            self.sync()
        if self.pending is not None and time.monotonic() - self.sent_at > 3:
            self.pending = None
            self.sync()
        if available and self.supported and not self.initialized and not busy:
            # Restore only the brightness preference. A new host session always starts off.
            self.initialized = True
            self.enabled.set(False)
            self.apply()
            return
        editable = (
            available
            and self.supported
            and self.initialized
            and not busy
            and self.pending is None
        )
        for control in (self.toggle, self.slider):
            control.state(["!disabled"] if editable else ["disabled"])
        if not show_status or self.pending is not None:
            return
        if not available:
            message = "Disconnected · light controls unavailable."
        elif self.supported is False:
            message = "Update the Pico firmware to enable lighting controls."
        elif self.reported:
            message = ("Simulator · " if self.reported.get("simulated") else "") + (
                f"Light on · {self.reported['brightness']}%"
                if self.reported["enabled"]
                else "Light off"
            )
            if busy:
                message += " · settings locked during motion/capture"
        else:
            message = "Waiting for lighting status…"
        self.status.set(message)
