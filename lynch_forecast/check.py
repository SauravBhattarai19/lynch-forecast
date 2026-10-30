# -*- coding: utf-8 -*-
"""
check.py — ``lynch-forecast check``: try every feed and show where each point
falls on the model's rivers, before the first cycle.

For each hydrograph point and inflow it prints the river cell the model will
use (the cell of largest drainage within ``snap_radius_cells``), its drainage
area and how far it moved, and writes ``check_map.html``: the basin, the
stations, each point as given and as used, and the flood areas.
"""

import json
import os
from datetime import timedelta

import numpy as np

from .cycle import ceil_hour, floor_hour, model_config, utcnow
from .sources import Context, all_sources


def snap_point(lat, lon, faccum, mask, transform, crs, radius):
    """(row, col, drainage cells, moved metres) of the river cell used for a point."""
    from pyproj import Transformer
    x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
    col, row = ~transform * (x, y)
    r0, c0 = int(np.floor(row)), int(np.floor(col))
    h, w = faccum.shape
    best = None
    for r in range(max(0, r0 - radius), min(h, r0 + radius + 1)):
        for c in range(max(0, c0 - radius), min(w, c0 + radius + 1)):
            if mask[r, c] and np.isfinite(faccum[r, c]):
                if best is None or faccum[r, c] > faccum[best]:
                    best = (r, c)
    if best is None:
        return None
    cx, cy = transform * (best[1] + 0.5, best[0] + 0.5)
    return best[0], best[1], float(faccum[best]), float(np.hypot(cx - x, cy - y)), (cx, cy)


def check_site(site, log=print, now=None):
    """Try the feeds and the points → dict of results (also printed)."""
    import rasterio
    from pyproj import Transformer
    from MRRpy.core.io_utils import routing_grid_crs

    now = (now or utcnow()).replace(second=0, microsecond=0)
    regs = all_sources()
    cfg = model_config(site)
    ready = os.path.isfile(cfg.ROUTING_FLOW_ACCUM_PATH)
    ctx = Context(now=now, cfg=cfg, cache_dir=site.dir("forecasts_cache"), log=lambda *_: None,
                  start=floor_hour(now), end=ceil_hour(now + timedelta(hours=site.forecast_hours)))
    out = {"stations": [], "forecasts": [], "points": [], "inflows": []}
    log("Rain stations")
    for e in site.rain_stations:
        try:
            got = regs["station"][e.source].func(e.spec(), ctx)
            for st in got:
                last = st.rain.index.max() if len(st.rain) else None
                age = f", {(now - last).total_seconds() / 60:.0f} min ago" if last is not None else ""
                log(f"  ok   {st.name}: {len(st.rain)} readings, last "
                    f"{last:%Y-%m-%d %H:%M} UTC{age}" if last is not None else
                    f"  ok   {st.name}: no readings yet")
                out["stations"].append({"name": st.name, "lat": st.lat, "lon": st.lon,
                                        "ok": True})
        except Exception as exc:                              # noqa: BLE001
            log(f"  FAIL {e.name}: {exc}")
            out["stations"].append({"name": e.name, "ok": False, "error": str(exc)})
    log("Rain forecasts")
    for e in site.rain_forecasts:
        if not ready and e.source == "open_meteo":
            log(f"  skip {e.name}: needs the basin outline — run 'lynch-forecast setup' first")
            continue
        try:
            fc = regs["forecast"][e.source].func(e.spec(), ctx)
            mean = fc.rain.mean(axis=1)                       # mm in the step ending at t
            ahead = (mean.index > now) & (mean.index <= now + timedelta(hours=site.forecast_hours))
            log(f"  ok   {e.name}: {len(fc.points)} rain points, to "
                f"{fc.rain.index.max():%Y-%m-%d %H:%M} UTC, {mean[ahead].sum():.1f} mm on average "
                f"in the next {site.forecast_hours:g} h")
            out["forecasts"].append({"name": e.name, "ok": True,
                                     "points": fc.points.to_dict(orient="records")})
        except Exception as exc:                              # noqa: BLE001
            log(f"  FAIL {e.name}: {exc}")
            out["forecasts"].append({"name": e.name, "ok": False, "error": str(exc)})
    if not ready:
        log("Points: run 'lynch-forecast setup' first (it prepares the rivers).")
        return out
    with rasterio.open(cfg.ROUTING_FLOW_ACCUM_PATH) as src:
        fac = src.read(1, masked=True).filled(np.nan).astype(float)
        tr, crs = src.transform, routing_grid_crs(cfg)
        cell_km2 = abs(tr.a * tr.e) / 1e6
    with rasterio.open(cfg.ROUTING_WATERSHED_MASK_PATH) as src:
        if src.transform == tr and src.shape == fac.shape:
            mask = src.read(1) > 0
        else:
            mask = np.isfinite(fac)
    back = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    log("Hydrograph points (the river cell used)")
    for kind, items in (("points", site.hydrograph_points), ("inflows", site.inflows)):
        for p in items:
            radius = getattr(p, "snap_radius_cells", 2)
            got = snap_point(p.lat, p.lon, fac, mask, tr, crs, radius)
            if got is None:
                log(f"  FAIL {p.name}: outside the modelled basin")
                out[kind].append({"name": p.name, "lat": p.lat, "lon": p.lon, "ok": False})
                continue
            r, c, cells, moved, (cx, cy) = got
            lon2, lat2 = back.transform(cx, cy)
            flag = "  (moved far: check the place)" if moved > 3 * abs(tr.a) else ""
            log(f"  ok   {p.name}: drainage {cells * cell_km2:,.1f} km², moved {moved:,.0f} m"
                f"{flag}")
            out[kind].append({"name": p.name, "lat": p.lat, "lon": p.lon, "ok": True,
                              "used_lat": lat2, "used_lon": lon2, "moved_m": moved,
                              "area_km2": cells * cell_km2})
    path = write_check_map(site, cfg, out)
    log(f"Map: {path}")
    out["map"] = path
    return out


