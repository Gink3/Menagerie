"""Confirmed PN532 wiring. The reader's switches/jumpers must select SPI mode.

Set PN532_PINS to None to disable RFID without changing motor/Hall operation.
Motor EN is GP21, DIR GP20, STEP GP19; Hall signal is GP26.
"""
PN532_PINS = {'miso': 12, 'cs': 13, 'sck': 14, 'mosi': 15}
