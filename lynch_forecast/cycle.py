# -*- coding: utf-8 -*-
"""
cycle.py — one forecast cycle.

    lock → read the stations and inflows → archive them
      → T_obs = the last time every working station has reported
      → start state = the newest saved state at least look_back_hours before T_obs
      → each forecast version: its rain points and forecast, the station rain
        spread onto those points up to T_obs → rain files
      → run every version from that state to T_obs + forecast_hours, side by side
        (the main version saves a state every hour up to T_obs and records the
        river flow for the flood maps)
      → keep the new states → flood maps for every area → web page → publish
      → status.json → unlock

A failure never empties the page: a forecast feed that fails is replaced by its
last good forecast (up to 12 h old) or its version is left out; if the main
version fails, the page keeps the last results and says since when.
"""

import fcntl
import json
import os
import shutil
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import archive, rainfiles, states
from .sitefile import NO_RAIN, slug
from .sources import Context, all_sources

STAMP = "%Y%m%dT%H%MZ"
FORECAST_MAX_AGE_H = 12.0
CATCH_UP_AFTER_H = 24.0


def utcnow():
    return datetime.utcnow().replace(second=0, microsecond=0)


def floor_hour(t):
    return t.replace(minute=0, second=0, microsecond=0)


def ceil_hour(t):
    f = floor_hour(t)
    return f if f == t else f + timedelta(hours=1)


def floor_step(t, step_s=rainfiles.STEP_S):
    s = int(rainfiles._secs([t])[0])
    return rainfiles._times([s - s % step_s])[0].to_pydatetime()


class CycleLock:
    """Only one cycle at a time per site folder (a lock file)."""

    def __init__(self, site):
        self.path = site.dir(".cycle.lock")
        self.f = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.f = open(self.path, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            self.f = None
            return False
        self.f.write(str(os.getpid()))
        self.f.flush()
        return True

    def __exit__(self, *exc):
        if self.f:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()


class Status:
    """What happened in a cycle, step by step (status.json)."""

    def __init__(self, cycle_time):
        self.data = {"cycle_utc": cycle_time.strftime("%Y-%m-%d %H:%M"),
                     "started_utc": utcnow().strftime("%Y-%m-%d %H:%M"),
                     "steps": [], "stations": {}, "forecasts": {}, "versions": {},
                     "areas": {}, "ok": False}

    def step(self, what, ok=True, note=""):
        self.data["steps"].append({"step": what, "ok": bool(ok), "note": note,
                                   "utc": utcnow().strftime("%Y-%m-%d %H:%M:%S")})

    def save(self, *paths):
        self.data["finished_utc"] = utcnow().strftime("%Y-%m-%d %H:%M")
        for p in paths:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".part"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=1, default=str)
            os.replace(tmp, p)


def last_status(site):
    p = site.dir("status.json")
    if os.path.isfile(p):
        with open(p) as f:
            return json.load(f)
    return None


def model_config(site, run_dir=None):
    """The site's model settings with OUTPUT_DIR = *run_dir* (default static/)."""
    from MRRpy.config import Config
    cfg = Config.from_file(site.model)
    if cfg.DEM_PATH and not os.path.isabs(cfg.DEM_PATH):   # a DEM kept beside the model file
        cfg.DEM_PATH = os.path.join(os.path.dirname(os.path.abspath(site.model)), cfg.DEM_PATH)
    cfg.OUTPUT_DIR = os.path.abspath(run_dir or site.dir("static"))
    cfg.update_output_paths()
    return cfg


# ── Readings ─────────────────────────────────────────────────────────────────

