#!/usr/bin/env python
# -*- coding: utf-8 -*-
##
## Connection test for the ADF4351 / Cypress FX2 based eval board
## (EVAL-ADF4351 and pin-compatible reference designs such as the CN-0285).
##
## This is a READ-ONLY liveness probe: it does NOT program any ADF4351
## register and does NOT change the RF output.  It only:
##   1. finds the USB device,
##   2. reads its USB descriptor strings + firmware version,
##   3. reads the Cypress chip revision (proves control transfers work),
##   4. reads MUXOUT via the custom GET_MUX vendor command (proves the
##      loaded firmware is actually running and responding).
##
## Run over SSH on the embedded board, e.g.:
##     python test_connection.py
##     sudo python test_connection.py      # if USB access is denied
##
## Exit code 0 = success, non-zero = failure (so it can be chained in scripts).

from __future__ import print_function

import sys

try:
    import usb.core
    import usb.util
except ImportError:
    print('ERROR: pyusb is not installed. Install it with:')
    print('    sudo apt install python-usb        # or: pip install pyusb')
    sys.exit(2)

from adf4351.backends.fx2 import FX2Backend


def main():
    print('=' * 60)
    print(' ADF4351 / FX2 eval board connection test')
    print('=' * 60)

    # ------------------------------------------------------------------
    # 1. Find the device and open it (uses the library's own FX2Backend,
    #    which looks for 0x0456:0xb40d and falls back to 0x0456:0xb403).
    # ------------------------------------------------------------------
    print('\n[1/4] Locating USB device (0x0456:0xb40d / 0xb403) ...')
    try:
        intf = FX2Backend()
    except ValueError:
        print('  FAIL: device not found.')
        print('        - Is the board connected?')
        print('        - Did the firmware load and re-enumerate?')
        print('          Check with:  lsusb | grep -i 0456')
        return 1
    except usb.core.USBError as e:
        print('  FAIL: USB error while opening the device: %s' % e)
        print('        This is usually a permissions problem. Try running with')
        print('        sudo, or add a udev rule granting access to 0456:b40d.')
        return 1

    dev = intf.dev
    print('  OK: device opened (idVendor=0x%04x, idProduct=0x%04x).'
          % (dev.idVendor, dev.idProduct))

    # ------------------------------------------------------------------
    # 2. Read USB descriptor strings + firmware version.
    # ------------------------------------------------------------------
    print('\n[2/4] Reading USB descriptors ...')
    try:
        manufacturer = intf.get_manufacturer_string()
        product = intf.get_product_string()
        serial = intf.get_serial_number_string()
        fw = intf.get_fw_version()   # bcdDevice, 2-byte BCD
        print('  Manufacturer  : %s' % manufacturer)
        print('  Product       : %s' % product)
        print('  Serial number : %s' % serial)
        if fw is not None:
            print('  FW version    : %d.%02d (bcdDevice=0x%04x)'
                  % (fw >> 8, fw & 0xFF, fw))
        else:
            print('  FW version    : (unavailable)')
    except usb.core.USBError as e:
        print('  WARN: could not read descriptor strings: %s' % e)

    # ------------------------------------------------------------------
    # 3. Cypress chip-revision read -- proves control transfers work.
    # ------------------------------------------------------------------
    print('\n[3/4] Reading Cypress chip revision (vendor req 0xA6) ...')
    try:
        rev = intf.get_chip_rev()
        print('  OK: chip revision = %s' % list(rev))
    except usb.core.USBError as e:
        print('  WARN: chip-revision read failed: %s' % e)

    # ------------------------------------------------------------------
    # 4. GET_MUX -- the custom vendor command. A successful read here is
    #    the definitive proof that the loaded firmware is alive.
    # ------------------------------------------------------------------
    print('\n[4/4] Reading MUXOUT via custom GET_MUX (vendor req 0xDF) ...')
    try:
        state = intf.get_mux()      # 0, 1, or None
        print('  OK: firmware responded. MUXOUT = %s (%s)'
              % (state, 'HIGH' if state else 'LOW'))
    except usb.core.USBError as e:
        print('  FAIL: GET_MUX failed: %s' % e)
        print('        The device enumerated but did not answer the custom')
        print('        vendor command -- the expected firmware may not be')
        print('        running. Re-load the FX2 firmware with cycfx2prog.')
        return 1

    print('\n' + '=' * 60)
    print(' RESULT: SUCCESS -- board is connected and firmware responds.')
    print('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
