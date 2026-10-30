# -*- coding: utf-8 -*-
"""
design.py — ``lynch-forecast design``: design storms (the 10- and 20-year
24-hour storms of NOAA Atlas 14, say) run through the forecast's own model, with
a flood map of every flood area, on a page of their own (``site/design/``).

The storms are set in ``design.yaml`` beside the forecast file::

    atlas14: design/atlas14.json   # NOAA Atlas 14 depths at the basin (fetched once)
    duration_hours: 24
    peak_at: 0.5                    # where the most intense 10 minutes fall in the storm
    lead_hours: 1                   # dry hours before the storm
    after_hours: 24                 # hours run on after the rain stops
    start_from: latest              # the newest saved state (the soil as it is), or a file
    storms:
      - {name: 10-year storm, years: 10}
      - {name: 20-year storm, years: 20}

Each storm is *nested* (the "balanced" or frequency storm of HEC-HMS): every
duration from 10 minutes to the whole storm holds the Atlas 14 depth of that
return period, the largest 10-minute block at ``peak_at`` and the next ones
alternating either side of it.  Return periods Atlas 14 does not give (20 years)
are read between its own (10 and 25) on the Gumbel scale.  The rain is the same
everywhere in the basin; no areal reduction (it would be about 3 % for 38 km²
over 24 hours, NOAA TP-40).

The runs start from a saved forecast state, so the soil is as wet as it is now,
and record the flows for the flood maps.  The maps are the forecast's own flood
areas (the same 1 m DEMs, saved at setup), drawn from each storm.  The page
compares the peak at the gauge point with the flood-frequency curve of the gauge's
yearly peaks when ``gauge_peaks`` gives them.
"""

import json
import math
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .sitefile import slug

PFDS = ("https://hdsc.nws.noaa.gov/cgi-bin/new/cgi_readH5.py?lat={lat:.4f}&lon={lon:.4f}"
        "&type=pf&data=depth&units=metric&series=pds")
# The rows of a PFDS table, in minutes.
DURATIONS_MIN = [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 4320, 5760, 10080,
                 14400, 28800, 43200, 64800, 86400]
ARI_YEARS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]
STEP_MIN = 10
WEB_FILES = ("index.html", "design.js")
WEB_DIR = os.path.join(os.path.dirname(__file__), "web", "design")


# ── NOAA Atlas 14 ────────────────────────────────────────────────────────────

def _pfds_arrays(text):
    """{'quantiles'|'upper'|'lower': depth [mm] (durations × ARIs)} and the
    metadata of a PFDS answer (``name = value;`` lines)."""
    import ast
    out, meta = {}, {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        value = value.rstrip(";").strip()
        if key in ("quantiles", "upper", "lower"):
            out[key] = np.array(ast.literal_eval(value), dtype=float)
        else:
            meta[key] = value.strip("'")
    if "quantiles" not in out:
        raise ValueError("NOAA PFDS sent no depths: " + text[:200])
    return out, meta


def fetch_atlas14(lat, lon, path):
    """Fetch the Atlas 14 depth table (partial duration series, mm) at a point → path."""
    from .sources._http import Session
    url = PFDS.format(lat=lat, lon=lon)
    arrays, meta = _pfds_arrays(Session(timeout=90).get(url))
    data = {"source": "NOAA Atlas 14 Precipitation Frequency Data Server (HDSC)",
            "url": url, "fetched_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            "lat": lat, "lon": lon, "series": "partial duration", "units": "mm",
            "volume": meta.get("volume"), "version": meta.get("version"),
            "region": meta.get("region"), "durations_min": DURATIONS_MIN,
            "ari_years": ARI_YEARS,
            **{k: v.tolist() for k, v in arrays.items()}}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=1)
    return path


def _gumbel(years):
    return -math.log(-math.log(1.0 - 1.0 / float(years)))


def depths_for(table, years, which="quantiles"):
    """Depth [mm] at every Atlas 14 duration for *years* (between its own return
    periods on the Gumbel scale) → Series indexed by minutes."""
    d = np.asarray(table[which], float)
    aris = list(table["ari_years"])
    if years in aris:
        col = d[:, aris.index(years)]
    else:
        if not aris[0] < years < aris[-1]:
            raise ValueError(f"{years}-year storm: Atlas 14 runs from {aris[0]} to {aris[-1]} years")
        j = next(i for i, a in enumerate(aris) if a > years)
        a0, a1 = aris[j - 1], aris[j]
        w = (_gumbel(years) - _gumbel(a0)) / (_gumbel(a1) - _gumbel(a0))
        col = d[:, j - 1] + w * (d[:, j] - d[:, j - 1])
    return pd.Series(col, index=table["durations_min"])