def read_stations(site, ctx, status, log):
    """Fetch every station, archive it → [(key, meta, rain table)] of the site's stations."""
    regs = all_sources()["station"]
    ctx.archived = archive.last_reports(site)        # readers may ask only for what is new
    for entry in site.rain_stations:
        try:
            got = regs[entry.source].func(entry.spec(), ctx)
            last = None
            for st in got:
                archive.save_station(site, entry.name, st)
                if len(st.rain):
                    last = max(last or st.rain.index.max(), st.rain.index.max())
            status.data["stations"][entry.name] = {
                "ok": True, "stations": len(got),
                "last_report_utc": last.strftime("%Y-%m-%d %H:%M") if last is not None else None}
            log(f"  Station {entry.name}: {len(got)} series, last report "
                f"{last:%Y-%m-%d %H:%M} UTC" if last is not None else
                f"  Station {entry.name}: no readings")
        except Exception as exc:                              # noqa: BLE001
            status.data["stations"][entry.name] = {"ok": False, "error": str(exc)}
            log(f"  Station {entry.name}: FAILED ({exc}) — using the archive")
    names = {e.name for e in site.rain_stations}
    out = []
    for key, (meta, table) in archive.stations(site).items():
        if meta.get("entry") in names and len(table):
            rain = table.dropna(subset=["rain_mm"])
            rain = rain[rain["time_utc"] <= ctx.now]
            out.append((key, meta, rain))
    return out


def observation_time(stations, now, silent_minutes):
    """T_obs and the stations still reporting (a station silent too long is missing)."""
    active = []
    for key, meta, rain in stations:
        if len(rain) and rain["time_utc"].max() >= now - timedelta(minutes=silent_minutes):
            active.append(key)
    if not active:
        return floor_step(now), active
    t = min(pd.Timestamp(r["time_utc"].max()).to_pydatetime()
            for k, m, r in stations if k in active)
    return floor_step(t), active


def read_inflows(site, ctx, status, log):
    """{name: Series of m³/s} — fetched, merged into archive/inflows/."""
    regs = all_sources()["inflow"]
    out = {}
    for inf in site.inflows:
        path = site.dir("archive", "inflows", slug(inf.name) + ".csv.gz")
        old = pd.Series(dtype=float)
        if os.path.isfile(path):
            df = pd.read_csv(path, compression="gzip", parse_dates=["time_utc"])
            old = pd.Series(df["Q_m3s"].to_numpy(float), index=pd.DatetimeIndex(df["time_utc"]))
        try:
            new = regs[inf.source].func(inf.spec(), ctx).flow
            both = pd.concat([old, new])
            both = both[~both.index.duplicated(keep="last")].sort_index()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            pd.DataFrame({"time_utc": both.index, "Q_m3s": both.values}).to_csv(
                path, index=False, compression="gzip")
            status.data.setdefault("inflows", {})[inf.name] = {"ok": True}
        except Exception as exc:                              # noqa: BLE001
            both = old
            status.data.setdefault("inflows", {})[inf.name] = {"ok": False, "error": str(exc)}
            log(f"  Inflow {inf.name}: FAILED ({exc}) — using the archive")
        out[inf.name] = both[both.index <= ctx.now]
    return out


def read_levels(site, ctx, status, log):
    """{gauge name: Series of m} — the river gauges' water level, fetched and merged
    into archive/levels/.  Shown on the page only; the model never uses it."""
    regs = all_sources()["level"]
    out = {}
    for g in site.river_gauges:
        old = archive.read_level(site, g.name)
        ctx.archived = {g.name: old.index.max().to_pydatetime()} if len(old) else {}
        st = status.data.setdefault("gauges", {})
        try:
            got = regs[g.source].func(g.spec(), ctx)
            both = archive.save_level(site, g.name, got.level)
            st[g.name] = {"ok": True, "lat": got.lat, "lon": got.lon, **got.info}
        except Exception as exc:                              # noqa: BLE001
            both = old
            st[g.name] = {"ok": False, "error": str(exc)}
            log(f"  River gauge {g.name}: FAILED ({exc}) — using the archive")
        both = both[both.index <= ctx.now]
        st[g.name]["last_report_utc"] = (both.index.max().strftime("%Y-%m-%d %H:%M")
                                         if len(both) else None)
        if st[g.name]["ok"]:
            log(f"  River gauge {g.name}: last reading {st[g.name]['last_report_utc']} UTC")
        out[g.name] = both
    return out


