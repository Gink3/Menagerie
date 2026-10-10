"""Cooperative PN532 ISO14443A UID reader, based on NXP UM0701-02.

No waiting loops: call tick() with the station's monotonic microsecond clock.
SPI transactions are skipped while the motor is moving. This reads identity,
not MIFARE/NTAG memory, NDEF records, or protected blocks.
"""

ACK = b'\x00\x00\xff\x00\xff\x00'


def reverse_byte(value):
    # RP2040 MicroPython does not support slices with a negative step.
    value = ((value & 0x55) << 1) | ((value >> 1) & 0x55)
    value = ((value & 0x33) << 2) | ((value >> 2) & 0x33)
    return ((value & 0x0F) << 4) | (value >> 4)


REVERSE = bytes(reverse_byte(n) for n in range(256))


def frame(command, parameters=b''):
    body = bytes((0xD4, command)) + parameters
    size = len(body)
    return b'\x00\x00\xff' + bytes((size, (-size) & 255)) + body + bytes(((-sum(body)) & 255, 0))


def decode_response(raw, command):
    if len(raw) < 9 or raw[:3] != b'\x00\x00\xff':
        raise ValueError('Invalid PN532 frame header')
    size = raw[3]
    if not 2 <= size <= 254 or (size + raw[4]) & 255 or len(raw) != size + 7:
        raise ValueError('Invalid PN532 frame length')
    body = raw[5:5 + size]
    if (sum(body) + raw[-2]) & 255 or raw[-1] != 0:
        raise ValueError('PN532 response checksum failed')
    if body[:2] != bytes((0xD5, command + 1)):
        raise ValueError('Unexpected PN532 response command')
    return body[2:]


def parse_tag(payload):
    if payload == b'\x00':
        return None
    if len(payload) < 6 or payload[0] != 1 or payload[1] != 1:
        raise ValueError('Invalid PN532 target response')
    length = payload[5]
    if length not in (4, 7, 10) or len(payload) < 6 + length:
        raise ValueError('Invalid tag UID length')
    return dict(uid=''.join('{:02X}'.format(n) for n in payload[6:6 + length]),
                uid_length=length, atqa=''.join('{:02X}'.format(n) for n in payload[2:4]),
                sak='{:02X}'.format(payload[4]), technology='ISO14443A')


class SPITransport:
    """MSB-first SPI with byte reversal for PN532's LSB-first wire format."""
    def __init__(self, spi, cs):
        self.spi, self.cs = spi, cs
        self.cs.value(1)

    def select(self, selected):
        self.cs.value(0 if selected else 1)

    def write(self, data):
        self.select(True)
        try:
            self.spi.write(bytes(REVERSE[n] for n in b'\x01' + data))
        finally:
            self.select(False)

    def ready(self):
        self.select(True)
        try:
            self.spi.write(bytes((REVERSE[2],)))
            return REVERSE[self.spi.read(1)[0]] == 1
        finally:
            self.select(False)

    def read(self, ack=False):
        self.select(True)
        try:
            self.spi.write(bytes((REVERSE[3],)))
            if ack:
                return bytes(REVERSE[n] for n in self.spi.read(6))
            header = bytes(REVERSE[n] for n in self.spi.read(5))
            if header[:3] != b'\x00\x00\xff' or not 2 <= header[3] <= 254 or (header[3] + header[4]) & 255:
                raise ValueError('Invalid PN532 response header; check wiring/SPI mode')
            return header + bytes(REVERSE[n] for n in self.spi.read(header[3] + 2))
        finally:
            self.select(False)


