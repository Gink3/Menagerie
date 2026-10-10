"""Live physical sensor diagnostics; never claims a simulated device is verified."""
import tkinter as tk
from tkinter import ttk


class DiagnosticsPanel:
    def __init__(self, parent, app):
        self.app = app
        self.hall = tk.StringVar(value='Connect the Pico to read GP26.')
        self.hall_result = tk.StringVar(value='Not tested')
        self.reader = tk.StringVar(value='Connect the Pico to test PN532 communication.')
        self.tag = tk.StringVar(value='No tag scanned in this test')
        self.simulated = False
        ttk.Label(parent, text='Sensor connection tests', font=('', 16, 'bold')).pack(anchor='w', pady=8)
        ttk.Label(parent, text='Connect on the Motor tab first. These tests do not rotate or home the motor.', wraplength=740).pack(anchor='w')
        hall = ttk.LabelFrame(parent, text='Hall sensor — GP26', padding=12)
        hall.pack(fill='x', pady=12)
        ttk.Label(hall, text='VCC → 3V3(OUT); GND → shared GND; digital OUT → GP26.\nStart the test, then bring the magnet near the sensor and move it away.\nExpected: HIGH (1) away; LOW (0) near. Try the opposite magnet pole if necessary.', wraplength=740).pack(anchor='w')
        app.button(hall, 'Start / reset Hall test', self.test_hall).pack(anchor='w', pady=8)
        ttk.Label(hall, textvariable=self.hall, wraplength=740).pack(anchor='w')
        ttk.Label(hall, textvariable=self.hall_result, wraplength=740).pack(anchor='w', pady=6)
        ttk.Label(hall, text='A steady HIGH also occurs with an unplugged sensor. A steady LOW may mean a nearby magnet, a short, or the wrong output type. Both levels must be observed to verify a change.', wraplength=740).pack(anchor='w')
        reader = ttk.LabelFrame(parent, text='RFID reader — PN532 over SPI', padding=12)
        reader.pack(fill='x', pady=12)
        ttk.Label(reader, text='MISO GP16 · SS/CS GP17 · SCK GP18 · MOSI GP19\nTest the reader first, then hold a tag near its antenna. A firmware-version response verifies SPI communication; a UID verifies that a tag was read.', wraplength=740).pack(anchor='w')
        app.button(reader, 'Test RFID reader / scan tag', self.test_reader).pack(anchor='w', pady=8)
        ttk.Label(reader, textvariable=self.reader, wraplength=740).pack(anchor='w')
        ttk.Label(reader, textvariable=self.tag, wraplength=740).pack(anchor='w', pady=6)
        ttk.Label(reader, text='SPI mode is selected on the PN532 module, usually by switches or jumpers. Their positions vary by board. A timeout cannot distinguish wrong mode from wiring or power problems.', wraplength=740).pack(anchor='w')

    def test_hall(self):
        self.hall_result.set('Testing: move the magnet near and away from GP26 sensor…')
        self.app.send('HALL_TEST')

    def test_reader(self):
        self.reader.set('Testing: requesting a fresh PN532 initialization…')
        self.tag.set('No tag scanned in this test')
        self.app.send('RFID_TEST')

    def on_event(self, event):
        kind = event.get('type')
        if kind == 'connected':
            self.simulated = self.app.port.get() == 'Simulator'
            self.hall.set('Waiting for sensor telemetry; upload updated firmware if this persists.')
            self.hall_result.set('Not tested')
            self.reader.set('Waiting for reader status; upload updated firmware if this persists.')
            self.tag.set('No tag scanned in this connection')
        elif kind == 'disconnected':
            self.hall.set('Pico disconnected')
            self.hall_result.set('Not tested — connect again')
            self.reader.set('Pico disconnected')
            self.tag.set('No current tag reading')
        hall = event.get('sensors', {}).get('hall')
        if hall:
            prefix = 'SIMULATED — ' if self.simulated else ''
            self.hall.set(prefix + 'GP26: {} ({}) — {} — {} transition(s)'.format(
                'LOW' if hall['raw'] == 0 else 'HIGH', hall['raw'],
                'magnet detected' if hall['active'] else 'magnet not detected', hall['transitions']))
            if self.simulated:
                self.hall_result.set('Simulator readings do not test physical wiring.')
            elif hall['toggled']:
                self.hall_result.set('Signal changed: HIGH and LOW both observed. Check that each change follows your magnet.')
            else:
                self.hall_result.set('Not verified yet: waiting to observe both HIGH and LOW.')
        reader = event.get('rfid')
        if kind == 'rfid_status':
            reader = event
        if reader:
            if reader.get('status') == 'ready' and reader.get('firmware'):
                self.reader.set('SPI communication verified — PN532 firmware ' + reader['firmware'])
            else:
                self.reader.set(reader.get('message', 'RFID status unavailable'))
        if kind == 'rfid_error':
            self.reader.set(event.get('message', 'RFID error'))
        if kind == 'rfid':
            self.tag.set('Tag read: {}\nUID bytes: {} · ATQA: {} · SAK: {}'.format(
                event.get('uid', '?'), event.get('uid_length', '?'), event.get('atqa', '?'), event.get('sak', '?')))
