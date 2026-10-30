# -*- coding: utf-8 -*-
"""
basinmap.py — one flood map of the whole basin, drawn in 2D as several boxes.

A 2D flood simulation of a whole basin on a 30 m DEM is large, and its time
step is set by the fastest water anywhere in it.  So the basin is covered with
boxes that overlap by ``overlap_km``; each box is a 2D flood map of its own
(MRRpy's ``make_flood_maps``: the routed rivers enter at its edges, the land
inside adds what the routing passed into its rivers), the boxes run side by
side, and the middle part of each box (its *core*, without the overlap) goes
into one map.  Boxes no river runs through are left out.

In the forecast file::

    basin_map: {method: 2d, boxes: auto}        # or boxes: [3, 3] (west–east, south–north)

``setup`` plans the boxes from the basin outline, downloads each box's DEM into
``static/basin_map/<box>/`` and notes which boxes are dry, so cycles run with
Earth Engine off.  The joined map is written like a flood area's
(``flood_depth_max.tif``, ``flood_velocity_max.tif``, ``flood_extent_max.geojson``,
``inundation_summary.json``) and shown on the page as the first flood map.

Checked on the Roshi rehearsal (28 Sept 2024 peak, FABDEM 30 m): 3 × 3 boxes,
45 s instead of 81 s for one box; within 500 m of a cut the depths match the
one-box map to 1 cm (median); the one box made unrealistic speeds in the gorge
below Mangaltar that the boxes mostly do not.
"""

import json
import math
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

import numpy as np

from .offline import earth_engine_blocked, thread_limit

NO_RIVER = "No river of the model runs"     # make_flood_maps: a box with no river in it


def plan_boxes(watershed_geojson, boxes="auto", overlap_km=1.5, cell_m=30.0,
               box_cells=250_000):
    """[{name, core, box}] (degrees, west/south/east/north) covering the basin."""
    import geopandas as gpd
    from shapely.geometry import box as sbox
    ws = gpd.read_file(watershed_geojson).to_crs(4326).union_all()
    w, s, e, n = ws.bounds
    lat = math.radians((s + n) / 2)
    width_m = (e - w) * 111_320 * math.cos(lat)
    height_m = (n - s) * 110_574
    if boxes == "auto":
        count = max(1, math.ceil(width_m * height_m / cell_m ** 2 / box_cells))
        nx = max(1, round(math.sqrt(count * width_m / max(height_m, 1.0))))
        ny = max(1, math.ceil(count / nx))
    else:
        nx, ny = int(boxes[0]), int(boxes[1])
    dx = overlap_km * 1000 / (111_320 * math.cos(lat))
    dy = overlap_km * 1000 / 110_574
    xs, ys = np.linspace(w, e, nx + 1), np.linspace(s, n, ny + 1)
    out = []
    for i in range(nx):
        for j in range(ny):
            core = [float(xs[i]), float(ys[j]), float(xs[i + 1]), float(ys[j + 1])]
            if not sbox(*core).intersects(ws):
                continue
            out.append({"name": f"box_{i + 1}_{j + 1}", "core": core,
                        "box": [max(w, core[0] - dx), max(s, core[1] - dy),
                                min(e, core[2] + dx), min(n, core[3] + dy)]})
    return out, (nx, ny)


def set_margin(margin_km):
    """The fine DEM is fetched *margin_km* around a map area (MRRpy's 3 km makes any
    box at 1 m over its 30 million cells).  For this process only."""
    if margin_km is not None:
        from MRRpy.core.inundation import finegrid
        finegrid.BUFFER_M = float(margin_km) * 1000.0


