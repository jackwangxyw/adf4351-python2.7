"""Software bit-bang backend over three GPIO lines.

Deliberately library-agnostic: you supply three callables that drive CLK,
DATA and LE, and optionally one that reads MUXOUT.  That makes this usable
with RPi.GPIO, gpiozero, periphery, sysfs, an MCU bridge, or a test double,
without this package depending on any of them.

Slow, but it works anywhere, and it is the reference for what the other
three-wire transports do in hardware.
"""

from __future__ import division, print_function

from .base import SerialWordBackend


class BitBangBackend(SerialWordBackend):
    """Drive the ADF4351's three-wire interface from arbitrary GPIO.

    Each of `set_clk`, `set_data` and `set_le` takes one truthy argument and
    drives the corresponding line.  `get_mux`, if given, returns the state of
    the MUXOUT pin.  `delay`, if given, is called between line transitions;
    on any machine slow enough to run Python this is rarely necessary, since
    the ADF4351 needs only nanoseconds of setup and hold.
    """

    def __init__(self, set_clk, set_data, set_le, get_mux=None, delay=None):
        self.set_clk = set_clk
        self.set_data = set_data
        self.set_le = set_le
        self._get_mux = get_mux
        self.delay = delay
        # Idle state: clock and latch low.
        self.set_clk(False)
        self.set_le(False)
        self.set_data(False)

    def _settle(self):
        if self.delay is not None:
            self.delay()

    def write_word(self, word):
        word &= 0xFFFFFFFF
        self.set_le(False)
        for bit in range(31, -1, -1):        # MSB first
            self.set_data((word >> bit) & 1)
            self._settle()
            self.set_clk(True)               # device samples on the rising edge
            self._settle()
            self.set_clk(False)
            self._settle()
        self.set_data(False)
        # Rising edge of LE transfers the shift register into the latch that
        # the three control bits selected.
        self.set_le(True)
        self._settle()
        self.set_le(False)

    def get_mux(self):
        if self._get_mux is None:
            return None
        return 1 if self._get_mux() else 0
