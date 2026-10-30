# -*- coding: utf-8 -*-
"""
products.py — what the web page shows, as plain files under ``site/data`` and
``site/maps``.

    data/latest.json            the cycle: times, versions, stations, points with
                                their highest expected flow and level, areas
    data/points/<point>.json    flow at the point for every version (UTC times),
                                and the measured water level if a river gauge is there
    data/points/<point>.csv     the same as a table, for download
    data/rain.json, rain.csv    basin-average rain per hour for every version
    data/basin.geojson          the modelled basin outline
    maps/<area>/depth.png       deepest water, on the web map's projection
    maps/<area>/depth.json      its corners and the depth classes
    maps/<area>/extent.geojson  the flooded outline
    maps/<area>/flood_depth_max.tif   the depth map itself, for GIS

Levels are given in words and in the tables, never by colour alone.
"""

import json
import os
import shutil
from datetime import timedelta

import numpy as np
import pandas as pd

from .sitefile import NO_RAIN, slug

DEPTH_CLASSES = [(0.1, 0.5, "#c6dbef"), (0.5, 1.0, "#6baed6"), (1.0, 2.0, "#2171b5"),
                 (2.0, 3.0, "#08519c"), (3.0, np.inf, "#08306b")]


def _iso(t):
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%MZ")


def _write_json(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=str)
    os.replace(tmp, path)


def level(q, point):
    """'danger' | 'warning' | 'normal' | None (no levels set) for flow *q*."""
    if point.warning_m3s is None and point.danger_m3s is None:
        return None
    if point.danger_m3s is not None and q >= point.danger_m3s:
        return "danger"
    if point.warning_m3s is not None and q >= point.warning_m3s:
        return "warning"
    return "normal"


LEVEL_WORDS = {"danger": "above danger level", "warning": "above warning level",
               "normal": "below warning level", None: ""}


def _flows(vdir, run_start, gauge):
    """Flow series [m³/s] at a gauge column (or the outlet) of one version run."""
    if gauge is None:
        df = pd.read_csv(os.path.join(vdir, "hydrograph.csv"))
        q = df["Q_m3s"]
    else:
        df = pd.read_csv(os.path.join(vdir, "gauges.csv"))
        q = df[f"{gauge}_Q_m3s"]
    t = pd.DatetimeIndex([run_start + timedelta(seconds=float(s)) for s in df["time_s"]])
    return pd.Series(q.to_numpy(float), index=t)


def _km(lat1, lon1, lat2, lon2):
    """Distance on the ground, km (short distances)."""
    dy = (lat2 - lat1) * 111.2
    dx = (lon2 - lon1) * 111.2 * np.cos(np.radians((lat1 + lat2) / 2))
    return float(np.hypot(dx, dy))


def _point_list(site, outlet_km=0.5):
    """The hydrograph points, plus the model's outlet unless a point is already
    within *outlet_km* of it (e.g. a gauge at the outlet)."""
    from .sitefile import Point
    pts = [(slug(p.name), p, slug(p.name)) for p in site.hydrograph_points]
    if "outlet" in [k for k, _, _ in pts]:
        return pts
    try:
        from MRRpy.config import Config
        lat, lon = Config.from_file(site.model).OUTPUT_POINT[:2]
        if any(_km(p.lat, p.lon, float(lat), float(lon)) < outlet_km
               for p in site.hydrograph_points):
            return pts
    except Exception:                                  # noqa: BLE001 — then show it
        pass
    pts.append(("outlet", Point("Outlet (end of the model)", 0.0, 0.0), None))
    return pts


def gauge_summary(level, now):
    """The newest reading of a water level, its change over the last hour and its
    height above the lowest reading of the 30 days before → dict (m)."""
    level = level[level.index <= now].dropna()
    if not len(level):
        return {"now_m": None}
    t, v = level.index[-1], float(level.iloc[-1])
    before = level[level.index <= t - timedelta(minutes=55)]
    month = level[level.index > t - timedelta(days=30)]
    return {"now_m": round(v, 3), "utc": _iso(t),
            "change_1h_m": (round(v - float(before.iloc[-1]), 3)
                            if len(before) and t - before.index[-1] <= timedelta(hours=2)
                            else None),
            "above_low_m": round(v - float(month.min()), 3),
            "low_since_utc": _iso(month.index[0])}


