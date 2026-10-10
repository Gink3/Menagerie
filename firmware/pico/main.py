"""Upload main.py, motion.py, pn532.py, rfid_config.py, diagnostics.py and lighting.py."""
from machine import Pin, PWM
import time
import sys
import select
import json
from motion import Motion
from diagnostics import HallMonitor
from lighting import Lighting, PWM_HZ, GPIO


class Hardware:
    def __init__(self):
        self.en = Pin(21, Pin.OUT, value=1)
        self.light_pin = Pin(GPIO, Pin.OUT, value=0)
        self.light_pwm = PWM(self.light_pin, freq=PWM_HZ, duty_u16=0)
        self.pulse = Pin(19, Pin.OUT, value=0)
        self.direction = Pin(20, Pin.OUT, value=0)
        # Optional active-low Hall sensor: GP26, physical pin 31.
        # Sensor VCC uses 3V3(OUT), not a GPIO. Leave Hall disabled until wired.
        self.sensor = Pin(26, Pin.IN, Pin.PULL_UP)

    def set_light(self, duty):
        self.light_pwm.duty_u16(duty)

    def enable(self, active):
        self.en.value(0 if active else 1)

    def step(self, direction, invert):
        self.direction.value((direction > 0) != invert)
        time.sleep_us(3)
        self.pulse.value(1)
        time.sleep_us(3)
        self.pulse.value(0)

    def hall_active(self):
        return self.sensor.value() == 0


reader = None
reader_message = 'RFID unconfigured: set PN532_PINS in rfid_config.py after checking wiring'


def emit(event):
    if 'state' in event:
        event['rfid'] = reader.snapshot() if reader else dict(status='disabled', message=reader_message)
        event['sensors'] = dict(hall=hall_monitor.snapshot())
        event['lighting'] = lighting.snapshot()
    print(json.dumps(event))


hardware = Hardware()
lighting = Lighting(hardware.set_light)
hall_monitor = HallMonitor(hardware.hall_active)
try:
    from rfid_config import PN532_PINS
    from pn532 import create_reader
    reader = create_reader(PN532_PINS, emit)
except Exception as error:
    # Optional RFID startup failures must not prevent USB/motor/Hall control.
    reader_message = 'RFID unavailable: ' + str(error)
controller = Motion(hardware, emit)
poller = select.poll()
poller.register(sys.stdin, select.POLLIN)
buffer = ""
discarding = False
previous = time.ticks_us()
now = 0
try:
    while True:
        current = time.ticks_us()
        now += time.ticks_diff(current, previous)
        previous = current
        if lighting.tick(now):
            controller.reply(0, "status", "Host heartbeat lost; light switched off")
        controller.tick(now)
        hall_monitor.tick(now)
        # Bound input work so even partial or excessive input cannot block STOP/motion.
        for _ in range(32):
            if not poller.poll(0):
                break
            char = sys.stdin.read(1)
            if char == "\n":
                if not discarding and buffer.strip():
                    try:
                        request = json.loads(buffer)
                        if not isinstance(request, dict):
                            raise ValueError("Expected JSON object")
                        command = request.get('cmd')
                        lighting.touch(now)
                        if command == 'LIGHT':
                            try:
                                lighting.handle(request, now, busy=controller.operation is not None)
                                controller.reply(request.get('id', 0), 'done', 'Light updated')
                            except (ValueError, TypeError) as error:
                                controller.reply(request.get('id', 0), 'error', str(error))
                        elif command in ('HALL_TEST', 'RFID_TEST') and controller.operation is not None:
                            controller.reply(request.get('id', 0), 'error', 'Wait for motion to finish before testing sensors')
                        elif command == 'HALL_TEST':
                            hall_monitor.reset()
                            controller.reply(request.get('id', 0), 'done', 'Hall test reset; move magnet near and away')
                        elif command in ('RFID_SCAN', 'RFID_STATUS', 'RFID_TEST'):
                            message = reader_message
                            if reader:
                                if command == 'RFID_TEST':
                                    message = reader.restart()
                                else:
                                    message = reader.rescan() if command == 'RFID_SCAN' else reader.message
                            controller.reply(request.get('id', 0), 'done', message)
                        else:
                            controller.handle(request, now)
                    except (ValueError, TypeError) as error:
                        emit(dict(id=0, type="error", message=str(error)))
                buffer = ""
                discarding = False
            elif not discarding:
                buffer += char
                if len(buffer) > 512:
                    buffer = ""
                    discarding = True
        if reader:
            reader.tick(now, paused=controller.operation is not None)
        time.sleep_us(100)
finally:
    try:
        lighting.off()
    finally:
        hardware.enable(False)
