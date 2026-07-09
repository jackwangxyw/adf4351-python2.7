"""Transport-independent backend interface.

A backend knows how to get a 32-bit word into the ADF4351's shift register.
Everything above it -- register encoding, frequency planning -- is transport
independent.

`abc` is deliberately not used: spelling an abstract base class portably
across Python 2 and 3 needs either `six` or a metaclass hack, and a plain
``raise NotImplementedError`` is clearer than either.
"""

from __future__ import division, print_function


class Backend(object):
    """Base class for every ADF4351 transport."""

    def write_word(self, word):
        """Shift one 32-bit register word into the device, MSB first."""
        raise NotImplementedError(
            '%s must implement write_word()' % type(self).__name__)

    def write_registers(self, regs):
        """Write ``[R0..R5]`` in the order the data sheet requires.

        The initialization sequence is R5, R4, R3, R2, R1, R0 -- R0 last,
        because writing it triggers VCO band selection.  Callers pass the
        registers indexed by number and this method handles the ordering, so
        no caller ever needs to reverse the list itself.
        """
        if len(regs) != 6:
            raise ValueError('expected 6 registers, got %d' % len(regs))
        for n in range(5, -1, -1):
            self.write_word(regs[n])

    def get_mux(self):
        """Read the MUXOUT pin: 1 (high), 0 (low), or None if unsupported."""
        return None

    def close(self):
        """Release the underlying device.  Safe to call more than once."""

    # Context-manager sugar, so callers can scope the device.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


class SerialWordBackend(Backend):
    """Base for the three-wire (CLK/DATA/LE) transports.

    The ADF4351 clocks data in MSB first on the rising edge of CLK, and
    transfers the shift register into the destination latch on the rising
    edge of LE.  CLK and LE idle low.  That is SPI mode 0 (CPOL=0, CPHA=0)
    followed by an LE pulse.
    """

    @staticmethod
    def word_to_bytes(word):
        """Split a 32-bit word into 4 bytes, most significant first."""
        word &= 0xFFFFFFFF
        return bytearray((
            (word >> 24) & 0xFF,
            (word >> 16) & 0xFF,
            (word >> 8) & 0xFF,
            word & 0xFF,
        ))
