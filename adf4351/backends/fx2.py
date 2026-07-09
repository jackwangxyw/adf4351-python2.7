"""Cypress FX2 USB backend.

Talks to an FX2-based ADF4351 evaluation board (EVAL-ADF4351 and
pin-compatible designs such as the CN-0285) over USB control transfers.  The
FX2 firmware receives a 32-bit register word and bit-bangs it out on the
three-wire interface.

The vendor request numbers below are interface constants required to
interoperate with that firmware; 0xA2 through 0xA9 are Cypress's own.
"""

from __future__ import division, print_function

from .base import Backend


# ADF4xxx USB Eval Board, then the ADF4xxx USB Adapter Board.
USB_IDS = ((0x0456, 0xB40D), (0x0456, 0xB403))

# Cypress standard vendor requests.
USB_REQ_CYPRESS_EEPROM_SB = 0xA2      # small-address EEPROM
USB_REQ_CYPRESS_EXT_RAM = 0xA3        # external RAM
USB_REQ_CYPRESS_CHIP_REV = 0xA6       # REVID register
USB_REQ_CYPRESS_RENUMERATE = 0xA8
USB_REQ_CYPRESS_EEPROM_DB = 0xA9      # large-address EEPROM
USB_REQ_LIBFX2_PAGE_SIZE = 0xB0

# Firmware-specific requests.
USB_REQ_SET_REG = 0xDD                # one 32-bit register
USB_REQ_EE_REGS = 0xDE                # store/clear the startup register set
USB_REQ_GET_MUX = 0xDF                # read the MUXOUT pin

# Argument to set_startup().
INIT_NEVER = 0                        # clear EEPROM, do not initialise
INIT_STANDALONE = 1                   # initialise after 2 s without USB
INIT_ALWAYS = 2

# Where the firmware keeps its 32-byte startup register set.
EEPROM_REG_ADDR = 8160
EEPROM_REG_SIZE = 32

_VENDOR_OUT = 0x40
_VENDOR_IN = 0xC0


class FX2Backend(Backend):
    """ADF4351 behind an FX2 USB bridge.  Requires `pyusb`."""

    def __init__(self, dev=None):
        import usb.core
        import usb.util
        self._usb_util = usb.util

        if dev is None:
            for vid, pid in USB_IDS:
                dev = usb.core.find(idVendor=vid, idProduct=pid)
                if dev is not None:
                    break
        if dev is None:
            raise ValueError(
                'no ADF4xxx USB board found (looked for %s)'
                % ', '.join('%04x:%04x' % ids for ids in USB_IDS))

        self.dev = dev
        self.dev.set_configuration()

    # -- Backend interface --------------------------------------------------

    def write_word(self, word):
        """Send one register word as 4 little-endian bytes.

        The firmware reads the destination register number from the low three
        bits of the first byte, so the control bits must already be correct.
        """
        word &= 0xFFFFFFFF
        data = [(word >> (8 * i)) & 0xFF for i in range(4)]
        self.dev.ctrl_transfer(
            bmRequestType=_VENDOR_OUT, bRequest=USB_REQ_SET_REG,
            wValue=0, wIndex=0, data_or_wLength=data)

    def get_mux(self):
        """Read MUXOUT.  The firmware returns a single byte, 0 or 1."""
        result = self.dev.ctrl_transfer(
            bmRequestType=_VENDOR_IN, bRequest=USB_REQ_GET_MUX,
            wValue=0, wIndex=0, data_or_wLength=1)
        if result is None or not len(result):
            return None
        return int(result[0]) & 1

    def close(self):
        if self.dev is not None:
            self._usb_util.dispose_resources(self.dev)
            self.dev = None

    # -- FX2-specific extras ------------------------------------------------

    def set_startup(self, init_type):
        """Store the current registers in EEPROM as the power-on default.

        `init_type` is INIT_NEVER, INIT_STANDALONE or INIT_ALWAYS.
        """
        self.dev.ctrl_transfer(
            bmRequestType=_VENDOR_OUT, bRequest=USB_REQ_EE_REGS,
            wValue=init_type, wIndex=0, data_or_wLength=None)

    def get_eeprom(self, addr=EEPROM_REG_ADDR, size=EEPROM_REG_SIZE):
        return self.dev.ctrl_transfer(
            bmRequestType=_VENDOR_IN, bRequest=USB_REQ_CYPRESS_EEPROM_DB,
            wValue=addr, wIndex=0, data_or_wLength=size)

    def set_eeprom(self, data, addr=EEPROM_REG_ADDR):
        self.dev.ctrl_transfer(
            bmRequestType=_VENDOR_OUT, bRequest=USB_REQ_CYPRESS_EEPROM_DB,
            wValue=addr, wIndex=0, data_or_wLength=data)

    def get_xram(self, addr=0x3E00, size=EEPROM_REG_SIZE):
        return self.dev.ctrl_transfer(
            bmRequestType=_VENDOR_IN, bRequest=USB_REQ_CYPRESS_EXT_RAM,
            wValue=addr, wIndex=0, data_or_wLength=size)

    def get_chip_rev(self):
        """Cypress REVID.  A successful read proves control transfers work."""
        return self.dev.ctrl_transfer(
            bmRequestType=_VENDOR_IN, bRequest=USB_REQ_CYPRESS_CHIP_REV,
            wValue=0, wIndex=0, data_or_wLength=1)

    def renumerate(self):
        return self.dev.ctrl_transfer(
            bmRequestType=_VENDOR_OUT, bRequest=USB_REQ_CYPRESS_RENUMERATE,
            wValue=0, wIndex=0, data_or_wLength=0)

    def get_fw_version(self):
        """Firmware version as the 2-byte BCD bcdDevice field."""
        return self.dev.bcdDevice

    def _string(self, index):
        if self.dev is not None and index:
            return self._usb_util.get_string(self.dev, index)
        return None

    def get_manufacturer_string(self):
        return self._string(self.dev.iManufacturer)

    def get_product_string(self):
        return self._string(self.dev.iProduct)

    def get_serial_number_string(self):
        return self._string(self.dev.iSerialNumber)
