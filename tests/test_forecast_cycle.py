# -*- coding: utf-8 -*-
"""
The live forecast (lynch_forecast) end to end on tiny_basin, offline: setup and
warm-up, two cycles that carry on from each other's state, rain conserved
through the 10-minute grid and the join at the last report, a failed forecast
replaced by the last good one, catch-up after a long gap, the lock, Earth
Engine switched off, and the web page's data files.
"""

import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import yaml

from lynch_forecast import load_site
from lynch_forecast import cycle as cyc
from lynch_forecast import rainfiles, states
from lynch_forecast.prepare import setup_site
from lynch_forecast.sources import FORECAST_SOURCES, ForecastRain, forecast_source

T0 = datetime(2026, 3, 1, 12, 5)              # the first cycle's clock (UTC)
FAKE = {"mm_per_h": 3.0, "fail": False, "calls": 0}


@forecast_source("test_fake", optional=("mm_per_h",))
def _fake(spec, ctx):
    """Two rain points; FAKE['mm_per_h'] of rain every hour of the window."""
    FAKE["calls"] += 1
    if FAKE["fail"]:
        raise RuntimeError("feed down")
    idx = pd.date_range(ctx.start + timedelta(hours=1), ctx.end, freq="1h")
    pts = pd.DataFrame({"point_id": ["A", "B"], "lat": [27.10, 27.11], "lon": [84.30, 84.31]})
    rain = pd.DataFrame({"A": FAKE["mm_per_h"], "B": FAKE["mm_per_h"] * 0.5}, index=idx)
    return ForecastRain(points=pts, rain=rain, step_s=3600, issued=ctx.now,
                        info={"attribution": "test data"})


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    from MRRpy.core.routing import surface
    monkeypatch.setattr(surface, "resolve_lulc_field",
                        lambda cfg, grid, column, default, source:
                            np.full(len(grid["s_rows"]), 0.5))
    FAKE.update(mm_per_h=3.0, fail=False, calls=0)
    yield
    assert "test_fake" in FORECAST_SOURCES


def _station_csv(path, until, start=datetime(2026, 2, 27)):
    t = pd.date_range(start, until, freq="10min")
    hours = (t - t[0]).total_seconds() / 3600
    rain = np.where((hours % 24 > 6) & (hours % 24 < 9), 1.5, 0.0)    # a 3-h shower a day
    pd.DataFrame({"time_utc": t.strftime("%Y-%m-%d %H:%M"), "rain_mm": rain}).to_csv(
        path, index=False)


@pytest.fixture
def site(tiny_basin, tmp_path):
    dem, (lat, lon) = tiny_basin
    model = {"DEM_PATH": dem, "OUTPUT_POINT": [lat, lon], "TARGET_CRS_EPSG": "EPSG:32645",
             "DELINEATION_ENGINE": "pysheds", "ROUTING_SCHEME": "kinematic",
             "CFL_DT_MAX": 30.0, "OUTPUT_INTERVAL_SECONDS": 600, "CHANNEL_QBF_M3S": 1.0,
             "RUNOFF_SOURCE": "physical", "RUNOFF_MECHANISMS": ["infiltration_excess"],
             "GA_KSAT_MMHR": 2.0, "VSA_SD_MAX_INITIAL": 0.5}
    (tmp_path / "model.yaml").write_text(yaml.safe_dump(model))
    _station_csv(tmp_path / "gauge.csv", T0)
    fc = {"name": "Tiny test", "model": "model.yaml", "warm_up_days": 1.5,
          "forecast_hours": 6, "look_back_hours": 12, "display_timezone": "Asia/Kathmandu",
          "rain_stations": [{"name": "Gauge", "source": "csv", "path": "gauge.csv",
                             "lat": lat + 0.003, "lon": lon}],
          "rain_forecasts": [{"name": "Fake", "source": "test_fake"},
                             {"name": "Fake dry", "source": "test_fake", "mm_per_h": 0}],
          "hydrograph_points": [{"name": "Outlet bridge", "lat": lat, "lon": lon,
                                 "warning_m3s": 0.01, "danger_m3s": 1000}],
          "flood_areas": [], "website": {"title": "Tiny test forecast"}}
    (tmp_path / "forecast.yaml").write_text(yaml.safe_dump(fc, sort_keys=False))
    s = load_site(str(tmp_path / "forecast.yaml"))
    setup_site(s, now=T0, log=lambda *_: None, parallel=False)
    return s


