# -*- coding: utf-8 -*-
"""
offline.py — run the model with Earth Engine switched off, and with a set number
of CPU threads.

Forecast cycles never use Earth Engine: setup saves every layer the model needs
into ``static/``.  While a cycle runs, ``import ee`` gives a stand-in whose every
use fails at once with a plain message, so a missing layer shows up as an error
(and the restart check catches a quiet fallback) instead of a run waiting on a
sign-in.
"""

import contextlib
import os
import sys
import types

MESSAGE = ("Earth Engine is switched off during forecast cycles, and the model asked for "
           "it — a layer is missing from the static/ folder. Run 'lynch-forecast setup' "
           "again.")


class EarthEngineBlocked(RuntimeError):
    pass


class _BlockedEE(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise EarthEngineBlocked(MESSAGE)


@contextlib.contextmanager
def earth_engine_blocked():
    """Inside the block, ``import ee`` gives the stand-in."""
    blocked = _BlockedEE("ee")
    saved = {k: v for k, v in sys.modules.items() if k == "ee" or k.startswith("ee.")}
    for k in saved:
        del sys.modules[k]
    sys.modules["ee"] = blocked
    # MRRpy's Earth Engine modules bind `ee` when first imported: swap it there too.
    pkg = __name__.rsplit(".", 2)[0] + ".gee"
    bound = {m: m.ee for k, m in list(sys.modules.items())
             if (k == pkg or k.startswith(pkg + ".")) and m is not None and hasattr(m, "ee")}
    for m in bound:
        m.ee = blocked
    try:
        yield
    finally:
        sys.modules.pop("ee", None)
        sys.modules.update(saved)
        for m, real in bound.items():
            m.ee = real


THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMBA_NUM_THREADS")


@contextlib.contextmanager
def thread_limit(n):
    """Set the thread-count variables that new processes read (numba, BLAS)."""
    saved = {k: os.environ.get(k) for k in THREAD_VARS}
    if n:
        n = max(1, min(int(n), os.cpu_count() or 1))     # numba refuses more than the cores
        for k in THREAD_VARS:
            os.environ[k] = str(n)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def threads_here(n):
    """Limit numba's threads in this process (the variables above are read only at
    start-up, and numba may be loaded already)."""
    try:
        import numba
    except ImportError:
        numba = None
    old = None
    if n and numba is not None:
        try:
            old = numba.get_num_threads()
            numba.set_num_threads(max(1, min(int(n), numba.config.NUMBA_NUM_THREADS)))
        except (ValueError, RuntimeError):
            old = None
    try:
        yield
    finally:
        if old is not None:
            numba.set_num_threads(old)
