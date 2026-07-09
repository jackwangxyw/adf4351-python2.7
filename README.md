# adf4351 python2.7 control

A Python library for the **Analog Devices ADF4351** wideband frequency synthesizer,
targeting **Python 2.7** (and working unchanged on Python 3).

The ADF4351 is a PLL synthesizer with an integrated VCO covering **34.375 MHz to
4.4 GHz**. Its VCO runs from 2200 to 4400 MHz and a chain of divide-by-1/2/4/…/64
stages brings the output down from there. It can operate in integer-N or
fractional-N mode, and is configured entirely by writing six 32-bit registers over
a three-wire serial interface.

This library does three things: it **encodes those six registers** exactly as the
data sheet specifies, it **plans** the INT/FRAC/MOD/R values needed to synthesize a
requested frequency, and it **ships four transports** for getting the registers into
the chip. Nothing above the transport layer knows or cares how the bits get there.

Register encoding is derived from the ADF4351 data sheet (Rev. 0), Figures 24–29.

---

## Requirements

* **Python 2.7** or later. The core library imports nothing outside the standard
  library, so `import adf4351` works with no third-party packages installed at all.
* Per-backend dependencies, imported only when you construct that backend:

  | Backend | Needs |
  |---|---|
  | FX2 USB | `pyusb`, and a `libusb-1.0` implementation |
  | FTDI MPSSE | `pyusb`, and a `libusb-1.0` implementation |
  | Linux spidev | `spidev` (Linux only) |
  | Bit-bang GPIO | nothing — you supply the GPIO callables |

Install the dependencies with pip:

```sh
pip install pyusb          # for the FX2 or FTDI backends
pip install spidev         # for the Linux SPI backend
```

On Debian and Ubuntu there used to be `python-usb` and `python-serial` packages,
but recent releases no longer ship Python 2 packages at all — `apt install
python-usb` fails with *"Unable to locate package"*. On a modern distribution the
realistic route to a working Python 2.7 environment is pip, or a conda environment
created with `conda create -n py27 python=2.7`, activated, then `pip install pyusb`.

## Installation

```sh
git clone https://github.com/jackwangxyw/adf4351-python2.7.git
cd adf4351-python2.7
python setup.py install
```

or, for development, `pip install -e .`

### USB permissions on Linux

Opening the FX2 board over USB normally needs root. Either run with `sudo`, or add
a udev rule granting access to the device, e.g. in
`/etc/udev/rules.d/60-adf4351.rules`:

```
SUBSYSTEM=="usb", ATTR{idVendor}=="0456", ATTR{idProduct}=="b40d", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="0456", ATTR{idProduct}=="b403", MODE="0666"
```

## Backends

| Name | Class | Hardware | Status |
|---|---|---|---|
| `fx2` | `FX2Backend` | Cypress FX2 USB board (EVAL-ADF4351, CN-0285) | **Verified on hardware** |
| `spidev` | `SpiDevBackend` | Linux SPI master, e.g. Raspberry Pi | Untested on hardware |
| `bitbang` | `BitBangBackend` | Any three GPIO lines | Untested on hardware |
| `ftdi` | `FtdiBackend` | FT232H / FT2232H in MPSSE mode | Untested on hardware |

Only the FX2 path has been exercised against a real board. The other three are
written from the data sheet and unit-tested at the byte level — the bit-bang shift
sequence and the FTDI MPSSE command stream are both asserted in `tests/` — but no
silicon has confirmed them. That distinction is worth knowing before you trust one.

Import the backend you want directly, so a missing dependency never breaks anything
else:

```python
from adf4351.backends.fx2 import FX2Backend
from adf4351.backends.spi import SpiDevBackend
```

`FtdiBackend` is built on `pyusb` and speaks the MPSSE protocol itself rather than
using `pyftdi`, because every `pyftdi` release requires Python 3.5 or newer and this
library must run on 2.7.

## Wiring

The ADF4351's serial interface is three signals — **CLK**, **DATA** and **LE**. Data
is shifted in **MSB first** and sampled on the **rising edge of CLK**; CLK idles low.
That is **SPI mode 0** (CPOL=0, CPHA=0). After all 32 bits are in, a **rising edge on
LE** transfers the shift register into one of the six latches, chosen by the three
least significant bits of the word.

For `SpiDevBackend` this means LE can be wired straight to the bus **chip select**:
a conventional active-low CS idles high, drops low for the transfer, and rises at the
end — exactly the pulse LE needs. Do not invert CS (`SPI_CS_HIGH`) trying to match
LE's active-high latch; that would hold LE high *during* the transfer and drop it at
the end, which is backwards. If your board routes LE to its own pin, pass a `le_gpio`
callable instead.

## Usage

Set a frequency and confirm the PLL locked:

```python
from adf4351 import ADF4351
from adf4351.backends.fx2 import FX2Backend

with ADF4351(FX2Backend(), ref_freq_mhz=25.0) as dev:
    plan = dev.set_frequency(1000.0)
    print(plan)                      # INT/FRAC/MOD/R/divider actually used
    print(dev.wait_for_lock())       # True, False, or None if unreadable
```

Over direct SPI on a Raspberry Pi, with LE on chip select:

```python
from adf4351 import ADF4351
from adf4351.backends.spi import SpiDevBackend

dev = ADF4351(SpiDevBackend(bus=0, device=0), ref_freq_mhz=25.0)
dev.set_frequency(2450.1)
```

