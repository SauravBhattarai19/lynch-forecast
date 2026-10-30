# -*- coding: utf-8 -*-
"""What 'serve' does by itself and the hand-over tools, without running the model:
settings fingerprints and retries, settings read again with the last good kept,
a new place from its outlet, the DEM box widened around a cut basin, the service
files and the doctor's report."""

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from lynch_forecast import autopilot
from lynch_forecast.cli import main

MODEL = {"DEM_PATH": "dem.tif", "OUTPUT_POINT": [27.6, 85.5], "RUNOFF_SOURCE": "physical",
         "RUNOFF_MECHANISMS": ["impervious", "infiltration_excess"]}


def _place(root, name="roshi", model=MODEL):
    d = root / "places" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.yaml").write_text(yaml.safe_dump(model))
    (d / "forecast.yaml").write_text(yaml.safe_dump({
        "name": name.title(), "model": "model.yaml",
        "rain_forecasts": [{"name": "ECMWF", "source": "open_meteo", "model": "ecmwf_ifs025"}],
        "hydrograph_points": [{"name": "Bridge", "lat": 27.58, "lon": 85.51}]}, sort_keys=False))
    return d


def test_fingerprints_skip_comments_and_the_soil_date(tmp_path):
    from lynch_forecast import load_site
    d = _place(tmp_path)
    site = load_site(str(d / "forecast.yaml"))
    fp = autopilot.model_fingerprint(site)
    (d / "model.yaml").write_text("# a comment\n" + yaml.safe_dump(MODEL) +
                                  "EVENT_START_UTC: '2026-10-28 00:00'\n")
    assert autopilot.model_fingerprint(site) == fp
    (d / "model.yaml").write_text(yaml.safe_dump(dict(MODEL, MANNINGS_N=0.05)))
    assert autopilot.model_fingerprint(site) != fp


def test_a_new_place_needs_setup_and_a_failure_waits_before_trying_again(tmp_path, monkeypatch):
    from lynch_forecast import load_site
    from lynch_forecast import prepare as prep
    site = load_site(str(_place(tmp_path) / "forecast.yaml"))
    st = autopilot.place_state(site)
    assert (st.need, st.can_run, st.note) == ("setup", False, "new place")

    def boom(*a, **k):
        raise prep.SetupError("Earth Engine: not signed in")
    monkeypatch.setattr(prep, "setup_site", boom)
    assert not autopilot.prepare(site, "setup", log=lambda *a: None)
    st = autopilot.place_state(site)
    assert st.need == "wait" and "not signed in" in st.note
    later = datetime.utcnow() + timedelta(hours=autopilot.RETRY_HOURS, minutes=5)
    assert autopilot.place_state(site, now=later).need == "setup"

    monkeypatch.setattr(prep, "setup_site", lambda site, **k: None)
    assert autopilot.prepare(site, "setup", log=lambda *a: None)
    assert autopilot.place_state(site) == autopilot.State("", True)
    rec = autopilot.read_record(site)
    assert "failed" not in rec and rec["model"] == autopilot.model_fingerprint(site)


