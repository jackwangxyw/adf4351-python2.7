#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Packaging for the adf4351 library.

All metadata lives here rather than in a setup.cfg.  Python 2's distutils
reads setup.cfg from the *current* directory during any build, and 2.7's
ConfigParser rejects indented keys, so a stray setup.cfg is an easy way to
make `pip install` fail from inside the source tree.  Keeping the metadata
in setup.py sidesteps that entirely.
"""

from setuptools import setup, find_packages


def get_version():
    """Read VERSION out of adf4351/__init__.py without importing it."""
    import re
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, 'adf4351', '__init__.py')) as handle:
        match = re.search(r"^VERSION = ['\"]([^'\"]+)['\"]",
                          handle.read(), re.M)
    if not match:
        raise RuntimeError('cannot find VERSION in adf4351/__init__.py')
    return match.group(1)


setup(
    name='adf4351',
    version=get_version(),
    description='Control the Analog Devices ADF4351 wideband synthesizer',
    long_description=(
        'A transport-independent Python library for the ADF4351 PLL '
        'synthesizer with integrated VCO. Register encoding is derived from '
        'the ADF4351 data sheet. Backends are provided for Cypress FX2 USB '
        'boards, Linux spidev, bit-banged GPIO, and FTDI MPSSE.'),
    packages=find_packages(exclude=['tests']),
    python_requires='>=2.7',
    extras_require={
        'fx2': ['pyusb'],
        'ftdi': ['pyusb'],
        'spidev': ['spidev'],
    },
    test_suite='tests',
    zip_safe=False,
    classifiers=[
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Topic :: System :: Hardware :: Hardware Drivers',
    ],
)
