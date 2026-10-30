# -*- coding: utf-8 -*-
"""
``rts`` station — a station of the RTS time-series system (Real Time Solutions,
https://rts.wscada.net), which needs a sign-in: ``scripts/save-credentials.sh rts``.

* ``station``: the RTS station number (``python -m lynch_forecast.sources.rts``
  lists the stations a sign-in can read; 468 is AWS at Roshi, the same sensor
  as alertnepal's 496).
* ``parameter``: the rain series, ``PCPN_10M`` (mm in the 10 minutes ending at
  the time, UTC) or ``PCPN_1H``.
* ``extra``: other series kept in the archive only (default the soil moisture
  at 10 and 30 cm).
* ``backfill_days`` (35): how far back a reading goes when the archive has
  nothing recent, so the warm-up runs on measured rain.  Otherwise it asks only
  for what came after the archive's newest reading, plus 3 hours for late
  readings.

The same station number with ``source: rts`` under ``river_gauges`` reads a
river gauge's water level instead (``parameter`` default ``WTR_LVL``; 471 is the
Roshi Khola at Panauti).

Readings flagged by RTS's own quality check (``qc_flag`` other than 0) are left
out, so the forecast treats them as missing.  The interface was read from the
RTS web application (sign in at ``auth/login`` → a token; ``api/station/<id>``,
``api/station/<id>/data-series``, ``api/observation/index``).
"""

import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..credentials import credentials
from . import LevelSeries, StationRain, level_source, station_source
from ._http import FeedError, Session

SERVER = "https://rts.wscada.net/"
OVERLAP = timedelta(hours=3)            # read again: readings can arrive late
CHUNK = timedelta(days=31)              # the longest window asked for at once
META_MAX_AGE = timedelta(days=1)        # station details are read again daily
_TOKENS = {}                            # (server, user) → token, for this process


def _utc(text):
    t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t.replace(microsecond=0)


def _stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Rts:
    """A signed-in connection to one RTS server."""

    def __init__(self, server=SERVER, sign_in="RTS", session=None):
        self.server = server.rstrip("/") + "/"
        self.sign_in = sign_in
        self.session = session or Session(timeout=90)

    def _login(self):
        user, password = credentials(self.sign_in)
        try:
            text = self.session.post(self.server + "auth/login",
                                     json.dumps({"username": user, "password": password}),
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json"})
        except FeedError as exc:
            if exc.code in (400, 401, 403):
                raise FeedError(f"RTS refused the sign-in (HTTP {exc.code}): check it with "
                                f"scripts/save-credentials.sh {self.sign_in.lower()}",
                                code=exc.code) from None
            raise
        try:
            token = json.loads(text)["token"]
        except (ValueError, KeyError, TypeError):
            raise FeedError("RTS answered the sign-in without a token") from None
        _TOKENS[(self.server, user)] = token
        return token

    def _token(self):
        user, _ = credentials(self.sign_in)
        return _TOKENS.get((self.server, user)) or self._login()

    def get(self, path):
        """GET *path* (JSON) with the token, signing in again once if it expired."""
        for attempt in (0, 1):
            headers = {"Accept": "application/json",
                       "Authorization": "Bearer " + (self._token() if attempt == 0
                                                     else self._login())}
            try:
                return self.session.get_json(self.server + path, headers=headers)
            except FeedError as exc:
                if exc.code != 401 or attempt:
                    raise
        raise AssertionError("unreachable")

    def stations(self):
        return self.get("api/station/")

    def station(self, station_id):
        """{name, lat, lon, series: {parameter code: series id}} of one station."""
        st = self.get(f"api/station/{int(station_id)}")
        if not isinstance(st, dict) or "id" not in st:
            raise FeedError(f"RTS has no station {station_id} for this sign-in")
        code_of = {p["parameter_id"]: p["parameter_code"]
                   for src in st.get("data_source") or [] for p in src.get("parameters") or []}
        series = {}
        for d in self.get(f"api/station/{int(station_id)}/data-series") or []:
            code = code_of.get(d.get("parameter_id"))
            if code and code not in series:
                series[code] = int(d["id"])
        return {"name": st.get("name") or str(station_id), "lat": float(st["latitude"]),
                "lon": float(st["longitude"]), "series": series}

    def observations(self, series_id, start, end):
        """Readings of one series from *start* to *end* (naive UTC) → Series."""
        rows = []
        a = start
        while a < end:
            b = min(a + CHUNK, end)
            got = self.get(f"api/observation/index?series_id={int(series_id)}"
                           f"&date_from={_stamp(a)}&date_to={_stamp(b)}")
            if not isinstance(got, list):
                raise FeedError(f"RTS sent no list of readings for series {series_id}")
            rows += [(_utc(r["time"]), r.get("value")) for r in got
                     if r.get("time") and r.get("qc_flag") in (0, None)]
            a = b
        if not rows:
            return pd.Series(dtype=float)
        s = pd.Series(pd.to_numeric([v for _, v in rows], errors="coerce"),
                      index=pd.DatetimeIndex([t for t, _ in rows]), dtype=float)
        return s[~s.index.duplicated(keep="last")].sort_index().dropna()


# ── Station details, kept a day (per forecast folder) ───────────────────────

def _state_path(ctx, server, station_id):
    host = server.split("//", 1)[-1].strip("/").replace("/", "_")
    return os.path.join(ctx.cache_dir, "rts", f"{host}_{int(station_id)}.json")


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)


