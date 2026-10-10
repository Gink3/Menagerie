"""Read-only Hall diagnostics. High alone does not prove a sensor is connected."""


class HallMonitor:
    def __init__(self, read_active):
        self.read_active = read_active
        self.next_sample = 0
        self.reset()

    def reset(self):
        self.active = bool(self.read_active())
        self.seen_high = not self.active
        self.seen_low = self.active
        self.transitions = 0

    def tick(self, now):
        if now < self.next_sample:
            return
        self.next_sample = now + 10000
        active = bool(self.read_active())
        if active != self.active:
            self.transitions += 1
        self.active = active
        self.seen_high = self.seen_high or not active
        self.seen_low = self.seen_low or active

    def snapshot(self):
        return dict(gpio=26, raw=0 if self.active else 1, active=self.active,
                    transitions=self.transitions, seen_high=self.seen_high,
                    seen_low=self.seen_low, toggled=self.seen_high and self.seen_low)
