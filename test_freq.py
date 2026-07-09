#!/usr/bin/env python
# -*- coding: utf-8 -*-
##
## Frequency test / setter for the ADF4351 on an FX2-based eval board
## (EVAL-ADF4351 and pin-compatible reference designs such as the CN-0285).
##
## Given a target output frequency it:
##   1. picks the RF output divider so the VCO lands in its legal 2200-4400 MHz band,
##   2. selects the PHASE-FREQUENCY DETECTOR (PFD) frequency as HIGH as possible
##      (higher PFD => lower phase noise / spurs), stepping it DOWN only when a
##      higher PFD cannot reach the requested frequency within the resolution
##      target -- so clean frequencies keep the full 25 MHz PFD and only the
##      fine-grained edge cases sacrifice phase noise,
##   3. forms INT / FRAC / MOD (best MOD<=4095 approximation; Integer-N when exact),
##   4. builds R0..R5 with this repo's own make_regs() (no re-implementation),
##   5. prints a full breakdown incl. the *actual* synthesized frequency + error,
##   6. writes the registers to the board and polls MUXOUT for lock (LOCKED/NOLOCK).
##
## The output frequency is:  f_out = (INT + FRAC/MOD) * f_PFD / output_divider
## with                      f_PFD = f_ref * (1+doubler) / ((1+div2) * R)
##
## Usage:
##     python test_freq.py 1000               # set 1000 MHz, write, check lock
##     python test_freq.py 2450.1             # exact at 25 MHz PFD -> best perf
##     python test_freq.py 2450.001           # needs finer step -> PFD drops
##     python test_freq.py 1000 --dry-run     # compute + print only, no transmit
##     python test_freq.py 4000 --resolution 50   # allow coarse step, force max PFD
##     python test_freq.py 1000 --ref-freq 10 # board has a 10 MHz reference
##
## Exit code 0 = success (locked, or dry-run), non-zero = failure.

from __future__ import division
from __future__ import print_function

import argparse
import sys
import time
from math import ceil, floor

try:
    import usb.core
except ImportError:
    print('ERROR: pyusb is not installed (pip install pyusb).')
    sys.exit(2)

from adf4351 import make_regs, MuxOut, FeedbackSelect, BandSelectClockMode
from adf4351.backends.fx2 import FX2Backend


# --- ADF4351 hardware limits -------------------------------------------------
VCO_MIN = 2200.0     # MHz, lowest VCO fundamental
VCO_MAX = 4400.0     # MHz, highest VCO fundamental
PFD_MAX_FRAC = 32.0  # MHz, max PFD in fractional-N mode
N_MIN = 75           # minimum feedback divisor for the 8/9 prescaler
N_MAX = 65535        # 16-bit INT field
MOD_MAX = 4095       # 12-bit modulus
R_MAX = 1023         # 10-bit reference counter
OUT_DIV_MAX = 64     # RF output divider is a power of two, 1..64


def choose_output_divider(freq):
    '''Smallest power-of-two output divider that keeps the VCO in band.
    Returns (output_divider, f_vco).'''
    k = 0
    while (1 << k) <= OUT_DIV_MAX:
        d = 1 << k
        vco = freq * d
        if vco >= VCO_MIN:
            if vco > VCO_MAX:
                raise ValueError(
                    'freq %g MHz unreachable: VCO would be %g MHz (>%g).'
                    % (freq, vco, VCO_MAX))
            return d, vco
        k += 1
    raise ValueError('freq %g MHz too low (min is %g MHz).'
                     % (freq, VCO_MIN / OUT_DIV_MAX))


def choose_frac_mod(n):
    '''Best INT/FRAC/MOD (MOD<=4095) representation of the divisor n.
       Exact fraction -> smallest MOD; zero fraction -> Integer-N.'''
    INT = int(floor(n + 1e-12))
    x = n - INT
    if x < 1e-12:
        return INT, 0, 2                      # Integer-N (MOD must still be >=2)

    best = None                               # (err, FRAC, MOD)
    for mod in range(2, MOD_MAX + 1):
        frac = int(round(x * mod))
        if frac > mod:
            frac = mod
        err = abs(frac / float(mod) - x)
        if best is None or err < best[0]:
            best = (err, frac, mod)
            if err == 0.0:
                break

    _, FRAC, MOD = best
    if FRAC >= MOD:                           # rounded up a whole cycle
        INT += 1
        FRAC, MOD = 0, 2
    return INT, FRAC, MOD


