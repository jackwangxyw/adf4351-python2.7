"""Frequency planning for the ADF4351.

Given a target output frequency and a reference oscillator, work out the RF
output divider, R counter, and INT/FRAC/MOD that synthesize it:

    f_PFD  = f_REF * (1 + D) / (R * (1 + T))
    f_VCO  = f_PFD * (INT + FRAC / MOD)
    f_OUT  = f_VCO / output_divider

where D is the reference doubler bit and T the reference divide-by-2 bit.

The PFD is driven as high as the data sheet allows, because phase noise
improves roughly 3 dB per doubling of the PFD frequency.  It is only reduced
when a higher PFD cannot reach the requested frequency within the requested
resolution.
"""

from __future__ import division, print_function

from fractions import Fraction
from math import ceil

from . import registers as _r


class Plan(object):
    """The result of :func:`plan` -- everything needed to build registers."""

    __slots__ = (
        'freq_mhz', 'ref_freq_mhz', 'resolution_khz',
        'INT', 'FRAC', 'MOD', 'r_counter', 'output_divider',
        'prescaler', 'band_select_clock_divider', 'band_select_clock_mode',
        'ref_doubler', 'ref_div2', 'feedback_select',
        'f_pfd_mhz', 'f_vco_mhz', 'f_out_mhz', 'f_pfd_max_mhz',
    )

    def __init__(self, **kw):
        for name in self.__slots__:
            setattr(self, name, kw[name])

    # -- derived quantities -------------------------------------------------

    @property
    def integer_n(self):
        return self.FRAC == 0

    @property
    def error_hz(self):
        return (self.f_out_mhz - self.freq_mhz) * 1e6

    @property
    def error_ppm(self):
        return (self.f_out_mhz - self.freq_mhz) / self.freq_mhz * 1e6

    @property
    def pfd_reduced(self):
        """True if the PFD was lowered below its maximum to hit the target."""
        return self.f_pfd_mhz < self.f_pfd_max_mhz - 1e-9

    @property
    def step_khz(self):
        """Finest channel step available at this PFD and modulus."""
        return self.f_pfd_mhz / _r.MOD_MAX / self.output_divider * 1e3

    @property
    def band_select_clock_khz(self):
        return self.f_pfd_mhz * 1e3 / self.band_select_clock_divider

    def make_regs(self, **overrides):
        """Build ``[R0..R5]`` for this plan.  Keyword args override defaults."""
        kw = dict(
            INT=self.INT, FRAC=self.FRAC, MOD=self.MOD,
            r_counter=self.r_counter,
            output_divider=self.output_divider,
            prescaler=self.prescaler,
            vco_freq_mhz=self.f_vco_mhz,
            ref_doubler=self.ref_doubler,
            ref_div2=self.ref_div2,
            feedback_select=self.feedback_select,
            band_select_clock_divider=self.band_select_clock_divider,
            band_select_clock_mode=self.band_select_clock_mode,
            # Above 45 MHz the data sheet requires VCO band select to be
            # disabled, which is what the phase adjust bit (R1 DB28) does.
            phase_adjust=self.f_pfd_mhz > _r.PFD_BAND_SELECT_DISABLE_MHZ,
        )
        kw.update(overrides)
        return _r.make_regs(**kw)

    def __repr__(self):
        return ('<Plan %.6f MHz: INT=%d FRAC=%d MOD=%d R=%d div=%d '
                'pfd=%.6f MHz err=%+.1f Hz>'
                % (self.freq_mhz, self.INT, self.FRAC, self.MOD,
                   self.r_counter, self.output_divider, self.f_pfd_mhz,
                   self.error_hz))


def choose_output_divider(freq_mhz):
    """Smallest power-of-two divider keeping the VCO in its legal band.

    Returns ``(output_divider, f_vco_mhz)``.
    """
    for divider in _r.OUTPUT_DIVIDERS:
        vco = freq_mhz * divider
        if vco >= _r.VCO_MIN_MHZ:
            if vco > _r.VCO_MAX_MHZ:
                raise ValueError(
                    'freq %g MHz unreachable: VCO would be %g MHz (max %g)'
                    % (freq_mhz, vco, _r.VCO_MAX_MHZ))
            return divider, vco
    raise ValueError('freq %g MHz is below the minimum of %g MHz'
                     % (freq_mhz, _r.RF_OUT_MIN_MHZ))


def split_n(n):
    """Best INT/FRAC/MOD representation of the divisor `n`, with MOD <= 4095.

    ``Fraction.limit_denominator`` gives the closest rational approximation
    whose denominator does not exceed the 12-bit modulus, which is exactly
    what the hardware can represent.
    """
    INT = int(n)
    remainder = n - INT
    if remainder <= 0.0:
        return INT, 0, _r.MOD_MIN            # integer-N; MOD must still be >= 2

    frac = Fraction(remainder).limit_denominator(_r.MOD_MAX)
    FRAC, MOD = frac.numerator, frac.denominator

    if FRAC == 0 or MOD < _r.MOD_MIN:
        # Rounded down to nothing, or to a denominator of 1.
        return INT, 0, _r.MOD_MIN
    if FRAC >= MOD:
        # Rounded up a whole cycle.
        return INT + 1, 0, _r.MOD_MIN
    return INT, FRAC, MOD


