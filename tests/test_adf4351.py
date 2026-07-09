# -*- coding: utf-8 -*-
"""Tests for the ADF4351 library.

Runs on Python 2.7 and 3.x:

    python -m unittest discover -s tests -v

The register encoding tests check against the data sheet, not against any
other implementation.  The "datasheet contradictions" tests pin down places
where a naive reading of a common third-party encoding disagrees with the
data sheet, so that the correct behaviour cannot silently regress.
"""

from __future__ import division, print_function

import unittest

from adf4351 import registers as r
from adf4351 import synth
from adf4351.backends.base import Backend, SerialWordBackend
from adf4351.backends import ftdi


class RecordingBackend(Backend):
    """Captures the words a caller writes, in the order they were written."""

    def __init__(self):
        self.words = []

    def write_word(self, word):
        self.words.append(word)


# ---------------------------------------------------------------------------
# Golden vectors
# ---------------------------------------------------------------------------

class TestDatasheetWorkedExample(unittest.TestCase):
    """ADF4351 data sheet, "RF Synthesizer -- A Worked Example".

    2112.6 MHz output from a 10 MHz reference with 200 kHz channel spacing.
    The data sheet states RF divider = 2, VCO = 4225.2 MHz, INT = 422,
    FRAC = 13, MOD = 25.
    """

    def setUp(self):
        self.plan = synth.plan(2112.6, ref_freq_mhz=10.0,
                               resolution_khz=200.0, r_counter=1)

    def test_int_frac_mod(self):
        self.assertEqual((self.plan.INT, self.plan.FRAC, self.plan.MOD),
                         (422, 13, 25))

    def test_divider_and_vco(self):
        self.assertEqual(self.plan.output_divider, 2)
        self.assertAlmostEqual(self.plan.f_vco_mhz, 4225.2, places=9)

    def test_pfd_and_output(self):
        self.assertAlmostEqual(self.plan.f_pfd_mhz, 10.0, places=9)
        self.assertAlmostEqual(self.plan.f_out_mhz, 2112.6, places=9)
        self.assertAlmostEqual(self.plan.error_hz, 0.0, places=3)

    def test_prescaler_is_8_9(self):
        # INT = 422 >= 75, so the 8/9 prescaler is required.
        self.assertEqual(self.plan.prescaler, r.Prescaler.Prescaler8_9)


class TestRegisterEncoding(unittest.TestCase):

    def test_control_bits(self):
        regs = r.make_regs(INT=100)
        for n, reg in enumerate(regs):
            self.assertEqual(reg & 0x7, n, 'R%d control bits' % n)

    def test_r5_reserved_bits_are_11(self):
        # Data sheet Figure 29: DB20:DB19 are reserved and must be 0b11.
        regs = r.make_regs(INT=100)
        self.assertEqual((regs[5] >> 19) & 0x3, 0x3)

    def test_r5_canonical_power_up_value(self):
        regs = r.make_regs(INT=100, ld_pin_mode=r.LDPinMode.DigitalLockDetect)
        self.assertEqual(regs[5], 0x00580005)

    def test_db31_never_set(self):
        # Keeps every word inside a Python 2 int, and DB31 is reserved anyway.
        regs = r.make_regs(INT=65535, FRAC=4094, MOD=4095, r_counter=1023)
        for reg in regs:
            self.assertEqual(reg & (1 << 31), 0)

    def test_roundtrip(self):
        regs = r.make_regs(
            INT=422, FRAC=13, MOD=25, phase=7, r_counter=3,
            output_divider=8, mux_out=r.MuxOut.DigitalLockDetect,
            charge_pump_current=4.69, output_power=2, aux_output_power=-1,
            band_select_clock_divider=80)
        d = r.decode_regs(regs)
        self.assertEqual(d['INT'], 422)
        self.assertEqual(d['FRAC'], 13)
        self.assertEqual(d['MOD'], 25)
        self.assertEqual(d['phase'], 7)
        self.assertEqual(d['r_counter'], 3)
        self.assertEqual(d['output_divider'], 8)
        self.assertEqual(d['mux_out'], r.MuxOut.DigitalLockDetect)
        self.assertAlmostEqual(d['charge_pump_current'], 4.69)
        self.assertEqual(d['output_power'], 2)
        self.assertEqual(d['aux_output_power'], -1)
        self.assertEqual(d['band_select_clock_divider'], 80)

    def test_decode_rejects_wrong_control_bits(self):
        regs = r.make_regs(INT=100)
        regs[3] ^= 0x1
        self.assertRaises(ValueError, r.decode_regs, regs)


