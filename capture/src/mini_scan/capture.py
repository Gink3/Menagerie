"""Six fresh captures, acknowledged moves, and an explicit return to zero."""

import time
import uuid


class Sequence:
    def __init__(
        self,
        send,
        camera,
        draft,
        notify,
        complete,
        settle=1.0,
        clock=time.monotonic,
        angle=None,
        metadata=lambda: None,
    ):
        self.send, self.camera, self.draft = send, camera, draft
        self.notify, self.complete, self.clock = notify, complete, clock
        self.metadata = metadata
        self.angle = angle
        self.settle = settle
        self.active = False
        self.wait_id = None
        self.token = None
        self.phase = "idle"
        self.index = 0
        self.remaining_moves = 0

    def start(self, state):
        if self.angle == "current":
            if state.get("busy"):
                raise ValueError("Wait for the motor to stop.")
            self.draft.mutable()
            self.active = True
            self.phase = "settle"
            self.deadline = self.clock() + self.settle
            return
        if self.angle is not None and self.angle not in (0, 60, 120, 180, 240, 300):
            raise ValueError("Choose an angle from 0 to 300 in 60 degree steps.")
        if state.get("busy"):
            raise ValueError("Wait for the motor to stop.")
        if not state.get("steps_per_rev"):
            raise ValueError(
                "Calibrate pulses per turn in Capture Station, then enter that value in Mini Scan."
            )
        self.draft.mutable()
        self.active = True
        self.index = 0
        self.move("HOME")

    def move(self, command, **kwargs):
        self.phase = command
        self.wait_id = self.send(command, **kwargs)
        self.deadline = self.clock() + 130
        self.notify(
            "Finding home…"
            if command == "HOME"
            else "Returning to zero…"
            if command == "RETURN"
            else "Rotating 60°…"
        )

    def event(self, event):
        if not self.active:
            return
        kind = event.get("type")
        if kind in (
            "connection_error",
            "disconnected",
            "camera_error",
            "camera_closed",
            "fault",
        ):
            self.cancel(event.get("message", "Device disconnected."))
            return
        if kind == "error" and event.get("id") == self.wait_id:
            self.cancel(event.get("message", "Motion failed."))
            return
        if (
            kind == "done"
            and self.wait_id is not None
            and event.get("id") == self.wait_id
        ):
            self.wait_id = None
            if self.phase == "RETURN":
                self.active = False
                self.complete(True, "Photos saved; turntable returned to zero.")
                return
            if self.phase == "HOME" and self.angle not in (None, 0):
                self.remaining_moves = self.angle // 60 - 1
                self.move("ROTATE", degrees=60)
                return
            if self.phase == "ROTATE" and self.remaining_moves:
                self.remaining_moves -= 1
                self.move("ROTATE", degrees=60)
                return
            if self.index == 6:
                self.move("RETURN")
                return
            self.phase = "settle"
            self.deadline = self.clock() + self.settle
        if (
            kind == "photo_saved"
            and self.token is not None
            and event.get("token") == self.token
        ):
            self.token = None
            try:
                self.draft.add_image(event["path"])
                entry = self.draft.data["images"][-1]
                if event.get("capture"):
                    entry["capture"] = event["capture"]
                entry["angle"] = (
                    self.index * 60
                    if self.angle is None
                    else None
                    if self.angle == "current"
                    else self.angle
                )
                self.draft.save()
                from pathlib import Path

                Path(event["path"]).unlink(missing_ok=True)
            except Exception as error:
                self.cancel(str(error))
                return
            self.index += 1
            if self.angle == "current":
                self.active = False
                self.complete(True, "Photo saved at the current position.")
            elif self.angle is not None:
                self.move("RETURN")
            else:
                self.move("ROTATE", degrees=60)

    def tick(self):
        if not self.active:
            return
        if self.clock() < self.deadline:
            return
        if self.phase == "settle":
            self.phase = "photo"
            self.deadline = self.clock() + 20
            self.token = uuid.uuid4().hex
            self.notify(
                f"Capturing photo {self.index + 1} of {6 if self.angle is None else 1}…"
            )
            self.camera.capture(
                self.token, self.draft.path / f"capture_{self.token}.png",
                metadata={"lighting": self.metadata()}
            )
        else:
            self.cancel(
                "Capture timed out. Check devices and home again before retrying."
            )

    def cancel(
        self,
        reason="Capture stopped. Partial photos retained; return to zero is not confirmed.",
    ):
        if not self.active:
            return
        self.active = False
        self.wait_id = None
        self.token = None
        self.send("STOP")
        self.complete(False, reason)