class Reader:
    # Normal SAM mode, version query, finite passive activation retries, scan.
    COMMANDS = ((0x14, b'\x01\x14\x00'), (0x02, b''),
                (0x32, b'\x05\xff\x01\x00'), (0x4A, b'\x01\x00'))

    def __init__(self, transport, emit):
        self.bus, self.emit = transport, emit
        self.status = 'starting'
        self.message = 'Starting PN532'
        self.version = None
        self.tag = None
        self.reported_uid = None
        self.misses = 0
        self.stage = 'wake'
        self.index = 0
        self.next_poll = 0
        self.deadline = 0
        self.paused_at = None

    def snapshot(self):
        return dict(status=self.status, message=self.message, firmware=self.version, tag=self.tag)

    def rescan(self):
        self.reported_uid = None
        if self.stage == 'retry':
            self.next_poll = 0
        return 'Waiting for a tag; scanning resumes when the motor is idle'

    def restart(self):
        self.bus.select(False)
        self.status = 'starting'
        self.message = 'Testing PN532 SPI communication…'
        self.version = None
        self.tag = None
        self.reported_uid = None
        self.misses = 0
        self.stage = 'wake'
        self.index = 0
        self.next_poll = 0
        self.paused_at = None
        return self.message

    def fail(self, error, now):
        self.bus.select(False)
        self.status = 'error'
        self.message = 'PN532: {}. Check SPI mode, wiring and power.'.format(error)
        self.tag = None
        self.reported_uid = None
        self.stage = 'retry'
        self.next_poll = now + 5000000
        self.emit(dict(type='rfid_error', message=self.message))

    def tick(self, now, paused=False):
        if paused:
            if self.paused_at is None:
                self.paused_at = now
            return
        if self.paused_at is not None:
            duration = now - self.paused_at
            self.deadline += duration
            self.next_poll += duration
            self.paused_at = None
        if now < self.next_poll:
            return
        self.next_poll = now + 5000
        try:
            self.advance(now)
        except (OSError, ValueError, IndexError) as error:
            self.fail(error, now)

    def advance(self, now):
        if self.stage in ('wake', 'retry'):
            self.status = 'starting'
            self.message = 'Starting PN532'
            self.bus.select(True)
            self.stage = 'waking'
            self.next_poll = now + 10000
        elif self.stage == 'waking':
            self.bus.select(False)
            self.bus.write(ACK)  # Abort a leftover command after host restart.
            self.index = 0
            self.stage = 'send'
        elif self.stage == 'send':
            command, params = self.COMMANDS[self.index]
            self.bus.write(frame(command, params))
            self.stage = 'ack'
            self.deadline = now + 1000000
        elif self.stage in ('ack', 'response'):
            if not self.bus.ready():
                if now >= self.deadline:
                    raise ValueError('timed out waiting for ' + self.stage)
                return
            if self.stage == 'ack':
                if self.bus.read(ack=True) != ACK:
                    raise ValueError('invalid acknowledgement')
                self.stage = 'response'
                self.deadline = now + 1000000
            else:
                payload = decode_response(self.bus.read(), self.COMMANDS[self.index][0])
                self.receive(payload)
                self.stage = 'send'
                if self.index < 3:
                    self.index += 1
                else:
                    self.next_poll = now + 150000

    def receive(self, payload):
        if self.index == 1:
            if len(payload) != 4 or payload[0] != 0x32:
                raise ValueError('unexpected PN532 firmware version')
            self.version = '{}.{}'.format(payload[1], payload[2])
        elif self.index == 2:
            self.status = 'ready'
            self.message = 'PN532 ready — scan a miniature tag'
            self.emit(dict(type='rfid_status', **self.snapshot()))
        elif self.index == 3:
            tag = parse_tag(payload)
            if tag is None:
                self.misses += 1
                if self.misses >= 2:
                    self.tag = None
                    self.reported_uid = None
                return
            self.misses = 0
            self.tag = tag
            if tag['uid'] != self.reported_uid:
                self.reported_uid = tag['uid']
                self.emit(dict(type='rfid', message='Tag scanned: ' + tag['uid'], **tag))


def create_reader(config, emit):
    """Validate all pins before touching GPIO. None leaves RFID unconfigured."""
    if config is None:
        return None
    pins = [config[name] for name in ('miso', 'cs', 'sck', 'mosi')]
    if len(set(pins)) != 4 or set(pins) != {12, 13, 14, 15}:
        raise ValueError('PN532 pins must each use one of GP12–GP15; motor/Hall pins are reserved')
    from machine import Pin, SoftSPI
    cs = Pin(config['cs'], Pin.OUT, value=1)
    spi = SoftSPI(baudrate=500000, polarity=0, phase=0,
                  sck=Pin(config['sck']), mosi=Pin(config['mosi']), miso=Pin(config['miso']))
    return Reader(SPITransport(spi, cs), emit)