# ---------------------------------------------------------------------------
# Places where the data sheet contradicts a naive encoding
# ---------------------------------------------------------------------------

class TestDatasheetContradictions(unittest.TestCase):

    def test_low_spur_mode_is_0b11_not_1(self):
        # R2 DB30:DB29.  00 = low noise, 11 = low spur, 01 and 10 reserved.
        self.assertEqual(r.LowNoiseSpurMode.LowSpurMode, 3)
        regs = r.make_regs(INT=100,
                           low_noise_spur_mode=r.LowNoiseSpurMode.LowSpurMode)
        self.assertEqual((regs[2] >> 29) & 0x3, 0x3)

    def test_low_noise_mode_is_0b00(self):
        regs = r.make_regs(INT=100,
                           low_noise_spur_mode=r.LowNoiseSpurMode.LowNoiseMode)
        self.assertEqual((regs[2] >> 29) & 0x3, 0x0)

    def test_reserved_noise_modes_rejected(self):
        for bad in (1, 2):
            self.assertRaises(ValueError, r.make_regs,
                              INT=100, low_noise_spur_mode=bad)

    def test_ldf_follows_integer_n_not_its_inverse(self):
        # R2 DB8: 0 => 40 PFD cycles (recommended for FRAC-N),
        #         1 =>  5 PFD cycles (recommended for INT-N).
        int_n = r.make_regs(INT=100, FRAC=0, MOD=2)
        frac_n = r.make_regs(INT=100, FRAC=1, MOD=25)
        self.assertEqual((int_n[2] >> 8) & 1, r.LDF.IntN)
        self.assertEqual((frac_n[2] >> 8) & 1, r.LDF.FracN)

    def test_ldf_ldp_pair_matches_the_datasheet_recommendation(self):
        # "For fractional-N applications, the recommended setting for
        #  Bits[DB8:DB7] is 00; for integer-N applications, the recommended
        #  setting for Bits[DB8:DB7] is 11."
        int_n = r.make_regs(INT=100, FRAC=0, MOD=2)
        frac_n = r.make_regs(INT=100, FRAC=1, MOD=25)
        self.assertEqual((int_n[2] >> 7) & 0x3, 0x3)
        self.assertEqual((frac_n[2] >> 7) & 0x3, 0x0)

    def test_abp_is_6ns_or_3ns_and_follows_mode(self):
        # R3 DB22: 0 => 6 ns (FRAC-N), 1 => 3 ns (INT-N).
        self.assertEqual(r.ABPWidth.ABP_6ns, 0)
        self.assertEqual(r.ABPWidth.ABP_3ns, 1)
        int_n = r.make_regs(INT=100, FRAC=0, MOD=2)
        frac_n = r.make_regs(INT=100, FRAC=1, MOD=25)
        self.assertEqual((int_n[3] >> 22) & 1, r.ABPWidth.ABP_3ns)
        self.assertEqual((frac_n[3] >> 22) & 1, r.ABPWidth.ABP_6ns)

    def test_abp_3ns_is_reachable(self):
        regs = r.make_regs(INT=100, FRAC=1, MOD=25, abp=r.ABPWidth.ABP_3ns)
        self.assertEqual((regs[3] >> 22) & 1, 1)

    def test_charge_pump_step_14_is_4_69_ma(self):
        self.assertAlmostEqual(r.CHARGE_PUMP_CURRENT_MA[14], 4.69)
        regs = r.make_regs(INT=100, charge_pump_current=4.69)
        self.assertEqual((regs[2] >> 9) & 0xF, 14)
        # 4.49 mA is not a real setting.
        self.assertRaises(ValueError, r.make_regs,
                          INT=100, charge_pump_current=4.49)

    def test_charge_cancel_is_integer_n_only(self):
        int_n = r.make_regs(INT=100, FRAC=0, MOD=2)
        frac_n = r.make_regs(INT=100, FRAC=1, MOD=25)
        self.assertEqual((int_n[3] >> 21) & 1, 1)
        self.assertEqual((frac_n[3] >> 21) & 1, 0)

    def test_phase_adjust_is_independent_of_the_phase_word(self):
        # DB28 also disables VCO band select, so supplying a phase word must
        # not set it as a side effect.
        regs = r.make_regs(INT=100, FRAC=1, MOD=25, phase=7)
        self.assertEqual((regs[1] >> 28) & 1, 0)
        self.assertEqual((regs[1] >> 15) & 0xFFF, 7)

        regs = r.make_regs(INT=100, FRAC=1, MOD=25, phase=7, phase_adjust=True)
        self.assertEqual((regs[1] >> 28) & 1, 1)

    def test_phase_word_must_be_less_than_mod(self):
        # Data sheet: "The phase word must be less than the MOD value".
        self.assertRaises(ValueError, r.make_regs,
                          INT=100, FRAC=1, MOD=25, phase=25)
        self.assertRaises(ValueError, r.make_regs,
                          INT=100, FRAC=1, MOD=1000, phase=5000)

    def test_phase_overflow_cannot_corrupt_neighbouring_fields(self):
        # A 13-bit phase word would otherwise bleed into DB27 (prescaler) and
        # DB28 (phase adjust).
        self.assertRaises(ValueError, r.make_regs,
                          INT=100, FRAC=1, MOD=4095, phase=5000)

    def test_output_divider_must_be_a_power_of_two(self):
        for bad in (3, 5, 6, 7, 128, 0):
            self.assertRaises(ValueError, r.make_regs,
                              INT=100, output_divider=bad)

    def test_output_divider_select_is_exact(self):
        for select, divider in enumerate(r.OUTPUT_DIVIDERS):
            self.assertEqual(r.output_divider_select(divider), select)
            regs = r.make_regs(INT=100, output_divider=divider)
            self.assertEqual((regs[4] >> 20) & 0x7, select)

    def test_mod_above_4095_rejected(self):
        self.assertRaises(ValueError, r.make_regs, INT=100, FRAC=1, MOD=25000)

    def test_float_mod_rejected(self):
        self.assertRaises(ValueError, r.make_regs, INT=100, FRAC=1, MOD=25.0)

    def test_ldp_rejects_a_physical_nanosecond_value(self):
        # ldp=10.0 (meaning 10 ns) must not be silently coerced to a truthy 1,
        # which is the 6 ns setting.
        self.assertRaises(ValueError, r.make_regs, INT=100, ldp=10.0)
        self.assertRaises(ValueError, r.make_regs, INT=100, ldp=6)

    def test_muxout_reserved_code_rejected(self):
        self.assertRaises(ValueError, r.make_regs, INT=100, mux_out=7)