def past_rain(site):
    """Where the rain before now comes from: 'stations', or (no stations listed)
    the site's missing_rain: 'forecast_model' or 'zero'."""
    return "stations" if site.rain_stations else site.missing_rain


def build(site, status, versions, main, run_start, t_obs, now, levels=None):
    """Write the data files of a finished cycle (see the module docstring).
    *levels*: {river gauge name: Series of m}, shown under their points."""
    data_dir = site.dir("site", "data")
    os.makedirs(os.path.join(data_dir, "points"), exist_ok=True)
    tz = site.display_timezone
    levels = levels or {}
    by_stations = past_rain(site) == "stations"
    points_out = []
    for pid, point, gauge in _point_list(site):
        series = {}
        for v, info in versions.items():
            try:
                series[v] = _flows(info["dir"], run_start, gauge)
            except (OSError, KeyError):
                continue
        if not series:
            continue
        times = series[main].index if main in series else next(iter(series.values())).index
        table = pd.DataFrame({v: s.reindex(times) for v, s in series.items()})
        ahead = table[table.index > t_obs]
        peaks = {}
        for v in table:
            col = ahead[v].dropna()
            if len(col):
                peaks[v] = {"q": round(float(col.max()), 3), "utc": _iso(col.idxmax()),
                            "level": level(float(col.max()), point)}
        now_q = table[main].loc[:t_obs].dropna() if main in table else pd.Series(dtype=float)
        info = {"id": pid, "name": point.name, "lat": point.lat if gauge else None,
                "lon": point.lon if gauge else None, "warning_m3s": point.warning_m3s,
                "danger_m3s": point.danger_m3s, "peaks": peaks,
                "now_q": round(float(now_q.iloc[-1]), 3) if len(now_q) else None,
                "level": peaks.get(main, {}).get("level")}
        g = site.gauge_of(point.name) if gauge else None
        lvl = levels.get(g.name, pd.Series(dtype=float)) if g else None
        measured = None
        if g is not None:
            gst = status.get("gauges", {}).get(g.name, {})
            info["gauge"] = dict(gauge_summary(lvl, now), name=g.name, ok=bool(gst.get("ok")))
            shown = lvl[(lvl.index >= table.index[0] - timedelta(minutes=10))
                        & (lvl.index <= now)].dropna()
            measured = {"name": g.name, "unit": "m", "times": [_iso(t) for t in shown.index],
                        "values": [round(float(x), 3) for x in shown]}
        points_out.append(info)
        _write_json(dict(info, times=[_iso(t) for t in table.index], observed_until=_iso(t_obs),
                         series={v: [None if not np.isfinite(x) else round(float(x), 3)
                                     for x in table[v]] for v in table}, measured=measured),
                    os.path.join(data_dir, "points", f"{pid}.json"))
        csv = table.copy()
        csv.insert(0, "part", np.where(csv.index <= t_obs, "with station rain" if by_stations
                                       else "with past rain", "forecast"))
        if g is not None and len(lvl):
            near = lvl[~lvl.index.duplicated()].sort_index()
            csv["measured_level_m"] = near.reindex(csv.index, method="nearest",
                                                   tolerance=pd.Timedelta("5min")).to_numpy()
        csv.index.name = "time_utc"
        csv.round(3).to_csv(os.path.join(data_dir, "points", f"{pid}.csv"))

    rain = pd.DataFrame({v: info["mean_mm"] for v, info in versions.items()})
    est = versions[main]["estimated"] if main in versions else np.zeros(len(rain), bool)
    hourly = rain.resample("1h", label="right", closed="right").sum()
    est_h = pd.Series(est, index=rain.index).resample("1h", label="right",
                                                      closed="right").max().astype(bool)
    _write_json({"times": [_iso(t) for t in hourly.index], "observed_until": _iso(t_obs),
                 "estimated": [bool(x) for x in est_h.reindex(hourly.index).fillna(False)],
                 "series": {v: [round(float(x), 2) for x in hourly[v]] for v in hourly}},
                os.path.join(data_dir, "rain.json"))
    out = hourly.round(2)
    out.insert(0, "part", np.where(out.index <= t_obs, "station rain" if by_stations
                                   else "past rain (" + ((site.past_rain_forecast or main)
                                                         if past_rain(site) ==
                                                         "forecast_model" else "none") + ")",
                                   "forecast"))
    out.index.name = "hour_ending_utc"
    out.to_csv(os.path.join(data_dir, "rain.csv"))
    past = rain[(rain.index > t_obs - timedelta(hours=site.look_back_hours))
                & (rain.index <= t_obs)]
    rain_summary = {"past_hours": site.look_back_hours,
                    "past_mm": round(float(past[main].sum()), 1) if main in past else None,
                    "next_24h_mm": {v: round(float(rain[v][(rain.index > t_obs) & (
                        rain.index <= t_obs + timedelta(hours=24))].sum()), 1) for v in rain},
                    "next_mm": {v: round(float(rain[v][rain.index > t_obs].sum()), 1)
                                for v in rain}}

    areas_out = []
    maps = [(a.name, list(a.box), a.method, False) for a in site.flood_areas]
    if site.basin_map:                                    # shown first
        maps.insert(0, (site.basin_map.name, None, site.basin_map.method, True))
    for name, box, method, basin in maps:
        st = status.get("areas", {}).get(name, {})
        entry = {"id": slug(name), "name": name, "box": box, "basin": basin,
                 "method": method, "ok": bool(st.get("ok"))}
        if st.get("ok"):
            try:
                entry.update(area_map(os.path.join(st["dir"], "flood_depth_max.tif"),
                                      os.path.join(st["dir"], "flood_extent_max.geojson"),
                                      site.dir("site", "maps", slug(name))))
            except Exception as exc:                          # noqa: BLE001
                entry.update(ok=False, error=f"map image: {exc}")
        else:
            entry["error"] = st.get("error")
        areas_out.append(entry)

    basin = site.dir("static", "watershed.geojson")
    if os.path.isfile(basin):
        import geopandas as gpd
        gpd.read_file(basin).to_crs(4326).to_file(os.path.join(data_dir, "basin.geojson"),
                                                  driver="GeoJSON")
    stations = [{"name": s, **v} for s, v in status.get("stations", {}).items()]
    from .archive import stations as kept
    extra = {m["entry"]: dict(lat=m["lat"], lon=m["lon"], last_report_utc=m.get("last_report_utc"),
                              **_station_now(table))
             for m, table in kept(site).values()}
    for s in stations:
        s.update(extra.get(s["name"], {}))

    rivers = None
    if main in versions:
        net = os.path.join(versions[main]["dir"], "inundation", "network.npz")
        if os.path.isfile(net):
            try:
                rivers = river_files(net, run_start, t_obs, data_dir)
            except Exception as exc:                          # noqa: BLE001 — the page goes on
                rivers = {"ok": False, "error": str(exc)}

    versions_out = []
    for v in site.versions():
        fc = status.get("forecasts", {}).get(v, {})
        run = status.get("versions", {}).get(v, {})
        versions_out.append({"name": v, "ran": bool(run.get("ok")), "main": v == main,
                             "issued_utc": fc.get("issued_utc"),
                             "note": (("no more rain after the last station report"
                                       if by_stations else "no more rain after now")
                                      if v == NO_RAIN else
                                      ("forecast feed failed; last good forecast used"
                                       if fc.get("reused") else fc.get("error", ""))),
                             "info": fc.get("info", {})})
    latest = {"title": site.website.get("title", site.name), "name": site.name,
              "notice": site.website.get("notice"), "links": site.website.get("links") or [],
              "display_timezone": tz, "cycle_utc": _iso(now), "t_obs_utc": _iso(t_obs),
              "run_start_utc": _iso(run_start), "forecast_hours": site.forecast_hours,
              "main": main, "past_forecast": site.past_rain_forecast or main,
              "versions": versions_out, "points": points_out,
              "areas": areas_out, "stations": stations, "rain": rain_summary,
              "past_rain": past_rain(site),
              "gauges": [dict(status.get("gauges", {}).get(g.name, {}), name=g.name,
                              point=g.point) for g in site.river_gauges],
              "rivers": rivers, "every_minutes": site.every_minutes,
              "next_update_utc": _iso(now + timedelta(minutes=site.every_minutes)),
              "estimated_past_rain": bool(np.any(est[np.asarray(rain.index <= t_obs)]))
              if len(rain) else False,
              "attribution": sorted({str(f.get("info", {}).get("attribution"))
                                     for f in status.get("forecasts", {}).values()
                                     if f.get("info", {}).get("attribution")})}
    _write_json(latest, os.path.join(data_dir, "latest.json"))
    return latest