def inflow_file(inf, flow, run_start, run_end, path):
    """A ROUTING_INFLOW_BC CSV for one run: readings, then held or receding."""
    t = pd.date_range(run_start, run_end, freq="10min")
    if not len(flow):
        q = np.zeros(len(t))
    else:
        known = flow.reindex(flow.index.union(t)).interpolate(method="time")
        q = known.reindex(t).to_numpy(float)
        q = np.where(t < flow.index[0], flow.iloc[0], q)
        after = t > flow.index[-1]
        hours = (t - flow.index[-1]).total_seconds() / 3600.0
        q_last = float(flow.iloc[-1])
        q = np.where(after, q_last * (np.exp(-hours / inf.recede_hours)
                                      if inf.ahead == "recede" else 1.0), q)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.DataFrame({"time_s": (t - run_start).total_seconds().astype(int),
                  "Q_m3s": np.maximum(q, 0.0)}).to_csv(path, index=False)
    return path


# ── Forecasts and rain ───────────────────────────────────────────────────────

def read_forecasts(site, ctx, status, log, keep=True):
    """{version: ForecastRain} (the newest good one stands in for a failed feed).
    *keep*: archive them (not for catch-up runs, whose window is all past)."""
    regs = all_sources()["forecast"]
    out = {}
    for fc_entry in site.rain_forecasts:
        name = fc_entry.name
        try:
            fc = regs[fc_entry.source].func(fc_entry.spec(), ctx)
            if fc.rain.empty:
                raise RuntimeError("the forecast was empty")
            if keep:
                archive.save_forecast(site, name, fc, ctx.now)
            status.data["forecasts"][name] = {
                "ok": True, "issued_utc": fc.issued.strftime("%Y-%m-%d %H:%M") if fc.issued
                else None, "points": int(len(fc.points)),
                "ends_utc": fc.rain.index.max().strftime("%Y-%m-%d %H:%M"), "info": fc.info}
            out[name] = fc
            log(f"  Forecast {name}: {len(fc.points)} rain points to "
                f"{fc.rain.index.max():%Y-%m-%d %H:%M} UTC")
        except Exception as exc:                              # noqa: BLE001
            old = archive.latest_forecast(site, name, ctx.now, FORECAST_MAX_AGE_H) if keep \
                else None
            status.data["forecasts"][name] = {"ok": False, "error": str(exc),
                                              "reused": bool(old)}
            if old is not None:
                out[name] = old
                log(f"  Forecast {name}: FAILED ({exc}) — using the one from "
                    f"{old.info.get('reused_from_cycle')} UTC")
            else:
                log(f"  Forecast {name}: FAILED ({exc}) — this version is left out")
    return out


def _bins_of(fc, times):
    cols = [rainfiles.to_bins(fc.rain[str(c)], fc.step_s, times).to_numpy()
            for c in fc.points["point_id"]]
    return np.column_stack(cols)


