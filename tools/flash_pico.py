"""Upload station firmware via mpremote, without requiring a VS Code extension."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = ('motion.py', 'pn532.py', 'rfid_config.py', 'diagnostics.py', 'lighting.py', 'main.py')


def choose_port(requested=None):
    if requested:
        return requested
    from serial.tools.list_ports import comports
    ports = [port.device for port in comports() if port.vid == 0x2E8A and port.pid == 0x0005]
    if len(ports) != 1:
        raise ValueError('Expected one MicroPython Pico; found {}. Use --port /dev/ttyACM0 (or a /dev/serial/by-id path) to select it.'.format(len(ports)))
    return ports[0]


def command(port):
    files = [ROOT / 'firmware' / 'pico' / name for name in FILES]
    checks = ['import hashlib, binascii']
    for path in files:
        data = path.read_bytes()
        compile(data, str(path), 'exec')
        digest = hashlib.sha256(data).hexdigest()
        checks.append("assert binascii.hexlify(hashlib.sha256(open({!r}, 'rb').read()).digest()).decode() == {!r}, {!r}".format(path.name, digest, 'Verification failed: ' + path.name))
    checks.append("print('All six firmware files verified')")
    # Exercise imports on MicroPython itself; desktop compilation is insufficient.
    smoke = (
        "import sys\n"
        "for name in ('motion', 'pn532', 'rfid_config', 'diagnostics', 'lighting'):\n"
        "    sys.modules.pop(name, None)\n"
        "import motion, pn532, rfid_config, diagnostics, lighting\n"
        "assert pn532.REVERSE[1] == 128 and pn532.REVERSE[128] == 1\n"
        "assert pn532.frame(2) == bytes.fromhex('0000ff02fed4022a00')\n"
        "assert diagnostics.HallMonitor(lambda: False).snapshot()['raw'] == 1\n"
        "levels = []\n"
        "lamp = lighting.Lighting(levels.append)\n"
        "lamp.handle({'enabled': True, 'brightness': 25}, 0)\n"
        "assert lamp.snapshot()['duty_u16'] == 16384\n"
        "lamp.tick(2000001)\n"
        "assert not lamp.enabled and levels[-1] == 0\n"
        "print('MicroPython firmware import checks passed')\n"
    )
    checks.append('exec(' + repr(smoke) + ')')
    args = [sys.executable, '-m', 'mpremote', 'connect', port,
            'exec', 'from machine import Pin, PWM; Pin(21, Pin.OUT, value=1); PWM(Pin(22), freq=20000, duty_u16=0).deinit(); Pin(22, Pin.OUT, value=0)', '+']
    # Copy dependencies before main.py; only reboot after every checksum matches.
    for path in files:
        args.extend(['fs', 'cp', str(path), ':' + path.name, '+'])
    args.extend(['exec', '; '.join(checks), '+', 'reset'])
    return args


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='Serial device; defaults to the single detected MicroPython Pico')
    parser.add_argument('--dry-run', action='store_true', help='Print the upload command without touching the Pico')
    options = parser.parse_args(argv)
    try:
        port = choose_port(options.port)
        args = command(port)
        if options.dry_run:
            print(shlex.join(args))
            return 0
        if importlib.util.find_spec('mpremote') is None:
            raise ValueError("Install upload support: capture/.venv/bin/python -m pip install -e 'capture[pico]'")
        # Detect another exclusive owner before mpremote interrupts the running program.
        import serial
        with serial.Serial(port, 115200, timeout=.1, exclusive=True):
            pass
        print('Uploading to {}. Disconnect the desktop app and close Thonny first.'.format(port), flush=True)
        result = subprocess.run(args, timeout=180)
        if result.returncode:
            print('Upload failed. Do not assume the firmware was updated. Check USB/port ownership and rerun the upload.', file=sys.stderr)
            return result.returncode
        print('Firmware uploaded, verified and import-tested on MicroPython; Pico restarted. Reconnect the desktop app and open Sensor tests.')
        return 0
    except (ValueError, OSError, SyntaxError, subprocess.TimeoutExpired) as error:
        print('Upload error: {}. Disconnect the app and close Thonny before retrying.'.format(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