def nested_storm(depths, hours=24.0, peak_at=0.5, step_min=STEP_MIN):
    """The nested (balanced) storm: mm in each *step_min* block, *hours* long."""
    n = int(round(hours * 60 / step_min))
    t = np.arange(1, n + 1) * step_min
    dmin, dval = np.asarray(depths.index, float), np.asarray(depths.values, float)
    cum = np.exp(np.interp(np.log(t), np.log(dmin), np.log(dval)))   # log-log
    blocks = np.sort(np.diff(np.r_[0.0, cum]))[::-1]                 # largest first
    p = min(n - 1, max(0, int(math.floor(peak_at * n))))
    order, left, right = [p], p - 1, p + 1
    while len(order) < n:                         # alternate: after, before, after …
        if right < n:
            order.append(right)
            right += 1
        if left >= 0 and len(order) < n:
            order.append(left)
            left -= 1
    out = np.zeros(n)
    out[np.array(order)] = blocks
    return out


# ── Settings ─────────────────────────────────────────────────────────────────

DEFAULTS = {"duration_hours": 24.0, "peak_at": 0.5, "lead_hours": 1.0, "after_hours": 24.0,
            "start_from": "latest", "storms": [], "pad_hours": 6.0,
            "margin_km": None}


def load_design(site):
    path = os.path.join(os.path.dirname(site.path), "design.yaml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no design storms are set: write {path} "
                                "(see 'lynch-forecast design --help')")
    import yaml
    with open(path) as f:
        d = dict(DEFAULTS, **(yaml.safe_load(f) or {}))
    d["path"] = path
    here = os.path.dirname(path)
    for key in ("atlas14", "gauge_peaks"):
        if d.get(key) and not os.path.isabs(d[key]):
            d[key] = os.path.join(here, d[key])
    if not d["storms"]:
        raise ValueError(f"{path}: list at least one storm under 'storms'")
    for s in d["storms"]:
        s.setdefault("id", slug(s["name"]))
    return d


def start_state(site, choice):
    """(clock time, state path) to start the storms from."""
    from . import states
    if choice and choice != "latest":
        path = choice if os.path.isabs(choice) else os.path.join(site.folder, choice)
        stamp = os.path.basename(path).split("_", 1)[1].split(".")[0]
        return datetime.strptime(stamp, "%Y%m%dT%H%MZ"), path
    got = states.pick_start(site, datetime(2100, 1, 1))
    if got is None:
        raise RuntimeError("no saved state yet: run 'lynch-forecast setup' first")
    return got


# ── Runs, maps, page ─────────────────────────────────────────────────────────

def gauge_frequency(path):
    """Log-Pearson III of a USGS peak-flow file (``peak_va`` in cfs) → dict."""
    from scipy import stats
    df = pd.read_csv(path, sep="\t", comment="#").iloc[1:]
    cfs = pd.to_numeric(df["peak_va"], errors="coerce").dropna()
    q = cfs.to_numpy(float) * 0.0283168466
    lq = np.log10(q)
    m, s, g = lq.mean(), lq.std(ddof=1), stats.skew(lq, bias=False)
    years = [2, 5, 10, 20, 25, 50, 100]
    return {"site": str(df["site_no"].iloc[0]), "peaks": int(len(q)),
            "from": str(df["peak_dt"].iloc[0])[:4], "to": str(df["peak_dt"].iloc[-1])[:4],
            "method": "log-Pearson III, station skew", "skew": round(float(g), 3),
            "q_m3s": {str(T): round(float(10 ** (m + stats.pearson3.ppf(1 - 1 / T, g) * s)), 1)
                      for T in years}}


def design_areas(d):
    """The design's own flood areas (design.yaml 'flood_areas') → [Area]."""
    from .sitefile import Area, _resolve
    out = []
    here = os.path.dirname(d.get("path") or ".")
    for a in d.get("flood_areas") or []:
        dem = str(a.get("dem", "auto"))
        if dem.lower().endswith((".tif", ".tiff")):
            dem = _resolve(here, dem)                         # beside design.yaml
        out.append(Area(str(a["name"]), tuple(float(v) for v in a["box"]),
                        str(a.get("method", "2d")), dem,
                        float(a["dem_scale_m"]) if a.get("dem_scale_m") is not None else None))
    return out