def version_rain(site, version, forecasts, stations, times, t_obs):
    """(points, table, estimated) for *version*; see rainfiles.assemble."""
    main = forecasts.get(site.main_forecast)
    fc = forecasts.get(version) if version != NO_RAIN else None
    if fc is not None:
        points = fc.points[["point_id", "lat", "lon"]].copy()
    elif main is not None:
        points = main.points[["point_id", "lat", "lon"]].copy()
    elif stations:
        points = pd.DataFrame({"point_id": [m["name"] for _, m, _ in stations],
                               "lat": [m["lat"] for _, m, _ in stations],
                               "lon": [m["lon"] for _, m, _ in stations]})
    else:
        raise RuntimeError("no rain station and no forecast: nothing to run on")
    plat, plon = points["lat"].to_numpy(float), points["lon"].to_numpy(float)
    observed = None
    if stations:
        V = np.column_stack([rainfiles.to_bins(pd.Series(r["rain_mm"].to_numpy(float),
                                                         index=pd.DatetimeIndex(r["time_utc"])),
                                               int(r["step_s"].iloc[-1]), times).to_numpy()
                             for _, _, r in stations])
        observed = rainfiles.spread(V, [m["lat"] for _, m, _ in stations],
                                    [m["lon"] for _, m, _ in stations], plat, plon)
    forecast = _bins_of(fc, times) if fc is not None else None
    fill = None
    past = forecasts.get(site.past_rain_forecast or site.main_forecast) or main
    if past is not None:
        mb = _bins_of(past, times)
        fill = rainfiles.spread(mb, past.points["lat"].to_numpy(float),
                                past.points["lon"].to_numpy(float), plat, plon)
    table, est = rainfiles.assemble(times, t_obs, observed, forecast, fill, site.missing_rain)
    points = points.assign(point_id=[str(p) for p in points["point_id"]])
    return points, pd.DataFrame(table, index=times, columns=list(points["point_id"])), est


# ── The cycle ────────────────────────────────────────────────────────────────

def _version_config(site, version, run_dir, run_start, hours, rain_paths, restart,
                    save_hours, inflow_paths, record_flood):
    cfg = model_config(site, run_dir)
    cfg.PRECIP_METHOD = "idw"
    cfg.PRECIP_GAUGE_FILE, cfg.PRECIP_TIMESERIES_FILE = rain_paths
    cfg.PRECIP_EXCLUDE_OUTSIDE_STATIONS = False
    cfg.TOTAL_SIMULATION_TIME_HOURS = float(hours)
    cfg.RESTART_FROM = restart
    cfg.SAVE_STATE_AT_HOURS = save_hours or None
    cfg.ROUTING_GAUGES = [{"name": slug(p.name), "lat": p.lat, "lon": p.lon,
                           "snap_to_channel": True, "snap_radius_cells": p.snap_radius_cells}
                          for p in site.hydrograph_points] or None
    cfg.ROUTING_INFLOW_BC = [{"name": slug(inf.name), "lat": inf.lat, "lon": inf.lon,
                              "csv": inflow_paths[inf.name], "snap_to_channel": True}
                             for inf in site.inflows] or None
    cfg.INUNDATION_MAP = bool(record_flood)
    cfg.INUNDATION_ANIMATION = False
    cfg.SAVE_FIELDS = False
    return cfg


def plan_runs(site, now, t_obs):
    """(run_start, restart_path or None, catch_up_to or None) for this cycle."""
    target = floor_hour(t_obs - timedelta(hours=site.look_back_hours))
    got = states.pick_start(site, target)
    if got is None:
        return target, None, None
    t, path = got
    if target - t > timedelta(hours=CATCH_UP_AFTER_H):
        return t, path, target
    return t, path, None


def run_cycle(site, now=None, log=print, publish=True, parallel=True, only_if_stale=None):
    """
    Run one forecast cycle at clock time *now* (default: now) → status dict.
    *only_if_stale* (minutes): do nothing if a cycle finished more recently.
    """
    now = (now or utcnow()).replace(second=0, microsecond=0)
    if only_if_stale:
        last = last_status(site)
        if last and last.get("ok") and last.get("finished_utc"):
            done = datetime.strptime(last["finished_utc"], "%Y-%m-%d %H:%M")
            if now - done < timedelta(minutes=float(only_if_stale)):
                log(f"The last cycle finished at {done:%H:%M} UTC, under "
                    f"{only_if_stale:g} minutes ago: nothing to do.")
                return {"skipped": "fresh", **last}
    lock = CycleLock(site)
    if not lock.__enter__():
        log("Another cycle is running in this folder; this one stops.")
        return {"skipped": "locked"}
    try:
        return _cycle(site, now, log, publish, parallel)
    finally:
        lock.__exit__()


