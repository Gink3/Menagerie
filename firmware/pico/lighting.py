"""Portable lighting state; hardware PWM is supplied by the Pico or simulator."""

PWM_HZ = 20000
GPIO = 22


class Lighting:
    def __init__(self, write_duty):
        self.write_duty = write_duty
        self.enabled = False
        self.brightness = 100
        self.last_contact = 0
        self.off()

    def snapshot(self):
        return dict(
            enabled=self.enabled,
            brightness=self.brightness,
            gpio=GPIO,
            frequency=PWM_HZ,
            duty_u16=round(self.brightness * 65535 / 100) if self.enabled else 0,
        )

    def touch(self, now):
        self.last_contact = now

    def handle(self, request, now, busy=False):
        enabled = request.get("enabled")
        brightness = request.get("brightness", self.brightness)
        if not isinstance(enabled, bool):
            raise ValueError("Light enabled must be true or false")
        if (
            isinstance(brightness, bool)
            or not isinstance(brightness, int)
            or not 0 <= brightness <= 100
        ):
            raise ValueError("Light brightness must be an integer from 0 to 100")
        if busy and enabled:
            raise ValueError("Wait for motion to finish before changing the light")
        self.write_duty(round(brightness * 65535 / 100) if enabled else 0)
        self.enabled, self.brightness = enabled, brightness
        self.touch(now)

    def off(self):
        self.write_duty(0)
        self.enabled = False

    def tick(self, now):
        if self.enabled and now - self.last_contact > 2000000:
            self.off()
            return True
        return False
