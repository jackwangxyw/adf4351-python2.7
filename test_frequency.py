#!/usr/bin/env python
# -*- coding: utf-8 -*-
##
## Frequency test / setter for the ADF4351 on an FX2-based eval board
## (EVAL-ADF4351 and pin-compatible reference designs such as the CN-0285).
##
## Given a target output frequency it:
##   1. asks the library to pick the RF output divider so the VCO lands in its
##      legal 2200-4400 MHz band,
##   2. maximizes the PFD frequency (higher PFD => lower phase noise / spurs),
##      stepping it down only when a higher PFD cannot reach the requested
##      frequency within the resolution target,
##   3. forms INT / FRAC / MOD (best MOD<=4095 approximation; Integer-N when
##      exact) and builds R0..R5 with the library's make_regs(),
##   4. prints a full breakdown incl. the *actual* synthesized frequency + error,
##   5. writes the registers to the board and polls MUXOUT for lock.
##
## The output frequency is:  f_out = (INT + FRAC/MOD) * f_PFD / output_divider
## with                      f_PFD = f_ref * (1+doubler) / ((1+div2) * R)
##
## Usage:
##     python test_frequency.py 1000               # set 1000 MHz, write, check lock
##     python test_frequency.py 2450.1             # exact at 25 MHz PFD -> best perf
##     python test_frequency.py 2450.001           # needs finer step -> PFD drops
##     python test_frequency.py 1000 --dry-run     # compute + print only, no transmit
##     python test_frequency.py 4000 --resolution 50   # allow coarse step, force max PFD
##     python test_frequency.py 1000 --ref-freq 10 # board has a 10 MHz reference
##     python test_frequency.py 1000 --doubler     # double the reference before the R counter
##     python test_frequency.py 1000 --div2        # halve the reference before the R counter
##
## Exit code 0 = success (locked, or dry-run), non-zero = failure.

from __future__ import division
from __future__ import print_function

import argparse
import sys

try:
    import usb.core
except ImportError:
    print('ERROR: pyusb is not installed (pip install pyusb).')
    sys.exit(2)

from adf4351 import ADF4351, plan as plan_frequency, MuxOut, Prescaler
from adf4351.backends.fx2 import FX2Backend


# MUXOUT is programmed to digital lock detect so the board can report lock;
# this matches ADF4351.set_frequency()'s own default.
_MUX = MuxOut.DigitalLockDetect

_PRESCALER_NAME = {
    Prescaler.Prescaler4_5: '4/5',
    Prescaler.Prescaler8_9: '8/9',
}


def build_plan(args):
    """Plan the requested frequency with the library. Raises ValueError on an
    out-of-range request, exactly as the library does."""
    return plan_frequency(
        args.freq,
        ref_freq_mhz=args.ref_freq,
        resolution_khz=args.resolution,
        ref_doubler=args.doubler,
        ref_div2=args.div2,
    )


def build_regs(p):
    """R0..R5 for plan `p`, with MUXOUT set to lock detect. This is the same
    call ADF4351.set_frequency() makes internally, so the printed registers are
    the ones that get written."""
    return p.make_regs(mux_out=_MUX)


def print_plan(p, regs):
    n = p.INT + p.FRAC / p.MOD
    print('=' * 60)
    print(' ADF4351 frequency plan')
    print('=' * 60)
    print('  Requested frequency : %.6f MHz' % p.freq_mhz)
    print('  Reference (f_ref)   : %.6f MHz' % p.ref_freq_mhz)
    if p.ref_doubler or p.ref_div2:
        print('  Reference path      : %s%s'
              % ('doubler ON ' if p.ref_doubler else '',
                 'div2 ON' if p.ref_div2 else ''))
    print('  Resolution target   : %.3f kHz' % p.resolution_khz)
    print('  Output divider      : %d   -> VCO = %.6f MHz'
          % (p.output_divider, p.f_vco_mhz))
    if p.pfd_reduced:
        print('  PFD                 : %.6f MHz  (reduced from max %.6f MHz'
              ' to reach the target)' % (p.f_pfd_mhz, p.f_pfd_max_mhz))
    else:
        print('  PFD                 : %.6f MHz  (maximum for this frequency)'
              % p.f_pfd_mhz)
    print('  R counter           : %d' % p.r_counter)
    print('  Prescaler           : %s' % _PRESCALER_NAME.get(p.prescaler, '?'))
    print('  Mode                : %s'
          % ('Integer-N' if p.integer_n else 'Fractional-N'))
    print('  N = INT + FRAC/MOD   : %.9f' % n)
    print('    INT               : %d' % p.INT)
    print('    FRAC              : %d' % p.FRAC)
    print('    MOD               : %d' % p.MOD)
    print('  Grid step (worst)   : %.3f kHz' % p.step_khz)
    print('  Band-sel clk divider: %d' % p.band_select_clock_divider)
    print('  ' + '-' * 56)
    print('  Synthesized freq    : %.6f MHz' % p.f_out_mhz)
    print('  Error vs requested  : %+.3f Hz  (%+.4f ppm)'
          % (p.error_hz, p.error_ppm))
    print('  ' + '-' * 56)
    for i, r in enumerate(regs):
        print('  R%d = 0x%08X' % (i, r))
    print('=' * 60)


