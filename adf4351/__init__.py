"""Control the Analog Devices ADF4351 wideband synthesizer from Python.

The register encoding in this package is derived from the ADF4351 data sheet
(Rev. 0), Figures 24 to 29.  Nothing here depends on a particular transport:
pick a backend from `adf4351.backends` -- FX2 USB, Linux spidev, bit-banged
GPIO, or FTDI MPSSE -- or write your own by subclassing `Backend`.

Importing this package pulls in nothing but the standard library.
"""

from __future__ import division, print_function

from .registers import (
    # encodings
    Prescaler,
    LowNoiseSpurMode,
    MuxOut,
    LDF,
    LDP,
    PDPolarity,
    BandSelectClockMode,
    ABPWidth,
    ClkDivMode,
    FeedbackSelect,
    AuxOutputSelect,
    LDPinMode,
    CHARGE_PUMP_CURRENT_MA,
    OUTPUT_POWER_DBM,
    OUTPUT_DIVIDERS,
    # limits
    VCO_MIN_MHZ,
    VCO_MAX_MHZ,
    RF_OUT_MIN_MHZ,
    RF_OUT_MAX_MHZ,
    PFD_MAX_FRAC_MHZ,
    PFD_MAX_INT_MHZ,
    INT_MIN_4_5,
    INT_MIN_8_9,
    INT_MAX,
    MOD_MIN,
    MOD_MAX,
    R_COUNTER_MIN,
    R_COUNTER_MAX,
    # functions
    make_regs,
    decode_regs,
)
from .synth import Plan, plan, choose_output_divider, split_n
from .device import ADF4351
from .backends import Backend, get_backend


VERSION = '1.0.0'

__all__ = [
    'VERSION',
    'ADF4351', 'Backend', 'get_backend',
    'Plan', 'plan', 'choose_output_divider', 'split_n',
    'make_regs', 'decode_regs',
    'Prescaler', 'LowNoiseSpurMode', 'MuxOut', 'LDF', 'LDP', 'PDPolarity',
    'BandSelectClockMode', 'ABPWidth', 'ClkDivMode', 'FeedbackSelect',
    'AuxOutputSelect', 'LDPinMode',
    'CHARGE_PUMP_CURRENT_MA', 'OUTPUT_POWER_DBM', 'OUTPUT_DIVIDERS',
    'VCO_MIN_MHZ', 'VCO_MAX_MHZ', 'RF_OUT_MIN_MHZ', 'RF_OUT_MAX_MHZ',
    'PFD_MAX_FRAC_MHZ', 'PFD_MAX_INT_MHZ',
    'INT_MIN_4_5', 'INT_MIN_8_9', 'INT_MAX', 'MOD_MIN', 'MOD_MAX',
    'R_COUNTER_MIN', 'R_COUNTER_MAX',
]