Bit-banging three GPIO lines, using whatever GPIO library you like:

```python
import RPi.GPIO as GPIO
from adf4351.backends.bitbang import BitBangBackend

backend = BitBangBackend(
    set_clk=lambda v: GPIO.output(11, v),
    set_data=lambda v: GPIO.output(13, v),
    set_le=lambda v: GPIO.output(15, v),
    get_mux=lambda: GPIO.input(16))
```

Plan without touching hardware, or build registers by hand:

```python
from adf4351 import plan, make_regs, MuxOut

p = plan(2112.6, ref_freq_mhz=10.0, resolution_khz=200.0)
print(p.INT, p.FRAC, p.MOD, p.output_divider)   # 422 13 25 2
print(['0x%08X' % r for r in p.make_regs()])

regs = make_regs(INT=422, FRAC=13, MOD=25, output_divider=2,
                 mux_out=MuxOut.DigitalLockDetect)
```

Register writes are always emitted **R5 first and R0 last**, as the data sheet's
initialization sequence requires — writing R0 is what triggers VCO band selection,
so it must come last. `write_registers()` handles that ordering, so callers pass the
list indexed by register number and never reverse it themselves.

## Writing your own backend

Subclass `Backend` and implement one method:

```python
from adf4351.backends.base import Backend

class MyBackend(Backend):
    def write_word(self, word):
        ...                       # shift 32 bits out, MSB first, then pulse LE

    def get_mux(self):
        return None               # or 0/1 if you can read the MUXOUT pin
```

`write_registers()`, the context-manager protocol, and everything above the
transport come for free. This is the seam to use for a custom USB controller.

## Utility scripts

### `test_connection.py`

A **read-only** liveness probe for an FX2 board. It programs no registers and does
not change the RF output. It finds the device, reads its USB descriptors and
firmware version, reads the Cypress chip revision (proving control transfers work),
and reads MUXOUT via the firmware's custom vendor command (proving the firmware is
running and answering). Exit code 0 on success.

```sh
python test_connection.py
sudo python test_connection.py     # if USB access is denied
```

### `test_freq.py`

Computes a frequency plan, prints a full breakdown including the *actual* synthesized
frequency and its error, writes the registers, and polls MUXOUT for lock.

```sh
python test_freq.py 1000                 # set 1000 MHz, write, check lock
python test_freq.py 2450.1               # exact at a 25 MHz PFD -> best phase noise
python test_freq.py 2450.001             # needs a finer step -> PFD drops
python test_freq.py 1000 --dry-run       # compute and print only, write nothing
python test_freq.py 4000 --resolution 50 # allow a coarse step, keep the PFD high
python test_freq.py 1000 --ref-freq 10   # board has a 10 MHz reference
```

## How the frequency plan is chosen

The output frequency is

```
f_out = (INT + FRAC/MOD) * f_PFD / output_divider
f_PFD = f_ref * (1 + doubler) / ((1 + div2) * R)
```

and the planner works outward from the hardware's constraints:

1. **Output divider.** Pick the smallest power-of-two divider that puts the VCO
   inside its legal 2200–4400 MHz band. This is forced; there is no freedom here.
2. **PFD frequency.** Phase noise improves by roughly 3 dB every time the PFD
   doubles, so the PFD is driven as high as the data sheet allows — smallest R
   first. It is stepped down **only** when a higher PFD cannot reach the requested
   frequency within the resolution target. Clean frequencies therefore keep the full
   reference-rate PFD, and only awkward ones trade phase noise for precision.
3. **INT / FRAC / MOD.** The remaining divisor is split into the closest fraction
   whose denominator fits the 12-bit modulus. When the divisor is a whole number the
   result is integer-N (`FRAC = 0`), which has better spur performance.

Values that follow from that choice are set automatically: the 8/9 prescaler when
`INT ≥ 75`, the lock-detect function and antibacklash pulse width appropriate to
integer-N vs fractional-N, charge cancellation in integer-N only, a band-select
clock divider that keeps that clock under its limit, and — above a 45 MHz PFD —
disabling VCO band select, which the data sheet requires.

## Hardware limits

Enforced by the library; every value is from the data sheet.

| Quantity | Range |
|---|---|
| RF output | 34.375 – 4400 MHz |
| VCO fundamental | 2200 – 4400 MHz |
| Output divider | 1, 2, 4, 8, 16, 32, 64 |
| PFD, fractional-N | ≤ 32 MHz |
| PFD, integer-N | ≤ 90 MHz |
| PFD > 45 MHz | requires VCO band select disabled |
| INT, 4/5 prescaler | 23 – 65535 (prescaler rated to 3.6 GHz) |
| INT, 8/9 prescaler | 75 – 65535 |
| FRAC | 0 – (MOD − 1) |
| MOD | 2 – 4095 |
| R counter | 1 – 1023 |
| Phase word | < MOD |
| Band select clock | ≤ 125 kHz (≤ 500 kHz in fast mode, divider ≤ 254) |

## Tests

```sh
python -m unittest discover -s tests -v
```

The suite runs on Python 2.7 and Python 3 and needs no hardware. It checks the
register encoding against the data sheet's own worked example (2112.6 MHz from a
10 MHz reference gives INT = 422, FRAC = 13, MOD = 25 with an RF divider of 2),
round-trips every field through `decode_regs()`, asserts the R5→R0 write order, and
verifies the bit-bang shift sequence and the FTDI MPSSE command stream byte for byte.
