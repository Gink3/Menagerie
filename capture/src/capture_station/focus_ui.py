"""Click/drag focus area, magnified native-pixel crop and a relative best score."""

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk


class FocusPanel:
    def __init__(self, parent, preview, camera, context=lambda: None):
        self.preview, self.camera, self.context = preview, camera, context
        self.frame = ttk.LabelFrame(parent, text="Manual focus aid", padding=8)
        self.frame.pack(fill="x", pady=6)
        self.message = tk.StringVar(value="Click a detail or drag an area in the preview, then turn the lens.")
        ttk.Label(self.frame, textvariable=self.message, wraplength=650).pack(anchor="w")
        ttk.Label(self.frame, text="Compare the same detail with lighting/exposure fixed. Higher is sharper; this is not a universal focus rating.", wraplength=650).pack(anchor="w")
        ttk.Button(self.frame, text="Reset best score", command=self.reset).pack(anchor="w")
        self.zoom = ttk.Label(self.frame)
        self.zoom.pack(anchor="w")
        self.best = None
        self.last = None
        self.signature = None
        self.size = (1, 1)
        self.roi = (0.4, 0.4, 0.6, 0.6)
        preview.bind("<ButtonPress-1>", self.start)
        preview.bind("<ButtonRelease-1>", self.finish)

    def reset(self):
        self.best = None

    def point(self, event):
        width, height = self.size
        x = event.x - max(0, (self.preview.winfo_width() - width) / 2)
        y = event.y - max(0, (self.preview.winfo_height() - height) / 2)
        return max(0, min(1, x / width)), max(0, min(1, y / height))

    def start(self, event):
        self.origin = self.point(event)

    def finish(self, event):
        camera = self.camera()
        if not camera or not hasattr(self, "origin"):
            return
        x, y = self.point(event)
        ox, oy = self.origin
        if abs(x - ox) * self.size[0] < 8 and abs(y - oy) * self.size[1] < 8:
            self.roi = (max(0, x - .07), max(0, y - .07), min(1, x + .07), min(1, y + .07))
        else:
            self.roi = (min(x, ox), min(y, oy), max(x, ox), max(y, oy))
        if min(self.roi[2] - self.roi[0], self.roi[3] - self.roi[1]) <= 0:
            return
        camera.set_focus_roi(self.roi)
        self.reset()

    def decorate(self, image):
        self.size = image.size
        image = image.copy()
        ImageDraw.Draw(image).rectangle((self.roi[0] * image.width, self.roi[1] * image.height,
                                        self.roi[2] * image.width - 1, self.roi[3] * image.height - 1), outline="#b68bff", width=2)
        return image

    def update(self):
        camera = self.camera()
        if not camera:
            self.best = self.last = None
            self.zoom.configure(image="")
            self.message.set("Open a camera to measure focus.")
            return
        result = camera.get_focus()
        if not result or self.last == (id(camera), result[0]):
            return
        self.last = (id(camera), result[0])
        _, data, crop = result
        signature = (id(camera), tuple(data["roi"]), repr(self.context()), crop.size)
        if signature != self.signature:
            self.best = None
            self.signature = signature
        score = data["score"]
        if score is None:
            self.message.set("Too little detail to measure focus. Choose an edge or textured area.")
        else:
            self.best = max(self.best or score, score)
            self.message.set(f"Sharpness: {score:.1f} · best: {self.best:.1f} · {(100 * score / self.best if self.best else 0):.0f}% of best in this area")
        crop = crop.copy()
        crop.thumbnail((200, 160), Image.Resampling.NEAREST)
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(crop)
        self.zoom.configure(image=self.photo)