def _run(site, now, **kw):
    _station_csv(os.path.join(os.path.dirname(site.path), "gauge.csv"), now)
    return cyc.run_cycle(site, now=now, log=lambda *_: None, publish=False, parallel=False,
                         **kw)


def _hyd(site, now, version="fake"):
    st = json.load(open(site.dir("status.json")))
    run_start = datetime.strptime(st["run_start_utc"], "%Y-%m-%d %H:%M")
    df = pd.read_csv(site.dir("cycles", now.strftime(cyc.STAMP), version, "hydrograph.csv"))
    return pd.Series(df["Q_m3s"].to_numpy(), index=[run_start + timedelta(seconds=float(s))
                                                   for s in df["time_s"]])


def test_setup_pins_the_soil_date_and_saves_the_first_states(site):
    cfg = yaml.safe_load(open(site.model))
    assert cfg["EVENT_START_UTC"].startswith("2026-03-01")
    kept = [t for t, _ in states.list_states(site)]
    assert kept[-1] == datetime(2026, 3, 1, 12, 0)            # the last whole hour reported
    assert datetime(2026, 3, 1, 0, 0) in kept                  # daily states on the way


def test_two_cycles_carry_on_from_each_other(site):
    s1 = _run(site, T0)
    assert s1["ok"], s1
    assert s1["t_obs_utc"] == "2026-03-01 12:00"
    assert s1["restart_from"] == "state_20260301T0000Z.npz"   # newest ≥ 12 h before T_obs
    assert s1["versions"]["Fake"]["ok"] and s1["versions"]["No more rain"]["ok"]
    assert "2026-03-01 12:00" in s1["states_kept"]
    q1 = _hyd(site, T0)
    t1 = T0 + timedelta(hours=1)
    s2 = _run(site, t1)
    assert s2["ok"] and s2["restart_from"] == "state_20260301T0100Z.npz"
    q2 = _hyd(site, t1)
    common = [t for t in q2.index if t <= datetime(2026, 3, 1, 12, 0)]
    assert len(common) > 60
    # The same station rain from the same state: the same river flow.
    np.testing.assert_allclose(q2[common].to_numpy(), q1[common].to_numpy(),
                               rtol=1e-6, atol=1e-9 * max(q1.max(), 1e-12))


def test_rain_is_conserved_and_the_forecast_starts_after_the_report(site):
    _run(site, T0)
    st = json.load(open(site.dir("status.json")))
    run_start = datetime.strptime(st["run_start_utc"], "%Y-%m-%d %H:%M")
    rdir = site.dir("cycles", T0.strftime(cyc.STAMP), "fake", "rain")
    ts = pd.read_csv(os.path.join(rdir, "timeseries.csv"))
    t = pd.DatetimeIndex([run_start + timedelta(seconds=float(s)) for s in ts["time_s"]])
    assert ts.iloc[0, 1:].sum() == 0 and ts.iloc[-1, 1:].sum() == 0          # zero padding
    assert np.all(np.diff(ts["time_s"]) == 600)
    g = pd.read_csv(os.path.join(os.path.dirname(site.path), "gauge.csv"),
                    parse_dates=["time_utc"]).set_index("time_utc")["rain_mm"]
    past = (t >= run_start) & (t <= datetime(2026, 3, 1, 12, 0))
    want = g[(g.index >= run_start) & (g.index <= datetime(2026, 3, 1, 12, 0))].sum()
    # one station: the same rain at every point, equal to the gauge's
    for col in ("P1", "P2"):
        assert ts[col][past].sum() == pytest.approx(want, abs=1e-6)
    ahead = t > datetime(2026, 3, 1, 12, 0)
    assert ts["P1"][ahead].sum() == pytest.approx(3.0 * 6, rel=1e-6)          # 6 h × 3 mm
    assert ts["P2"][ahead].sum() == pytest.approx(1.5 * 6, rel=1e-6)
    dry = pd.read_csv(site.dir("cycles", T0.strftime(cyc.STAMP), "no_more_rain", "rain",
                               "timeseries.csv"))
    assert dry.iloc[:, 1:].to_numpy()[ahead].sum() == 0


def test_a_failed_forecast_is_replaced_by_the_last_good_one(site):
    _run(site, T0)
    FAKE["fail"] = True
    st = _run(site, T0 + timedelta(minutes=30))
    assert st["ok"]
    assert st["forecasts"]["Fake"]["ok"] is False and st["forecasts"]["Fake"]["reused"]
    assert st["versions"]["Fake"]["ok"]


