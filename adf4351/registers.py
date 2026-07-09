"""Bit-exact encoding of the six ADF4351 registers.

Every bit position, encoding and limit in this module is taken from the
Analog Devices ADF4351 data sheet (Rev. 0), Figures 24 to 29 and the
accompanying register descriptions.

This module is pure: it performs no I/O and imports nothing outside the
standard library.  Serial format, from the data sheet: each register is a
32-bit word shifted in MSB first on the rising edge of CLK, and the three
LSBs (DB2:DB0) are control bits naming the destination register.
"""

from __future__ import division, print_function

import numbers


try:                            # pragma: no cover - trivial 2/3 shim
    _INTEGER_TYPES = (int, long)        # noqa: F821  (Python 2)
except NameError:               # pragma: no cover
    _INTEGER_TYPES = (int,)             # Python 3


# ---------------------------------------------------------------------------
# Hardware limits (data sheet, Specifications and register descriptions)
# ---------------------------------------------------------------------------

VCO_MIN_MHZ = 2200.0            # VCO fundamental range
VCO_MAX_MHZ = 4400.0
OUTPUT_DIVIDERS = (1, 2, 4, 8, 16, 32, 64)
RF_OUT_MAX_MHZ = VCO_MAX_MHZ
RF_OUT_MIN_MHZ = VCO_MIN_MHZ / OUTPUT_DIVIDERS[-1]      # 34.375 MHz

PFD_MAX_FRAC_MHZ = 32.0         # max PFD in fractional-N mode
PFD_MAX_INT_MHZ = 90.0          # max PFD in integer-N mode
PFD_BAND_SELECT_DISABLE_MHZ = 45.0  # above this, VCO band select must be off

PRESCALER_4_5_MAX_MHZ = 3600.0  # 4/5 prescaler is only rated to 3.6 GHz

INT_MIN_4_5 = 23                # N_MIN for the 4/5 prescaler
INT_MIN_8_9 = 75                # N_MIN for the 8/9 prescaler
INT_MAX = 65535                 # 16-bit field

FRAC_MIN = 0
MOD_MIN = 2
MOD_MAX = 4095                  # 12-bit field
PHASE_MAX = 4095                # 12-bit field
R_COUNTER_MIN = 1
R_COUNTER_MAX = 1023            # 10-bit field
CLOCK_DIVIDER_MAX = 4095        # 12-bit field

BAND_SELECT_CLOCK_DIVIDER_MIN = 1
BAND_SELECT_CLOCK_DIVIDER_MAX = 255     # 8-bit field
BAND_SELECT_CLOCK_DIVIDER_MAX_FAST = 254  # when band select clock mode is High
BAND_SELECT_CLOCK_MAX_LOW_KHZ = 125.0
BAND_SELECT_CLOCK_MAX_HIGH_KHZ = 500.0


# ---------------------------------------------------------------------------
# Field encodings.  Plain classes rather than enum: `enum` is Python 3.4+ and
# this library must run on Python 2.7.
# ---------------------------------------------------------------------------

class Prescaler(object):
    """R1 DB27.  4/5 is only valid up to 3.6 GHz."""
    Prescaler4_5 = 0
    Prescaler8_9 = 1


class LowNoiseSpurMode(object):
    """R2 DB30:DB29.  01 and 10 are reserved -- low spur is 0b11, not 1."""
    LowNoiseMode = 0
    LowSpurMode = 3


class MuxOut(object):
    """R2 DB28:DB26."""
    ThreeState = 0
    DVdd = 1
    DGND = 2
    RCounterOutput = 3
    NDividerOutput = 4
    AnalogLockDetect = 5
    DigitalLockDetect = 6


class LDF(object):
    """R2 DB8: number of consecutive PFD cycles lock detect monitors."""
    FracN = 0                   # 40 cycles; recommended when FRAC != 0
    IntN = 1                    # 5 cycles;  recommended when FRAC == 0


class LDP(object):
    """R2 DB7: lock detect comparison window."""
    LDP_10ns = 0
    LDP_6ns = 1


