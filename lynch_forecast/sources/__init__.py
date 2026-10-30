# -*- coding: utf-8 -*-
"""
Where live data comes from: rain stations, rain forecasts, river inflows and
river gauges (measured water level, shown next to the forecast).

Each kind has a registry, so a new feed is one small module::

    from lynch_forecast.sources import station_source

    @station_source("my_network", required=("station",), optional=("url",))
    def read_my_network(spec, ctx):
        ...
        return [StationRain(name=..., lat=..., lon=..., rain=series)]

* A **station source** returns a list of :class:`StationRain` — one per station
  (a gridded product returns many).  ``rain`` is a pandas Series of mm per
  interval, indexed by the UTC time at the *end* of each interval (naive
  datetimes), up to ``ctx.now``.
* A **forecast source** returns a :class:`ForecastRain`: rain points
  (lat/lon) and a table of mm per interval at each point, indexed the same way.
* An **inflow source** returns a :class:`FlowSeries`: discharge [m³/s] at times
  (UTC, naive) up to ``ctx.now``.
* A **level source** returns a :class:`LevelSeries`: a river gauge's water level
  [m] at times (UTC, naive) up to ``ctx.now``.  It is only shown, never run.

``spec`` is the entry from the forecast file (a dict with ``name``, ``source``
and the source's own keys); ``ctx`` is a :class:`Context` with the clock, the
model settings and a cache folder.
"""

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

STATION_SOURCES = {}
FORECAST_SOURCES = {}
INFLOW_SOURCES = {}
LEVEL_SOURCES = {}


@dataclass
class Source:
    """A registered reader and the keys it takes in the forecast file."""
    name: str
    func: object
    required: tuple = ()
    optional: tuple = ()
    help: str = ""


def _register(registry, name, required, optional, help):
    def deco(func):
        registry[name] = Source(name, func, tuple(required), tuple(optional),
                                help or (func.__doc__ or "").strip().split("\n")[0])
        return func
    return deco


def station_source(name, required=(), optional=(), help=""):
    """Register a rain-station reader (see the module docstring)."""
    return _register(STATION_SOURCES, name, required, optional, help)


def forecast_source(name, required=(), optional=(), help=""):
    """Register a rain-forecast reader (see the module docstring)."""
    return _register(FORECAST_SOURCES, name, required, optional, help)


def inflow_source(name, required=(), optional=(), help=""):
    """Register a river-inflow reader (see the module docstring)."""
    return _register(INFLOW_SOURCES, name, required, optional, help)


def level_source(name, required=(), optional=(), help=""):
    """Register a river-gauge (water level) reader (see the module docstring)."""
    return _register(LEVEL_SOURCES, name, required, optional, help)


@dataclass
class Context:
    """What a reader may need: the clock, the model settings and a cache folder."""
    now: datetime                       # naive UTC
    cfg: object = None                  # the model Config (watershed, CRS, …)
    cache_dir: str = "."
    start: Optional[datetime] = None    # forecast window (naive UTC)
    end: Optional[datetime] = None
    log: object = print
    replay: bool = False                # a rehearsal on a fake clock
    archived: dict = field(default_factory=dict)   # station entry → newest archived reading


@dataclass
class StationRain:
    name: str
    lat: float
    lon: float
    rain: pd.Series                     # mm per interval, index = interval end (UTC)
    step_s: int = 600
    extra: dict = field(default_factory=dict)   # other measured series, archived only


@dataclass
class ForecastRain:
    points: pd.DataFrame                # columns: point_id, lat, lon
    rain: pd.DataFrame                  # mm per interval; index = interval end (UTC)
    step_s: int = 3600
    issued: Optional[datetime] = None
    info: dict = field(default_factory=dict)


@dataclass
class FlowSeries:
    flow: pd.Series                     # m³/s, index = time (UTC)
    info: dict = field(default_factory=dict)


@dataclass
class LevelSeries:
    level: pd.Series                    # m, index = time (UTC)
    lat: Optional[float] = None
    lon: Optional[float] = None
    info: dict = field(default_factory=dict)   # e.g. the gauge's own name


def basin_centre(cfg):
    """(lat, lon) of the middle of the modelled basin (watershed.geojson)."""
    import geopandas as gpd
    gdf = gpd.read_file(cfg.WATERSHED_GEOJSON)
    shape = gdf.to_crs(gdf.estimate_utm_crs()).union_all()
    c = gpd.GeoSeries([shape.representative_point()], crs=gdf.estimate_utm_crs()).to_crs(4326)
    return float(c.y.iloc[0]), float(c.x.iloc[0])


def all_sources():
    """Load the built-in readers (importing registers them)."""
    from . import alertnepal, files, open_meteo, rts, usgs  # noqa: F401
    return {"station": STATION_SOURCES, "forecast": FORECAST_SOURCES,
            "inflow": INFLOW_SOURCES, "level": LEVEL_SOURCES}


def replace(obj, **kw):
    return dataclasses.replace(obj, **kw)
