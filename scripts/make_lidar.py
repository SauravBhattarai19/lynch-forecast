#!/usr/bin/env python
"""
The 1 m lidar DEM for the flood maps, from the USGS's own 3DEP 1 m tiles.

    python scripts/make_lidar.py --box -90.303 32.258 -90.174 32.362 --crs EPSG:32615 \
        --project MS_NatchezTrace_2016 --out places/lynch/dem/lynch_3dep1m.tif --also 4

Earth Engine's USGS/3DEP/1m mosaic holds only the newest survey of each place
(here MS_NRCS_East_2018, which covers 0.1 % of Lynch Creek) and gives 0 m where
it has none, so its 1 m downloads are mostly a 90 m pit.  This reads the tiles
of one survey from the National Map (prd-tnm on S3) instead, reprojects them to
the model's grid, and fills whatever that survey misses from other surveys, then
from the 1/3 arc-second (10 m) DEM.  ``--also 4`` writes a 4 m copy (mean of
the 1 m cells) beside it.  A note of what was used goes to ``<out>.json``.
"""

import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds

TNM = "https://tnmaccess.nationalmap.gov/api/v1/products"
DEM_10M = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
           "{tile}/USGS_13_{tile}.tif")
NODATA = -9999.0


def tiles(box):
    """The 1 m tiles over *box* (west, south, east, north) → [(project, url)]."""
    r = requests.get(TNM, params={"datasets": "Digital Elevation Model (DEM) 1 meter",
                                  "bbox": ",".join(f"{v:.5f}" for v in box), "max": 200},
                     timeout=120)
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        url = it["downloadURL"]
        if "/Projects/" in url and url.endswith(".tif"):
            out.append((url.split("/Projects/")[1].split("/")[0], url))
    return out


def paint(dst, transform, crs, url, resampling=Resampling.bilinear, tiles_dir=None):
    """Fill the NaN cells of *dst* from the raster at *url* (only what overlaps); a copy
    of it in *tiles_dir* is read instead when there is one."""
    local = os.path.join(tiles_dir, os.path.basename(url)) if tiles_dir else None
    path = local if local and os.path.isfile(local) else "/vsicurl/" + url
    part = np.full(dst.shape, np.nan, dtype="float32")
    with rasterio.open(path) as src:
        h, w = dst.shape
        x0, y1 = transform * (0, 0)
        x1, y0 = transform * (w, h)
        b = transform_bounds(crs, src.crs, x0, y0, x1, y1, densify_pts=21)
        win = rasterio.windows.from_bounds(*b, transform=src.transform)
        win = win.round_offsets().round_lengths().intersection(
            rasterio.windows.Window(0, 0, src.width, src.height))
        if win.width < 1 or win.height < 1:
            return 0
        a = src.read(1, window=win, masked=True).astype("float32").filled(np.nan)
        a[a < -1000] = np.nan
        reproject(a, part, src_transform=src.window_transform(win), src_crs=src.crs,
                  src_nodata=np.nan, dst_transform=transform, dst_crs=crs, dst_nodata=np.nan,
                  resampling=resampling)
    take = np.isnan(dst) & np.isfinite(part)
    dst[take] = part[take]
    return int(take.sum())


def write(path, a, transform, crs):
    prof = {"driver": "GTiff", "height": a.shape[0], "width": a.shape[1], "count": 1,
            "dtype": "float32", "crs": crs, "transform": transform, "nodata": NODATA,
            "compress": "deflate", "predictor": 3, "tiled": True, "blockxsize": 512,
            "blockysize": 512, "BIGTIFF": "IF_SAFER"}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(np.where(np.isfinite(a), a, NODATA).astype("float32"), 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--box", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    p.add_argument("--crs", default="EPSG:32615")
    p.add_argument("--project", default=None, help="the survey to prefer (e.g. MS_NatchezTrace_2016)")
    p.add_argument("--out", required=True)
    p.add_argument("--also", type=float, nargs="*", default=[], help="coarser copies [m], e.g. 4")
    p.add_argument("--dem10", default="n33w091", help="1/3 arc-second tile to fill the rest from")
    p.add_argument("--only-project", action="store_true",
                   help="use only --project, then the 10 m DEM (no other survey)")
    p.add_argument("--tiles-dir", default=None,
                   help="a folder of 1 m tiles already downloaded (curl them: windowed reads "
                        "over the web are slow for these files)")
    a = p.parse_args()

    x0, y0, x1, y1 = transform_bounds("EPSG:4326", a.crs, *a.box, densify_pts=21)
    x0, y0, x1, y1 = np.floor(x0), np.floor(y0), np.ceil(x1), np.ceil(y1)
    w, h = int(x1 - x0), int(y1 - y0)
    tr = from_origin(x0, y1, 1.0, 1.0)
    print(f"1 m grid {w} × {h} = {w * h / 1e6:.0f} M cells in {a.crs}")
    dem = np.full((h, w), np.nan, dtype="float32")

    found = tiles(a.box)
    projects = sorted({pr for pr, _ in found}, key=lambda pr: (pr != a.project, pr))
    if a.only_project:
        projects = [pr for pr in projects if pr == a.project]
    used = []
    for pr in projects:
        if not np.isnan(dem).any():
            break
        n = 0
        for _, url in [t for t in found if t[0] == pr]:
            n += paint(dem, tr, a.crs, url, tiles_dir=a.tiles_dir)
        share = 100.0 * n / dem.size
        print(f"  {pr}: {share:.2f} % of the cells")
        if n:
            used.append({"project": pr, "percent": round(share, 3),
                         "tiles": [u for q, u in found if q == pr]})
    gap = np.isnan(dem)
    filled = 0
    if gap.any():
        filled = paint(dem, tr, a.crs, DEM_10M.format(tile=a.dem10))
        print(f"  3DEP 1/3 arc-second (10 m), for the rest: {100.0 * filled / dem.size:.2f} %")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    write(a.out, dem, tr, a.crs)
    print(f"Wrote {a.out}: z {np.nanmin(dem):.1f}–{np.nanmax(dem):.1f} m, "
          f"{100.0 * np.isnan(dem).mean():.3f} % still empty")
    copies = []
    for res in a.also:
        k = int(round(res))
        hh, ww = h // k, w // k
        c = np.nanmean(dem[:hh * k, :ww * k].reshape(hh, k, ww, k), axis=(1, 3)).astype("float32")
        path = a.out.replace("1m", f"{k}m") if "1m" in a.out else a.out[:-4] + f"_{k}m.tif"
        write(path, c, from_origin(x0, y1, float(k), float(k)), a.crs)
        copies.append(os.path.basename(path))
        print(f"Wrote {path}: {ww} × {hh} cells at {k} m")
    with open(a.out[:-4] + ".json", "w") as f:
        json.dump({"dem": os.path.basename(a.out), "copies": copies,
                   "made_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                   "box_wgs84": a.box, "crs": a.crs, "cell_m": 1.0, "surveys": used,
                   "filled_from_10m_percent": round(100.0 * filled / dem.size, 3),
                   "source": "USGS 3DEP 1 m DEM tiles, The National Map (prd-tnm)"}, f, indent=1)


if __name__ == "__main__":
    main()
