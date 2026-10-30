# -*- coding: utf-8 -*-
"""
archive.py — the forecast's own record of what the feeds sent.

Live feeds keep only a short history (alertnepal: about 25 hours), but a cycle
runs the last hours again and a rehearsal or a check of the forecast needs
more, so every reading is kept:

    archive/stations/<station>.csv.gz   time_utc, rain_mm, step_s, other series
    archive/stations/<station>.json     name, lat, lon, last report
    archive/levels/<gauge>.csv.gz       time_utc, level_m (river gauges)
    archive/forecasts/<version>/<YYYYmmddTHHMMZ>.csv.gz   rain at each point
    archive/forecasts/<version>/<…>.json                   the points, issue time

A new reading of a time already kept replaces the old one (feeds correct late
data).  Forecasts are kept ``keep_days``; the newest good one stands in when a
forecast feed fails.
"""

import glob
import json
import os
from datetime import datetime, timedelta

import pandas as pd

from .sitefile import slug

STAMP = "%Y%m%dT%H%MZ"


def _write_csv(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    df.to_csv(tmp, index=False, compression="gzip")
    os.replace(tmp, path)


def _write_json(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


# ── Stations ─────────────────────────────────────────────────────────────────

def station_paths(site, name):
    base = site.dir("archive", "stations", slug(name))
    return base + ".csv.gz", base + ".json"


def save_station(site, entry_name, st):
    """Merge a :class:`StationRain` into the archive → the whole kept rain series."""
    key = entry_name if st.name == entry_name else f"{entry_name}_{st.name}"
    csv_path, meta_path = station_paths(site, key)
    new = pd.DataFrame({"time_utc": st.rain.index, "rain_mm": st.rain.to_numpy(float),
                        "step_s": int(st.step_s)})
    for code, s in (st.extra or {}).items():
        new = new.merge(pd.DataFrame({"time_utc": s.index, code: s.to_numpy(float)}),
                        on="time_utc", how="outer")
    old = read_station_table(csv_path)
    both = pd.concat([old, new], ignore_index=True) if len(old) else new
    both["time_utc"] = pd.to_datetime(both["time_utc"])
    both = (both.drop_duplicates("time_utc", keep="last").sort_values("time_utc")
            .reset_index(drop=True))
    _write_csv(both, csv_path)
    rain = both.dropna(subset=["rain_mm"])
    meta = {"name": st.name, "entry": entry_name, "lat": st.lat, "lon": st.lon,
            "last_report_utc": (rain["time_utc"].max().strftime("%Y-%m-%d %H:%M")
                                if len(rain) else None),
            "rows": int(len(both))}
    _write_json(meta, meta_path)
    return key, rain


def read_station_table(csv_path):
    if not os.path.isfile(csv_path):
        return pd.DataFrame()
    df = pd.read_csv(csv_path, compression="gzip")
    df["time_utc"] = pd.to_datetime(df["time_utc"])
    return df


def last_reports(site):
    """{forecast-file entry name: its newest archived rain reading (naive UTC)}."""
    out = {}
    for meta_path in glob.glob(site.dir("archive", "stations", "*.json")):
        with open(meta_path) as f:
            meta = json.load(f)
        if meta.get("entry") and meta.get("last_report_utc"):
            t = datetime.strptime(meta["last_report_utc"], "%Y-%m-%d %H:%M")
            out[meta["entry"]] = max(out.get(meta["entry"], t), t)
    return out


def stations(site):
    """{key: (meta, table)} for every archived station."""
    out = {}
    for meta_path in sorted(glob.glob(site.dir("archive", "stations", "*.json"))):
        with open(meta_path) as f:
            meta = json.load(f)
        table = read_station_table(meta_path[:-5] + ".csv.gz")
        out[os.path.basename(meta_path)[:-5]] = (meta, table)
    return out


# ── River gauges ─────────────────────────────────────────────────────────────

def level_path(site, name):
    return site.dir("archive", "levels", slug(name) + ".csv.gz")


def read_level(site, name):
    """The kept water level of gauge *name* → Series (m, UTC index)."""
    path = level_path(site, name)
    if not os.path.isfile(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, compression="gzip", parse_dates=["time_utc"])
    return pd.Series(df["level_m"].to_numpy(float), index=pd.DatetimeIndex(df["time_utc"]))


def save_level(site, name, level):
    """Merge new readings into the kept level of gauge *name* → the whole Series."""
    both = pd.concat([read_level(site, name), level.astype(float)])
    both = both[~both.index.duplicated(keep="last")].sort_index().dropna()
    _write_csv(pd.DataFrame({"time_utc": both.index, "level_m": both.values}),
               level_path(site, name))
    return both


# ── Forecasts ────────────────────────────────────────────────────────────────

def save_forecast(site, version, fc, cycle_time):
    """Keep a :class:`ForecastRain` from the cycle at *cycle_time*."""
    base = site.dir("archive", "forecasts", slug(version), cycle_time.strftime(STAMP))
    rain = fc.rain.copy()
    rain.index.name = "time_utc"
    _write_csv(rain.reset_index(), base + ".csv.gz")
    _write_json({"version": version, "cycle_utc": cycle_time.strftime("%Y-%m-%d %H:%M"),
                 "issued_utc": fc.issued.strftime("%Y-%m-%d %H:%M") if fc.issued else None,
                 "step_s": fc.step_s, "info": fc.info,
                 "points": fc.points.to_dict(orient="list")}, base + ".json")


def latest_forecast(site, version, now, max_age_hours=12.0):
    """The newest kept forecast of *version* no older than *max_age_hours*, or None."""
    from .sources import ForecastRain
    files = sorted(glob.glob(site.dir("archive", "forecasts", slug(version), "*.json")))
    for meta_path in reversed(files):
        stamp = datetime.strptime(os.path.basename(meta_path)[:-5], STAMP)
        if stamp > now:
            continue
        if now - stamp > timedelta(hours=max_age_hours):
            return None
        with open(meta_path) as f:
            meta = json.load(f)
        rain = pd.read_csv(meta_path[:-5] + ".csv.gz", compression="gzip")
        rain["time_utc"] = pd.to_datetime(rain["time_utc"])
        rain = rain.set_index("time_utc")
        rain.columns = [str(c) for c in rain.columns]
        issued = (datetime.strptime(meta["issued_utc"], "%Y-%m-%d %H:%M")
                  if meta.get("issued_utc") else stamp)
        info = dict(meta.get("info") or {}, reused_from_cycle=meta["cycle_utc"])
        return ForecastRain(points=pd.DataFrame(meta["points"]), rain=rain,
                            step_s=int(meta["step_s"]), issued=issued, info=info)
    return None


def prune_forecasts(site, now, keep_days):
    cut = now - timedelta(days=float(keep_days))
    for meta_path in glob.glob(site.dir("archive", "forecasts", "*", "*.json")):
        stamp = datetime.strptime(os.path.basename(meta_path)[:-5], STAMP)
        if stamp < cut:
            for p in (meta_path, meta_path[:-5] + ".csv.gz"):
                if os.path.exists(p):
                    os.remove(p)
