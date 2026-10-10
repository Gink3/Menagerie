import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from menagerie_pico.pn532 import ACK, REVERSE, Reader, SPITransport, create_reader, decode_response, frame, parse_tag


def response(command, payload):
    body = bytes((0xD5, command + 1)) + payload
    return b'\x00\x00\xff' + bytes((len(body), (-len(body)) & 255)) + body + bytes(((-sum(body)) & 255, 0))


TAG = bytes.fromhex('010100040004DEADBEEF')


class FakeBus:
    def __init__(self):
        self.messages = []
        self.reply = []
        self.tag = TAG
        self.respond = True
        self.selected = False

    def select(self, selected):
        self.selected = selected

    def write(self, packet):
        self.messages.append(packet)
        if packet == ACK:
            self.reply.clear()
            return
        command = packet[6]
        payload = {0x14: b'', 0x02: bytes.fromhex('32010607'), 0x32: b'', 0x4A: self.tag}[command]
        self.reply = [ACK, response(command, payload)]

    def ready(self):
        return self.respond and bool(self.reply)

    def read(self, ack=False):
        return self.reply.pop(0)


class ProtocolTests(unittest.TestCase):
    def test_bit_reversal_for_all_byte_values(self):
        for value in range(256):
            expected = sum(((value >> bit) & 1) << (7 - bit) for bit in range(8))
            self.assertEqual(REVERSE[value], expected)
            self.assertEqual(REVERSE[REVERSE[value]], value)

    def test_known_get_firmware_command_and_response(self):
        self.assertEqual(frame(2), bytes.fromhex('0000ff02fed4022a00'))
        self.assertEqual(decode_response(bytes.fromhex('0000ff06fad50332010607e800'), 2), bytes.fromhex('32010607'))

    def test_corruption_and_wrong_command_are_rejected(self):
        raw = bytearray.fromhex('0000ff06fad50332010607e800')
        for index in (0, 3, 4, 5, 7, 10, 11):
            broken = raw.copy()
            broken[index] ^= 1
            with self.assertRaises(ValueError):
                decode_response(broken, 2)
        with self.assertRaises(ValueError):
            decode_response(raw, 0x14)

    def test_uid_lengths_and_metadata(self):
        for uid in ('DEADBEEF', '04AABBCC112233', '0102030405060708090A'):
            payload = bytes.fromhex('0101000400') + bytes((len(uid) // 2,)) + bytes.fromhex(uid)
            tag = parse_tag(payload)
            self.assertEqual(tag['uid'], uid)
            self.assertEqual(tag['atqa'], '0004')
            self.assertEqual(tag['sak'], '00')
        self.assertIsNone(parse_tag(b'\x00'))
        for payload in (b'', b'\x02', TAG[:-1], bytes.fromhex('0101000400051122334455')):
            with self.assertRaises(ValueError):
                parse_tag(payload)

    def test_unconfigured_and_conflicting_pins_never_import_machine(self):
        self.assertIsNone(create_reader(None, lambda event: None))
        for pins in ({'miso': 16, 'cs': 17, 'sck': 18, 'mosi': 12}, {'miso': 16, 'cs': 16, 'sck': 18, 'mosi': 19}, {'miso': 16, 'cs': 17, 'sck': 18, 'mosi': 26}):
            with self.assertRaises(ValueError):
                create_reader(pins, lambda event: None)

    def test_confirmed_configuration_initializes_only_reader_pins(self):
        from menagerie_pico.rfid_config import PN532_PINS
        pin = Mock()
        spi = Mock()
        with patch.dict('sys.modules', {'machine': SimpleNamespace(Pin=pin, SoftSPI=spi)}):
            reader = create_reader(PN532_PINS, lambda event: None)
        self.assertIsInstance(reader, Reader)
        self.assertEqual([call.args[0] for call in pin.call_args_list], [13, 14, 15, 12])
        self.assertEqual(spi.call_args.kwargs['polarity'], 0)
        self.assertEqual(spi.call_args.kwargs['phase'], 0)

    def test_spi_reverses_bits_and_keeps_frame_in_one_transaction(self):
        class SPI:
            def __init__(self):
                self.writes = []
                self.incoming = b''
            def write(self, data):
                self.writes.append(data)
            def read(self, count):
                result, self.incoming = self.incoming[:count], self.incoming[count:]
                return result
        class CS:
            def __init__(self):
                self.values = []
            def value(self, value):
                self.values.append(value)
        spi, cs = SPI(), CS()
        transport = SPITransport(spi, cs)
        transport.write(frame(2))
        self.assertEqual(spi.writes[-1], bytes.fromhex('800000ff407f2b405400'))
        raw = bytes.fromhex('0000ff06fad50332010607e800')
        spi.incoming = bytes(REVERSE[n] for n in raw)
        before = len(cs.values)
        self.assertEqual(transport.read(), raw)
        self.assertEqual(cs.values[before:], [0, 1])


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.events = []
        self.reader = Reader(self.bus, self.events.append)
        self.now = 0

    def run_for(self, duration):
        end = self.now + duration
        while self.now < end:
            self.reader.tick(self.now)
            self.now += 5000

    def tags(self):
        return [e for e in self.events if e['type'] == 'rfid']

    def test_initializes_and_reads_without_duplicate_events(self):
        self.run_for(1000000)
        self.assertEqual(self.reader.status, 'ready')
        self.assertEqual(self.reader.version, '1.6')
        self.assertEqual(len(self.tags()), 1)
        self.assertEqual(self.tags()[0]['uid'], 'DEADBEEF')
        retries = next(m for m in self.bus.messages if m != ACK and m[6] == 0x32)
        self.assertEqual(retries[7:-2], bytes.fromhex('05ff0100'))

    def test_removal_and_rescan_allow_same_tag_again(self):
        self.run_for(500000)
        self.bus.tag = b'\x00'
        self.run_for(500000)
        self.assertIsNone(self.reader.tag)
        self.bus.tag = TAG
        self.run_for(500000)
        self.assertEqual(len(self.tags()), 2)
        self.reader.rescan()
        self.run_for(500000)
        self.assertEqual(len(self.tags()), 3)

    def test_disconnected_reader_errors_then_recovers(self):
        self.bus.respond = False
        self.run_for(1200000)
        self.assertEqual(self.reader.status, 'error')
        self.assertEqual(self.events[-1]['type'], 'rfid_error')
        self.bus.respond = True
        self.reader.rescan()
        self.run_for(500000)
        self.assertEqual(self.reader.status, 'ready')

    def test_long_motor_move_pauses_spi_and_timeout(self):
        self.run_for(25000)
        previous = len(self.bus.messages)
        self.reader.tick(self.now, paused=True)
        self.now += 10000000
        self.reader.tick(self.now, paused=True)
        self.assertEqual(len(self.bus.messages), previous)
        self.run_for(500000)
        self.assertEqual(self.reader.status, 'ready')
        self.assertFalse(any(e['type'] == 'rfid_error' for e in self.events))
