# -*- coding: utf-8 -*-
"""
Readers for plain files or web addresses (CSV).

* ``csv`` station — one rain gauge: columns ``time_utc`` and ``rain_mm`` (mm in
  the interval *ending* at the time).  ``timezone:`` reads local times instead.
* ``gauge_files`` station — many gauges in MRRpy's own rain-gauge format
  (``gauges.csv`` + ``timeseries.csv`` with ``time_s`` from ``start_utc``), e.g.
  satellite rain prepared for a rehearsal of a past flood.
* ``csv`` forecast — rain forecast at one point, same columns as the station.
* ``csv`` inflow — river discharge: columns ``time_utc`` and ``Q_m3s``.
* ``csv`` river gauge — water level: columns ``time_utc`` and ``level_m``.
"""

import io
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import (FlowSeries, ForecastRain, LevelSeries, StationRain, forecast_source,
               inflow_source, level_source, station_source)
from ._http import FeedError, Session


def read_table(path):
    """A CSV from a file or a web address."""
    if str(path).startswith(("http://", "https://")):
        return pd.read_csv(io.StringIO(Session().get(str(path))))
    if not os.path.isfile(str(path)):
        raise FeedError(f"file not found: {path}")
    return pd.read_csv(path)


def _times(df, spec, default="time_utc"):
    col = spec.get("time_column", default)
    if col not in df.columns:
        raise FeedError(f"{spec.get('path')} has no '{col}' column (it has {list(df.columns)})")
    t = pd.to_datetime(df[col])
    zone = spec.get("timezone")
    if t.dt.tz is None:
        t = t.dt.tz_localize(zone or "UTC")
    return pd.DatetimeIndex(t.dt.tz_convert("UTC").dt.tz_localize(None))


def _values(df, spec, default):
    col = spec.get("value_column", default)
    if col not in df.columns:
        raise FeedError(f"{spec.get('path')} has no '{col}' column (it has {list(df.columns)})")
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)


def _step(index, spec):
    if spec.get("step_minutes"):
        return int(round(float(spec["step_minutes"]) * 60))
    if len(index) < 2:
        return 600
    return int(np.median(np.diff(index.values).astype("timedelta64[s]").astype(np.int64)))


def _rain_series(spec, ctx):
    df = read_table(spec["path"])
    t = _times(df, spec)
    s = pd.Series(np.clip(_values(df, spec, "rain_mm"), 0.0, None), index=t).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s, _step(s.index, spec)


@station_source("csv", required=("path", "lat", "lon"),
                optional=("time_column", "value_column", "timezone", "step_minutes"),
                help="a rain gauge in a CSV file or at a web address (time_utc, rain_mm)")
def read_csv_station(spec, ctx):
    s, step = _rain_series(spec, ctx)
    return [StationRain(name=spec["name"], lat=float(spec["lat"]), lon=float(spec["lon"]),
                        rain=s[s.index <= ctx.now], step_s=step)]


@station_source("gauge_files", required=("gauges", "timeseries", "start_utc"),
                optional=("crs",),
                help="many gauges in MRRpy's rain-gauge files (gauges.csv + timeseries.csv)")
def read_gauge_files(spec, ctx):
    from pyproj import Transformer
    from MRRpy.core.io_utils import routing_grid_crs

    g = read_table(spec["gauges"])
    ts = read_table(spec["timeseries"])
    start = datetime.fromisoformat(str(spec["start_utc"]))
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    crs = spec.get("crs") or routing_grid_crs(ctx.cfg)
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(
        g["easting_m"].to_numpy(float), g["northing_m"].to_numpy(float))
    t = pd.DatetimeIndex([start + timedelta(seconds=float(x)) for x in ts["time_s"]])
    step = _step(t, spec)
    # MRRpy's gauge files give the rain from each row's time to the next row's;
    # a station's readings are labelled with the end of their interval.
    t = t + timedelta(seconds=step)
    out = []
    for i, gid in enumerate(g["gauge_id"]):
        s = pd.Series(np.clip(ts[str(gid)].to_numpy(float), 0.0, None), index=t).dropna()
        name = str(g["name"].iloc[i]) if "name" in g.columns else str(gid)
        out.append(StationRain(name=f"{spec['name']}: {name}", lat=float(lat[i]),
                               lon=float(lon[i]), rain=s[s.index <= ctx.now], step_s=step))
    return out


@forecast_source("csv", required=("path", "lat", "lon"),
                 optional=("time_column", "value_column", "timezone", "step_minutes"),
                 help="a rain forecast at one point in a CSV file (time_utc, rain_mm)")
def read_csv_forecast(spec, ctx):
    s, step = _rain_series(spec, ctx)
    points = pd.DataFrame({"point_id": ["F1"], "lat": [float(spec["lat"])],
                           "lon": [float(spec["lon"])]})
    return ForecastRain(points=points, rain=pd.DataFrame({"F1": s}), step_s=step,
                        issued=ctx.now)


@inflow_source("csv", required=("path",),
               optional=("time_column", "value_column", "timezone"),
               help="river discharge in a CSV file or at a web address (time_utc, Q_m3s)")
def read_csv_inflow(spec, ctx):
    df = read_table(spec["path"])
    s = pd.Series(_values(df, spec, "Q_m3s"), index=_times(df, spec)).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return FlowSeries(flow=s[(s.index <= ctx.now) & (s >= 0)])


@level_source("csv", required=("path",),
              optional=("time_column", "value_column", "timezone"),
              help="a river gauge's water level in a CSV file or at a web address "
                   "(time_utc, level_m)")
def read_csv_level(spec, ctx):
    df = read_table(spec["path"])
    s = pd.Series(_values(df, spec, "level_m"), index=_times(df, spec)).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return LevelSeries(level=s[s.index <= ctx.now])
