# -*- coding: utf-8 -*-
"""
newplace.py — ``lynch-forecast new FOLDER --name … --outlet LAT,LON``: a new place
from just its outlet.

It writes a model file with the physics of the Roshi Khola forecast (90 m bare-earth
terrain, Green-Ampt infiltration + impervious runoff, semi-implicit diffusive
routing) and a forecast file with the three global forecast models, the outlet as
the first hydrograph point and a 2-D flood map of the whole basin.  The DEM box
starts small around the outlet: ``setup`` widens it until the basin fits.  With a
places file, the new place is added to it, and a running ``serve`` sets it up by
itself.
"""

import os
import re

NEPAL = (80.0, 26.3, 88.3, 30.5)          # lon/lat box: Nepal's own settings apply

MODEL = """\
# Model settings of {name} (MRRpy), written by 'lynch-forecast new'.
# The same physics as the Roshi Khola forecast. Rain comes from forecast.yaml.
# Explain any setting with:  MRRpy explain NAME

# ── Where ─────────────────────────────────────────────────────────────────
OUTPUT_POINT: [{lat}, {lon}]   # the outlet: lat, lon on the river
# A box around the basin (west, south, east, north). 'lynch-forecast setup'
# widens it by itself until the whole basin above the outlet fits.
DEM_BOUNDS_WGS84: [{w}, {s}, {e}, {n}]
DEM_PATH: ''                  # '' = download the DEM below with Earth Engine
DEM_SOURCE: fabdem            # bare earth (buildings and trees taken off)
DEM_SCALE_M: 90
TARGET_CRS_EPSG: {epsg}     # the UTM zone of the outlet
MODEL_AREA: watershed
DELINEATION_ENGINE: pyflwdir
OUTPUT_DIR: static
GEE_PROJECT: {project}{project_note}

# ── Runoff: impervious ground + Green-Ampt infiltration with recovery ───────
PRECIP_METHOD: idw
RUNOFF_SOURCE: physical
RUNOFF_MECHANISMS: [impervious, infiltration_excess]
IMPERVIOUS_SOURCE: lcz
GA_KSAT_SOURCE: gee           # HiHydroSoil v2.0
GA_KSAT_DEPTH_CM: 60
GA_SUCTION_SOURCE: texture
GA_RECOVERY: true
SOILGRIDS_DEPTH: b30
VSA_SD_SOURCE: gee
SERVES_SATELLITE: landsat
SERVES_SEARCH_WINDOW: 30

# ── Rivers ────────────────────────────────────────────────────────────────
MANNINGS_N_SOURCE: lcz
MANNINGS_N: 0.09
MANNINGS_N_CHANNEL: 0.035
CHANNEL_QBF_M3S: {qbf}{qbf_note}
CHANNEL_GEOMETRY: discharge
CHANNEL_SUBGRID: true
CHANNEL_MIN_AREA_KM2: 10.0
ROUTING_SCHEME: diffusive_implicit
ADAPTIVE_TIMESTEP: true
IMPLICIT_CFL_TARGET: 2.0
IMPLICIT_MAX_ITERS: 8
IMPLICIT_TOL: 0.0001
OUTPUT_INTERVAL_SECONDS: 600
MASS_BALANCE_REPORT: true
BACKEND: cpu
"""

FORECAST = """\
# Forecast file of {name}, written by 'lynch-forecast new'.
# Check it with:  lynch-forecast check -f {rel}
# Times are UTC unless a setting says otherwise.

name: {name} flood forecast
model: model.yaml
folder: .
display_timezone: {timezone}
every_minutes: 30
forecast_hours: 72
look_back_hours: 12
warm_up_days: 30
missing_rain: forecast_model   # no station reported: use the main forecast model's rain

# Live rain stations. None (as written): a FORECAST-ONLY place. The rain before
# now is the main forecast model's own, and the page says so. Add stations here
# when the place has some, as many as there are:
rain_stations: []
  # - {{name: My gauge, source: csv, path: https://example.org/gauge.csv, lat: …, lon: …}}
  # - {{name: AWS at Roshi, source: rts, station: 468}}

rain_forecasts:                # each one is a version of the forecast
  - {{name: ECMWF, source: open_meteo, model: ecmwf_ifs025}}
  - {{name: GFS,   source: open_meteo, model: gfs_seamless}}
  - {{name: ICON,  source: open_meteo, model: icon_seamless}}{extra_forecasts}
main_forecast: ECMWF           # this one draws the flood maps
no_rain_version: true          # also: what if no more rain falls?

hydrograph_points:             # add towns, bridges, gauges; levels are optional
  - {{name: Outlet, lat: {lat}, lon: {lon}}}
  # - {{name: Town bridge, lat: …, lon: …, warning_m3s: 150, danger_m3s: 300}}

flood_areas: []                # close-up flood maps; box = [west, south, east, north]
  # - {{name: Town, box: [85.50, 27.58, 85.54, 27.61], method: 2d}}

basin_map: {{method: 2d, boxes: auto}}   # a flood map of every river in the basin

website:
  title: {name} flood forecast (experimental)
"""


