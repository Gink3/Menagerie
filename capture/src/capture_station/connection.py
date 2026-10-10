"""Serial I/O stays off the UI thread; simulator runs the real motion state machine."""
import json
import queue
import threading
import time

from menagerie_pico.motion import Motion
from menagerie_pico.lighting import Lighting
from menagerie_pico.diagnostics import HallMonitor


class SimHardware:
    def __init__(self):
        self.position = 0
        self.revolution = 3200
        self.light_duty = 0

    def set_light(self, duty):
        self.light_duty = duty

    def enable(self, active):
        pass

    def step(self, direction, invert):
        self.position += direction

    def hall_active(self):
        return self.position % self.revolution < 10


class Connection:
    def __init__(self, port, events):
        self.port = port
        self.events = events
        self.commands = queue.Queue()
        self.closing = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, request):
        self.commands.put(request)

    def close(self):
        self.closing.set()

    def run(self):
        serial_port = None
        lighting = None
        try:
            simulated = self.port == "Simulator"
            if simulated:
                hw = SimHardware()
                hall_monitor = HallMonitor(hw.hall_active)
                lighting = Lighting(hw.set_light)
                def emit_simulated(event):
                    event['sensors'] = dict(hall=hall_monitor.snapshot())
                    event['lighting'] = dict(lighting.snapshot(), simulated=True)
                    event['rfid'] = dict(status='simulated', message='Simulator: no physical RFID reader is being tested')
                    self.events.put(event)
                controller = Motion(hw, emit_simulated)
            else:
                import serial
                serial_port = serial.Serial(self.port, 115200, timeout=0, write_timeout=0.25, exclusive=True)
            connected = simulated
            if connected:
                self.events.put({"type": "connected"})
            heartbeat = 0
            last_response = time.monotonic()
            buffer = bytearray()
            diagnostic = bytearray()
            while not self.closing.is_set():
                now = time.monotonic()
                outgoing = []
                for _ in range(16):
                    try:
                        outgoing.append(self.commands.get_nowait())
                    except queue.Empty:
                        break
                if now >= heartbeat:
                    outgoing.append({"id": 0, "cmd": "PING"})
                    heartbeat = now + 0.5
                for request in outgoing:
                    if simulated:
                        lighting.touch(int(now * 1000000))
                        if request.get('cmd') == 'LIGHT':
                            try:
                                lighting.handle(request, int(now * 1000000), busy=controller.operation is not None)
                                controller.reply(request.get('id', 0), 'done', 'Simulated light updated')
                            except (ValueError, TypeError) as error:
                                controller.reply(request.get('id', 0), 'error', str(error))
                        elif request.get('cmd') == 'HALL_TEST':
                            hall_monitor.reset()
                            controller.reply(request.get('id', 0), 'done', 'Simulated Hall test reset')
                        elif request.get('cmd') in ('RFID_SCAN', 'RFID_STATUS', 'RFID_TEST'):
                            controller.reply(request.get('id', 0), 'done', 'RFID hardware is unavailable in motor Simulator mode')
                        else:
                            controller.handle(request, int(now * 1000000))
                        hw.revolution = controller.steps_per_rev or 3200
                    else:
                        serial_port.write((json.dumps(request) + "\n").encode())
                if simulated:
                    if lighting.tick(int(now * 1000000)):
                        controller.reply(0, "status", "Host heartbeat lost; simulated light switched off")
                    controller.tick(int(now * 1000000))
                    hall_monitor.tick(int(now * 1000000))
                else:
                    incoming = serial_port.read(4096)
                    buffer.extend(incoming)
                    diagnostic.extend(incoming)
                    diagnostic = diagnostic[-2048:]
                    while b"\n" in buffer:
                        line, _, remaining = buffer.partition(b"\n")
                        buffer = bytearray(remaining)
                        try:
                            event = json.loads(line)
                            state = event.get("state") if isinstance(event, dict) else None
                            valid_state = isinstance(state, dict) and all(
                                key in state for key in ("busy", "position", "enabled", "known", "steps_per_rev")
                            )
                            if isinstance(event, dict) and "type" in event and (connected or valid_state):
                                if not connected:
                                    connected = True
                                    self.events.put({"type": "connected"})
                                self.events.put(event)
                                last_response = now
                        except (ValueError, UnicodeDecodeError):
                            pass  # Retain boot/REPL text for connection diagnostics.
                    if len(buffer) > 16384:
                        raise RuntimeError("Invalid serial data: response exceeds limit")
                    if now - last_response > 3:
                        if connected:
                            raise RuntimeError("Pico stopped responding; reconnect its USB cable.")
                        detail = diagnostic.decode(errors="replace").strip()
                        if detail:
                            raise RuntimeError(
                                "Serial port opened, but capture firmware did not respond. "
                                "Upload all six firmware files, including firmware/pico/main.py, firmware/pico/motion.py and firmware/pico/diagnostics.py, to the Pico, "
                                "then restart it and close Thonny. Pico output: " + detail[-600:]
                            )
                        raise RuntimeError(
                            "Serial port opened, but the Pico sent no data. Reconnect its USB cable. "
                            "Ensure all six firmware files, including firmware/pico/main.py, firmware/pico/motion.py and firmware/pico/diagnostics.py, are installed on "
                            "the Pico, then restart it and close Thonny."
                        )
                self.closing.wait(0.001)
        except Exception as error:
            self.events.put({"type": "connection_error", "message": str(error)})
        finally:
            if lighting:
                lighting.off()
            if serial_port:
                try:
                    serial_port.write(b'{"id":0,"cmd":"LIGHT","enabled":false}\n{"id":0,"cmd":"RELEASE"}\n')
                except Exception:
                    pass
                serial_port.close()
            self.events.put({"type": "disconnected"})
