# -*- coding: utf-8 -*-
"""
rainfiles.py — the rain a forecast version runs on, as MRRpy rain-gauge files.

Everything is put on one clock: 10-minute bins on the UTC grid (…:00, :10, …),
each holding the mm that fell in the 10 minutes *ending* at its time (the
gauge-file convention).  Station readings and forecasts of any interval — 10
minutes, an hour, an hour that starts at :15 — are shared out over the bins by
overlap, so no rain is lost or added.

A version's rain points are its forecast's points (an Open-Meteo model's
cells).  Up to the last station report (T_obs) the stations' rain is spread
onto those points by inverse distance (one station gives the same rain
everywhere); after it, the forecast's.  A bin no station reported is filled from
the main forecast model, or set to zero, and marked *estimated*.

The files keep the bin that ends at the run start (the model's rain between two
rows is a straight line, so the first minutes of the run read it) and add a
zero row before and after: a run split in two then gets exactly the rain one
long run gets, and the model's rain adds up to the table's.
"""

import os

import numpy as np
import pandas as pd

STEP_S = 600
_EPOCH = np.datetime64("1970-01-01T00:00:00", "s")


def _secs(index):
    return (pd.DatetimeIndex(index).values.astype("datetime64[s]") - _EPOCH).astype(np.int64)


def _times(secs):
    return pd.DatetimeIndex((_EPOCH + np.asarray(secs, dtype=np.int64)).astype("datetime64[ns]"))


def grid(t0, t1):
    """Bin end times from the first bin ending at or after *t0* to *t1*."""
    a = int(np.ceil(_secs([t0])[0] / STEP_S)) * STEP_S
    b = int(np.floor(_secs([t1])[0] / STEP_S)) * STEP_S
    return _times(np.arange(a, b + 1, STEP_S))


def to_bins(series, step_s, times=None):
    """
    mm per interval (index = interval end, each *step_s* long) → mm per
    10-minute bin, shared out by overlap.  Bins no interval touches are NaN
    (no reading); *times* picks the bins returned (default: all touched).
    """
    s = series.dropna()
    if not len(s):
        return pd.Series(np.nan, index=times if times is not None else pd.DatetimeIndex([]))
    ends = _secs(s.index).astype(np.float64)
    starts = ends - float(step_s)
    rate = s.to_numpy(float) / float(step_s)
    lo = int(np.floor(starts.min() / STEP_S))
    hi = int(np.ceil(ends.max() / STEP_S))
    n = hi - lo
    rain = np.zeros(n)
    cover = np.zeros(n)
    k0 = np.floor(starts / STEP_S).astype(np.int64)
    k1 = np.ceil(ends / STEP_S).astype(np.int64)
    for k in range(int((k1 - k0).max())):          # each interval spans few bins
        kb = k0 + k
        b_start = kb * STEP_S
        ov = np.minimum(ends, b_start + STEP_S) - np.maximum(starts, b_start)
        ok = ov > 0
        np.add.at(rain, kb[ok] - lo, rate[ok] * ov[ok])
        np.add.at(cover, kb[ok] - lo, ov[ok])
    bin_end = (np.arange(lo, hi) + 1) * STEP_S
    out = pd.Series(np.where(cover > 0, rain, np.nan), index=_times(bin_end))
    return out.reindex(times) if times is not None else out


def _xy(lat, lon, lat0):
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    return np.column_stack([lon * 111320.0 * np.cos(np.radians(lat0)), lat * 110540.0])


def spread(values, st_lat, st_lon, pt_lat, pt_lon, power=2.0):
    """
    Station rain (bins × stations, NaN = no reading) → rain at the points
    (bins × points) by inverse distance over the stations that reported in
    each bin.  NaN where none did.
    """
    V = np.asarray(values, float)
    lat0 = float(np.mean(np.r_[st_lat, pt_lat]))
    d = np.linalg.norm(_xy(pt_lat, pt_lon, lat0)[:, None, :]
                       - _xy(st_lat, st_lon, lat0)[None, :, :], axis=2)   # (P, S)
    W = 1.0 / np.maximum(d, 1.0) ** power
    has = np.isfinite(V)
    num = np.where(has, V, 0.0) @ W.T
    den = has.astype(float) @ W.T
    return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)


def assemble(times, t_obs, observed=None, forecast=None, fill=None, missing="forecast_model"):
    """
    The rain table of one version (bins × points) and which bins are estimated.

    *observed*: station rain at the points (NaN = no report), used up to
    *t_obs*; *forecast*: this version's forecast at the points, used after it
    (NaN = beyond its end → 0); *fill*: what stands in for a bin no station
    reported (the main forecast model at the points), when *missing* is
    'forecast_model'.  All are arrays shaped (len(times), n_points) or None.
    """
    n_t = len(times)
    past = np.asarray(pd.DatetimeIndex(times) <= t_obs)
    shape = (n_t, (observed if observed is not None else
                   forecast if forecast is not None else fill).shape[1])
    obs = np.full(shape, np.nan) if observed is None else np.asarray(observed, float)
    fc = np.zeros(shape) if forecast is None else np.nan_to_num(np.asarray(forecast, float))
    out = np.where(past[:, None], obs, fc)
    gap = past[:, None] & ~np.isfinite(out)
    estimated = gap.any(axis=1)
    if gap.any():
        stand_in = np.zeros(shape)
        if missing == "forecast_model" and fill is not None:
            stand_in = np.nan_to_num(np.asarray(fill, float))
        out = np.where(gap, stand_in, out)
    return np.maximum(out, 0.0), estimated


def write_rain_files(out_dir, points, table, run_start, crs):
    """
    Write ``gauges.csv`` + ``timeseries.csv`` for a run that starts at
    *run_start*.  *points*: DataFrame point_id/lat/lon; *table*: DataFrame of
    mm per bin (index = bin end, UTC; one column per point_id) that starts with
    the bin ending at *run_start*.  Returns (gauges_path, timeseries_path).
    """
    from pyproj import Transformer

    os.makedirs(out_dir, exist_ok=True)
    ids = [f"P{i + 1}" for i in range(len(points))]
    e, n = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        points["lon"].to_numpy(float), points["lat"].to_numpy(float))
    g = pd.DataFrame({"gauge_id": ids, "name": [str(p) for p in points["point_id"]],
                      "easting_m": e, "northing_m": n})
    secs = _secs(table.index) - _secs([run_start])[0]
    if secs[0] != 0 or np.any(np.diff(secs) != STEP_S):
        raise ValueError("the rain table must start at the run start and have one row "
                         "every 10 minutes")
    depth = table[[str(p) for p in points["point_id"]]].to_numpy(float)
    if not np.isfinite(depth).all():
        raise ValueError("the rain table has empty values")
    zero = np.zeros((1, depth.shape[1]))
    ts = pd.DataFrame(np.vstack([zero, depth, zero]), columns=ids)
    ts.insert(0, "time_s", np.r_[-STEP_S, secs, secs[-1] + STEP_S].astype(np.int64))
    gp, tp = os.path.join(out_dir, "gauges.csv"), os.path.join(out_dir, "timeseries.csv")
    g.to_csv(gp, index=False)
    ts.to_csv(tp, index=False, float_format="%.9g")
    back = pd.read_csv(tp)[ids].to_numpy(float).sum()
    if abs(back - depth.sum()) > 1e-4 * max(1.0, depth.sum()):
        raise RuntimeError(f"rain file total {back:.6f} mm ≠ table {depth.sum():.6f} mm")
    return gp, tp