def test_changed_flood_maps_and_changed_model(tmp_path, monkeypatch):
    from lynch_forecast import load_site
    from lynch_forecast import prepare as prep
    d = _place(tmp_path)
    site = load_site(str(d / "forecast.yaml"))
    monkeypatch.setattr(prep, "setup_site", lambda site, **k: os.makedirs(site.dir("static"),
                                                                         exist_ok=True))
    autopilot.prepare(site, "setup", log=lambda *a: None)
    os.makedirs(site.dir("states"))
    data = yaml.safe_load((d / "forecast.yaml").read_text())
    data["flood_areas"] = [{"name": "Town", "box": [85.50, 27.58, 85.54, 27.61]}]
    (d / "forecast.yaml").write_text(yaml.safe_dump(data))
    site = load_site(str(d / "forecast.yaml"))
    st = autopilot.place_state(site)
    assert (st.need, st.can_run) == ("maps", True)                # cycles go on meanwhile
    calls = []
    monkeypatch.setattr(prep, "latest_flood_run", lambda site: "run")
    monkeypatch.setattr(prep, "map_dems", lambda site, run, log: calls.append(run))
    assert autopilot.prepare(site, "maps", log=lambda *a: None) and calls == ["run"]
    assert autopilot.place_state(site).need == ""

    (d / "model.yaml").write_text(yaml.safe_dump(dict(MODEL, MANNINGS_N=0.05)))
    st = autopilot.place_state(site)
    assert (st.need, st.can_run, st.note) == ("setup", False, "model settings changed")
    said = []
    assert autopilot.prepare(site, "setup", log=said.append)
    old = os.listdir(site.dir("old"))
    assert len(old) == 1 and sorted(os.listdir(site.dir("old", old[0]))) == ["states", "static"]
    assert any("fits the old model" in s for s in said)
    assert autopilot.place_state(site).need == ""


def test_settings_keep_the_last_good_version(tmp_path):
    d = _place(tmp_path)
    (tmp_path / "places.yaml").write_text(yaml.safe_dump({"places": [
        "places/roshi/forecast.yaml"]}))
    s = autopilot.Settings(str(tmp_path / "places.yaml"))
    portal, items = s.read()
    assert [pid for pid, _ in items] == ["roshi"] and not s.problems
    (d / "forecast.yaml").write_text("name: Roshi\nmodle: model.yaml\n")      # a typo
    portal, items = s.read()
    assert [pid for pid, _ in items] == ["roshi"]
    assert "the last good version is used" in s.problems[0] and "model" in s.problems[0]
    (tmp_path / "places.yaml").write_text("places: [")                        # broken YAML
    portal, items = s.read()
    assert portal is not None and len(s.problems) >= 1
    with pytest.raises(Exception):
        autopilot.Settings(str(tmp_path / "places.yaml")).read()             # nothing good yet


def test_a_new_place_from_its_outlet(tmp_path, monkeypatch):
    from lynch_forecast import load_site
    from lynch_forecast.places import load_portal
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEE_PROJECT", raising=False)
    assert main(["new", "places/nakhu", "--name", "Nakhu Khola", "--outlet", "27.66,85.29",
                 "-p", "places.yaml", "--gee-project", "ee-test"]) == 0
    site = load_site("places/nakhu/forecast.yaml")                    # parses and checks
    model = yaml.safe_load(open("places/nakhu/model.yaml"))
    assert model["TARGET_CRS_EPSG"] == "EPSG:32645" and model["CHANNEL_QBF_M3S"] == "wecs_nepal"
    assert model["OUTPUT_POINT"] == [27.66, 85.29] and model["GEE_PROJECT"] == "ee-test"
    w, s, e, n = model["DEM_BOUNDS_WGS84"]
    assert w < 85.29 < e and s < 27.66 < n
    assert site.display_timezone == "Asia/Kathmandu" and site.basin_map is not None
    assert [p.name for p in site.hydrograph_points] == ["Outlet"]
    assert site.rain_stations == [] and "alertnepal" in site.versions()   # forecast only
    assert [p.id for p in load_portal("places.yaml").places] == ["nakhu"]
    # the next place reuses the first one's Earth Engine project; outside Nepal: UTC, auto
    assert main(["new", "places/thames", "--outlet", "51.5,-0.1"]) == 0
    model = yaml.safe_load(open("places/thames/model.yaml"))
    assert model["GEE_PROJECT"] == "ee-test" and model["CHANNEL_QBF_M3S"] == "auto"
    assert model["TARGET_CRS_EPSG"] == "EPSG:32630"
    assert "alertnepal" not in load_site("places/thames/forecast.yaml").versions()
    assert [p.id for p in load_portal("places.yaml").places] == ["nakhu", "thames"]
    assert main(["new", "places/thames", "--outlet", "51.5,-0.1"]) == 1     # never overwrites
    assert main(["new", "places/x", "--outlet", "51.5"]) == 1


