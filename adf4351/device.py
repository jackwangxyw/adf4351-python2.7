"""High-level ADF4351 control: a planner and a transport, joined up."""

from __future__ import division, print_function

import time

from . import registers as _r
from . import synth as _synth


class ADF4351(object):
    """An ADF4351 reachable over some `Backend`.

    ``dev = ADF4351(FX2Backend(), ref_freq_mhz=25.0)``
    ``plan = dev.set_frequency(1000.0)``
    ``dev.wait_for_lock()``
    """

    def __init__(self, backend, ref_freq_mhz=25.0):
        self.backend = backend
        self.ref_freq_mhz = ref_freq_mhz
        self.regs = None
        self.plan = None

    # -- planning -----------------------------------------------------------

    def plan_frequency(self, freq_mhz, **kwargs):
        """Plan `freq_mhz` without touching the hardware."""
        kwargs.setdefault('ref_freq_mhz', self.ref_freq_mhz)
        return _synth.plan(freq_mhz, **kwargs)

    # -- register access ----------------------------------------------------

    def write_registers(self, regs):
        """Write ``[R0..R5]``; the backend emits them R5 first, R0 last."""
        self.backend.write_registers(regs)
        self.regs = list(regs)

    def set_frequency(self, freq_mhz, resolution_khz=1.0,
                      mux_out=_r.MuxOut.DigitalLockDetect, **kwargs):
        """Plan, encode and program `freq_mhz`.  Returns the `Plan`.

        MUXOUT defaults to digital lock detect so that :meth:`wait_for_lock`
        has something to read.
        """
        overrides = {}
        for key in list(kwargs):
            if key not in ('ref_freq_mhz', 'ref_doubler', 'ref_div2',
                           'feedback_select', 'band_select_clock_mode',
                           'r_counter'):
                overrides[key] = kwargs.pop(key)

        plan = self.plan_frequency(freq_mhz, resolution_khz=resolution_khz,
                                   **kwargs)
        overrides.setdefault('mux_out', mux_out)
        regs = plan.make_regs(**overrides)
        self.write_registers(regs)
        self.plan = plan
        return plan

    # -- lock detect --------------------------------------------------------

    def locked(self):
        """Current state of MUXOUT, or None if the backend cannot read it."""
        return self.backend.get_mux()

    def wait_for_lock(self, timeout=1.0, interval=0.05):
        """Poll MUXOUT until it reads high, or `timeout` seconds elapse.

        Returns True on lock, False on timeout, and None if the backend has
        no way to read MUXOUT.  Requires that MUXOUT be programmed to digital
        lock detect, which :meth:`set_frequency` does by default.
        """
        deadline = time.time() + timeout
        while True:
            state = self.backend.get_mux()
            if state is None:
                return None
            if state:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(interval)

    # -- lifecycle ----------------------------------------------------------

    def close(self):
        self.backend.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
