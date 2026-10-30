# -*- coding: utf-8 -*-
"""
sitefile.py — the forecast file: what a live forecast reads, runs and shows.

The forecast file is a short YAML file next to the usual MRRpy settings file
(``model:``).  It lists the rain stations, the rain forecasts, the river inflows,
the points that get a hydrograph, the river gauges whose measured level is shown
under a point's hydrograph and the areas that get a flood map — each as many as
wanted.  ``load_site`` reads and checks it; every problem is reported at
once, in plain words, with "did you mean" for a misspelt key.  ``template()`` is
the commented file ``lynch-forecast new`` writes.
"""

import difflib
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional


class SiteFileError(ValueError):
    """A forecast file that cannot be used; the message lists every problem."""


def slug(name):
    """A file-name-safe form of *name* ("Panauti town" → "panauti_town")."""
    s = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return s or "x"


@dataclass
class Entry:
    """A station, forecast or inflow: its name, source and the source's own keys."""
    name: str
    source: str
    options: dict

    def spec(self):
        return dict(self.options, name=self.name, source=self.source)


@dataclass
class Inflow(Entry):
    lat: float = 0.0
    lon: float = 0.0
    ahead: str = "hold"            # after the last reading: hold | recede
    recede_hours: float = 24.0


@dataclass
class RiverGauge(Entry):
    """A measured water level, drawn under the hydrograph of *point*."""
    point: str = ""


@dataclass
class Point:
    name: str
    lat: float
    lon: float
    warning_m3s: Optional[float] = None
    danger_m3s: Optional[float] = None
    snap_radius_cells: int = 2             # a larger radius slides points downstream


@dataclass
class Area:
    name: str
    box: tuple                     # (west, south, east, north), degrees
    method: str = "hand"           # hand | 2d
    dem: str = "auto"              # auto | model_grid | a DEM name | a file
    dem_scale_m: Optional[float] = None


@dataclass
class BasinMap:
    """One flood map of the whole basin, drawn as overlapping boxes (basinmap.py)."""
    name: str = "Whole basin"
    method: str = "2d"             # 2d | hand
    boxes: object = "auto"         # auto | [west–east, south–north]
    overlap_km: float = 1.5
    dem: str = "auto"
    dem_scale_m: Optional[float] = None
    box_cells: int = 250_000       # boxes: auto aims at about this many cells a box
    workers: int = 0               # boxes at the same time (0 = all)
    threads: int = 0               # CPU threads a box (0 = the cores shared out)


@dataclass
class Site:
    path: str                                  # the forecast file
    name: str
    model: str                                 # MRRpy settings file (absolute)
    folder: str                                # where everything is kept (absolute)
    display_timezone: str = "UTC"
    every_minutes: int = 30
    forecast_hours: float = 48.0
    look_back_hours: float = 12.0
    warm_up_days: float = 30.0
    missing_rain: str = "forecast_model"       # forecast_model | zero
    station_silent_minutes: float = 60.0
    rain_stations: List[Entry] = field(default_factory=list)
    rain_forecasts: List[Entry] = field(default_factory=list)
    main_forecast: Optional[str] = None
    past_rain_forecast: Optional[str] = None   # whose own past stands in for station rain
    no_rain_version: bool = True
    inflows: List[Inflow] = field(default_factory=list)
    hydrograph_points: List[Point] = field(default_factory=list)
    river_gauges: List[RiverGauge] = field(default_factory=list)
    flood_areas: List[Area] = field(default_factory=list)
    basin_map: Optional[BasinMap] = None
    website: dict = field(default_factory=dict)
    workers: int = 0                           # parallel model runs (0 = one per version)
    threads_per_run: int = 8
    keep_cycles_days: float = 3.0
    keep_states_days: float = 30.0

    def dir(self, *parts):
        """A path inside the site folder."""
        return os.path.join(self.folder, *parts)

    def versions(self):
        """Forecast version names, the main one first, then 'no rain'."""
        names = [f.name for f in self.rain_forecasts]
        if self.main_forecast in names:
            names.remove(self.main_forecast)
            names.insert(0, self.main_forecast)
        if self.no_rain_version:
            names.append(NO_RAIN)
        return names

    def forecast(self, name):
        return next((f for f in self.rain_forecasts if f.name == name), None)

    def gauge_of(self, point_name):
        """The river gauge drawn under the point called *point_name*, or None."""
        return next((g for g in self.river_gauges if g.point == point_name), None)

    def has_maps(self):
        """Does a cycle draw any flood map (so the main run records the flows)?"""
        return bool(self.flood_areas or self.basin_map)


