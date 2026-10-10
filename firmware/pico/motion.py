"""Portable, cooperative motion state machine. Times are monotonic microseconds."""


class Motion:
    def __init__(self, hardware, emit):
        self.hw = hardware
        self.emit = emit
        self.position = 0
        self.destination = 0.0
        self.known = False
        self.enabled = False
        self.steps_per_rev = 0
        self.speed = 200
        self.invert = False
        self.hall = False
        self.target = None
        self.operation = None
        self.phase = None
        self.next_step = 0
        self.last_contact = 0
        self.home_steps = 0
        self.home_deadline = 0
        self.home_limit = 0
        self.hw.enable(False)

    def snapshot(self):
        return dict(position=self.position, known=self.known, enabled=self.enabled,
                    busy=self.operation is not None, phase=self.phase,
                    steps_per_rev=self.steps_per_rev, speed=self.speed,
                    angle=(self.position * 360 / self.steps_per_rev) % 360
                    if self.known and self.steps_per_rev else None)

    def reply(self, ident, kind, message=""):
        self.emit(dict(id=ident, type=kind, message=message, state=self.snapshot()))

    def abort(self, message, release=False):
        previous = self.operation
        self.operation = self.target = self.phase = None
        self.known = False
        if release:
            self.enabled = False
            self.hw.enable(False)
        self.destination = self.position
        if previous is not None:
            self.reply(previous, "error", message)

    def handle(self, request, now):
        ident = request.get("id", 0)
        try:
            command = request["cmd"]
            self.last_contact = now
            if command in ("PING", "STATUS"):
                self.reply(ident, "status")
                return
            if command in ("STOP", "RELEASE"):
                self.abort("Motion stopped; reference position cleared", command == "RELEASE")
                self.reply(ident, "done")
                return
            if self.operation is not None:
                raise ValueError("Motor is busy")
            if command == "CONFIG":
                steps = int(request["steps_per_rev"])
                speed = int(request["speed"])
                if not 0 <= steps <= 1000000 or not 20 <= speed <= 1000:
                    raise ValueError("Calibration must be 0–1000000; speed must be 20–1000 pulses/s")
                if steps != self.steps_per_rev or bool(request.get("invert")) != self.invert:
                    self.known = False
                self.steps_per_rev, self.speed = steps, speed
                self.destination = self.position
                self.invert = bool(request.get("invert"))
                self.hall = bool(request.get("hall"))
            elif command == "ZERO":
                self.hw.enable(True)
                self.enabled = True
                self.position = 0
                self.destination = 0.0
                self.known = True
            elif command in ("JOG", "ROTATE", "RETURN", "HOME"):
                if command == "JOG":
                    delta = int(request["steps"])
                    if not 1 <= abs(delta) <= 100000:
                        raise ValueError("Jog must be 1–100000 pulses")
                    self.target = self.position + delta
                    self.destination = self.target
                else:
                    if not self.steps_per_rev:
                        raise ValueError("Calibrate pulses per turn first")
                    if command == "ROTATE":
                        degrees = int(request["degrees"])
                        if degrees not in (-60, 60):
                            raise ValueError("Rotation must be +60 or -60 degrees")
                        # Round absolute endpoints to avoid accumulating fractional-step error.
                        self.destination += degrees * self.steps_per_rev / 360
                        self.target = round(self.destination)
                    elif command == "RETURN":
                        if not self.known:
                            raise ValueError("Set home or use sensor homing first")
                        self.target = round(self.position / self.steps_per_rev) * self.steps_per_rev
                        self.destination = self.target
                    else:
                        if not self.hall:
                            raise ValueError("Hall sensor is not configured")
                        self.known = False
                        self.phase = "clear" if self.hw.hall_active() else "seek"
                        self.home_steps = 0
                        self.home_limit = self.steps_per_rev * 2
                        self.home_deadline = now + min(120000000, int(self.home_limit / min(self.speed, 200) * 1000000 + 2000000))
                self.hw.enable(True)
                self.enabled = True
                self.operation = ident
                self.next_step = now + 1000
                self.reply(ident, "accepted")
                return
            else:
                raise ValueError("Unknown command")
            self.reply(ident, "done")
        except (KeyError, ValueError, TypeError) as error:
            self.reply(ident, "error", str(error))

    def tick(self, now):
        if self.enabled and now - self.last_contact > 2000000:
            self.abort("Host heartbeat lost; motor released", True)
            self.reply(0, "error", "Host heartbeat lost; motor released")
        if self.operation is None:
            return
        if self.phase:
            if now > self.home_deadline or self.home_steps >= self.home_limit:
                self.abort("Homing limit reached; check sensor and calibration", True)
                return
            active = self.hw.hall_active()
            if self.phase == "clear" and not active:
                self.phase = "seek"
            elif self.phase == "seek" and active:
                self.position = 0
                self.destination = 0.0
                self.known = True
                self.finish()
                return
            direction = -1
        else:
            if self.position == self.target:
                self.finish()
                return
            direction = 1 if self.target > self.position else -1
        if now >= self.next_step:
            self.hw.step(direction, self.invert)
            self.position += direction
            self.home_steps += 1
            speed = min(self.speed, 200) if self.phase else self.speed
            self.next_step = now + int(1000000 / speed)

    def finish(self):
        ident = self.operation
        self.operation = self.target = self.phase = None
        self.reply(ident, "done")
