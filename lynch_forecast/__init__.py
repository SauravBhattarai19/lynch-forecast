# -*- coding: utf-8 -*-
"""
lynch_forecast — a live flood forecast built on the MRRpy model.

Every half hour (or as set) a *cycle* reads the latest rain from the stations,
adds rain forecasts, carries the model on from the state the last cycle saved,
and writes hydrographs at chosen points, flood maps for chosen areas and a
static web page.  The hydrology and the river and flood hydraulics are MRRpy's
own: this package only feeds the model and shows what it gives.  A place needs
two files: the usual MRRpy settings file (``model.yaml``) and a short forecast
file (``forecast.yaml``) that lists the rain stations, the forecasts, the
inflow points, the hydrograph points and the flood areas.

    lynch-forecast new FOLDER       templates to start from
    lynch-forecast check  -f forecast.yaml
    lynch-forecast setup  -f forecast.yaml    (once: terrain, soil, area DEMs)
    lynch-forecast run    -f forecast.yaml    (one cycle)
    lynch-forecast serve  -f forecast.yaml    (a cycle every every_minutes)

In Python::

    from lynch_forecast import load_site, setup_site, run_cycle
    site = load_site("forecast.yaml")
    setup_site(site)
    run_cycle(site)
"""

__version__ = "0.1.0"

from .sitefile import SiteFileError, load_site, template  # noqa: E402,F401


def missing_mrrpy_features():
    """What the forecast needs from MRRpy that the installed MRRpy lacks, in words."""
    try:
        from MRRpy.config import Config
    except ImportError:
        return ["MRRpy itself"]
    missing = []
    if not hasattr(Config, "RESTART_FROM"):
        missing.append("restart files (RESTART_FROM, SAVE_STATE_AT_HOURS)")
    try:
        from MRRpy.core.precip import openmeteo  # noqa: F401
    except ImportError:
        missing.append("Open-Meteo rain (MRRpy.core.precip.openmeteo)")
    try:
        import inspect
        from MRRpy.core.inundation import make_flood_maps, model2d  # noqa: F401
        if "cache_dir" not in inspect.signature(make_flood_maps).parameters:
            missing.append("flood maps for several areas (make_flood_maps(cache_dir=…))")
    except ImportError:
        missing.append("2-D flood maps (MRRpy.core.inundation)")
    return missing


def setup_site(site, **kw):
    """Prepare a site once (see prepare.py)."""
    from .prepare import setup_site as _setup
    return _setup(site, **kw)


def run_cycle(site, **kw):
    """Run one forecast cycle (see cycle.py)."""
    from .cycle import run_cycle as _run
    return _run(site, **kw)