def _box_job(cfg_dict, box, out_dir, cache_dir, log_path, allow_ee, margin_km=None):
    """One box's 2D map, in its own process → small dict (dry: no river in it)."""
    import contextlib
    from MRRpy.config import Config
    from MRRpy.core.inundation import make_flood_maps
    set_margin(margin_km)
    t0 = time.time()
    res = {"ok": False, "dry": False, "seconds": 0.0, "error": None}
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as logf, contextlib.redirect_stdout(logf), \
            contextlib.redirect_stderr(logf), \
            (contextlib.nullcontext() if allow_ee else earth_engine_blocked()):
        try:
            cfg = Config.from_dict(cfg_dict)
            cfg.INUNDATION_AREA = list(box)
            make_flood_maps(cfg, log=lambda m: print(m, flush=True), out_dir=out_dir,
                            cache_dir=cache_dir)
            res["ok"] = True
        except ValueError as exc:
            if NO_RIVER in str(exc):
                res.update(ok=True, dry=True)
            else:
                traceback.print_exc()
                res["error"] = f"ValueError: {exc}"
        except IndexError as exc:
            # A box whose creeks all lack routed flow (a headwater edge): MRRpy has no
            # river level to map and fails writing the empty map.  Nothing floods there.
            if "write_outputs" in traceback.format_exc():
                print(f"  Basin map: no creek with routed flow in this box ({exc}); dry.")
                res.update(ok=True, dry=True)
            else:
                traceback.print_exc()
                res["error"] = f"IndexError: {exc}"
        except Exception as exc:                          # noqa: BLE001 — reported
            traceback.print_exc()
            res["error"] = f"{type(exc).__name__}: {exc}"
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _settings(site, cfg):
    """The model settings for the boxes' maps (2D or HAND, the basin map's DEM)."""
    bm = site.basin_map
    cfg.INUNDATION_METHOD = bm.method
    if bm.dem.lower().endswith((".tif", ".tiff")):
        cfg.INUNDATION_DEM, cfg.INUNDATION_DEM_PATH = "file", bm.dem
    else:
        cfg.INUNDATION_DEM = bm.dem
    if bm.dem_scale_m:
        cfg.INUNDATION_DEM_SCALE_M = bm.dem_scale_m
    cfg.INUNDATION_ANIMATION = False
    return cfg.to_dict()


