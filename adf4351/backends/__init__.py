"""ADF4351 transports.

Each backend lives in its own submodule and imports its third-party
dependency at construction time.  Nothing heavyweight is imported here, so
``import adf4351`` succeeds on a machine that has none of pyusb, spidev or an
FTDI part, and you only pay for the transport you actually use.

Import the one you want directly::

    from adf4351.backends.fx2 import FX2Backend
    from adf4351.backends.spi import SpiDevBackend

or look one up by name, which defers the import until you ask::

    from adf4351.backends import get_backend
    FX2Backend = get_backend('fx2')
"""

from __future__ import division, print_function

import importlib

from .base import Backend, SerialWordBackend


__all__ = ['Backend', 'SerialWordBackend', 'get_backend', 'BACKENDS']


#: Short name -> (submodule, class name, the module you need installed).
BACKENDS = {
    'fx2': ('.fx2', 'FX2Backend', 'pyusb'),
    'spidev': ('.spi', 'SpiDevBackend', 'spidev'),
    'bitbang': ('.bitbang', 'BitBangBackend', None),
    'ftdi': ('.ftdi', 'FtdiBackend', 'pyusb'),
}


def get_backend(name):
    """Return a backend class by short name, importing it on demand.

    Raises ImportError with a useful message if the transport's dependency is
    not installed, and ValueError if the name is not a known backend.
    """
    try:
        module_name, class_name, dependency = BACKENDS[name]
    except KeyError:
        raise ValueError(
            'unknown backend %r; known backends are %s'
            % (name, ', '.join(sorted(BACKENDS))))
    try:
        module = importlib.import_module(module_name, __name__)
    except ImportError:
        raise ImportError(
            'the %r backend requires the %r module, which is not installed'
            % (name, dependency))
    return getattr(module, class_name)