def _cycle(site, now, log, publish, parallel):
    from MRRpy.core.io_utils import routing_grid_crs

    t_wall = time.time()
    cid = now.strftime(STAMP)
    status = Status(now)
    cyc_dir = site.dir("cycles", cid)
    os.makedirs(cyc_dir, exist_ok=True)
    log(f"Forecast cycle {now:%Y-%m-%d %H:%M} UTC — {site.name}")
    static_cfg = model_config(site)
    if not os.path.isfile(static_cfg.ROUTING_DEM_PATH):
        raise RuntimeError(f"{static_cfg.ROUTING_DEM_PATH} is missing: run "
                           "'lynch-forecast setup' first.")
    crs = routing_grid_crs(static_cfg)
    ctx = Context(now=now, cfg=static_cfg, cache_dir=site.dir("forecasts_cache"), log=log)

    stations = read_stations(site, ctx, status, log)
    t_obs, active = observation_time(stations, now, site.station_silent_minutes)
    stations = [s for s in stations if s[0] in active] if active else stations
    status.data["t_obs_utc"] = t_obs.strftime("%Y-%m-%d %H:%M")
    status.data["stations_reporting"] = active
    status.step("stations", bool(active) or not site.rain_stations,
                f"last report {t_obs:%Y-%m-%d %H:%M} UTC" if active else
                "no station reported in time: past rain estimated")
    inflows = read_inflows(site, ctx, status, log)
    levels = read_levels(site, ctx, status, log)

    run_start, restart, catch_up = plan_runs(site, now, t_obs)
    if restart is None:
        log(f"  No saved state yet: the model starts dry at {run_start:%Y-%m-%d %H:%M} UTC "
            "(run 'lynch-forecast setup' for a warm start).")
    if catch_up is not None:
        log(f"  The newest state is from {run_start:%Y-%m-%d %H:%M} UTC: catching up to "
            f"{catch_up:%Y-%m-%d %H:%M} UTC first.")
        run_start, restart = _catch_up(site, ctx, stations, inflows, run_start, restart,
                                       catch_up, t_obs, crs, cyc_dir, status, log, parallel)
    run_end = ceil_hour(t_obs + timedelta(hours=site.forecast_hours))
    hours = (run_end - run_start).total_seconds() / 3600.0
    status.data.update(run_start_utc=run_start.strftime("%Y-%m-%d %H:%M"),
                       run_end_utc=run_end.strftime("%Y-%m-%d %H:%M"),
                       restart_from=os.path.basename(restart) if restart else None)

    ctx.start, ctx.end = run_start, run_end
    forecasts = read_forecasts(site, ctx, status, log)
    status.step("forecasts", bool(forecasts) or not site.rain_forecasts,
                f"{len(forecasts)} of {len(site.rain_forecasts)}")

    times = rainfiles.grid(run_start, run_end)
    main = site.versions()[0] if site.versions() else NO_RAIN
    save_hours = [h for h in range(1, int(hours) + 1)
                  if run_start + timedelta(hours=h) <= t_obs]
    jobs, rain_info = {}, {}
    for version in site.versions():
        if version != NO_RAIN and version not in forecasts:
            status.data["versions"][version] = {"ok": False, "error": "no forecast"}
            continue
        vdir = os.path.join(cyc_dir, slug(version))
        try:
            points, table, est = version_rain(site, version, forecasts, stations, times, t_obs)
            rain_paths = rainfiles.write_rain_files(os.path.join(vdir, "rain"), points, table,
                                                    run_start, crs)
            inflow_paths = {inf.name: inflow_file(inf, inflows[inf.name], run_start, run_end,
                                                  os.path.join(vdir, "inflows",
                                                               slug(inf.name) + ".csv"))
                            for inf in site.inflows}
        except Exception as exc:                              # noqa: BLE001
            status.data["versions"][version] = {"ok": False, "error": f"rain: {exc}"}
            log(f"  {version}: no rain files ({exc})")
            continue
        from .models import link_static
        link_static(site.dir("static"), vdir)
        cfg = _version_config(site, version, vdir, run_start, hours, rain_paths, restart,
                              save_hours if version == main else None, inflow_paths,
                              record_flood=(version == main and site.has_maps()))
        jobs[version] = (cfg.to_dict(), run_start.strftime("%Y-%m-%d %H:%M"),
                         os.path.join(vdir, "run.log"))
        rain_info[version] = {"dir": vdir, "mean_mm": table.mean(axis=1), "estimated": est}

    from .models import last_lines, run_many
    log(f"  Running {len(jobs)} version(s) from {run_start:%Y-%m-%d %H:%M} to "
        f"{run_end:%Y-%m-%d %H:%M} UTC ({hours:g} h)"
        + (f", starting from {os.path.basename(restart)}" if restart else ", starting dry"))
    results = run_many(jobs, site.workers, site.threads_per_run, parallel, log)
    for version, res in results.items():
        res = dict(res)
        if not res["ok"]:
            res["log_end"] = last_lines(res["log"])
            log(f"  {version} FAILED: {res['error']}\n{res['log_end']}")
        status.data["versions"][version] = res
    main_ok = results.get(main, {}).get("ok", False)
    status.step("models", main_ok, f"{sum(r['ok'] for r in results.values())} of "
                                   f"{len(site.versions())} versions ran")

    if main_ok:
        kept = states.keep(site, os.path.join(rain_info[main]["dir"], "state"), run_start, t_obs)
        states.prune(site, now)
        status.data["states_kept"] = [t.strftime("%Y-%m-%d %H:%M") for t in kept]
        _flood_maps(site, jobs[main][0], rain_info[main]["dir"], status, log)

    from . import products, website
    ok_versions = {v: rain_info[v] for v, r in results.items() if r["ok"]}
    if main_ok:
        try:
            products.build(site, status.data, ok_versions, main, run_start, t_obs, now,
                           levels=levels)
            status.step("products", True)
        except Exception as exc:                              # noqa: BLE001
            status.step("products", False, str(exc))
            log(f"  Products FAILED: {exc}")
    status.data["ok"] = bool(main_ok)
    status.data["seconds"] = round(time.time() - t_wall, 1)
    status.save(site.dir("status.json"), site.dir("site", "data", "status.json"))
    website.write(site)
    if publish and site.website.get("publish"):
        from .publish import publish as _publish
        try:
            _publish(site, log)
            status.step("publish", True)
        except Exception as exc:                              # noqa: BLE001
            status.step("publish", False, str(exc))
            log(f"  Publish FAILED: {exc}")
        status.save(site.dir("status.json"), site.dir("site", "data", "status.json"))
    _prune_cycles(site, now)
    log(f"Cycle {'done' if main_ok else 'FAILED'} in {status.data['seconds']:g} s")
    return status.data