class TestPrescalerRules(unittest.TestCase):

    def test_int_minimum_depends_on_prescaler(self):
        # 4/5 => N_MIN 23; 8/9 => N_MIN 75.
        r.make_regs(INT=23, prescaler=r.Prescaler.Prescaler4_5)
        self.assertRaises(ValueError, r.make_regs,
                          INT=22, prescaler=r.Prescaler.Prescaler4_5)
        r.make_regs(INT=75, prescaler=r.Prescaler.Prescaler8_9)
        self.assertRaises(ValueError, r.make_regs,
                          INT=74, prescaler=r.Prescaler.Prescaler8_9)

    def test_auto_prescaler(self):
        self.assertEqual(r.auto_prescaler(74), r.Prescaler.Prescaler4_5)
        self.assertEqual(r.auto_prescaler(75), r.Prescaler.Prescaler8_9)

    def test_4_5_prescaler_rejected_above_3_6_ghz(self):
        self.assertRaises(ValueError, r.make_regs, INT=50,
                          prescaler=r.Prescaler.Prescaler4_5,
                          vco_freq_mhz=3700.0)


class TestBandSelectClock(unittest.TestCase):

    def test_low_mode_keeps_clock_at_or_below_125_khz(self):
        divider = synth.band_select_clock_divider(25.0)
        self.assertLessEqual(25.0 * 1e3 / divider,
                             r.BAND_SELECT_CLOCK_MAX_LOW_KHZ + 1e-9)

    def test_high_mode_divider_capped_at_254(self):
        self.assertRaises(ValueError, r.make_regs, INT=100,
                          band_select_clock_mode=r.BandSelectClockMode.High,
                          band_select_clock_divider=255)
        r.make_regs(INT=100,
                    band_select_clock_mode=r.BandSelectClockMode.High,
                    band_select_clock_divider=254)


# ---------------------------------------------------------------------------
# Frequency planning
# ---------------------------------------------------------------------------

