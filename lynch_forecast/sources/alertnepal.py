# -*- coding: utf-8 -*-
"""
alertnepal.online: live weather stations and an hourly rain forecast (Nepal).

* ``alertnepal`` (station) — an automatic weather station of the alertnepal AWS
  dashboard, e.g. 496 "AWS at Roshi".  The dashboard sends its data over
  socket.io (protocol 3, long-polling); the server keeps each session on one
  machine by cookie, so every request goes through one cookie jar.  Rain is
  ``PCPN_10M`` (mm in the 10 minutes ending at the time, UTC), else
  ``PCPN_1H``.  Only about the last 25 hours are served, which is why the
  forecast keeps its own archive.  Soil moisture and the other series are
  archived too.
* ``alertnepal_forecast`` — the hourly rain forecast (``precipitation_qpf``,
  mm in the hour that starts at the time, 240 hours) at one or more points.
  Its times are Nepal time but carry a "Z" (UTC) mark; the reader tells the two
  apart by where the forecast starts (the current hour) and converts to UTC.

These are the dashboard's own interfaces, not a published API: ask the
alertnepal team before running a forecast on them.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from . import ForecastRain, StationRain, forecast_source, station_source
from ._http import FeedError, Session

SOCKET_SERVER = "https://gss.alertnepal.online"
FORECAST_URL = "https://alertnepal.online/mero-alert/api/v2/weather/forecast/hourly"
NEPAL_OFFSET = timedelta(hours=5, minutes=45)
ATTRIBUTION = "Station data and forecast: alertnepal.online"


# ── socket.io (protocol 3, long-polling) ─────────────────────────────────────

def _packets(text):
    """Split a polling payload ``<len>:<packet><len>:<packet>…`` into packets."""
    out, i = [], 0
    while i < len(text):
        j = text.find(":", i)
        if j <= i or not text[i:j].isdigit():
            break
        n = int(text[i:j])
        out.append(text[j + 1:j + 1 + n])
        i = j + 1 + n
    return out


def _event(text, name):
    """The data of socket.io event *name* in a polling payload, or None."""
    for p in _packets(text):
        if p.startswith("42"):
            try:
                ev = json.loads(p[2:])
            except ValueError:
                continue
            if isinstance(ev, list) and ev and ev[0] == name:
                return ev[1] if len(ev) > 1 else None
    i = text.find(f'42["{name}"')            # a frame whose length was counted differently
    if i >= 0:
        try:
            ev, _ = json.JSONDecoder().raw_decode(text[i + 2:])
            return ev[1] if len(ev) > 1 else None
        except ValueError:
            pass
    return None


def fetch_dashboard(station_id, server=SOCKET_SERVER, session=None, polls=4):
    """The dashboard's record of one station (a dict with ``observations``)."""
    s = session or Session()
    base = f"{server.rstrip('/')}/socket.io/?EIO=3&transport=polling"
    hello = s.get(base)
    sid = None
    for p in _packets(hello) or [hello]:
        if p.startswith("0"):
            try:
                sid = json.loads(p[1:]).get("sid")
            except ValueError:
                pass
    if not sid:                                      # a frame whose length was off
        m = re.search(r'"sid"\s*:\s*"([^"]+)"', hello)
        sid = m.group(1) if m else None
    if not sid:
        raise FeedError(f"the alertnepal station server did not open a session: {hello[:120]!r}")
    url = f"{base}&sid={sid}"
    msg = "42" + json.dumps(["client_request", {"name_space": "aws_dashboard",
                                                "location": {"id": int(station_id)}}],
                            separators=(",", ":"))
    s.post(url, f"{len(msg)}:{msg}", headers={"Content-Type": "text/plain;charset=UTF-8"})
    for _ in range(polls):
        data = _event(s.get(url), "aws_dashboard")
        if data:
            for st in data:
                if int(st.get("id", -1)) == int(station_id):
                    return st
            raise FeedError(f"alertnepal sent no station {station_id} "
                            f"(it sent {[st.get('id') for st in data]})")
    raise FeedError(f"alertnepal sent no data for station {station_id} after {polls} polls")


def _utc(text):
    t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t.replace(microsecond=0)


def _series(obs):
    rows = [(_utc(d["datetime"]), d.get("value")) for d in (obs.get("data") or [])
            if d.get("datetime") is not None]
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series([np.nan if v is None else float(v) for _, v in rows],
                  index=pd.DatetimeIndex([t for t, _ in rows]))
    return s[~s.index.duplicated(keep="last")].sort_index().dropna()