def _catch_up(site, ctx, stations, inflows, run_start, restart, until, t_obs, crs,
              cyc_dir, status, log, parallel, record_flood=False, name="catch_up"):
    """A main-only run with past rain from the old state (or dry, *restart* None)
    to *until* → (until, state at *until*).  Setup's warm-up is one too."""
    from .models import link_static, run_many
    ctx.start, ctx.end = run_start, until
    forecasts = read_forecasts(site, ctx, Status(ctx.now), lambda *_: None, keep=False)
    times = rainfiles.grid(run_start, until)
    version = site.versions()[0]
    vdir = os.path.join(cyc_dir, name)
    points, table, _ = version_rain(site, version, forecasts, stations, times, until)
    rain_paths = rainfiles.write_rain_files(os.path.join(vdir, "rain"), points, table,
                                            run_start, crs)
    inflow_paths = {inf.name: inflow_file(inf, inflows[inf.name], run_start, until,
                                          os.path.join(vdir, "inflows", slug(inf.name) + ".csv"))
                    for inf in site.inflows}
    hours = int((until - run_start).total_seconds() // 3600)
    link_static(site.dir("static"), vdir)
    keep_h = [h for h in range(1, hours + 1)
              if (run_start + timedelta(hours=h)).hour == 0 or h == hours]
    cfg = _version_config(site, version, vdir, run_start, hours, rain_paths, restart, keep_h,
                          inflow_paths, record_flood=record_flood)
    res = run_many({name: (cfg.to_dict(), run_start.strftime("%Y-%m-%d %H:%M"),
                           os.path.join(vdir, "run.log"))}, 1, site.threads_per_run,
                   parallel, log)[name]
    status.data[name] = res
    if not res["ok"]:
        from .models import last_lines
        raise RuntimeError(f"the {name.replace('_', '-')} run failed: {res['error']}\n"
                           f"{last_lines(res['log'])}")
    states.keep(site, os.path.join(vdir, "state"), run_start, until)
    return until, states.state_path(site, until)


def _flood_maps(site, main_cfg, main_dir, status, log):
    from MRRpy.config import Config
    from MRRpy.core.inundation import make_flood_maps
    from .offline import earth_engine_blocked

    for area in site.flood_areas:
        t0 = time.time()
        cfg = Config.from_dict(main_cfg)
        cfg.INUNDATION_AREA = list(area.box)
        cfg.INUNDATION_METHOD = area.method
        if area.dem.lower().endswith((".tif", ".tiff")):
            cfg.INUNDATION_DEM, cfg.INUNDATION_DEM_PATH = "file", area.dem
        else:
            cfg.INUNDATION_DEM = area.dem
        if area.dem_scale_m:
            cfg.INUNDATION_DEM_SCALE_M = area.dem_scale_m
        cfg.INUNDATION_ANIMATION = False
        out_dir = os.path.join(main_dir, "areas", slug(area.name))
        lines = []
        try:
            with earth_engine_blocked():
                paths = make_flood_maps(cfg, log=lines.append, out_dir=out_dir,
                                        cache_dir=site.dir("static", "areas", slug(area.name)))
            status.data["areas"][area.name] = {"ok": True, "dir": out_dir,
                                               "seconds": round(time.time() - t0, 1),
                                               "files": sorted(paths)}
            log(f"  Flood map {area.name}: done in {time.time() - t0:.0f} s")
        except Exception as exc:                              # noqa: BLE001
            status.data["areas"][area.name] = {"ok": False, "error": str(exc),
                                               "log_end": "\n".join(lines[-5:])}
            log(f"  Flood map {area.name}: FAILED ({exc})")
    if site.basin_map:
        _basin_map(site, main_cfg, main_dir, status, log)


def _basin_map(site, main_cfg, main_dir, status, log):
    from MRRpy.config import Config
    from . import basinmap
    bm, t0 = site.basin_map, time.time()
    out_dir = os.path.join(main_dir, "areas", slug(bm.name))
    try:
        summary = basinmap.draw(site, Config.from_dict(main_cfg), main_dir, out_dir, log)
        status.data["areas"][bm.name] = {"ok": True, "dir": out_dir, "basin": True,
                                         "seconds": round(time.time() - t0, 1),
                                         "boxes": len(summary["boxes"])}
        log(f"  Flood map {bm.name}: done in {time.time() - t0:.0f} s "
            f"({len(summary['boxes'])} boxes, {summary['flooded_km2']:g} km² under water)")
    except Exception as exc:                              # noqa: BLE001
        status.data["areas"][bm.name] = {"ok": False, "basin": True, "error": str(exc)}
        log(f"  Flood map {bm.name}: FAILED ({exc})")


def _prune_cycles(site, now):
    cut = now - timedelta(days=float(site.keep_cycles_days))
    root = site.dir("cycles")
    for name in os.listdir(root) if os.path.isdir(root) else ():
        try:
            t = datetime.strptime(name, STAMP)
        except ValueError:
            continue
        if t < cut:
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