NO_RAIN = "No more rain"

_TOP = {
    "name": "a title for this forecast",
    "model": "the MRRpy settings file (model.yaml)",
    "folder": "where the forecast keeps its files (default: next to this file)",
    "display_timezone": "time zone for the web page, e.g. America/Chicago",
    "every_minutes": "minutes between cycles (serve)",
    "forecast_hours": "how far ahead to forecast",
    "look_back_hours": "hours of past rain each cycle runs again",
    "warm_up_days": "days of past rain before the first cycle (setup)",
    "missing_rain": "rain when no station reported: forecast_model or zero",
    "station_silent_minutes": "a station silent this long counts as missing",
    "rain_stations": "list of rain stations",
    "rain_forecasts": "list of rain forecasts",
    "main_forecast": "the forecast that drives the flood maps",
    "past_rain_forecast": "the forecast whose own past is the rain before now (default: main_forecast)",
    "no_rain_version": "also run a version with no more rain",
    "inflows": "list of river inflows at the model edge",
    "hydrograph_points": "list of points that get a hydrograph",
    "river_gauges": "list of river gauges whose measured level is shown under a point",
    "flood_areas": "list of areas that get a flood map",
    "basin_map": "a flood map of the whole basin, e.g. {method: 2d, boxes: auto}",
    "website": "the web page: title, publish",
    "workers": "model runs at the same time (0 = all versions at once)",
    "threads_per_run": "CPU threads for each model run",
    "keep_cycles_days": "days to keep each cycle's folder",
    "keep_states_days": "days to keep daily saved states",
}
_POINT = {"name", "lat", "lon", "warning_m3s", "danger_m3s", "snap_radius_cells"}
_AREA = {"name", "box", "method", "dem", "dem_scale_m"}
_BASIN_MAP = {"name", "method", "boxes", "overlap_km", "dem", "dem_scale_m", "box_cells",
              "workers", "threads"}
_INFLOW = {"name", "lat", "lon", "source", "ahead", "recede_hours"}
_WEBSITE = {"title", "publish", "notice", "links"}
_PATH_KEYS = ("path", "gauges", "timeseries")


def _did_you_mean(word, options):
    m = difflib.get_close_matches(str(word), list(options), n=1, cutoff=0.6)
    return f" — did you mean '{m[0]}'?" if m else ""