def test_the_dem_box_grows_where_the_basin_is_cut(tmp_path):
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_origin
    from lynch_forecast.prepare import basin_cut_sides, grow_box, write_box
    n, cell, ox, oy = 40, 90.0, 500000.0, 3060000.0               # zone 45's middle
    tr = from_origin(ox, oy, cell, cell)
    prof = dict(driver="GTiff", height=n, width=n, count=1, crs="EPSG:32645", transform=tr)
    dem = np.full((n, n), 100.0, "float32")
    basin = np.zeros((n, n), "uint8")
    basin[10:30, 15:] = 1                                         # runs to the east edge
    with rasterio.open(tmp_path / "reprojected_dem.tif", "w", dtype="float32",
                       nodata=-9999.0, **prof) as r:
        r.write(dem, 1)
    with rasterio.open(tmp_path / "watershed.tif", "w", dtype="uint8", nodata=0, **prof) as r:
        r.write(basin, 1)
    to = Transformer.from_crs(32645, 4326, always_xy=True)
    lons, lats = to.transform([ox, ox + n * cell], [oy - n * cell, oy])
    cfg = SimpleNamespace(OUTPUT_DIR=str(tmp_path),
                          ROUTING_WATERSHED_MASK_PATH=str(tmp_path / "watershed.tif"),
                          DEM_BOUNDS_WGS84=[lons[0], lats[0], lons[1], lats[1]])
    assert basin_cut_sides(cfg) == ["east"]
    basin[:] = 0
    basin[10:30, 10:30] = 1
    with rasterio.open(tmp_path / "watershed.tif", "w", dtype="uint8", nodata=0, **prof) as r:
        r.write(basin, 1)
    assert basin_cut_sides(cfg) == []
    assert grow_box([85.0, 27.0, 85.4, 27.2], ["east", "north"]) == [85.0, 27.0, 85.6, 27.3]
    model = tmp_path / "model.yaml"
    model.write_text("# keep me\nDEM_BOUNDS_WGS84: [1, 2, 3, 4]   # old\nDEM_SCALE_M: 90\n")
    write_box(SimpleNamespace(model=str(model)), [0.5, 2, 3, 4.5])
    text = model.read_text()
    assert text.startswith("# keep me\nDEM_BOUNDS_WGS84: [0.5, 2, 3, 4.5]")
    assert yaml.safe_load(text)["DEM_SCALE_M"] == 90


def test_service_files(tmp_path, monkeypatch):
    from lynch_forecast import service
    unit = service.unit_text(str(tmp_path), ["-p", str(tmp_path / "places.yaml")])
    assert "Restart=always" in unit and f"WorkingDirectory={tmp_path}" in unit
    assert "-m lynch_forecast serve -p " + str(tmp_path / "places.yaml") in unit
    said = []
    monkeypatch.setattr(service, "has_systemd", lambda: False)
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/" + name)
    assert service.install(str(tmp_path), ["-p", "places.yaml"], log=said.append,
                           dry_run=True) == 0
    assert "*/10 * * * *" in said[0] and service.CRON_MARK in said[0]


def test_doctor_says_what_to_fix(tmp_path, monkeypatch, capsys):
    import MRRpy.gee.auth as auth
    _place(tmp_path)
    (tmp_path / "places.yaml").write_text(yaml.safe_dump({"places": [
        "places/roshi/forecast.yaml"]}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auth, "status", lambda project=None: (False, "not signed in"))
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FIX   Earth Engine: not signed in" in out and "earth-engine-login" in out
    assert "Place roshi: not set up yet (new place)" in out
    monkeypatch.setattr(auth, "status", lambda project=None: (True, "Connected"))
    assert main(["doctor"]) == 0
