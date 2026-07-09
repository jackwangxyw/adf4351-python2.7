"""Linux spidev backend: hardware SPI straight into the ADF4351."""

from __future__ import division, print_function

from .base import SerialWordBackend


class SpiDevBackend(SerialWordBackend):
    """ADF4351 on a Linux SPI bus.  Requires the `spidev` module.

    About LE.  The ADF4351 wants LE held low while the 32 bits are clocked
    in, then taken high to latch them.  A conventional active-low chip select
    does exactly that: it idles high, drops low for the transfer, and rises
    again at the end.  So by default LE is simply wired to the bus chip
    select and no extra GPIO is needed.

    Do *not* try to invert CS (``SPI_CS_HIGH``) to make it "match" LE's
    active-high latch: that would hold LE high during the transfer and drop
    it at the end, which is backwards.

    If your board routes LE to its own pin, pass `le_gpio` -- any callable
    taking a single truthy argument to drive the line.
    """

    #: SPI mode 0: CPOL=0, CPHA=0.  Data is sampled on the rising clock edge,
    #: which is what the ADF4351's shift register expects.
    MODE = 0

    def __init__(self, bus=0, device=0, max_speed_hz=1000000, le_gpio=None,
                 spi=None):
        if spi is None:
            import spidev
            spi = spidev.SpiDev()
            spi.open(bus, device)
        self.spi = spi
        self.spi.mode = self.MODE
        self.spi.max_speed_hz = max_speed_hz
        self.spi.bits_per_word = 8
        self.le_gpio = le_gpio

    def write_word(self, word):
        data = list(self.word_to_bytes(word))
        if self.le_gpio is None:
            # Chip select provides the LE pulse: low for the transfer, high
            # afterwards.
            self.spi.xfer2(data)
        else:
            self.le_gpio(False)
            self.spi.xfer2(data)
            self.le_gpio(True)
            self.le_gpio(False)

    def close(self):
        if self.spi is not None:
            self.spi.close()
            self.spi = None