def utm_epsg(lat, lon):
    zone = int((lon + 180.0) // 6.0) % 60 + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def in_nepal(lat, lon):
    w, s, e, n = NEPAL
    return w <= lon <= e and s <= lat <= n


def parse_outlet(text):
    try:
        lat, lon = (float(x) for x in str(text).replace(" ", "").split(","))
    except ValueError:
        raise ValueError(f"--outlet must be LAT,LON in degrees, e.g. 27.44,85.81 (not {text!r})")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"--outlet {lat},{lon} is not a place on Earth (LAT,LON)")
    return lat, lon


def known_project(places_path):
    """An Earth Engine project to reuse: the environment's, else another place's."""
    for key in ("GEE_PROJECT", "EARTHENGINE_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        if os.environ.get(key):
            return os.environ[key]
    if places_path and os.path.isfile(places_path):
        try:
            from MRRpy.config import Config
            from .places import load_portal, sites
            for _, site in sites(load_portal(places_path)):
                project = Config.from_file(site.model).GEE_PROJECT
                if project:
                    return project
        except Exception:                           # noqa: BLE001 — only a convenience
            pass
    return None


def write_place(folder, name, outlet, timezone=None, project=None, half_box_deg=0.15):
    """Write FOLDER/model.yaml and FOLDER/forecast.yaml → their paths."""
    lat, lon = outlet
    nepal = in_nepal(lat, lon)
    os.makedirs(folder, exist_ok=True)
    d = half_box_deg
    model = MODEL.format(
        name=name, lat=round(lat, 5), lon=round(lon, 5),
        w=round(lon - d, 4), s=round(lat - d, 4), e=round(lon + d, 4), n=round(lat + d, 4),
        epsg=utm_epsg(lat, lon), project=project or "your-earth-engine-project",
        project_note="" if project else "   # ← put your Earth Engine project here",
        qbf="wecs_nepal" if nepal else "auto",
        qbf_note="   # Nepal's regional 2-year flood formula" if nepal else
                 "   # 2-year flood from HydroATLAS at the outlet")
    tz = timezone or ("Asia/Kathmandu" if nepal else "UTC")
    paths = (os.path.join(folder, "model.yaml"), os.path.join(folder, "forecast.yaml"))
    with open(paths[0], "w", encoding="utf-8") as f:
        f.write(model)
    with open(paths[1], "w", encoding="utf-8") as f:
        f.write(FORECAST.format(name=name, lat=round(lat, 5), lon=round(lon, 5), timezone=tz,
                                rel=os.path.relpath(paths[1]),
                                extra_forecasts=("\n  - {name: alertnepal, source: "
                                                 "alertnepal_forecast}   # Nepal's own "
                                                 "hourly forecast" if nepal else "")))
    return paths


PLACES = """\
# The places of this flood-forecast site, written by 'lynch-forecast new'.
# 'lynch-forecast serve' (or the service) reads it again every round: add a
# place's forecast.yaml here and it is set up and published by itself.
title: Flood forecasts (experimental)
notice: Computer models run automatically; not an official warning.
every_minutes: 30
places:
# Where the pages go (else they are only written to public/):
# publish: {git: {remote: https://github.com/USER/REPO.git, state: false}}
"""


def add_to_places(places_path, forecast_path):
    """Add *forecast_path* to the places list of *places_path* (written first if
    missing), keeping its comments.  → True if added (False: already there)."""
    import yaml
    rel = os.path.relpath(forecast_path, os.path.dirname(os.path.abspath(places_path)))
    rel = rel.replace(os.sep, "/")
    if not os.path.isfile(places_path):
        with open(places_path, "w", encoding="utf-8") as f:
            f.write(PLACES)
    with open(places_path, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"(?m)^places\s*:\s*\[\s*\]\s*$", "places:", text)
    data = yaml.safe_load(text) or {}
    items = data.get("places") or []
    listed = {str(x if isinstance(x, str) else (x or {}).get("file", "")) for x in items}
    if rel in listed:
        return False
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^places\s*:\s*(#.*)?$", ln)), None)
    if start is None:
        if "places" in data:
            raise ValueError(f"add '- {rel}' to the places list in {places_path} by hand "
                             "(it is not written as one item a line)")
        lines += ["places:", f"  - {rel}"]
    else:
        end, indent = start, "  "
        for i in range(start + 1, len(lines)):
            m = re.match(r"^(\s*)-\s", lines[i])
            if m:
                end, indent = i, m.group(1)
            elif lines[i].strip() and not lines[i].lstrip().startswith("#"):
                break
        lines.insert(end + 1, f"{indent}- {rel}")
    with open(places_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True