def _area_job(cfgs, dirs, area, cache_dir, log_path, pad_hours=None, margin_km=None):
    """One flood area for every storm, in its own process (its DEM is fetched once,
    with Earth Engine, then kept in *cache_dir*) → [(storm, area name, result)].
    *pad_hours*: 2-D hours simulated before the flood's rise and after its fall
    (MRRpy's 24 h is long at 1 m, where a simulated hour takes minutes)."""
    import contextlib
    import traceback
    from MRRpy.config import Config
    from MRRpy.core.inundation import make_flood_maps, model2d
    from .basinmap import set_margin
    if pad_hours is not None:
        model2d.PAD_S = float(pad_hours) * 3600.0             # this process only
    set_margin(margin_km)
    out = []
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as logf, contextlib.redirect_stdout(logf), \
            contextlib.redirect_stderr(logf):
        for sid, cfg_dict in cfgs.items():
            t0 = time.time()
            res = {"ok": False, "method": area.method}
            try:
                cfg = Config.from_dict(cfg_dict)
                cfg.INUNDATION_AREA = list(area.box)
                cfg.INUNDATION_METHOD = area.method
                if area.dem.lower().endswith((".tif", ".tiff")):
                    cfg.INUNDATION_DEM, cfg.INUNDATION_DEM_PATH = "file", area.dem
                else:
                    cfg.INUNDATION_DEM = area.dem
                if area.dem_scale_m:
                    cfg.INUNDATION_DEM_SCALE_M = area.dem_scale_m
                cfg.INUNDATION_ANIMATION = False
                res["dir"] = os.path.join(dirs[sid], "areas", slug(area.name))
                make_flood_maps(cfg, log=lambda m: print(m, flush=True), out_dir=res["dir"],
                                cache_dir=cache_dir)
                res["ok"] = True
            except Exception as exc:                          # noqa: BLE001 — reported
                traceback.print_exc()
                res["error"] = f"{type(exc).__name__}: {exc}"
            res["seconds"] = round(time.time() - t0, 1)
            print(f"== {sid}: {'done' if res['ok'] else 'FAILED'} in {res['seconds']} s",
                  flush=True)
            out.append((sid, area.name, res))
    return out