def plan(freq, f_ref, resolution_khz):
    '''Work out every value needed to synthesize freq, choosing the highest PFD
       that reaches it within +/- half of resolution_khz. Returns a dict.'''
    if freq < VCO_MIN / OUT_DIV_MAX or freq > VCO_MAX:
        raise ValueError(
            'freq %g MHz outside ADF4351 range [%g ... %g] MHz.'
            % (freq, VCO_MIN / OUT_DIV_MAX, VCO_MAX))

    output_divider, f_vco = choose_output_divider(freq)
    tol = (resolution_khz / 1000.0) / 2.0     # max acceptable error, in MHz

    # PFD search window: highest PFD first (smallest R), stepping down.
    r_lo = int(ceil(f_ref / min(PFD_MAX_FRAC, f_vco / N_MIN)))
    if r_lo < 1:
        r_lo = 1
    r_hi = int(floor(f_ref * N_MAX / f_vco))  # keep N = f_vco/f_pfd <= N_MAX
    if r_hi > R_MAX:
        r_hi = R_MAX
    if r_hi < r_lo:
        r_hi = r_lo
    pfd_max = f_ref / r_lo

    chosen = None
    best = None                               # fallback: smallest error seen
    for r in range(r_lo, r_hi + 1):
        f_pfd = f_ref / r
        n = f_vco / f_pfd
        INT, FRAC, MOD = choose_frac_mod(n)
        if INT < N_MIN or INT > N_MAX:
            continue
        f_out_real = f_pfd * (INT + FRAC / float(MOD)) / output_divider
        err = abs(f_out_real - freq)
        cand = dict(r_counter=r, f_pfd=f_pfd, N=n, INT=INT, FRAC=FRAC, MOD=MOD,
                    f_out_real=f_out_real, err_mhz=err)
        if best is None or err < best['err_mhz']:
            best = cand
        if err <= tol:
            chosen = cand
            break
    if chosen is None:                        # nothing met the target; use finest
        chosen = best

    # band select clock divider, same rule the library uses (Low mode)
    bscd = int(min(ceil(8.0 * chosen['f_pfd']), 255))

    chosen.update(
        freq=freq, f_ref=f_ref, resolution_khz=resolution_khz,
        output_divider=output_divider, f_vco=chosen['f_pfd'] * chosen['N'],
        integer_n=(chosen['FRAC'] == 0),
        pfd_max=pfd_max, pfd_reduced=(chosen['f_pfd'] < pfd_max - 1e-9),
        step_khz=chosen['f_pfd'] / MOD_MAX / output_divider * 1000.0,
        bscd=bscd,
        err_hz=(chosen['f_out_real'] - freq) * 1e6,
        err_ppm=(chosen['f_out_real'] - freq) / freq * 1e6,
    )
    return chosen


def build_regs(p):
    'Build R0..R5 for the plan dict p using the library make_regs().'
    return make_regs(
        INT=p['INT'], FRAC=p['FRAC'], MOD=p['MOD'],
        r_counter=p['r_counter'],
        output_divider=p['output_divider'],
        vco_freq_mhz=p['f_vco'],            # lets the library check the prescaler
        band_select_clock_divider=p['bscd'],
        band_select_clock_mode=BandSelectClockMode.Low,
        feedback_select=FeedbackSelect.Fundamental,
        mux_out=MuxOut.DigitalLockDetect,   # so we can read lock via MUXOUT
    )