def read_from(archived_until, want_from, now):
    """Where to start reading: after the archive's newest reading, if it is recent."""
    if archived_until is not None and want_from <= archived_until <= now:
        return max(want_from, archived_until - OVERLAP)
    return want_from


def _meta(spec, ctx, rts, server, now):
    """The station's details, read again once a day."""
    path = _state_path(ctx, server, spec["station"])
    state = _load(path)
    meta = state.get("station")
    read = datetime.fromisoformat(state.get("read_utc", "1970-01-01T00:00:00"))
    if not meta or not read <= now <= read + META_MAX_AGE:
        meta = rts.station(spec["station"])
        state.update(station=meta, read_utc=now.isoformat())
        _save(path, state)
    return meta


@station_source("rts", required=("station",),
                optional=("parameter", "extra", "backfill_days", "server", "sign_in"),
                help="a station of the RTS system (rts.wscada.net; needs a sign-in, "
                     "scripts/save-credentials.sh rts)")
def read_station(spec, ctx, client=None):
    server = str(spec.get("server") or SERVER)
    rts = client or Rts(server, sign_in=str(spec.get("sign_in") or "RTS"))
    now = ctx.now.replace(second=0, microsecond=0)
    meta = _meta(spec, ctx, rts, server, now)

    parameter = str(spec.get("parameter") or "PCPN_10M")
    codes = [parameter] + [c for c in (spec.get("extra") or ("SM_10_10M", "SM_30_10M"))
                           if c != parameter]
    if parameter not in meta["series"]:
        raise FeedError(f"RTS station {spec['station']} ({meta['name']}) has no {parameter} "
                        f"series (it has {sorted(meta['series'])})")
    want_from = now - timedelta(days=float(spec.get("backfill_days", 35)))
    start = read_from(ctx.archived.get(spec.get("name")), want_from, now)
    got = {code: rts.observations(meta["series"][code], start, now)
           for code in codes if code in meta["series"]}

    rain = got[parameter].clip(lower=0.0)
    return [StationRain(name=spec.get("name") or meta["name"], lat=meta["lat"], lon=meta["lon"],
                        rain=rain[rain.index <= now],
                        step_s=3600 if parameter.endswith("1H") else 600,
                        extra={c: s for c, s in got.items() if c != parameter and len(s)})]


@level_source("rts", required=("station",),
              optional=("parameter", "backfill_days", "server", "sign_in"),
              help="a river gauge of the RTS system: its water level (WTR_LVL)")
def read_level(spec, ctx, client=None):
    server = str(spec.get("server") or SERVER)
    rts = client or Rts(server, sign_in=str(spec.get("sign_in") or "RTS"))
    now = ctx.now.replace(second=0, microsecond=0)
    meta = _meta(spec, ctx, rts, server, now)
    parameter = str(spec.get("parameter") or "WTR_LVL")
    if parameter not in meta["series"]:
        raise FeedError(f"RTS station {spec['station']} ({meta['name']}) has no {parameter} "
                        f"series (it has {sorted(meta['series'])})")
    want_from = now - timedelta(days=float(spec.get("backfill_days", 35)))
    start = read_from(ctx.archived.get(spec.get("name")), want_from, now)
    level = rts.observations(meta["series"][parameter], start, now)
    return LevelSeries(level=level[level.index <= now], lat=meta["lat"], lon=meta["lon"],
                       info={"gauge": meta["name"], "station": int(spec["station"])})


def list_stations(client=None):
    """[(number, name, lat, lon)] of the stations this sign-in can read."""
    rts = client or Rts()
    return [(int(s["id"]), s.get("name"), float(s["latitude"]), float(s["longitude"]))
            for s in rts.stations()]


if __name__ == "__main__":
    for number, name, lat, lon in list_stations():
        print(f"{number:6d}  {name}  ({lat:.5f}, {lon:.5f})")