def write_and_check(p, no_lock_check):
    """Open the board, program the plan, and (optionally) confirm PLL lock.
    Uses the ADF4351 device class so the write order and lock poll live in the
    library, not here."""
    print('\nOpening board and writing registers (RF OUTPUT ON) ...')
    try:
        backend = FX2Backend()
    except ValueError:
        print('  FAIL: device not found. Is the board connected and the')
        print('        firmware loaded?')
        return 1
    except usb.core.USBError as e:
        print('  FAIL: USB error opening device: %s' % e)
        print('        Try running with sudo or add a udev rule for 0456:b40d.')
        return 1

    dev = ADF4351(backend, ref_freq_mhz=p.ref_freq_mhz)
    try:
        # set_frequency() re-plans with the same arguments and writes R5..R0 in
        # the order the data sheet requires. It returns the Plan it programmed,
        # which will match the one we printed.
        try:
            dev.set_frequency(p.freq_mhz, resolution_khz=p.resolution_khz,
                              ref_doubler=p.ref_doubler, ref_div2=p.ref_div2)
        except usb.core.USBError as e:
            print('  FAIL: writing registers failed: %s' % e)
            return 1
        print('  OK: registers written.')

        if no_lock_check:
            print('  (lock check skipped)')
            return 0

        print('\nChecking PLL lock via MUXOUT (digital lock detect) ...')
        try:
            locked = dev.wait_for_lock(timeout=1.0, interval=0.05)
        except usb.core.USBError as e:
            print('  WARN: MUXOUT read failed: %s' % e)
            locked = False

        if locked:
            print('  RESULT: LOCKED -- board is generating the frequency.')
            return 0
        if locked is None:
            print('  RESULT: backend cannot read MUXOUT; lock unverified.')
            return 0
        print('  RESULT: NOLOCK -- PLL did not report lock.')
        print('          Check reference frequency (--ref-freq), the RF '
              'connection,')
        print('          and that MUXOUT (FX2 PB0) is wired for lock detect.')
        return 1
    finally:
        dev.close()


def main():
    ap = argparse.ArgumentParser(
        description='Set and verify an ADF4351 output frequency. Planning is '
                    'done by the adf4351 library: the PFD is maximized for best '
                    'phase noise and only reduced when a higher PFD cannot '
                    'reach the frequency within --resolution.')
    ap.add_argument('freq', type=float, help='target output frequency in MHz')
    ap.add_argument('--ref-freq', type=float, default=25.0,
                    help='board reference oscillator in MHz (default 25.0)')
    ap.add_argument('--resolution', type=float, default=1.0,
                    help='frequency step target in kHz (default 1.0); larger '
                         'values keep the PFD higher, smaller values chase '
                         'exact frequencies at the cost of phase noise')
    ap.add_argument('--doubler', action='store_true',
                    help='enable the reference doubler (f_ref x2 before R)')
    ap.add_argument('--div2', action='store_true',
                    help='enable reference divide-by-2 (f_ref /2 before R)')
    ap.add_argument('--dry-run', action='store_true',
                    help='compute and print only; do NOT write to the board')
    ap.add_argument('--no-lock-check', action='store_true',
                    help='write registers but skip the MUXOUT lock check')
    args = ap.parse_args()

    if args.resolution <= 0:
        print('ERROR: --resolution must be positive.')
        return 2

    try:
        p = build_plan(args)
    except ValueError as e:
        print('ERROR: %s' % e)
        return 2

    regs = build_regs(p)
    print_plan(p, regs)

    if abs(p.error_hz) > args.resolution * 1000.0 / 2.0 + 1.0:
        print('\nNOTE: could not reach the target within %.3f kHz even at the '
              'lowest PFD; the closest achievable frequency is shown above.'
              % args.resolution)

    if args.dry_run:
        print('\n(dry run -- nothing written to the board)')
        return 0

    return write_and_check(p, args.no_lock_check)


if __name__ == '__main__':
    sys.exit(main())