def print_plan(p, regs):
    print('=' * 60)
    print(' ADF4351 frequency plan')
    print('=' * 60)
    print('  Requested frequency : %.6f MHz' % p['freq'])
    print('  Reference (f_ref)   : %.6f MHz' % p['f_ref'])
    print('  Resolution target   : %.3f kHz' % p['resolution_khz'])
    print('  Output divider      : %d   -> VCO = %.6f MHz'
          % (p['output_divider'], p['f_vco']))
    if p['pfd_reduced']:
        print('  PFD                 : %.6f MHz  (reduced from max %.6f MHz'
              ' to reach the target)' % (p['f_pfd'], p['pfd_max']))
    else:
        print('  PFD                 : %.6f MHz  (maximum for this frequency)'
              % p['f_pfd'])
    print('  R counter           : %d' % p['r_counter'])
    print('  Mode                : %s'
          % ('Integer-N' if p['integer_n'] else 'Fractional-N'))
    print('  N = INT + FRAC/MOD   : %.9f' % p['N'])
    print('    INT               : %d' % p['INT'])
    print('    FRAC              : %d' % p['FRAC'])
    print('    MOD               : %d' % p['MOD'])
    print('  Grid step (worst)   : %.3f kHz' % p['step_khz'])
    print('  Band-sel clk divider: %d' % p['bscd'])
    print('  ' + '-' * 56)
    print('  Synthesized freq    : %.6f MHz' % p['f_out_real'])
    print('  Error vs requested  : %+.3f Hz  (%+.4f ppm)'
          % (p['err_hz'], p['err_ppm']))
    print('  ' + '-' * 56)
    for i, r in enumerate(regs):
        print('  R%d = 0x%08X' % (i, r))
    print('=' * 60)


def write_and_check(regs, no_lock_check):
    'Send the registers to the board and (optionally) confirm PLL lock.'
    print('\nOpening board and writing registers (RF OUTPUT ON) ...')
    try:
        intf = FX2Backend()
    except ValueError:
        print('  FAIL: device not found. Is the board connected and the')
        print('        firmware loaded?')
        return 1
    except usb.core.USBError as e:
        print('  FAIL: USB error opening device: %s' % e)
        print('        Try running with sudo or add a udev rule for 0456:b40d.')
        return 1

    try:
        # write_registers() emits R5..R0 itself, as the data sheet requires.
        intf.write_registers(regs)
    except usb.core.USBError as e:
        print('  FAIL: writing registers failed: %s' % e)
        return 1
    print('  OK: registers written.')

    if no_lock_check:
        print('  (lock check skipped)')
        return 0

    print('\nChecking PLL lock via MUXOUT (digital lock detect) ...')
    locked = False
    for _ in range(20):                     # up to ~1 s
        time.sleep(0.05)
        try:
            mux = intf.get_mux()            # 0, 1, or None
        except usb.core.USBError as e:
            print('  WARN: MUXOUT read failed: %s' % e)
            break
        if mux:
            locked = True
            break

    if locked:
        print('  RESULT: LOCKED -- board is generating the frequency.')
        return 0
    print('  RESULT: NOLOCK -- PLL did not report lock.')
    print('          Check reference frequency (--ref-freq), the RF connection,')
    print('          and that MUXOUT (FX2 PB0) is wired for lock detect.')
    return 1


def main():
    ap = argparse.ArgumentParser(
        description='Set and verify an ADF4351 output frequency. The PFD is '
                    'maximized for best phase noise and only reduced when a '
                    'higher PFD cannot reach the frequency within --resolution.')
    ap.add_argument('freq', type=float, help='target output frequency in MHz')
    ap.add_argument('--ref-freq', type=float, default=25.0,
                    help='board reference oscillator in MHz (default 25.0)')
    ap.add_argument('--resolution', type=float, default=1.0,
                    help='frequency step target in kHz (default 1.0); larger '
                         'values keep the PFD higher, smaller values chase '
                         'exact frequencies at the cost of phase noise')
    ap.add_argument('--dry-run', action='store_true',
                    help='compute and print only; do NOT write to the board')
    ap.add_argument('--no-lock-check', action='store_true',
                    help='write registers but skip the MUXOUT lock check')
    args = ap.parse_args()

    if args.resolution <= 0:
        print('ERROR: --resolution must be positive.')
        return 2

    try:
        p = plan(args.freq, args.ref_freq, args.resolution)
    except ValueError as e:
        print('ERROR: %s' % e)
        return 2

    regs = build_regs(p)
    print_plan(p, regs)

    if abs(p['err_hz']) > args.resolution * 1000.0 / 2.0 + 1.0:
        print('\nNOTE: could not reach the target within %.3f kHz even at the '
              'lowest PFD; the closest achievable frequency is shown above.'
              % args.resolution)

    if args.dry_run:
        print('\n(dry run -- nothing written to the board)')
        return 0

    return write_and_check(regs, args.no_lock_check)


if __name__ == '__main__':
    sys.exit(main())