class PDPolarity(object):
    """R2 DB6."""
    Negative = 0
    Positive = 1


class BandSelectClockMode(object):
    """R3 DB23."""
    Low = 0
    High = 1


class ABPWidth(object):
    """R3 DB22: PFD antibacklash pulse width."""
    ABP_6ns = 0                 # recommended for fractional-N
    ABP_3ns = 1                 # recommended for integer-N


class ClkDivMode(object):
    """R3 DB16:DB15.  0b11 is reserved."""
    ClockDividerOff = 0
    FastLockEnable = 1
    ResyncEnable = 2


class FeedbackSelect(object):
    """R4 DB23."""
    Divided = 0
    Fundamental = 1


class AuxOutputSelect(object):
    """R4 DB9."""
    DividedOutput = 0
    Fundamental = 1


class LDPinMode(object):
    """R5 DB23:DB22."""
    Low = 0
    DigitalLockDetect = 1
    LowAlt = 2
    High = 3


#: R2 DB12:DB9.  Charge pump current in mA for the nominal 5.1 kohm RSET.
CHARGE_PUMP_CURRENT_MA = (
    0.31, 0.63, 0.94, 1.25, 1.56, 1.88, 2.19, 2.50,
    2.81, 3.13, 3.44, 3.75, 4.06, 4.38, 4.69, 5.00,
)

#: R4 DB4:DB3 and DB7:DB6.  Output power in dBm.
OUTPUT_POWER_DBM = (-4, -1, 2, 5)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _uint(name, value, width, minimum=0, maximum=None):
    """Validate an unsigned field and return it masked to `width` bits.

    Masking is belt-and-braces: validation already rejects out-of-range
    values, so a field can never bleed into its neighbour.
    """
    if maximum is None:
        maximum = (1 << width) - 1
    if isinstance(value, bool) or not isinstance(value, _INTEGER_TYPES):
        raise ValueError(
            '%s must be an integer in [%d..%d], got %r'
            % (name, minimum, maximum, value))
    if value < minimum or value > maximum:
        raise ValueError(
            '%s must be in [%d..%d], got %d' % (name, minimum, maximum, value))
    return value & ((1 << width) - 1)


def _flag(name, value):
    """Coerce a boolean-ish field to 0 or 1."""
    if isinstance(value, numbers.Number) or isinstance(value, bool):
        return 1 if value else 0
    raise ValueError('%s must be a boolean, got %r' % (name, value))


def _choice(name, value, allowed):
    if value not in allowed:
        raise ValueError(
            '%s must be one of %r, got %r' % (name, sorted(allowed), value))
    return value