def test_no_forecast_at_all_still_runs_the_no_rain_version(site):
    FAKE["fail"] = True
    st = _run(site, T0)
    assert not st["versions"]["Fake"]["ok"]
    assert st["versions"]["No more rain"]["ok"]


def test_the_web_page_data(site):
    _run(site, T0)
    data = site.dir("site", "data")
    latest = json.load(open(os.path.join(data, "latest.json")))
    assert latest["title"] == "Tiny test forecast"
    assert latest["t_obs_utc"] == "2026-03-01T12:00Z"
    names = [p["name"] for p in latest["points"]]
    assert names == ["Outlet bridge"]          # at the outlet: no second 'Outlet' point
    pk = latest["points"][0]["peaks"]["Fake"]
    assert pk["q"] > 0 and pk["level"] in ("warning", "normal")
    pt = json.load(open(os.path.join(data, "points", "outlet_bridge.json")))
    assert set(pt["series"]) == {"Fake", "Fake dry", "No more rain"}
    assert len(pt["series"]["Fake"]) == len(pt["times"])
    assert pt["peaks"]["Fake"]["q"] >= pt["peaks"]["No more rain"]["q"]
    rain = json.load(open(os.path.join(data, "rain.json")))
    assert len(rain["times"]) == len(rain["series"]["Fake"])
    for f in ("index.html", "app.js", "style.css", ".nojekyll"):
        assert os.path.isfile(site.dir("site", f))
    assert os.path.isfile(os.path.join(data, "basin.geojson"))
    assert "test data" in latest["attribution"]