def draw_areas(site, areas, cfgs, dirs, log=print, pad_hours=None, margin_km=None):
    """Every design flood area for every storm, the areas side by side."""
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context
    from .offline import thread_limit
    if not areas:
        return []
    threads = max(1, min(48, (os.cpu_count() or 2) // len(areas)))
    log(f"  Flood maps: {len(areas)} area(s) × {len(cfgs)} storm(s), "
        f"{threads} threads an area …")
    done = []
    with thread_limit(threads), ProcessPoolExecutor(len(areas),
                                                    mp_context=get_context("spawn")) as ex:
        futs = {a.name: ex.submit(_area_job, cfgs, dirs, a,
                                  site.dir("static", "areas", slug(a.name)),
                                  site.dir("design", "logs", slug(a.name) + ".log"), pad_hours,
                                  margin_km)
                for a in areas}
        for name, fut in futs.items():
            try:
                got = fut.result()
            except Exception as exc:                          # the process itself died
                got = [(sid, name, {"ok": False, "error": str(exc)}) for sid in cfgs]
            for sid, _, r in got:
                log(f"  Flood map {name}, {sid}: " + ("done in %.0f s" % r["seconds"]
                                                      if r["ok"] else f"FAILED ({r.get('error')})"))
            done.extend(got)
    return done


def _ran(vdir):
    """A storm run that finished: its flows and its flood record are there."""
    return all(os.path.isfile(os.path.join(vdir, f)) for f in ("gauges.csv", "hydrograph.csv")) \
        and os.path.isdir(os.path.join(vdir, "inundation"))


def _drawn(dirs, areas):
    """2-D maps already written → [(storm, area name, result)] (no new ones drawn)."""
    out = []
    for sid, vdir in dirs.items():
        for a in areas:
            d = os.path.join(vdir, "areas", slug(a.name))
            if os.path.isfile(os.path.join(d, "flood_depth_max.tif")):
                out.append((sid, a.name, {"ok": True, "method": a.method, "dir": d}))
    return out


def run_design(site, log=print, parallel=True, maps=True, reuse=False, areas=True):
    """Run every design storm, draw its flood maps and write ``site/design/``.
    *reuse*: keep storm runs that finished before; *areas* False: no new 2-D maps
    (those already drawn are shown), only the basin map."""
    from . import cycle, models, rainfiles
    from .products import _flows, _iso, _point_list, area_map
    from .sources import basin_centre
    from MRRpy.core.io_utils import routing_grid_crs

    t_wall = time.time()
    d = load_design(site)
    if not d.get("atlas14") or not os.path.isfile(d["atlas14"]):
        lat, lon = basin_centre(cycle.model_config(site))
        path = d.get("atlas14") or os.path.join(site.folder, "design", "atlas14.json")
        log(f"  NOAA Atlas 14 at {lat:.4f}, {lon:.4f} …")
        d["atlas14"] = fetch_atlas14(round(lat, 4), round(lon, 4), path)
    with open(d["atlas14"]) as f:
        atlas = json.load(f)
    t_state, state = start_state(site, d["start_from"])
    lead, hours = float(d["lead_hours"]), float(d["duration_hours"])
    total = lead + hours + float(d["after_hours"])
    run_start = t_state
    storm_start = run_start + timedelta(hours=lead)
    log(f"Design storms of {site.name}: {len(d['storms'])} storm(s), {hours:g} h each, "
        f"from the state of {t_state:%Y-%m-%d %H:%M} UTC")

    static_cfg = cycle.model_config(site)
    crs = routing_grid_crs(static_cfg)
    lat, lon = basin_centre(static_cfg)
    points = pd.DataFrame({"point_id": ["basin"], "lat": [lat], "lon": [lon]})
    times = rainfiles.grid(run_start, run_start + timedelta(hours=total))
    root = site.dir("design")
    jobs, storms = {}, []
    for s in d["storms"]:
        dep = depths_for(atlas, s["years"])
        blocks = nested_storm(dep, hours, d["peak_at"])
        mm = pd.Series(0.0, index=times)
        k0 = int(round(lead * 60 / STEP_MIN))
        mm.iloc[k0 + 1:k0 + 1 + len(blocks)] = blocks          # bins end after the start
        vdir = os.path.join(root, "runs", s["id"])
        rain = rainfiles.write_rain_files(os.path.join(vdir, "rain"), points,
                                          pd.DataFrame({"basin": mm.to_numpy()}, index=times),
                                          run_start, crs)
        models.link_static(site.dir("static"), vdir)
        cfg = cycle._version_config(site, s["name"], vdir, run_start, total, rain, state,
                                    None, {}, record_flood=True)
        jobs[s["name"]] = (cfg.to_dict(), run_start.strftime("%Y-%m-%d %H:%M"),
                           os.path.join(vdir, "run.log"))
        hourly = pd.Series(blocks).groupby(np.arange(len(blocks)) // (60 // STEP_MIN)).sum()
        storms.append(dict(s, dir=vdir, depth_mm=round(float(blocks.sum()), 1),
                           depth_in=round(float(blocks.sum()) / 25.4, 2),
                           peak_10min_mm=round(float(blocks.max()), 1),
                           peak_1h_mm=round(float(hourly.max()), 1),
                           atlas14_mm={str(m): round(float(v), 1) for m, v in dep.items()
                                       if m <= hours * 60},
                           blocks=[round(float(x), 3) for x in blocks]))
        log(f"  {s['name']}: {blocks.sum():.0f} mm ({blocks.sum() / 25.4:.2f} in) in {hours:g} h, "
            f"{hourly.max():.0f} mm in the wettest hour")
    log(f"  Running {len(jobs)} storm(s), {total:g} h each …")
    todo = {k: v for k, v in jobs.items()
            if not (reuse and _ran(next(s["dir"] for s in storms if s["name"] == k)))}
    if len(todo) < len(jobs):
        log(f"  Kept the earlier run of: {', '.join(k for k in jobs if k not in todo)}")
    results = models.run_many(todo, site.workers, site.threads_per_run, parallel, log) \
        if todo else {}
    bad = [k for k, r in results.items() if not r["ok"]]
    if bad:
        raise RuntimeError("design runs failed: " + "; ".join(
            f"{k}: {results[k]['error']}\n{models.last_lines(results[k]['log'])}" for k in bad))

    out = site.dir("site", "design")
    data_dir = os.path.join(out, "data")
    os.makedirs(os.path.join(data_dir, "points"), exist_ok=True)
    areas_out = []
    if maps:
        cfgs = {s["id"]: jobs[s["name"]][0] for s in storms}
        dirs = {s["id"]: s["dir"] for s in storms}
        if areas:
            done = draw_areas(site, design_areas(d), cfgs, dirs, log, d["pad_hours"],
                              d["margin_km"])
        else:
            done = _drawn(dirs, design_areas(d))
            log(f"  Flood maps: no new 2-D maps; {len(done)} drawn before")
        if d.get("basin_map", True) and site.basin_map:
            for s in storms:
                st = cycle.Status(datetime.utcnow())
                cycle._basin_map(site, cfgs[s["id"]], s["dir"], st, log)
                done.extend((s["id"], name, r) for name, r in st.data["areas"].items())
        for sid, name, st in done:
            entry = {"storm": sid, "area": name, "id": f"{sid}__{slug(name)}",
                     "basin": bool(st.get("basin")),
                     "method": site.basin_map.method if st.get("basin") else st.get("method", "2d"),
                     "ok": bool(st.get("ok")), "seconds": st.get("seconds")}
            if st.get("ok"):
                entry.update(area_map(os.path.join(st["dir"], "flood_depth_max.tif"),
                                      os.path.join(st["dir"], "flood_extent_max.geojson"),
                                      os.path.join(out, "maps", sid, slug(name))))
                entry.update({k: f"maps/{sid}/{v.split('/', 1)[1]}" for k, v in
                              entry.items() if k in ("png", "extent", "tif")})
            else:
                entry["error"] = st.get("error")
            areas_out.append(entry)

    pts = []
    for pid, point, gauge in _point_list(site):
        series = {}
        for s in storms:
            try:
                series[s["id"]] = _flows(s["dir"], run_start, gauge)
            except (OSError, KeyError):
                continue
        if not series:
            continue
        table = pd.DataFrame(series)
        rel_h = (table.index - storm_start).total_seconds() / 3600.0
        peaks = {k: {"q": round(float(table[k].max()), 2),
                     "hours": round(float(rel_h[int(np.argmax(table[k].to_numpy()))]), 2)}
                 for k in table}
        pts.append({"id": pid, "name": point.name, "lat": point.lat if gauge else None,
                    "lon": point.lon if gauge else None, "peaks": peaks,
                    "warning_m3s": point.warning_m3s, "danger_m3s": point.danger_m3s})
        with open(os.path.join(data_dir, "points", f"{pid}.json"), "w") as f:
            json.dump({"id": pid, "name": point.name, "hours": [round(float(h), 3) for h in rel_h],
                       "series": {k: [round(float(x), 3) for x in table[k]] for k in table}}, f)
        csv = table.copy()
        csv.index = pd.Index(np.round(rel_h, 3), name="hours_from_storm_start")
        csv.round(3).to_csv(os.path.join(data_dir, "points", f"{pid}.csv"))

    ffa = None
    if d.get("gauge_peaks") and os.path.isfile(d["gauge_peaks"]):
        ffa = gauge_frequency(d["gauge_peaks"])
        ffa["point"] = d.get("gauge_point")
    basin = site.dir("static", "watershed.geojson")
    if os.path.isfile(basin):
        import geopandas as gpd
        gpd.read_file(basin).to_crs(4326).to_file(os.path.join(data_dir, "basin.geojson"),
                                                  driver="GeoJSON")
    summary = {"title": site.website.get("title", site.name), "name": site.name,
               "made_utc": _iso(datetime.utcnow()), "state_utc": _iso(t_state),
               "state": os.path.basename(state), "lead_hours": lead,
               "duration_hours": hours, "after_hours": float(d["after_hours"]),
               "peak_at": float(d["peak_at"]), "step_min": STEP_MIN,
               "pad_hours": float(d["pad_hours"]),
               "atlas14": {k: atlas.get(k) for k in ("source", "url", "volume", "version",
                                                    "region", "lat", "lon", "fetched_utc")},
               "storms": [{k: v for k, v in s.items() if k not in ("dir", "areas")}
                          for s in storms],
               "points": pts, "areas": areas_out, "gauge_frequency": ffa,
               "display_timezone": site.display_timezone,
               "seconds": round(time.time() - t_wall, 1)}
    with open(os.path.join(data_dir, "design.json"), "w") as f:
        json.dump(summary, f, separators=(",", ":"))
    import shutil
    for name in WEB_FILES:
        shutil.copyfile(os.path.join(WEB_DIR, name), os.path.join(out, name))
    for name in ("style.css", "leaflet.css"):
        shutil.copyfile(os.path.join(os.path.dirname(WEB_DIR), name), os.path.join(out, name))
    log(f"Design storms done in {time.time() - t_wall:.0f} s: {out}")
    return summary