def _station_now(table):
    """Rain in the last hour and day before the station's last report, and its newest
    soil moisture (RTS stations) → dict."""
    if table is None or not len(table):
        return {}
    t = pd.to_datetime(table["time_utc"])
    last = t.max()
    rain = pd.to_numeric(table["rain_mm"], errors="coerce")
    out = {"rain_1h_mm": round(float(rain[t > last - timedelta(hours=1)].sum()), 1),
           "rain_24h_mm": round(float(rain[t > last - timedelta(hours=24)].sum()), 1)}
    for col, key in (("SM_10_10M", "soil_10cm_pct"), ("SM_30_10M", "soil_30cm_pct")):
        if col in table:
            v = pd.to_numeric(table[col], errors="coerce").dropna()
            if len(v):
                out[key] = round(float(v.iloc[-1]), 1)
    return out


PIECE_CELLS = 12                 # river layer: one line every this many cells at most


def _sig(x):
    """3 significant figures, for the river files."""
    x = float(x)
    return 0.0 if not np.isfinite(x) or x <= 0 else float(f"{x:.3g}")


def river_files(net_path, run_start, t_obs, data_dir):
    """
    The main version's flow on every river cell of the model, for the page's
    river layer: ``rivers.geojson`` (one line a reach, split at confluences; each
    vertex's column in the flow table, its drainage area and highest expected
    flow) and ``rivers_q.json`` (hourly highest flow of every river cell).
    """
    from pyproj import Transformer
    z = np.load(net_path, allow_pickle=False)
    meta = json.loads(str(z["meta_json"]))
    a, b, c, d, e, f = meta["transform"]
    rows, cols, ds = z["s_rows"], z["s_cols"], z["ds"]
    ridx = z["river_idx"]
    col_of = {int(cell): k for k, cell in enumerate(ridx)}
    x = c + a * (cols + 0.5) + b * (rows + 0.5)
    y = f + d * (cols + 0.5) + e * (rows + 0.5)
    lon, lat = Transformer.from_crs(meta["crs"], 4326, always_xy=True).transform(x, y)
    t = pd.DatetimeIndex([pd.Timestamp(run_start) + timedelta(seconds=float(s))
                          for s in z["times_s"]])
    q = pd.DataFrame(z["q_series"], index=t)
    hourly = q.resample("1h", label="right", closed="right").max()
    ahead = q[q.index > t_obs]
    peak = ahead.max().to_numpy() if len(ahead) else np.zeros(len(ridx))
    t_peak = ahead.idxmax() if len(ahead) else None
    ups = {}
    for cell in ridx:
        dn = int(ds[cell])
        if dn in col_of:
            ups[dn] = ups.get(dn, 0) + 1
    feats = []
    starts = [int(cell) for cell in ridx if ups.get(int(cell), 0) != 1]
    for start in starts:
        chain = [start]
        while True:
            dn = int(ds[chain[-1]])
            if dn not in col_of:
                break
            if ups.get(dn, 0) != 1:                       # a confluence: the next reach starts
                chain.append(dn)
                break
            chain.append(dn)
        for i in range(0, max(1, len(chain) - 1), PIECE_CELLS):   # pieces, so widths follow Q
            piece = chain[i:i + PIECE_CELLS + 1]
            if len(piece) < 2:
                continue
            ks = [col_of[cell] for cell in piece]
            top = ks[int(np.argmax(peak[ks]))]
            feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [
                [round(float(lon[cell]), 5), round(float(lat[cell]), 5)] for cell in piece]},
                "properties": {"k": ks, "a": [round(float(z["area_km2"][cell]), 1)
                                              for cell in piece],
                               "q": _sig(peak[top]),
                               "t": _iso(t_peak[top]) if t_peak is not None else None}})
    _write_json({"type": "FeatureCollection", "features": feats},
                os.path.join(data_dir, "rivers.geojson"))
    _write_json({"times": [_iso(x) for x in hourly.index], "observed_until": _iso(t_obs),
                 "q": [[_sig(v) for v in hourly[k]] for k in range(len(ridx))]},
                os.path.join(data_dir, "rivers_q.json"))
    return {"ok": True, "cells": int(len(ridx)), "reaches": len(feats),
            "max_q": _sig(peak.max()) if len(peak) else 0.0}