class TestPlanning(unittest.TestCase):

    def test_mod_never_exceeds_4095(self):
        # Sweep the region where a fixed 1 kHz modulus would demand MOD=25000.
        for i in range(0, 200):
            freq = 2200.0 + i * 0.001
            p = synth.plan(freq, ref_freq_mhz=25.0, resolution_khz=1.0)
            self.assertLessEqual(p.MOD, r.MOD_MAX)
            self.assertGreaterEqual(p.MOD, r.MOD_MIN)
            p.make_regs()       # must not raise

    def test_fractional_n_never_exceeds_32_mhz_pfd(self):
        for freq in (2200.001, 2450.001, 3000.7, 4399.9):
            p = synth.plan(freq, ref_freq_mhz=25.0, resolution_khz=1.0)
            if not p.integer_n:
                self.assertLessEqual(p.f_pfd_mhz, r.PFD_MAX_FRAC_MHZ + 1e-9)

    def test_integer_n_when_exact(self):
        p = synth.plan(1000.0, ref_freq_mhz=25.0)
        self.assertTrue(p.integer_n)
        self.assertEqual(p.FRAC, 0)
        self.assertEqual(p.INT, 160)
        self.assertAlmostEqual(p.f_pfd_mhz, 25.0)
        self.assertEqual(p.output_divider, 4)

    def test_prefers_the_highest_pfd(self):
        p = synth.plan(2450.1, ref_freq_mhz=25.0, resolution_khz=1.0)
        self.assertAlmostEqual(p.f_pfd_mhz, 25.0)
        self.assertFalse(p.pfd_reduced)

    def test_reduces_pfd_only_when_it_must(self):
        p = synth.plan(2450.001, ref_freq_mhz=25.0, resolution_khz=1.0)
        self.assertTrue(p.pfd_reduced)
        self.assertLess(p.f_pfd_mhz, 25.0)

    def test_vco_stays_in_band(self):
        for freq in (34.375, 100.0, 1000.0, 2200.0, 4400.0):
            p = synth.plan(freq, ref_freq_mhz=25.0, resolution_khz=100.0)
            self.assertGreaterEqual(p.f_vco_mhz, r.VCO_MIN_MHZ - 1e-6)
            self.assertLessEqual(p.f_vco_mhz, r.VCO_MAX_MHZ + 1e-6)

    def test_out_of_range_rejected(self):
        self.assertRaises(ValueError, synth.plan, 30.0)
        self.assertRaises(ValueError, synth.plan, 4500.0)

    def test_split_n_exact_fraction(self):
        self.assertEqual(synth.split_n(422.52), (422, 13, 25))

    def test_split_n_integer(self):
        self.assertEqual(synth.split_n(160.0), (160, 0, 2))

    def test_pfd_above_90_mhz_is_rejected(self):
        # Integer-N tops out at a 90 MHz PFD, so a 100 MHz reference at R=1
        # has no legal solution.
        self.assertRaises(ValueError, synth.plan, 1000.0,
                          ref_freq_mhz=100.0, r_counter=1)

    def test_high_pfd_sets_phase_adjust_to_disable_band_select(self):
        # Above 45 MHz the data sheet requires VCO band select to be disabled,
        # which is what R1 DB28 does.  A 60 MHz PFD with VCO = 3000 MHz gives
        # N = 50 exactly: integer-N (so the 32 MHz FRAC-N ceiling does not
        # apply) and below 3.6 GHz (so the 4/5 prescaler's N_MIN of 23 is what
        # binds, not the 8/9 prescaler's 75).  The band select clock needs the
        # fast mode to stay under its limit at this PFD.
        p = synth.plan(3000.0, ref_freq_mhz=60.0, resolution_khz=1000.0,
                       r_counter=1,
                       band_select_clock_mode=r.BandSelectClockMode.High)
        self.assertTrue(p.integer_n)
        self.assertEqual(p.INT, 50)
        self.assertEqual(p.prescaler, r.Prescaler.Prescaler4_5)
        self.assertGreater(p.f_pfd_mhz, r.PFD_BAND_SELECT_DISABLE_MHZ)
        regs = p.make_regs()
        self.assertEqual((regs[1] >> 28) & 1, 1)

    def test_band_select_clock_unreachable_in_low_mode_at_high_pfd(self):
        # At 60 MHz PFD the low-mode 125 kHz limit needs a divider of 480,
        # beyond the 8-bit field, so no candidate survives.
        self.assertRaises(ValueError, synth.band_select_clock_divider, 60.0)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class TestWriteOrder(unittest.TestCase):

    def test_registers_are_written_r5_first_r0_last(self):
        regs = r.make_regs(INT=100)
        backend = RecordingBackend()
        backend.write_registers(regs)
        self.assertEqual(backend.words, [regs[5], regs[4], regs[3],
                                         regs[2], regs[1], regs[0]])

    def test_control_bits_identify_each_word(self):
        regs = r.make_regs(INT=100)
        backend = RecordingBackend()
        backend.write_registers(regs)
        self.assertEqual([w & 0x7 for w in backend.words], [5, 4, 3, 2, 1, 0])

    def test_wrong_length_rejected(self):
        backend = RecordingBackend()
        self.assertRaises(ValueError, backend.write_registers, [0] * 5)

    def test_base_backend_requires_write_word(self):
        self.assertRaises(NotImplementedError, Backend().write_word, 0)

    def test_get_mux_defaults_to_none(self):
        self.assertIsNone(Backend().get_mux())