def _lookup_nearest(name, value, table, tolerance):
    """Map a physical value (mA, dBm) onto its register code."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('%s must be numeric, got %r' % (name, value))
    for code, candidate in enumerate(table):
        if abs(candidate - value) <= tolerance:
            return code
    raise ValueError(
        '%s must be one of %r, got %r' % (name, list(table), value))


def output_divider_select(output_divider):
    """RF divider select code (R4 DB22:DB20) for a power-of-two divider.

    Uses an exact table lookup rather than int(log(d, 2)) so that a
    non-power-of-two divider is rejected instead of silently truncated.
    """
    try:
        return OUTPUT_DIVIDERS.index(output_divider)
    except ValueError:
        raise ValueError(
            'output_divider must be one of %r, got %r'
            % (list(OUTPUT_DIVIDERS), output_divider))


def charge_pump_current_code(current_ma):
    return _lookup_nearest('charge_pump_current', current_ma,
                           CHARGE_PUMP_CURRENT_MA, 0.005)


def output_power_code(power_dbm):
    return _lookup_nearest('output_power', power_dbm, OUTPUT_POWER_DBM, 0.001)


def auto_prescaler(INT, vco_freq_mhz=None):
    """Pick the prescaler the data sheet allows for this INT (and VCO).

    The 8/9 prescaler requires N >= 75; the 4/5 prescaler requires N >= 23
    and is only rated for a prescaler input up to 3.6 GHz.
    """
    if INT >= INT_MIN_8_9:
        return Prescaler.Prescaler8_9
    if vco_freq_mhz is not None and vco_freq_mhz > PRESCALER_4_5_MAX_MHZ:
        raise ValueError(
            'INT=%d requires the 4/5 prescaler, but it is only rated to '
            '%.0f MHz and the VCO is at %.3f MHz'
            % (INT, PRESCALER_4_5_MAX_MHZ, vco_freq_mhz))
    return Prescaler.Prescaler4_5


# ---------------------------------------------------------------------------
# Register construction
# ---------------------------------------------------------------------------

def make_regs(
        INT=100,
        FRAC=0,
        MOD=2,
        phase=1,
        phase_adjust=False,
        prescaler=None,
        vco_freq_mhz=None,
        low_noise_spur_mode=LowNoiseSpurMode.LowNoiseMode,
        mux_out=MuxOut.ThreeState,
        ref_doubler=False,
        ref_div2=False,
        r_counter=1,
        double_buff_r4=False,
        charge_pump_current=2.50,
        ldf=None,
        ldp=None,
        pd_polarity=PDPolarity.Positive,
        powerdown=False,
        cp_three_state=False,
        counter_reset=False,
        band_select_clock_mode=BandSelectClockMode.Low,
        abp=None,
        charge_cancel=None,
        csr=False,
        clk_div_mode=ClkDivMode.ClockDividerOff,
        clock_divider_value=150,
        feedback_select=FeedbackSelect.Fundamental,
        output_divider=1,
        band_select_clock_divider=200,
        vco_powerdown=False,
        mute_till_lock_detect=False,
        aux_output_select=AuxOutputSelect.DividedOutput,
        aux_output_enable=False,
        aux_output_power=-4,
        output_disable=False,
        output_power=5,
        ld_pin_mode=LDPinMode.DigitalLockDetect):
    """Build the six register words and return them as ``[R0, ..., R5]``.

    `ldf`, `abp`, `charge_cancel` and `prescaler` default to ``None``, meaning
    "derive the data-sheet-recommended value".  Lock detect function and
    antibacklash pulse width follow integer-N vs fractional-N operation, and
    charge cancellation is an integer-N-only feature.

    `phase_adjust` (DB28) is deliberately separate from the `phase` word.
    Setting DB28 also disables VCO band selection, which a caller who merely
    wants a phase offset does not want.

    Note that the returned list is indexed by register number.  It is *not*
    the order the registers must be written in; see `Backend.write_registers`,
    which emits R5 down to R0 as the data sheet requires.
    """
    integer_n = (FRAC == 0)

    if prescaler is None:
        prescaler = auto_prescaler(INT, vco_freq_mhz)
    # Data sheet, Register 2: "For fractional-N applications, the recommended
    # setting for Bits[DB8:DB7] is 00; for integer-N applications, the
    # recommended setting for Bits[DB8:DB7] is 11."  DB8 is LDF, DB7 is LDP,
    # so the pair moves together with the operating mode.
    if ldf is None:
        ldf = LDF.IntN if integer_n else LDF.FracN
    if ldp is None:
        ldp = LDP.LDP_6ns if integer_n else LDP.LDP_10ns
    if abp is None:
        abp = ABPWidth.ABP_3ns if integer_n else ABPWidth.ABP_6ns
    if charge_cancel is None:
        charge_cancel = integer_n

    # Validate everything once, up front, then pack only validated values.
    prescaler = _choice('prescaler', prescaler,
                        (Prescaler.Prescaler4_5, Prescaler.Prescaler8_9))
    int_min = (INT_MIN_8_9 if prescaler == Prescaler.Prescaler8_9
               else INT_MIN_4_5)
    if (prescaler == Prescaler.Prescaler4_5 and vco_freq_mhz is not None
            and vco_freq_mhz > PRESCALER_4_5_MAX_MHZ):
        raise ValueError(
            'the 4/5 prescaler is only rated to %.0f MHz; VCO is %.3f MHz'
            % (PRESCALER_4_5_MAX_MHZ, vco_freq_mhz))

    INT = _uint('INT', INT, 16, int_min, INT_MAX)
    MOD = _uint('MOD', MOD, 12, MOD_MIN, MOD_MAX)
    FRAC = _uint('FRAC', FRAC, 12, FRAC_MIN, MOD - 1)
    # The data sheet requires the phase word be less than MOD.
    phase = _uint('phase', phase, 12, 0, min(PHASE_MAX, MOD - 1))
    r_counter = _uint('r_counter', r_counter, 10,
                      R_COUNTER_MIN, R_COUNTER_MAX)
    clock_divider_value = _uint('clock_divider_value', clock_divider_value,
                                12, 0, CLOCK_DIVIDER_MAX)

    band_select_clock_mode = _choice(
        'band_select_clock_mode', band_select_clock_mode,
        (BandSelectClockMode.Low, BandSelectClockMode.High))
    bscd_max = (BAND_SELECT_CLOCK_DIVIDER_MAX_FAST
                if band_select_clock_mode == BandSelectClockMode.High
                else BAND_SELECT_CLOCK_DIVIDER_MAX)
    band_select_clock_divider = _uint(
        'band_select_clock_divider', band_select_clock_divider, 8,
        BAND_SELECT_CLOCK_DIVIDER_MIN, bscd_max)

    low_noise_spur_mode = _choice(
        'low_noise_spur_mode', low_noise_spur_mode,
        (LowNoiseSpurMode.LowNoiseMode, LowNoiseSpurMode.LowSpurMode))
    mux_out = _uint('mux_out', mux_out, 3, 0, 6)   # 7 is reserved
    clk_div_mode = _choice('clk_div_mode', clk_div_mode,
                           (ClkDivMode.ClockDividerOff,
                            ClkDivMode.FastLockEnable,
                            ClkDivMode.ResyncEnable))
    ld_pin_mode = _uint('ld_pin_mode', ld_pin_mode, 2)

    # These are two-valued selectors, not booleans.  Reject anything else, so
    # that a physical value such as ldp=10.0 (nanoseconds) is an error rather
    # than being silently coerced to a truthy 1.
    ldf = _choice('ldf', ldf, (LDF.FracN, LDF.IntN))
    ldp = _choice('ldp', ldp, (LDP.LDP_10ns, LDP.LDP_6ns))
    abp = _choice('abp', abp, (ABPWidth.ABP_6ns, ABPWidth.ABP_3ns))
    pd_polarity = _choice('pd_polarity', pd_polarity,
                          (PDPolarity.Negative, PDPolarity.Positive))
    feedback_select = _choice('feedback_select', feedback_select,
                              (FeedbackSelect.Divided,
                               FeedbackSelect.Fundamental))
    aux_output_select = _choice('aux_output_select', aux_output_select,
                                (AuxOutputSelect.DividedOutput,
                                 AuxOutputSelect.Fundamental))

    div_select = output_divider_select(output_divider)
    cp_code = charge_pump_current_code(charge_pump_current)
    out_pwr = output_power_code(output_power)
    aux_pwr = output_power_code(aux_output_power)

    regs = [0] * 6

    # R0 -- Figure 24
    regs[0] = (
        INT << 15 |
        FRAC << 3 |
        0)

    # R1 -- Figure 25
    regs[1] = (
        _flag('phase_adjust', phase_adjust) << 28 |
        prescaler << 27 |
        phase << 15 |
        MOD << 3 |
        1)

    # R2 -- Figure 26
    regs[2] = (
        low_noise_spur_mode << 29 |
        mux_out << 26 |
        _flag('ref_doubler', ref_doubler) << 25 |
        _flag('ref_div2', ref_div2) << 24 |
        r_counter << 14 |
        _flag('double_buff_r4', double_buff_r4) << 13 |
        cp_code << 9 |
        ldf << 8 |
        ldp << 7 |
        pd_polarity << 6 |
        _flag('powerdown', powerdown) << 5 |
        _flag('cp_three_state', cp_three_state) << 4 |
        _flag('counter_reset', counter_reset) << 3 |
        2)

    # R3 -- Figure 27
    regs[3] = (
        band_select_clock_mode << 23 |
        abp << 22 |
        _flag('charge_cancel', charge_cancel) << 21 |
        _flag('csr', csr) << 18 |
        clk_div_mode << 15 |
        clock_divider_value << 3 |
        3)

    # R4 -- Figure 28
    regs[4] = (
        feedback_select << 23 |
        div_select << 20 |
        band_select_clock_divider << 12 |
        _flag('vco_powerdown', vco_powerdown) << 11 |
        _flag('mute_till_lock_detect', mute_till_lock_detect) << 10 |
        aux_output_select << 9 |
        _flag('aux_output_enable', aux_output_enable) << 8 |
        aux_pwr << 6 |
        (0 if output_disable else 1) << 5 |
        out_pwr << 3 |
        4)

    # R5 -- Figure 29.  DB20:DB19 are reserved and must be written as 0b11.
    regs[5] = (
        ld_pin_mode << 22 |
        3 << 19 |
        5)

    return regs


# ---------------------------------------------------------------------------
# Decoding, for tests and for pretty-printing
# ---------------------------------------------------------------------------

def decode_regs(regs):
    """Unpack ``[R0..R5]`` back into a dict of field values."""
    for n, reg in enumerate(regs):
        if reg & 0x7 != n:
            raise ValueError(
                'R%d has control bits %d, expected %d' % (n, reg & 0x7, n))
    r0, r1, r2, r3, r4, r5 = regs
    return {
        'INT': (r0 >> 15) & 0xFFFF,
        'FRAC': (r0 >> 3) & 0xFFF,
        'phase_adjust': (r1 >> 28) & 0x1,
        'prescaler': (r1 >> 27) & 0x1,
        'phase': (r1 >> 15) & 0xFFF,
        'MOD': (r1 >> 3) & 0xFFF,
        'low_noise_spur_mode': (r2 >> 29) & 0x3,
        'mux_out': (r2 >> 26) & 0x7,
        'ref_doubler': (r2 >> 25) & 0x1,
        'ref_div2': (r2 >> 24) & 0x1,
        'r_counter': (r2 >> 14) & 0x3FF,
        'double_buff_r4': (r2 >> 13) & 0x1,
        'charge_pump_current': CHARGE_PUMP_CURRENT_MA[(r2 >> 9) & 0xF],
        'ldf': (r2 >> 8) & 0x1,
        'ldp': (r2 >> 7) & 0x1,
        'pd_polarity': (r2 >> 6) & 0x1,
        'powerdown': (r2 >> 5) & 0x1,
        'cp_three_state': (r2 >> 4) & 0x1,
        'counter_reset': (r2 >> 3) & 0x1,
        'band_select_clock_mode': (r3 >> 23) & 0x1,
        'abp': (r3 >> 22) & 0x1,
        'charge_cancel': (r3 >> 21) & 0x1,
        'csr': (r3 >> 18) & 0x1,
        'clk_div_mode': (r3 >> 15) & 0x3,
        'clock_divider_value': (r3 >> 3) & 0xFFF,
        'feedback_select': (r4 >> 23) & 0x1,
        'output_divider': OUTPUT_DIVIDERS[(r4 >> 20) & 0x7],
        'band_select_clock_divider': (r4 >> 12) & 0xFF,
        'vco_powerdown': (r4 >> 11) & 0x1,
        'mute_till_lock_detect': (r4 >> 10) & 0x1,
        'aux_output_select': (r4 >> 9) & 0x1,
        'aux_output_enable': (r4 >> 8) & 0x1,
        'aux_output_power': OUTPUT_POWER_DBM[(r4 >> 6) & 0x3],
        'output_disable': 0 if (r4 >> 5) & 0x1 else 1,
        'output_power': OUTPUT_POWER_DBM[(r4 >> 3) & 0x3],
        'ld_pin_mode': (r5 >> 22) & 0x3,
    }