def band_select_clock_divider(f_pfd_mhz, mode=_r.BandSelectClockMode.Low):
    """Smallest divider keeping the band select clock under its limit."""
    limit_khz = (_r.BAND_SELECT_CLOCK_MAX_HIGH_KHZ
                 if mode == _r.BandSelectClockMode.High
                 else _r.BAND_SELECT_CLOCK_MAX_LOW_KHZ)
    maximum = (_r.BAND_SELECT_CLOCK_DIVIDER_MAX_FAST
               if mode == _r.BandSelectClockMode.High
               else _r.BAND_SELECT_CLOCK_DIVIDER_MAX)
    divider = int(ceil(f_pfd_mhz * 1e3 / limit_khz))
    divider = max(_r.BAND_SELECT_CLOCK_DIVIDER_MIN, divider)
    if divider > maximum:
        raise ValueError(
            'band select clock cannot be brought under %g kHz at a PFD of '
            '%g MHz (would need a divider of %d, max is %d)'
            % (limit_khz, f_pfd_mhz, divider, maximum))
    return divider


def plan(freq_mhz,
         ref_freq_mhz=25.0,
         resolution_khz=1.0,
         ref_doubler=False,
         ref_div2=False,
         feedback_select=_r.FeedbackSelect.Fundamental,
         band_select_clock_mode=_r.BandSelectClockMode.Low,
         r_counter=None):
    """Plan the synthesis of `freq_mhz`.

    The highest legal PFD is preferred; it is stepped down only when a higher
    PFD cannot reach `freq_mhz` to within half of `resolution_khz`.  If no PFD
    can, the closest achievable frequency is returned -- inspect
    ``plan.error_hz`` to find out.

    Pass `r_counter` to pin the R counter instead of searching for it.
    """
    if resolution_khz <= 0:
        raise ValueError('resolution_khz must be positive')
    if freq_mhz < _r.RF_OUT_MIN_MHZ or freq_mhz > _r.RF_OUT_MAX_MHZ:
        raise ValueError(
            'freq %g MHz outside the ADF4351 range [%g .. %g] MHz'
            % (freq_mhz, _r.RF_OUT_MIN_MHZ, _r.RF_OUT_MAX_MHZ))

    output_divider, f_vco_mhz = choose_output_divider(freq_mhz)
    f_ref_eff = ref_freq_mhz * (2.0 if ref_doubler else 1.0) \
        / (2.0 if ref_div2 else 1.0)

    # The N counter sees the VCO directly, or the divided output.
    n_source = (f_vco_mhz if feedback_select == _r.FeedbackSelect.Fundamental
                else freq_mhz)

    tolerance_mhz = (resolution_khz / 1e3) / 2.0

    if r_counter is not None:
        r_values = [r_counter]
    else:
        r_values = range(_r.R_COUNTER_MIN, _r.R_COUNTER_MAX + 1)

    f_pfd_max = None
    best = None                 # closest candidate seen, as a fallback
    chosen = None

    for r in r_values:
        f_pfd = f_ref_eff / r
        if f_pfd > _r.PFD_MAX_INT_MHZ:
            continue            # illegal even in the most permissive mode

        INT, FRAC, MOD = split_n(n_source / f_pfd)

        # Fractional-N cannot run the PFD above 32 MHz.
        if FRAC != 0 and f_pfd > _r.PFD_MAX_FRAC_MHZ:
            continue
        if INT > _r.INT_MAX:
            continue
        try:
            prescaler = _r.auto_prescaler(INT, f_vco_mhz)
        except ValueError:
            continue
        int_min = (_r.INT_MIN_8_9
                   if prescaler == _r.Prescaler.Prescaler8_9
                   else _r.INT_MIN_4_5)
        if INT < int_min:
            continue
        try:
            bscd = band_select_clock_divider(f_pfd, band_select_clock_mode)
        except ValueError:
            continue

        if f_pfd_max is None:
            f_pfd_max = f_pfd   # first legal PFD is the highest, R ascends

        f_out = f_pfd * (INT + FRAC / MOD) / output_divider
        error = abs(f_out - freq_mhz)

        candidate = Plan(
            freq_mhz=freq_mhz, ref_freq_mhz=ref_freq_mhz,
            resolution_khz=resolution_khz,
            INT=INT, FRAC=FRAC, MOD=MOD, r_counter=r,
            output_divider=output_divider, prescaler=prescaler,
            band_select_clock_divider=bscd,
            band_select_clock_mode=band_select_clock_mode,
            ref_doubler=ref_doubler, ref_div2=ref_div2,
            feedback_select=feedback_select,
            f_pfd_mhz=f_pfd, f_vco_mhz=f_pfd * (INT + FRAC / MOD),
            f_out_mhz=f_out, f_pfd_max_mhz=f_pfd_max)

        if best is None or error < abs(best.f_out_mhz - freq_mhz):
            best = candidate
        if error <= tolerance_mhz:
            chosen = candidate
            break

    if best is None:
        raise ValueError(
            'no legal R counter reaches %g MHz from a %g MHz reference'
            % (freq_mhz, ref_freq_mhz))

    return chosen if chosen is not None else best