def _num(value, what, errors, lo=None, hi=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        errors.append(f"{what} must be a number, not {value!r}.")
        return None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        errors.append(f"{what} = {value} is out of range "
                      f"({'' if lo is None else lo} … {'' if hi is None else hi}).")
    return v


def _resolve(base, value):
    s = str(value)
    if s.startswith(("http://", "https://")) or os.path.isabs(s):
        return s
    return os.path.normpath(os.path.join(base, s))


def _entries(raw, kind, registry, base, errors, extra_keys=()):
    out, names = [], set()
    if raw is None:
        return out
    if not isinstance(raw, list):
        errors.append(f"{kind} must be a list (one '- ' line per entry).")
        return out
    for i, item in enumerate(raw, 1):
        where = f"{kind} #{i}"
        if not isinstance(item, dict):
            errors.append(f"{where} must be a set of keys, e.g. {{name: …, source: …}}.")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            errors.append(f"{where} has no name.")
            name = f"{kind}_{i}"
        where = f"{kind} '{name}'"
        if name in names:
            errors.append(f"{where} appears twice; names must differ.")
        names.add(name)
        source = item.get("source")
        if source not in registry:
            errors.append(f"{where}: source {source!r} is not known"
                          f"{_did_you_mean(source, registry)} "
                          f"(known: {', '.join(sorted(registry))}).")
            continue
        src = registry[source]
        allowed = set(src.required) | set(src.optional) | {"name", "source"} | set(extra_keys)
        for k in item:
            if k not in allowed:
                errors.append(f"{where}: '{k}' is not a setting of source '{source}'"
                              f"{_did_you_mean(k, allowed)}.")
        for k in src.required:
            if k not in item:
                errors.append(f"{where}: source '{source}' needs '{k}'.")
        opts = {k: v for k, v in item.items() if k not in ("name", "source") + tuple(extra_keys)}
        for k in _PATH_KEYS:
            if k in opts and opts[k]:
                opts[k] = _resolve(base, opts[k])
        out.append((name, source, opts, item))
    return out


def _latlon(item, where, errors):
    lat = _num(item.get("lat"), f"{where}: lat", errors, -90, 90)
    lon = _num(item.get("lon"), f"{where}: lon", errors, -180, 180)
    return lat, lon


def parse_site(data, path="forecast.yaml"):
    """A :class:`Site` from the parsed forecast file *data* (a dict)."""
    from .sources import all_sources

    regs = all_sources()
    errors = []
    base = os.path.dirname(os.path.abspath(path))
    if not isinstance(data, dict):
        raise SiteFileError(f"{path}: the forecast file must be a set of 'key: value' lines.")
    for k in data:
        if k not in _TOP:
            errors.append(f"'{k}' is not a forecast-file setting{_did_you_mean(k, _TOP)}.")
    if not data.get("model"):
        errors.append("model: is missing — the MRRpy settings file (e.g. model: model.yaml).")
    model = _resolve(base, data.get("model") or "model.yaml")
    if data.get("model") and not os.path.isfile(model):
        errors.append(f"model: file not found: {model}")

    site = Site(path=os.path.abspath(path), name=str(data.get("name") or "Flood forecast"),
                model=model, folder=_resolve(base, data.get("folder") or "."))
    for key in ("every_minutes", "forecast_hours", "look_back_hours", "warm_up_days",
                "station_silent_minutes", "keep_cycles_days", "keep_states_days"):
        if key in data:
            v = _num(data[key], key, errors, lo=0)
            if v is not None:
                setattr(site, key, int(v) if key == "every_minutes" else v)
    for key in ("workers", "threads_per_run"):
        if key in data:
            v = _num(data[key], key, errors, lo=0)
            if v is not None:
                setattr(site, key, int(v))
    if site.forecast_hours <= 0:
        errors.append("forecast_hours must be more than 0.")
    if "display_timezone" in data:
        site.display_timezone = str(data["display_timezone"])
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(site.display_timezone)
        except Exception:
            errors.append(f"display_timezone '{site.display_timezone}' is not a time zone "
                          "name (e.g. Asia/Kathmandu, UTC).")
    if "missing_rain" in data:
        site.missing_rain = str(data["missing_rain"])
        if site.missing_rain not in ("forecast_model", "zero"):
            errors.append("missing_rain must be forecast_model or zero.")
    if "no_rain_version" in data:
        site.no_rain_version = bool(data["no_rain_version"])

    for name, source, opts, item in _entries(data.get("rain_stations"), "rain_stations",
                                             regs["station"], base, errors):
        site.rain_stations.append(Entry(name, source, opts))
    for name, source, opts, item in _entries(data.get("rain_forecasts"), "rain_forecasts",
                                             regs["forecast"], base, errors):
        if name == NO_RAIN:
            errors.append(f"rain_forecasts: '{NO_RAIN}' is the name of the no-rain version; "
                          "pick another name.")
        site.rain_forecasts.append(Entry(name, source, opts))
    for name, source, opts, item in _entries(data.get("inflows"), "inflows", regs["inflow"],
                                             base, errors, extra_keys=("lat", "lon", "ahead",
                                                                       "recede_hours")):
        lat, lon = _latlon(item, f"inflows '{name}'", errors)
        ahead = str(item.get("ahead", "hold"))
        if ahead not in ("hold", "recede"):
            errors.append(f"inflows '{name}': ahead must be hold or recede.")
        site.inflows.append(Inflow(name, source, opts, lat=lat or 0.0, lon=lon or 0.0,
                                   ahead=ahead,
                                   recede_hours=float(item.get("recede_hours", 24.0))))

    if not site.rain_stations and not site.rain_forecasts:
        errors.append("List at least one rain station or rain forecast.")
    names = [f.name for f in site.rain_forecasts]
    if "main_forecast" in data:
        site.main_forecast = str(data["main_forecast"])
        if site.main_forecast not in names:
            errors.append(f"main_forecast '{site.main_forecast}' is not one of the "
                          f"rain_forecasts{_did_you_mean(site.main_forecast, names)} "
                          f"({', '.join(names) or 'none listed'}).")
    elif names:
        site.main_forecast = names[0]
    if data.get("past_rain_forecast") is not None:
        site.past_rain_forecast = str(data["past_rain_forecast"])
        if site.past_rain_forecast not in names:
            errors.append(f"past_rain_forecast '{site.past_rain_forecast}' is not one of the "
                          f"rain_forecasts{_did_you_mean(site.past_rain_forecast, names)} "
                          f"({', '.join(names) or 'none listed'}).")

    seen = set()
    for i, item in enumerate(data.get("hydrograph_points") or [], 1):
        where = f"hydrograph_points #{i}"
        if not isinstance(item, dict):
            errors.append(f"{where} must be a set of keys, e.g. {{name: …, lat: …, lon: …}}.")
            continue
        for k in item:
            if k not in _POINT:
                errors.append(f"{where}: '{k}' is not a point setting{_did_you_mean(k, _POINT)}.")
        name = str(item.get("name") or f"Point {i}")
        if slug(name) in seen:
            errors.append(f"hydrograph point '{name}' appears twice; names must differ.")
        seen.add(slug(name))
        lat, lon = _latlon(item, f"hydrograph point '{name}'", errors)
        warn = item.get("warning_m3s")
        danger = item.get("danger_m3s")
        warn = _num(warn, f"'{name}': warning_m3s", errors, lo=0) if warn is not None else None
        danger = _num(danger, f"'{name}': danger_m3s", errors, lo=0) if danger is not None else None
        if warn is not None and danger is not None and danger < warn:
            errors.append(f"hydrograph point '{name}': danger_m3s is below warning_m3s.")
        site.hydrograph_points.append(Point(name, lat or 0.0, lon or 0.0, warn, danger,
                                            int(item.get("snap_radius_cells", 2))))

    points = [p.name for p in site.hydrograph_points]
    for name, source, opts, item in _entries(data.get("river_gauges"), "river_gauges",
                                             regs["level"], base, errors, extra_keys=("point",)):
        point = str(item.get("point") or "")
        if not point:
            errors.append(f"river_gauges '{name}' needs point: the hydrograph point it is "
                          f"drawn under ({', '.join(points) or 'none listed'}).")
        elif point not in points:
            errors.append(f"river_gauges '{name}': point '{point}' is not one of the "
                          f"hydrograph_points{_did_you_mean(point, points)} "
                          f"({', '.join(points) or 'none listed'}).")
        elif any(g.point == point for g in site.river_gauges):
            errors.append(f"river_gauges '{name}': point '{point}' already has a gauge.")
        site.river_gauges.append(RiverGauge(name, source, opts, point=point))

    seen = set()
    for i, item in enumerate(data.get("flood_areas") or [], 1):
        where = f"flood_areas #{i}"
        if not isinstance(item, dict):
            errors.append(f"{where} must be a set of keys, e.g. {{name: …, box: […]}}.")
            continue
        for k in item:
            if k not in _AREA:
                errors.append(f"{where}: '{k}' is not an area setting{_did_you_mean(k, _AREA)}.")
        name = str(item.get("name") or f"Area {i}")
        if slug(name) in seen:
            errors.append(f"flood area '{name}' appears twice; names must differ.")
        seen.add(slug(name))
        box = item.get("box")
        ok = isinstance(box, (list, tuple)) and len(box) == 4
        if ok:
            try:
                box = tuple(float(v) for v in box)
                ok = box[0] < box[2] and box[1] < box[3]
            except (TypeError, ValueError):
                ok = False
        if not ok:
            errors.append(f"flood area '{name}': box must be [west, south, east, north] in "
                          "degrees, west < east and south < north.")
            box = (0.0, 0.0, 0.0, 0.0)
        method = str(item.get("method", "hand"))
        if method not in ("hand", "2d"):
            errors.append(f"flood area '{name}': method must be hand or 2d.")
        dem = str(item.get("dem", "auto"))
        if dem.lower().endswith((".tif", ".tiff")):
            dem = _resolve(base, dem)
        scale = item.get("dem_scale_m")
        site.flood_areas.append(Area(name, box, method, dem,
                                     float(scale) if scale is not None else None))

    bm = data.get("basin_map")
    if bm in ("2d", "hand"):
        bm = {"method": bm}
    if bm:
        if not isinstance(bm, dict):
            errors.append("basin_map must be 2d, hand or a set of keys, e.g. "
                          "{method: 2d, boxes: auto}.")
            bm = {}
        for k in bm:
            if k not in _BASIN_MAP:
                errors.append(f"basin_map: '{k}' is not a basin map setting"
                              f"{_did_you_mean(k, _BASIN_MAP)}.")
        b = BasinMap()
        b.name = str(bm.get("name", b.name))
        if slug(b.name) in seen:
            errors.append(f"basin_map: its name '{b.name}' is also a flood area's.")
        b.method = str(bm.get("method", b.method))
        if b.method not in ("hand", "2d"):
            errors.append("basin_map: method must be 2d or hand.")
        boxes = bm.get("boxes", "auto")
        if boxes != "auto" and not (isinstance(boxes, (list, tuple)) and len(boxes) == 2
                                    and all(isinstance(v, int) and v >= 1 for v in boxes)):
            errors.append("basin_map: boxes must be auto or [west–east, south–north], "
                          "e.g. [3, 3].")
            boxes = "auto"
        b.boxes = boxes if boxes == "auto" else [int(v) for v in boxes]
        if b.method == "hand" and "boxes" not in bm:
            b.boxes = [1, 1]                       # HAND is quick on the whole basin at once
                                                   # (at 30 m; a 1 m DEM needs boxes)
        b.overlap_km = _num(bm.get("overlap_km", b.overlap_km), "basin_map: overlap_km",
                            errors, lo=0)
        b.dem = str(bm.get("dem", b.dem))
        if b.dem.lower().endswith((".tif", ".tiff")):
            b.dem = _resolve(base, b.dem)
        if bm.get("dem_scale_m") is not None:
            b.dem_scale_m = _num(bm["dem_scale_m"], "basin_map: dem_scale_m", errors, lo=1)
        for key in ("box_cells", "workers", "threads"):
            if key in bm:
                v = bm[key]
                if not isinstance(v, int) or v < 0 or (key == "box_cells" and v < 10_000):
                    errors.append(f"basin_map: {key} must be a whole number"
                                  + (" of at least 10000" if key == "box_cells" else ", 0 or more")
                                  + ".")
                else:
                    setattr(b, key, v)
        site.basin_map = b

    web = data.get("website") or {}
    if not isinstance(web, dict):
        errors.append("website must be a set of keys (title, publish).")
        web = {}
    for k in web:
        if k not in _WEBSITE:
            errors.append(f"website: '{k}' is not a website setting{_did_you_mean(k, _WEBSITE)}.")
    pub = web.get("publish")
    if pub is not None:
        if not isinstance(pub, dict) or not (set(pub) <= {"git", "command"}) or not pub:
            errors.append("website: publish must be {git: {remote: …}} or {command: \"…\"}.")
        elif "git" in pub and not (isinstance(pub["git"], dict) and pub["git"].get("remote")):
            errors.append("website: publish: git needs remote: (e.g. "
                          "git@github.com:USER/REPO.git).")
    site.website = dict(web, title=str(web.get("title") or site.name))

    if site.model and os.path.isfile(site.model) and not errors:
        errors += check_model(site)
    if errors:
        raise SiteFileError(f"{path} has {len(errors)} problem(s):\n  - " + "\n  - ".join(errors))
    return site


def check_model(site):
    """Problems with the model settings for a live forecast (a list of strings)."""
    from MRRpy.config import Config
    errors = []
    try:
        cfg = Config.from_file(site.model)
    except Exception as exc:                       # noqa: BLE001 — reported in words
        return [f"model: {site.model} could not be read: {exc}"]
    if cfg.ROUTING_SCHEME == "dynamic":
        errors.append("model: ROUTING_SCHEME 'dynamic' cannot carry on from a saved state "
                      "yet; use kinematic, diffusive_implicit or muskingum.")
    if cfg.RUNOFF_SOURCE == "scs_cn":
        errors.append("model: RUNOFF_SOURCE 'scs_cn' adds up rain from the start of the run "
                      "and never recovers, so it cannot run for months; use 'physical'.")
    if cfg.RUNOFF_SOURCE in ("raster",):
        errors.append("model: RUNOFF_SOURCE 'raster' reads runoff maps tied to one storm; "
                      "use 'physical' or 'coefficient'.")
    if (cfg.RUNOFF_SOURCE == "physical" and "saturation_excess" in (cfg.RUNOFF_MECHANISMS or [])
            and getattr(cfg, "VSA_PER_POLYGON", True)):
        errors.append("model: saturation excess keeps one soil store per rain point, and each "
                      "forecast has its own rain points; set VSA_PER_POLYGON: false.")
    interval = float(cfg.OUTPUT_INTERVAL_SECONDS or cfg.TIME_STEP_SECONDS)
    if 3600.0 % interval:
        errors.append(f"model: OUTPUT_INTERVAL_SECONDS ({interval:g}) must divide an hour "
                      "(e.g. 600 or 900), so states can be saved on the hour.")
    return errors


def load_site(path):
    """Read and check a forecast file → :class:`Site` (raises SiteFileError)."""
    import yaml
    if not os.path.isfile(str(path)):
        raise SiteFileError(f"forecast file not found: {path}")
    with open(path, encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            raise SiteFileError(f"{path} is not valid YAML: {exc}") from None
    return parse_site(data, path)


TEMPLATE = """\
# Forecast file for MRRpy's live flood forecast.
# Check it with:  lynch-forecast check -f forecast.yaml
# Times are UTC unless a setting says otherwise.

name: My river flood forecast
model: model.yaml              # the usual MRRpy settings (DEM, outlet, runoff, routing)
folder: .                      # archive/, states/, static/, cycles/, site/ go here
display_timezone: UTC          # times on the web page, e.g. America/Chicago
every_minutes: 30              # a cycle every 30 minutes (lynch-forecast serve)
forecast_hours: 48             # how far ahead
look_back_hours: 12            # each cycle runs the last 12 h of station rain again
warm_up_days: 30               # setup runs this much past rain to wet the soil
missing_rain: forecast_model   # no station reported: use the main forecast model, or zero

rain_stations:                 # as many as you have
  - {name: My gauge, source: csv, path: gauge.csv, lat: 27.57, lon: 85.49}
  # - {name: AWS at Roshi, source: alertnepal, station: 496}

rain_forecasts:                # each one is run as its own version
  - {name: ECMWF, source: open_meteo, model: ecmwf_ifs025}
  - {name: GFS,   source: open_meteo, model: gfs_seamless}
  - {name: ICON,  source: open_meteo, model: icon_seamless}
main_forecast: ECMWF           # this one drives the flood maps
# past_rain_forecast: HRRR     # no stations? this forecast's own past is the rain before now
no_rain_version: true          # also: what if no more rain falls?

inflows: []                    # rivers entering at the model edge, e.g.
  # - {name: Upstream, source: csv, path: https://example.org/q.csv, lat: …, lon: …,
  #    ahead: hold}            # after the last reading: hold, or recede

hydrograph_points:             # any number; warning/danger levels are optional
  - {name: Town bridge, lat: 27.60, lon: 85.52, warning_m3s: 150, danger_m3s: 300}

river_gauges: []               # measured water level, drawn under a point's hydrograph
  # - {name: Town gauge, source: csv, path: https://example.org/level.csv, point: Town bridge}
  # - {name: Lynch Creek at Jackson, source: usgs, site: "02486100", point: Town bridge}

flood_areas:                   # any number; box = [west, south, east, north]
  - {name: Town, box: [85.50, 27.58, 85.54, 27.61], method: hand}

# basin_map: {method: 2d, boxes: auto}   # a flood map of the whole basin, drawn in
                                         # overlapping boxes side by side

website:
  title: My river flood forecast (experimental)
  # publish: {git: {remote: git@github.com:USER/REPO.git}}
  # publish: {command: "rsync -a {site}/ host:/var/www/flood/"}
"""


def template():
    """The commented forecast file that ``lynch-forecast new`` writes."""
    return TEMPLATE