def _run_boxes(site, cfg_dict, boxes, run_dir, allow_ee, log):
    """Every box side by side → {box name: result}."""
    bm = site.basin_map
    n = len(boxes)
    workers = bm.workers or n
    threads = bm.threads or max(1, (os.cpu_count() or 2) // min(n, workers))
    jobs = {b["name"]: (cfg_dict, b["box"], os.path.join(run_dir, b["name"]),
                        site.dir("static", "basin_map", b["name"]),
                        os.path.join(run_dir, b["name"] + ".log"), allow_ee, bm.margin_km)
            for b in boxes}
    results = {}
    with thread_limit(threads), ProcessPoolExecutor(min(n, workers),
                                                    mp_context=get_context("spawn")) as ex:
        futs = {name: ex.submit(_box_job, *args) for name, args in jobs.items()}
        for name, fut in futs.items():
            try:
                results[name] = fut.result()
            except Exception as exc:                      # the process itself died
                results[name] = {"ok": False, "dry": False, "seconds": 0.0, "error": str(exc)}
    bad = [k for k, r in results.items() if not r["ok"]]
    log(f"  Basin map: {n} boxes ({threads} threads each), "
        f"{sum(r['dry'] for r in results.values())} without a river"
        + (f", FAILED: {', '.join(bad)}" if bad else ""))
    return results


def plan_path(site):
    return site.dir("static", "basin_map", "boxes.json")


def prepare(site, run_dir, cfg, log=print):
    """At setup: plan the boxes, fetch each box's DEM (Earth Engine on), note dry boxes."""
    bm = site.basin_map
    boxes, (nx, ny) = plan_boxes(site.dir("static", "watershed.geojson"), bm.boxes,
                                 bm.overlap_km, bm.dem_scale_m or 30.0, bm.box_cells)
    log(f"  Basin map: {len(boxes)} boxes ({nx} × {ny}, {bm.overlap_km:g} km overlap) …")
    t0 = time.time()
    res = _run_boxes(site, _settings(site, cfg), boxes,
                     os.path.join(run_dir, "areas", "_basin_boxes"), True, log)
    bad = [k for k, r in res.items() if not r["ok"]]
    if bad:
        raise RuntimeError(f"basin map: boxes {', '.join(bad)} failed at setup; see "
                           f"{os.path.join(run_dir, 'areas', '_basin_boxes')}")
    for b in boxes:
        b["dry"] = res[b["name"]]["dry"]
    os.makedirs(os.path.dirname(plan_path(site)), exist_ok=True)
    with open(plan_path(site), "w") as f:
        json.dump({"grid": [nx, ny], "overlap_km": bm.overlap_km, "method": bm.method,
                   "boxes": boxes}, f, indent=1)
    log(f"  Basin map: ready in {time.time() - t0:.0f} s "
        f"({sum(not b['dry'] for b in boxes)} boxes with rivers)")


def draw(site, cfg, run_dir, out_dir, log=print):
    """In a cycle: every wet box, then the joined map in *out_dir* → summary dict."""
    if not os.path.isfile(plan_path(site)):
        raise RuntimeError("the basin map is not set up: run 'lynch-forecast setup' again")
    with open(plan_path(site)) as f:
        plan = json.load(f)
    boxes = [b for b in plan["boxes"] if not b.get("dry")]
    work = os.path.join(run_dir, "areas", "_basin_boxes")
    res = _run_boxes(site, _settings(site, cfg), boxes, work, False, log)
    bad = [k for k, r in res.items() if not r["ok"]]
    if bad:
        raise RuntimeError(f"boxes {', '.join(bad)} failed: "
                           + "; ".join(str(res[k]["error"]) for k in bad)[:300])
    wet = [b for b in boxes if not res[b["name"]]["dry"]]
    return join_boxes(wet, work, out_dir, plan.get("grid"))


def join_boxes(boxes, work, out_dir, grid=None):
    """The cores of the boxes' maps → one map in *out_dir* → summary dict."""
    import geopandas as gpd
    import pandas as pd
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    from shapely.geometry import box as sbox

    tifs = {b["name"]: os.path.join(work, b["name"], "flood_depth_max.tif") for b in boxes}
    with rasterio.open(next(iter(tifs.values()))) as r0:
        crs, res, nodata = r0.crs, r0.res[0], r0.nodata
    bounds = []
    for t in tifs.values():
        with rasterio.open(t) as r:
            bounds.append(r.bounds)
    x0 = math.floor(min(b.left for b in bounds) / res) * res
    y1 = math.ceil(max(b.top for b in bounds) / res) * res
    x1 = math.ceil(max(b.right for b in bounds) / res) * res
    y0 = math.floor(min(b.bottom for b in bounds) / res) * res
    w, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    tr = from_origin(x0, y1, res, res)
    os.makedirs(out_dir, exist_ok=True)
    layers, extents, per_box = {}, [], []
    for layer in ("flood_depth_max", "flood_velocity_max"):
        out = np.full((h, w), np.nan, dtype="float32")
        for b in boxes:
            src_path = os.path.join(work, b["name"], layer + ".tif")
            cx0, cy0, cx1, cy1 = transform_bounds("EPSG:4326", crs, *b["core"], densify_pts=21)
            c0, c1 = max(0, int((cx0 - x0) // res)), min(w, int(math.ceil((cx1 - x0) / res)))
            r0_, r1_ = max(0, int((y1 - cy1) // res)), min(h, int(math.ceil((y1 - cy0) / res)))
            part = np.full((h, w), np.nan, dtype="float32")
            with rasterio.open(src_path) as r:
                reproject(rasterio.band(r, 1), part, src_nodata=r.nodata, dst_transform=tr,
                          dst_crs=crs, dst_nodata=np.nan, resampling=Resampling.nearest)
            win = part[r0_:r1_, c0:c1]
            tgt = out[r0_:r1_, c0:c1]
            take = np.isnan(tgt)                           # first box wins on a shared edge
            tgt[take] = win[take]
        layers[layer] = out
        profile = {"driver": "GTiff", "height": h, "width": w, "count": 1, "dtype": "float32",
                   "crs": crs, "transform": tr, "nodata": nodata if nodata is not None else -9999.0,
                   "compress": "deflate"}
        with rasterio.open(os.path.join(out_dir, layer + ".tif"), "w", **profile) as dst:
            dst.write(np.where(np.isnan(out), profile["nodata"], out).astype("float32"), 1)
    for b in boxes:
        core = sbox(*transform_bounds("EPSG:4326", crs, *b["core"], densify_pts=21))
        ext = os.path.join(work, b["name"], "flood_extent_max.geojson")
        if os.path.isfile(ext):
            g = gpd.read_file(ext)
            if len(g):
                extents.append(gpd.clip(g.to_crs(crs), core))
        with open(os.path.join(work, b["name"], "inundation_summary.json")) as f:
            s = json.load(f)
        per_box.append({"name": b["name"], "core": b["core"], "box": b["box"],
                        **{k: s.get(k) for k in ("flooded_km2", "max_depth_m", "max_speed_m_s",
                                                 "steps", "seconds", "water_balance_error")}})
    if extents:
        g = gpd.GeoDataFrame(pd.concat(extents, ignore_index=True), crs=crs)
        g = gpd.GeoDataFrame(geometry=[g.union_all()], crs=crs).explode(index_parts=False)
        g["area_km2"] = g.area / 1e6
        g.to_file(os.path.join(out_dir, "flood_extent_max.geojson"), driver="GeoJSON")
    else:
        with open(os.path.join(out_dir, "flood_extent_max.geojson"), "w") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
    depth, speed = layers["flood_depth_max"], layers["flood_velocity_max"]
    wet = np.isfinite(depth) & (depth > 0)
    summary = {"method": "2d", "boxes": per_box, "grid": grid, "cell_size_m": res,
               "flooded_km2": round(float(wet.sum() * res * res / 1e6), 3),
               "max_depth_m": round(float(np.nanmax(depth[wet])), 2) if wet.any() else 0.0,
               "max_speed_m_s": round(float(np.nanmax(np.nan_to_num(speed[wet]))), 2)
               if wet.any() else 0.0}
    with open(os.path.join(out_dir, "inundation_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    return summary