def _rewrite(site, **changes):
    data = yaml.safe_load(open(site.path))
    data.update(changes)
    with open(site.path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return load_site(site.path)


def test_a_river_gauge_is_drawn_under_its_point(site):
    t = pd.date_range(T0 - timedelta(days=3), T0 + timedelta(hours=1), freq="10min")
    level = 100.0 + np.arange(len(t)) * 0.001                    # rising 6 mm an hour
    pd.DataFrame({"time_utc": t.strftime("%Y-%m-%d %H:%M"), "level_m": level}).to_csv(
        os.path.join(os.path.dirname(site.path), "level.csv"), index=False)
    site = _rewrite(site, river_gauges=[{"name": "Bridge gauge", "source": "csv",
                                         "path": "level.csv", "point": "Outlet bridge"}])
    _run(site, T0)
    data = site.dir("site", "data")
    latest = json.load(open(os.path.join(data, "latest.json")))
    assert latest["past_rain"] == "stations"
    g = latest["gauges"][0]
    assert (g["name"], g["point"], g["ok"], g["last_report_utc"]) == (
        "Bridge gauge", "Outlet bridge", True, "2026-03-01 12:05")     # nothing after now (12:05)
    sm = latest["points"][0]["gauge"]
    assert sm["now_m"] == pytest.approx(level[t <= T0][-1], abs=1e-3)
    assert sm["change_1h_m"] == pytest.approx(0.006, abs=1e-3)
    assert sm["above_low_m"] == pytest.approx(level[t <= T0][-1] - 100.0, abs=1e-3)
    pt = json.load(open(os.path.join(data, "points", "outlet_bridge.json")))
    m = pt["measured"]
    assert m["name"] == "Bridge gauge" and len(m["times"]) == len(m["values"]) > 60
    assert m["times"][0] >= pt["times"][0][:14] and m["times"][-1] == "2026-03-01T12:05Z"
    csv = pd.read_csv(os.path.join(data, "points", "outlet_bridge.csv"))
    assert csv["measured_level_m"].notna().sum() > 60
    kept = pd.read_csv(site.dir("archive", "levels", "bridge_gauge.csv.gz"))
    assert len(kept) == int((t <= T0).sum())                      # the whole history is kept


def test_without_rain_stations_the_past_rain_is_the_main_forecast_s(site):
    site = _rewrite(site, rain_stations=[])
    st = _run(site, T0)
    assert st["ok"] and st["t_obs_utc"] == "2026-03-01 12:00"     # now, to the 10 minutes
    data = site.dir("site", "data")
    latest = json.load(open(os.path.join(data, "latest.json")))
    assert latest["past_rain"] == "forecast_model" and latest["stations"] == []
    assert [v["note"] for v in latest["versions"] if v["name"] == "No more rain"] == [
        "no more rain after now"]
    rain = pd.read_csv(os.path.join(data, "rain.csv"))
    assert set(rain["part"]) == {"past rain (Fake)", "forecast"}
    past = rain[rain["part"] != "forecast"]
    assert past["Fake"].sum() > 0                                  # the Fake forecast's own rain


def test_catch_up_after_a_long_gap(site):
    later = T0 + timedelta(days=2)
    st = _run(site, later)
    assert st["ok"] and st["catch_up"]["ok"]
    assert st["restart_from"].startswith("state_20260303")


def test_only_if_stale_and_the_lock(site):
    _run(site, T0)
    st = cyc.run_cycle(site, now=T0 + timedelta(minutes=10), log=lambda *_: None,
                       publish=False, parallel=False, only_if_stale=45)
    assert st["skipped"] == "fresh"
    with cyc.CycleLock(site) as got:
        assert got
        st = cyc.run_cycle(site, now=T0 + timedelta(hours=2), log=lambda *_: None,
                           publish=False, parallel=False)
        assert st == {"skipped": "locked"}


def test_versions_run_side_by_side_in_fresh_processes(site):
    st = cyc.run_cycle(site, now=T0, log=lambda *_: None, publish=False, parallel=True)
    assert st["ok"], st["versions"]
    assert all(v["ok"] for v in st["versions"].values())


def test_earth_engine_is_switched_off():
    import sys
    from lynch_forecast.offline import EarthEngineBlocked, earth_engine_blocked
    with earth_engine_blocked():
        import ee
        with pytest.raises(EarthEngineBlocked, match="static"):
            ee.Initialize()
    assert not isinstance(sys.modules.get("ee"), type(ee)) or "ee" not in sys.modules \
        or sys.modules["ee"] is not ee


def test_inflow_file_holds_or_recedes():
    from lynch_forecast.sitefile import Inflow
    import tempfile
    flow = pd.Series([10.0, 20.0], index=pd.DatetimeIndex(["2026-03-01 00:00",
                                                           "2026-03-01 01:00"]))
    start, end = datetime(2026, 3, 1), datetime(2026, 3, 1, 3)
    with tempfile.TemporaryDirectory() as d:
        hold = pd.read_csv(cyc.inflow_file(Inflow("a", "csv", {}, ahead="hold"), flow, start,
                                           end, os.path.join(d, "h.csv")))
        rec = pd.read_csv(cyc.inflow_file(Inflow("a", "csv", {}, ahead="recede",
                                                 recede_hours=1.0), flow, start, end,
                                          os.path.join(d, "r.csv")))
    assert hold["Q_m3s"].iloc[3] == pytest.approx(15.0)               # 00:30, between
    assert hold["Q_m3s"].iloc[-1] == pytest.approx(20.0)
    assert rec["Q_m3s"].iloc[-1] == pytest.approx(20.0 * np.exp(-2.0))


def test_to_bins_shares_rain_by_overlap():
    # an hour starting at :15 (Nepal-time forecasts) over 10-minute bins
    s = pd.Series([6.0], index=pd.DatetimeIndex(["2026-03-01 04:15"]))
    b = rainfiles.to_bins(s, 3600)
    assert b.sum() == pytest.approx(6.0)
    assert b.loc["2026-03-01 03:20"] == pytest.approx(0.5)            # 5 of the 10 minutes
    assert b.loc["2026-03-01 03:30"] == pytest.approx(1.0)
    gap = rainfiles.to_bins(pd.Series([1.0, 1.0], index=pd.DatetimeIndex(
        ["2026-03-01 00:10", "2026-03-01 00:30"])), 600)
    assert np.isnan(gap.loc["2026-03-01 00:20"])                      # no reading ≠ zero


def test_spread_uses_only_stations_that_reported():
    v = np.array([[1.0, 3.0], [np.nan, 3.0], [np.nan, np.nan]])
    out = rainfiles.spread(v, [27.0, 27.0], [85.0, 85.1], [27.0], [85.0])
    assert out[0, 0] == pytest.approx(1.0, abs=1e-3)                  # on top of station 1
    assert out[1, 0] == pytest.approx(3.0)                            # only station 2 reported
    assert np.isnan(out[2, 0])


def test_flood_area_maps_on_the_web_page(site, tiny_basin):
    from lynch_forecast.sitefile import Area
    dem, (lat, lon) = tiny_basin
    site.flood_areas = [Area("Valley", (lon - 0.004, lat - 0.002, lon + 0.004, lat + 0.008),
                             "hand", "model_grid")]
    FAKE["mm_per_h"] = 40.0
    st = _run(site, T0)
    assert st["areas"]["Valley"]["ok"], st["areas"]
    latest = json.load(open(site.dir("site", "data", "latest.json")))
    a = latest["areas"][0]
    assert a["ok"] and a["png"] == "maps/valley/depth.png"
    for f in ("depth.png", "depth.json", "extent.geojson", "flood_depth_max.tif"):
        assert os.path.isfile(site.dir("site", "maps", "valley", f))
    (s, w), (n, e) = a["bounds"]
    assert s < n and w < e
    assert sum(c["area_km2"] for c in a["classes"]) == pytest.approx(a["flooded_km2"], abs=1e-6)


def test_whole_basin_map_from_boxes(site, tiny_basin):
    from lynch_forecast import basinmap
    from lynch_forecast.prepare import latest_flood_run, map_dems
    from lynch_forecast.sitefile import BasinMap
    site.basin_map = BasinMap(method="2d", boxes=[2, 1], overlap_km=0.3, dem="model_grid",
                              threads=1)
    FAKE["mm_per_h"] = 40.0
    st = _run(site, T0)                                       # boxes not set up yet:
    assert not st["areas"]["Whole basin"]["ok"]               # said so, the cycle goes on
    assert "setup" in st["areas"]["Whole basin"]["error"]
    map_dems(site, latest_flood_run(site), log=lambda *_: None)   # setup --maps-only
    plan = json.load(open(basinmap.plan_path(site)))
    assert len(plan["boxes"]) == 2 and all(b["box"][0] <= b["core"][0] for b in plan["boxes"])
    st = _run(site, T0 + timedelta(minutes=30))
    assert st["areas"]["Whole basin"]["ok"], st["areas"]
    latest = json.load(open(site.dir("site", "data", "latest.json")))
    a = latest["areas"][0]                                    # the basin map comes first
    assert a["basin"] and a["box"] is None and a["png"] == "maps/whole_basin/depth.png"
    joined = json.load(open(os.path.join(st["areas"]["Whole basin"]["dir"],
                                         "inundation_summary.json")))
    assert [b["name"] for b in joined["boxes"]] == ["box_1_1", "box_2_1"]
    assert joined["flooded_km2"] > 0 and joined["max_depth_m"] == pytest.approx(
        max(b["max_depth_m"] for b in joined["boxes"]), abs=0.005)   # (shallow: under the page's 10 cm)
    assert os.path.isfile(site.dir("site", "maps", "whole_basin", "flood_depth_max.tif"))


def test_basin_boxes_leave_out_land_outside_the_basin(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Polygon
    from lynch_forecast.basinmap import plan_boxes
    ell = Polygon([(85.0, 27.0), (85.2, 27.0), (85.2, 27.1), (85.1, 27.1), (85.1, 27.2),
                   (85.0, 27.2)])                             # an L: the north-east quarter is out
    path = tmp_path / "ws.geojson"
    gpd.GeoDataFrame(geometry=[ell.buffer(-1e-4)], crs=4326).to_file(path, driver="GeoJSON")
    boxes, grid = plan_boxes(str(path), boxes=[2, 2], overlap_km=1.0)
    assert grid == (2, 2) and sorted(b["name"] for b in boxes) == ["box_1_1", "box_1_2", "box_2_1"]
    b = next(b for b in boxes if b["name"] == "box_1_1")
    assert b["box"][2] - b["core"][2] == pytest.approx(1.0 / (111.32 * np.cos(np.radians(27.1))), rel=1e-3)
    auto, (nx, ny) = plan_boxes(str(path), boxes="auto", box_cells=100_000)
    assert nx * ny >= 20000 * 22000 / 30 ** 2 / 100_000 and len(auto) < nx * ny


def test_rivers_anywhere_on_the_map(site, tiny_basin):
    """Every river cell's forecast flow, for clicks on the map (main version)."""
    from lynch_forecast.sitefile import BasinMap
    site.basin_map = BasinMap(method="2d", boxes=[1, 1], dem="model_grid", threads=1)
    FAKE["mm_per_h"] = 40.0
    _run(site, T0)
    latest = json.load(open(site.dir("site", "data", "latest.json")))
    assert latest["rivers"]["ok"] and latest["every_minutes"] == 30
    assert latest["next_update_utc"] > latest["cycle_utc"]
    g = json.load(open(site.dir("site", "data", "rivers.geojson")))
    rq = json.load(open(site.dir("site", "data", "rivers_q.json")))
    assert len(rq["q"]) == latest["rivers"]["cells"] > 0
    assert len(rq["times"]) == len(rq["q"][0]) and rq["observed_until"]
    for f in g["features"]:
        p = f["properties"]
        assert len(p["k"]) == len(p["a"]) <= len(f["geometry"]["coordinates"])
        assert p["q"] == pytest.approx(max(max(rq["q"][k]) for k in p["k"]), rel=0.01)
    hyd = _hyd(site, T0)                         # the outlet's cell is a river cell
    assert max(max(q) for q in rq["q"]) == pytest.approx(hyd.max(), rel=0.05)


def _write_fresh_place(root, tiny_basin):
    dem, (lat, lon) = tiny_basin
    d = root / "places" / "tiny"
    d.mkdir(parents=True)
    model = {"DEM_PATH": dem, "OUTPUT_POINT": [lat, lon], "TARGET_CRS_EPSG": "EPSG:32645",
             "DELINEATION_ENGINE": "pysheds", "ROUTING_SCHEME": "kinematic",
             "CFL_DT_MAX": 30.0, "OUTPUT_INTERVAL_SECONDS": 600, "CHANNEL_QBF_M3S": 1.0,
             "RUNOFF_SOURCE": "physical", "RUNOFF_MECHANISMS": ["infiltration_excess"],
             "GA_KSAT_MMHR": 2.0}
    (d / "model.yaml").write_text(yaml.safe_dump(model))
    now = datetime.utcnow()
    _station_csv(d / "gauge.csv", now, start=now - timedelta(days=3))
    fc = {"name": "Tiny", "model": "model.yaml", "warm_up_days": 1, "forecast_hours": 6,
          "rain_stations": [{"name": "Gauge", "source": "csv", "path": "gauge.csv",
                             "lat": lat + 0.003, "lon": lon}],
          "rain_forecasts": [{"name": "Fake", "source": "test_fake"}],
          "hydrograph_points": [{"name": "Outlet", "lat": lat, "lon": lon}]}
    (d / "forecast.yaml").write_text(yaml.safe_dump(fc, sort_keys=False))
    (root / "places.yaml").write_text("title: Test\nplaces:\n  - places/tiny/forecast.yaml\n")
    return d


def test_serve_sets_up_a_new_place_and_follows_changes(tiny_basin, tmp_path):
    """Hand-over: an operator only edits files; 'serve' does the rest."""
    from argparse import Namespace
    from lynch_forecast import autopilot
    from lynch_forecast.cli import tend
    d = _write_fresh_place(tmp_path, tiny_basin)
    args = Namespace(no_publish=True, serial=True, only_if_stale=None, restore_state=False)
    settings = autopilot.Settings(str(tmp_path / "places.yaml"))
    said = []
    assert tend(args, *settings.read(), log=said.append) == 0          # new: set up + run
    assert any("setting up (new place)" in s for s in said)
    cards = json.load(open(tmp_path / "public" / "places.json"))["places"]
    assert cards[0]["has_forecast"] and os.path.isfile(
        tmp_path / "public" / "tiny" / "data" / "latest.json")
    site = settings.sites["tiny"]
    first = last = cyc.last_status(site)["cycle_utc"]
    assert autopilot.place_state(site).need == ""

    fc = yaml.safe_load((d / "forecast.yaml").read_text())          # a level: no new setup
    fc["hydrograph_points"][0]["warning_m3s"] = 5.0
    (d / "forecast.yaml").write_text(yaml.safe_dump(fc, sort_keys=False))
    portal, items = settings.read()
    assert items[0][1].hydrograph_points[0].warning_m3s == 5.0
    assert autopilot.place_state(items[0][1]).need == ""

    model = yaml.safe_load((d / "model.yaml").read_text())           # the model: set up again
    model["MANNINGS_N"] = 0.06
    (d / "model.yaml").write_text(yaml.safe_dump(model))
    said.clear()
    assert tend(args, *settings.read(), log=said.append) == 0
    assert any("model settings changed" in s for s in said)
    assert os.listdir(d / "old") and os.path.isdir(d / "static")
    last = cyc.last_status(settings.sites["tiny"])["cycle_utc"]
    assert last >= first
    assert autopilot.place_state(settings.sites["tiny"]).need == ""

    (d / "forecast.yaml").write_text("name: Tiny\nmodle: model.yaml\n")    # a typo
    portal, items = settings.read()
    assert items and "the last good version is used" in settings.problems[0]
