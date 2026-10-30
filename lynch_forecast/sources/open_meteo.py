# -*- coding: utf-8 -*-
"""
``open_meteo`` forecast — weather-model rain from Open-Meteo, through MRRpy's
own Open-Meteo precipitation source (``core/precip/openmeteo.py``).

Each model cell over the basin becomes a rain point.  The download is cached
in the forecast's folder and fetched again when its window moves, which with
whole-hour windows is at most once an hour — at 10 km spacing about 25 points
per model, well inside Open-Meteo's free 10,000 calls a day.

``api: forecast`` (default) is the live forecast, with the model's own recent
past.  ``api: single_run`` takes one model run as it was issued — the latest
run at ``run_hours`` that was out ``delay_hours`` before the clock — for a
rehearsal of a past flood with no look-ahead (ECMWF IFS from March 2024).
"""

import contextlib
import copy
import io
import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import ForecastRain, forecast_source


def issued_run(now, run_hours=(0, 6, 12, 18), delay_hours=7.0):
    """The newest model run at one of *run_hours* (UTC) out by *now*."""
    t = (now - timedelta(hours=float(delay_hours))).replace(minute=0, second=0, microsecond=0)
    for _ in range(48):
        if t.hour in [int(h) for h in run_hours]:
            return t
        t -= timedelta(hours=1)
    raise ValueError(f"no model run at hours {list(run_hours)}")


@forecast_source("open_meteo", required=("model",),
                 optional=("api", "spacing_m", "buffer_m", "run_hours", "delay_hours"),
                 help="a weather model through Open-Meteo, e.g. model: ecmwf_ifs025, "
                      "gfs_seamless or icon_seamless")
def read_open_meteo(spec, ctx):
    from pyproj import Transformer
    from MRRpy.core.io_utils import routing_grid_crs
    from MRRpy.core.precip import openmeteo

    api = str(spec.get("api", "forecast"))
    start, end = ctx.start, ctx.end
    run = None
    if api == "single_run":
        run = issued_run(ctx.now, spec.get("run_hours", (0, 6, 12, 18)),
                         spec.get("delay_hours", 7.0))
        start = max(start, run)                # a run forecasts only after its issue
    cfg = copy.copy(ctx.cfg)
    slug = "".join(c if c.isalnum() else "_" for c in str(spec["name"])).lower()
    cfg.PRECIP_OPENMETEO_DIR = os.path.join(ctx.cache_dir, slug) + os.sep
    cfg.EVENT_START_UTC = start.strftime("%Y-%m-%d %H:%M")
    cfg.TOTAL_SIMULATION_TIME_HOURS = (end - start).total_seconds() / 3600.0
    cfg.OPENMETEO_MODEL = str(spec["model"])
    cfg.OPENMETEO_SOURCE = api
    cfg.OPENMETEO_RUN = run.strftime("%Y-%m-%d %H:%M") if run else None
    cfg.OPENMETEO_MINUTELY_15 = False
    cfg.OPENMETEO_POINT_SPACING_M = float(spec.get("spacing_m", 10000.0))
    if spec.get("buffer_m") is not None:
        cfg.OPENMETEO_BBOX_BUFFER_M = float(spec["buffer_m"])
    cfg.PRECIP_OPENMETEO_FORCE_DOWNLOAD = False

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            gauges_path, ts_path = openmeteo.ensure_openmeteo_data(cfg)
    finally:
        for line in buf.getvalue().splitlines():
            if line.strip() and not set(line.strip()) <= {"="}:
                ctx.log(f"    {line.strip()}")
    g = pd.read_csv(gauges_path)
    ts = pd.read_csv(ts_path)
    lon, lat = Transformer.from_crs(routing_grid_crs(cfg), "EPSG:4326", always_xy=True) \
        .transform(g["easting_m"].to_numpy(float), g["northing_m"].to_numpy(float))
    index = pd.DatetimeIndex([start + timedelta(seconds=float(s)) for s in ts["time_s"]])
    table = pd.DataFrame({str(k): ts[str(k)].to_numpy(float) for k in g["gauge_id"]},
                         index=index).clip(lower=0.0)
    meta_path = os.path.join(cfg.PRECIP_OPENMETEO_DIR, "openmeteo_meta.json")
    meta = {}
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    issued = None
    if meta.get("downloaded_at"):
        issued = datetime.fromisoformat(meta["downloaded_at"]).replace(tzinfo=None)
    points = pd.DataFrame({"point_id": [str(k) for k in g["gauge_id"]],
                           "lat": np.round(lat, 5), "lon": np.round(lon, 5)})
    return ForecastRain(points=points, rain=table, step_s=3600, issued=run or issued,
                        info={"api": meta.get("api", api), "model": cfg.OPENMETEO_MODEL,
                              "run_utc": meta.get("run_utc"),
                              "attribution": openmeteo.ATTRIBUTION})