def write_check_map(site, cfg, out):
    import geopandas as gpd
    basin = json.loads(gpd.read_file(cfg.WATERSHED_GEOJSON).to_crs(4326).to_json())
    payload = {"basin": basin, "stations": out["stations"], "points": out["points"],
               "inflows": out["inflows"],
               "areas": [{"name": a.name, "box": list(a.box)} for a in site.flood_areas]}
    html = CHECK_HTML.replace("__DATA__", json.dumps(payload)).replace(
        "__TITLE__", f"{site.name}: check")
    path = site.dir("check_map.html")
    with open(path, "w") as f:
        f.write(html)
    return path


CHECK_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>__TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>body{margin:0;font:15px system-ui,sans-serif}#map{height:80vh}
p,table{margin:8px 16px}td,th{border:1px solid #ccc;padding:2px 6px}</style></head><body>
<p><b>__TITLE__</b>. Squares: rain stations. Hollow circles: points as written in the
forecast file. Filled circles: the river cell the model uses (joined by a line). Boxes:
flood areas.</p><div id="map"></div><table id="t"><tr><th>Point</th><th>Drainage (km²)</th>
<th>Moved (m)</th></tr></table>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>var D=__DATA__,m=L.map('map');L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
{attribution:'© OpenStreetMap contributors'}).addTo(m);var g=L.geoJSON(D.basin,{style:{color:'#333',
fill:false}}).addTo(m);m.fitBounds(g.getBounds());D.stations.forEach(function(s){if(s.lat)
L.marker([s.lat,s.lon],{title:s.name}).bindTooltip('station '+s.name).addTo(m)});
D.points.concat(D.inflows).forEach(function(p){L.circleMarker([p.lat,p.lon],{radius:7,fill:false})
.bindTooltip(p.name+' (as given)').addTo(m);if(p.ok){L.circleMarker([p.used_lat,p.used_lon],
{radius:5,fillOpacity:1}).bindTooltip(p.name+' (used)').addTo(m);L.polyline([[p.lat,p.lon],
[p.used_lat,p.used_lon]]).addTo(m)}var r=document.createElement('tr');r.innerHTML='<td>'+p.name+
'</td><td>'+(p.ok?p.area_km2.toFixed(1):'outside')+'</td><td>'+(p.ok?Math.round(p.moved_m):'')+
'</td>';document.getElementById('t').appendChild(r)});D.areas.forEach(function(a){var b=a.box;
L.rectangle([[b[1],b[0]],[b[3],b[2]]],{fill:false,dashArray:'4 4'}).bindTooltip(a.name).addTo(m)});
</script></body></html>
"""