def station_from_dashboard(st, parameter="PCPN_10M"):
    """A :class:`StationRain` from a dashboard record (see fetch_dashboard)."""
    obs = {o.get("parameter_code"): o for o in st.get("observations") or []}
    rain, step = None, None
    for code, step_s in ((parameter, 600 if parameter.endswith("10M") else 3600),
                         ("PCPN_10M", 600), ("PCPN_1H", 3600)):
        if code in obs:
            s = _series(obs[code])
            if len(s):
                rain, step = s.clip(lower=0.0), step_s
                break
    if rain is None:
        raise FeedError(f"alertnepal station {st.get('id')} ({st.get('name')}) reported no rain")
    extra = {code: _series(o) for code, o in obs.items()
             if code not in ("PCPN_10M", "PCPN_1H")}
    return StationRain(name=str(st.get("name") or st.get("id")),
                       lat=float(st["latitude"]), lon=float(st["longitude"]),
                       rain=rain, step_s=step, extra={k: v for k, v in extra.items() if len(v)})


@station_source("alertnepal", required=("station",), optional=("parameter", "server"),
                help="an alertnepal automatic weather station (station: its number)")
def read_station(spec, ctx):
    st = fetch_dashboard(spec["station"], server=spec.get("server", SOCKET_SERVER))
    out = station_from_dashboard(st, spec.get("parameter", "PCPN_10M"))
    out.rain = out.rain[out.rain.index <= ctx.now]
    out.name = spec.get("name") or out.name
    return [out]


# ── Hourly forecast ──────────────────────────────────────────────────────────

def forecast_times(labels, now, zone_offset=NEPAL_OFFSET):
    """
    UTC start times of the forecast hours, and how the labels were read.

    The labels are Nepal time marked "Z".  Should the server one day send real
    UTC, the first hour would start within the current hour in UTC instead:
    whichever reading puts the first hour nearest *now* is taken.
    """
    t = pd.DatetimeIndex([_utc(x) for x in labels]).floor("min")
    as_utc, as_local = t, t - zone_offset
    def miss(ts):
        return abs((ts[0] + timedelta(minutes=30) - now).total_seconds())
    if miss(as_local) <= miss(as_utc):
        return as_local, "Nepal time marked Z (converted to UTC)"
    return as_utc, "UTC"


def forecast_from_response(js, now, point_id="AN1"):
    rows = js.get("forecast") or []
    if not rows:
        raise FeedError("the alertnepal forecast was empty")
    starts, how = forecast_times([r["datetime"] for r in rows], now)
    vals = [float((r.get("parameters") or {}).get("precipitation_qpf") or 0.0) for r in rows]
    s = pd.Series(np.clip(vals, 0.0, None), index=starts + timedelta(hours=1), name=point_id)
    pos = js.get("position") or {}
    return s[~s.index.duplicated()], float(pos.get("latitude", np.nan)), \
        float(pos.get("longitude", np.nan)), how


@forecast_source("alertnepal_forecast", optional=("points",),
                 help="the alertnepal hourly rain forecast (points: [[lat, lon], …]; "
                      "default the middle of the basin)")
def read_forecast(spec, ctx):
    from . import basin_centre
    pts = spec.get("points") or [basin_centre(ctx.cfg)]
    s = Session()
    cols, lat, lon, how = {}, [], [], None
    for i, (la, lo) in enumerate(pts):
        q = urlencode({"latitude": f"{float(la):.4f}", "longitude": f"{float(lo):.4f}",
                       "parameters": "precipitation_qpf"})
        series, la2, lo2, how = forecast_from_response(s.get_json(f"{FORECAST_URL}?{q}"),
                                                       ctx.now, f"AN{i + 1}")
        cols[series.name] = series
        lat.append(la2 if np.isfinite(la2) else float(la))
        lon.append(lo2 if np.isfinite(lo2) else float(lo))
    table = pd.DataFrame(cols).fillna(0.0)
    points = pd.DataFrame({"point_id": list(cols), "lat": lat, "lon": lon})
    return ForecastRain(points=points, rain=table, step_s=3600, issued=ctx.now,
                        info={"times": how, "attribution": ATTRIBUTION})
