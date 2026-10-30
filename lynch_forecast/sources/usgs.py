# -*- coding: utf-8 -*-
"""
``usgs`` river gauge — a USGS stream gauge's water level (gage height), from the
USGS instantaneous-values service (no sign-in).

* ``site``: the USGS site number, e.g. ``"02486100"`` (Lynch Creek at Jackson, MS).
  Write it in quotes: it starts with a 0.
* ``parameter`` (``00065``, gage height in feet): any IV parameter in feet.
* ``backfill_days`` (35): how far back the first reading goes; later ones ask
  only for what came after the archive's newest reading, plus 3 hours.

The page shows metres, so feet are converted (1 ft = 0.3048 m).  The level is
the gauge's own reading above its datum, not the depth.  It is read from
waterservices.usgs.gov, and from the newer api.waterdata.usgs.gov when that
does not answer (USGS is moving its services there).
"""

from datetime import timedelta

import pandas as pd

from . import LevelSeries, level_source
from ._http import FeedError, Session
from .rts import read_from

IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
OGC_URL = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/continuous/items"
FT = 0.3048
NO_DATA = -999.0                    # USGS writes -999999 where a sensor gave nothing


def _utc(text):
    t = pd.to_datetime(pd.Series(text), utc=True)
    return pd.DatetimeIndex(t.dt.tz_localize(None))


def _from_iv(session, site, parameter, start, end):
    url = (f"{IV_URL}?format=json&sites={site}&parameterCd={parameter}&siteStatus=all"
           f"&startDT={start:%Y-%m-%dT%H:%MZ}&endDT={end:%Y-%m-%dT%H:%MZ}")
    d = session.get_json(url)
    series = d.get("value", {}).get("timeSeries", [])
    if not series:
        raise FeedError(f"USGS {site} has no parameter {parameter} in that window")
    ts = series[0]
    info = ts.get("sourceInfo", {})
    geo = info.get("geoLocation", {}).get("geogLocation", {})
    rows = [v for block in ts.get("values", []) for v in block.get("value", [])]
    values = pd.to_numeric(pd.Series([r["value"] for r in rows], dtype=object), errors="coerce")
    s = pd.Series(values.to_numpy(float), index=_utc([r["dateTime"] for r in rows]))
    return s, info.get("siteName"), geo.get("latitude"), geo.get("longitude")


def _from_ogc(session, site, parameter, start, end):
    url = (f"{OGC_URL}?monitoring_location_id=USGS-{site}&parameter_code={parameter}"
           f"&time={start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}"
           "&f=json&skipGeometry=true&limit=50000")
    feats = session.get_json(url).get("features", [])
    if not feats:
        raise FeedError(f"USGS {site} has no parameter {parameter} in that window")
    p = [f["properties"] for f in feats]
    values = pd.to_numeric(pd.Series([x["value"] for x in p], dtype=object), errors="coerce")
    return pd.Series(values.to_numpy(float), index=_utc([x["time"] for x in p])), None, None, None


@level_source("usgs", required=("site",), optional=("parameter", "backfill_days"),
              help="a USGS stream gauge's gage height (waterservices.usgs.gov), in metres")
def read_usgs_level(spec, ctx, session=None):
    site = str(spec["site"]).strip()
    if not site.isdigit():
        raise FeedError(f"USGS site must be its number, e.g. '02486100' (not {site!r})")
    parameter = str(spec.get("parameter") or "00065")
    now = ctx.now.replace(second=0, microsecond=0)
    want_from = now - timedelta(days=float(spec.get("backfill_days", 35)))
    start = read_from(ctx.archived.get(spec.get("name")), want_from, now)
    session = session or Session()
    try:
        s, name, lat, lon = _from_iv(session, site, parameter, start, now)
    except FeedError as first:
        try:
            s, name, lat, lon = _from_ogc(session, site, parameter, start, now)
        except FeedError as second:
            raise FeedError(f"{first}; {second}") from None
    s = s[(s > NO_DATA) & (s.index <= now)].dropna() * FT
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return LevelSeries(level=s, lat=lat, lon=lon,
                       info={"gauge": name or f"USGS {site}", "site": site,
                             "parameter": parameter})