MAX_PNG_PX = 4096               # flood map pictures: at most this many pixels a side


def area_map(depth_tif, extent_geojson, out_dir):
    """depth.png on Web Mercator + depth.json (corners, classes) + extent → dict."""
    import rasterio
    from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
    from PIL import Image

    os.makedirs(out_dir, exist_ok=True)
    with rasterio.open(depth_tif) as src:
        dst_crs = "EPSG:3857"
        tr, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height,
                                               *src.bounds)
        shrink = max(w, h) > MAX_PNG_PX            # a 1 m map of a whole basin: the
        if shrink:                                 # picture coarser, the GeoTIFF as it is
            tr, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height,
                                                   *src.bounds,
                                                   resolution=abs(tr.a) * max(w, h) / MAX_PNG_PX)
        depth = np.full((h, w), np.nan, dtype="float32")
        reproject(rasterio.band(src, 1), depth, src_nodata=src.nodata, dst_transform=tr,
                  dst_crs=dst_crs, dst_nodata=np.nan,
                  resampling=Resampling.max if shrink else Resampling.nearest)
        bounds = transform_bounds(dst_crs, "EPSG:4326",
                                  tr.c, tr.f + tr.e * h, tr.c + tr.a * w, tr.f)
        shutil.copyfile(depth_tif, os.path.join(out_dir, "flood_depth_max.tif"))
        cell_km2 = abs(src.transform.a * src.transform.e) / 1e6
        with rasterio.open(depth_tif) as s2:
            raw = s2.read(1, masked=True).filled(np.nan)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    classes = []
    for lo, hi, colour in DEPTH_CLASSES:
        m = (depth >= lo) & (depth < hi)
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        rgba[m] = (r, g, b, 210)
        n = int(np.sum((raw >= lo) & (raw < hi)))
        classes.append({"from_m": lo, "to_m": None if np.isinf(hi) else hi, "colour": colour,
                        "area_km2": round(n * cell_km2, 3)})
    Image.fromarray(rgba, "RGBA").save(os.path.join(out_dir, "depth.png"), optimize=True)
    wet = raw[np.isfinite(raw) & (raw >= DEPTH_CLASSES[0][0])]
    info = {"bounds": [[bounds[1], bounds[0]], [bounds[3], bounds[2]]],  # [[S, W], [N, E]]
            "classes": classes, "max_depth_m": round(float(wet.max()), 2) if wet.size else 0.0,
            "flooded_km2": round(float(wet.size * cell_km2), 3)}
    _write_json(info, os.path.join(out_dir, "depth.json"))
    if os.path.isfile(extent_geojson):
        import geopandas as gpd
        g = gpd.read_file(extent_geojson)
        dst = os.path.join(out_dir, "extent.geojson")
        if len(g):
            g.to_crs(4326).to_file(dst, driver="GeoJSON")
        else:
            with open(dst, "w") as f:
                json.dump({"type": "FeatureCollection", "features": []}, f)
    rel = os.path.basename(out_dir)
    return dict(info, png=f"maps/{rel}/depth.png", extent=f"maps/{rel}/extent.geojson",
                tif=f"maps/{rel}/flood_depth_max.tif")