class TestWordToBytes(unittest.TestCase):

    def test_msb_first(self):
        self.assertEqual(list(SerialWordBackend.word_to_bytes(0x12345678)),
                         [0x12, 0x34, 0x56, 0x78])

    def test_masks_to_32_bits(self):
        self.assertEqual(list(SerialWordBackend.word_to_bytes(0x1FFFFFFFF)),
                         [0xFF, 0xFF, 0xFF, 0xFF])


class TestBitBang(unittest.TestCase):

    def test_shifts_msb_first_and_pulses_le_after_32_bits(self):
        from adf4351.backends.bitbang import BitBangBackend

        events = []
        backend = BitBangBackend(
            set_clk=lambda v: events.append(('clk', bool(v))),
            set_data=lambda v: events.append(('data', bool(v))),
            set_le=lambda v: events.append(('le', bool(v))))
        events[:] = []                     # discard the idle-state writes

        backend.write_word(0x80000005)

        # Data bits are the value present at each rising clock edge.
        bits = []
        current = None
        for name, value in events:
            if name == 'data':
                current = value
            elif name == 'clk' and value:
                bits.append(1 if current else 0)
        self.assertEqual(len(bits), 32)
        self.assertEqual(bits[0], 1)                  # MSB of 0x80000005
        self.assertEqual(bits[-3:], [1, 0, 1])        # control bits of R5

        # LE must be low for the whole shift, then pulse high, then low.
        le_events = [v for n, v in events if n == 'le']
        self.assertEqual(le_events, [False, True, False])
        last_clock = max(i for i, (n, v) in enumerate(events)
                         if n == 'clk' and v)
        first_le_high = min(i for i, (n, v) in enumerate(events)
                            if n == 'le' and v)
        self.assertGreater(first_le_high, last_clock)


class TestFtdiMpsse(unittest.TestCase):
    """The MPSSE byte stream, verified without any FTDI hardware."""

    def test_clock_divisor(self):
        # TCK = 60 MHz / ((1 + divisor) * 2)
        self.assertEqual(ftdi.clock_divisor(1000000), 29)
        self.assertEqual(ftdi.clock_divisor(30000000), 0)
        self.assertRaises(ValueError, ftdi.clock_divisor, 0)

    def test_init_sets_mode_0(self):
        commands = list(ftdi.mpsse_init_commands(1000000))
        self.assertEqual(commands, [
            ftdi.CMD_DISABLE_CLK_DIV5,
            ftdi.CMD_DISABLE_ADAPTIVE_CLK,
            ftdi.CMD_DISABLE_3PHASE_CLK,
            ftdi.CMD_SET_CLOCK_DIVISOR, 29, 0,
            ftdi.CMD_SET_BITS_LOW, 0x00, ftdi.DIRECTION,
        ])

    def test_word_command_stream(self):
        commands = list(ftdi.mpsse_word_commands(0x00580005))
        self.assertEqual(commands, [
            ftdi.CMD_SET_BITS_LOW, 0x00, ftdi.DIRECTION,     # LE low
            ftdi.CMD_CLOCK_BYTES_OUT_NEG_MSB, 0x03, 0x00,    # 4 bytes, len-1
            0x00, 0x58, 0x00, 0x05,                          # MSB first
            ftdi.CMD_SET_BITS_LOW, ftdi.PIN_LE, ftdi.DIRECTION,   # LE high
            ftdi.CMD_SET_BITS_LOW, 0x00, ftdi.DIRECTION,     # LE low
            ftdi.CMD_SEND_IMMEDIATE,
        ])

    def test_direction_mask(self):
        # SCK, DO and LE are outputs; DI is an input.
        self.assertEqual(ftdi.DIRECTION, 0x0B)
        self.assertEqual(ftdi.DIRECTION & ftdi.PIN_DI, 0)


class TestBackendRegistry(unittest.TestCase):

    def test_unknown_backend(self):
        from adf4351.backends import get_backend
        self.assertRaises(ValueError, get_backend, 'nope')

    def test_bitbang_needs_no_dependency(self):
        from adf4351.backends import get_backend
        self.assertTrue(callable(get_backend('bitbang')))


if __name__ == '__main__':
    unittest.main()
