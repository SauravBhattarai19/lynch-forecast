#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_dem.py — the routing DEM of a US place: USGS 3DEP 1/3 arc-second (~10 m),
with the NHDPlus creeks burned in.

Why burn: on a lidar DEM, road and railway embankments stand across the creeks
where the water really goes through a culvert or under a bridge.  On Lynch Creek
the bare 3DEP DEM sends the creek north along a ditch below the USGS gauge, so the
basin above the Pearl came out as 352 km² instead of 38 km².  Each NHDPlus line is
put through the lowest ground within ``--snap`` cells of it, made to run downhill
all the way, and lowered ``--burn`` m below that.  Slopes along the creeks stay
those of the DEM.  Only the routing uses this DEM: the flood maps are drawn on the
3DEP 1 m lidar as it is.

    python scripts/make_dem.py --box -90.31 32.255 -90.165 32.365 --crs EPSG:32615 \\
        --comid 15756806 --out places/lynch/dem/lynch_3dep10m_burned.tif

The DEM is read from the USGS's own copy (prd-tnm.s3.amazonaws.com, the 1x1 degree
tiles), the creeks from the USGS NLDI (all NHDPlus lines upstream of ``--comid``).
No Earth Engine and no sign-in.  Writes the DEM, the creek lines it burned
(``nhdplus_flowlines.geojson``) and ``dem_source.json`` beside it.
"""

import argparse
import json
import math
import os
import urllib.request
from datetime import datetime, timezone

import numpy as np

TILE = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
        "{t}/USGS_13_{t}.tif")
NLDI = ("https://api.water.usgs.gov/nldi/linked-data/comid/{comid}/navigation/UT/"
        "flowlines?distance={km}")
NODATA = -9999.0


def tiles(box):
    """The 1x1 degree 3DEP tile names covering *box* (west, south, east, north)."""
    w, s, e, n = box
    out = []
    for top in range(math.floor(s) + 1, math.ceil(n) + 1):
        for left in range(math.floor(w), math.ceil(e)):
            ns = f"n{top:02d}" if top >= 0 else f"s{-top:02d}"
            ew = f"w{-left:03d}" if left < 0 else f"e{left:03d}"
            out.append(ns + ew)
    return out


def read_3dep(box, crs, res):
    """3DEP 1/3 arc-second over *box*, reprojected to *crs* at *res* m (bilinear)."""
    import rasterio
    from rasterio.merge import merge
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    srcs = [rasterio.open("/vsicurl/" + TILE.format(t=t)) for t in tiles(box)]
    pad = 0.005
    mosaic, tr = merge(srcs, bounds=(box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
                       nodata=NODATA)
    src_crs = srcs[0].crs
    for s in srcs:
        s.close()
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", crs, *box, densify_pts=21)
    x0, y0 = math.floor(x0 / res) * res, math.floor(y0 / res) * res
    x1, y1 = math.ceil(x1 / res) * res, math.ceil(y1 / res) * res
    w, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    dst_tr = from_origin(x0, y1, res, res)
    dem = np.full((h, w), NODATA, dtype="float32")
    reproject(mosaic[0], dem, src_transform=tr, src_crs=src_crs, src_nodata=NODATA,
              dst_transform=dst_tr, dst_crs=crs, dst_nodata=NODATA,
              resampling=Resampling.bilinear)
    return dem, dst_tr


def flowlines(comid, km=200):
    """Every NHDPlus line upstream of *comid* (itself included), as a GeoDataFrame."""
    import geopandas as gpd
    req = urllib.request.Request(NLDI.format(comid=comid, km=km),
                                 headers={"User-Agent": "lynch-forecast make_dem.py"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    return gpd.GeoDataFrame.from_features(data["features"], crs=4326)


def burn(dem, transform, lines, snap=2, depth=3.0):
    """Burn *lines* (GeoDataFrame in the DEM's CRS, drawn downstream) into *dem*."""
    out = dem.copy()
    valid = dem > NODATA / 2
    ny, nx = dem.shape
    res = abs(transform.a)
    inv = ~transform
    burned = 0
    for geom in lines.geometry:
        for part in getattr(geom, "geoms", [geom]):
            n = max(2, int(math.ceil(part.length / (0.5 * res))) + 1)
            cells = []
            for d in np.linspace(0.0, part.length, n):
                p = part.interpolate(d)
                c, r = inv * (p.x, p.y)
                rc = (int(r), int(c))
                if 0 <= rc[0] < ny and 0 <= rc[1] < nx and (not cells or cells[-1] != rc):
                    cells.append(rc)
            level = np.inf
            for r, c in cells:                       # upstream → downstream
                win = dem[max(r - snap, 0):r + snap + 1, max(c - snap, 0):c + snap + 1]
                ok = win[win > NODATA / 2]
                if not ok.size or not valid[r, c]:
                    continue
                level = min(level, float(ok.min()))  # never uphill
                out[r, c] = min(out[r, c], level - depth)
                burned += 1
    return out, burned


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--box", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    ap.add_argument("--crs", required=True, help="e.g. EPSG:32615")
    ap.add_argument("--res", type=float, default=10.0, help="cell size, m (default 10)")
    ap.add_argument("--comid", required=True, help="NHDPlus COMID of the creek at the outlet")
    ap.add_argument("--snap", type=int, default=2, help="cells either side to find the bed")
    ap.add_argument("--burn", type=float, default=3.0, help="m below the bed (default 3)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    import geopandas as gpd  # noqa: F401 — needed by flowlines()
    import rasterio
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    print(f"3DEP 1/3 arc-second tiles {tiles(a.box)} → {a.crs} at {a.res:g} m …")
    dem, tr = read_3dep(a.box, a.crs, a.res)
    print(f"  {dem.shape[1]} × {dem.shape[0]} cells, {np.mean(dem > NODATA / 2):.1%} with data")
    lines = flowlines(a.comid).to_crs(a.crs)
    print(f"NHDPlus: {len(lines)} lines upstream of COMID {a.comid}, "
          f"{lines.length.sum() / 1000:.1f} km")
    out, n = burn(dem, tr, lines, a.snap, a.burn)
    print(f"Burned {n} cells ({a.burn:g} m below the lowest ground within {a.snap} cells)")
    profile = {"driver": "GTiff", "height": out.shape[0], "width": out.shape[1], "count": 1,
               "dtype": "float32", "crs": a.crs, "transform": tr, "nodata": NODATA,
               "compress": "deflate", "predictor": 3, "tiled": True}
    with rasterio.open(a.out, "w", **profile) as dst:
        dst.write(out, 1)
    folder = os.path.dirname(os.path.abspath(a.out))
    lines.to_crs(4326).to_file(os.path.join(folder, "nhdplus_flowlines.geojson"),
                               driver="GeoJSON")
    with open(os.path.join(folder, "dem_source.json"), "w") as f:
        json.dump({"dem": os.path.basename(a.out), "made_utc": datetime.now(timezone.utc)
                   .strftime("%Y-%m-%d %H:%M"), "source": [TILE.format(t=t) for t in tiles(a.box)],
                   "box_wgs84": a.box, "crs": a.crs, "cell_m": a.res,
                   "creeks": NLDI.format(comid=a.comid, km=200), "lines": int(len(lines)),
                   "snap_cells": a.snap, "burn_m": a.burn, "cells_burned": int(n)}, f, indent=1)
    print(f"Written: {a.out}")


if __name__ == "__main__":
    main()
