# -*- coding: utf-8 -*-
"""Several places, one web site: the places file, the front page's data and the
copied place pages."""

import json
import os
from datetime import datetime

import pytest
import yaml

from lynch_forecast import SiteFileError
from lynch_forecast.cli import main
from lynch_forecast.places import build, load_portal

MODEL = {"DEM_PATH": "dem.tif", "RUNOFF_SOURCE": "physical",
         "RUNOFF_MECHANISMS": ["impervious", "infiltration_excess"]}


def _place(root, name, title, latest=None):
    d = root / "places" / name
    d.mkdir(parents=True)
    (d / "model.yaml").write_text(yaml.safe_dump(MODEL))
    (d / "forecast.yaml").write_text(yaml.safe_dump({
        "name": title, "model": "model.yaml", "display_timezone": "Asia/Kathmandu",
        "rain_forecasts": [{"name": "ECMWF", "source": "open_meteo", "model": "ecmwf_ifs025"}],
        "main_forecast": "ECMWF",
        "hydrograph_points": [{"name": "Bridge", "lat": 27.58, "lon": 85.51,
                               "warning_m3s": 100}],
        "website": {"title": title}}, sort_keys=False))
    if latest:
        (d / "site" / "data").mkdir(parents=True)
        (d / "site" / "index.html").write_text("<p>page</p>")
        (d / "site" / "data" / "latest.json").write_text(json.dumps(latest))
    return f"places/{name}/forecast.yaml"


LATEST = {"title": "Roshi Khola", "cycle_utc": "2026-10-28T14:32Z",
          "t_obs_utc": "2026-10-28T14:20Z", "main": "ECMWF", "forecast_hours": 72,
          "rain": {"next_mm": {"ECMWF": 41.6, "No more rain": 0}},
          "points": [{"name": "Bridge", "warning_m3s": 100, "peaks": {
              "ECMWF": {"q": 120.5, "utc": "2026-10-30T02:00Z", "level": "warning"},
              "GFS": {"q": 30.0, "utc": "2026-10-29T20:00Z", "level": "normal"},
              "No more rain": {"q": 2.0, "utc": "2026-10-28T14:30Z", "level": "normal"}}}]}


@pytest.fixture
def two_places(tmp_path):
    a = _place(tmp_path, "roshi", "Roshi Khola", LATEST)
    b = _place(tmp_path, "nakhu", "Nakhu Khola")                    # not run yet
    (tmp_path / "places.yaml").write_text(yaml.safe_dump(
        {"title": "Flood forecasts", "places": [a, b]}))
    return tmp_path


def test_the_site_has_a_front_page_and_each_place(two_places):
    portal = load_portal(str(two_places / "places.yaml"))
    assert [p.id for p in portal.places] == ["roshi", "nakhu"]
    out = build(portal, now=datetime(2026, 10, 28, 14, 40), log=lambda *a: None)
    for f in ("index.html", "portal.js", "style.css", "leaflet.css", ".nojekyll",
              "roshi/index.html", "roshi/data/latest.json"):
        assert os.path.isfile(os.path.join(out, f)), f
    assert not os.path.exists(os.path.join(out, "nakhu"))
    cards = json.load(open(os.path.join(out, "places.json")))["places"]
    roshi, nakhu = cards
    assert (roshi["has_forecast"], roshi["late"], roshi["rain_next_mm"]) == (True, False, 41.6)
    pt = roshi["points"][0]
    assert (pt["main_q"], pt["level"], pt["low_q"], pt["high_q"], pt["has_levels"]) == \
        (120.5, "warning", 30.0, 120.5, True)                          # 'no more rain' left out
    assert nakhu == {"id": "nakhu", "name": "Nakhu Khola", "short": "Nakhu Khola",
                     "timezone": "Asia/Kathmandu", "has_forecast": False}
    assert (pt["worst"], pt["n_above_warning"], pt["n_forecasts"]) == ("warning", 1, 2)
    assert roshi["worst"] == "warning"


def test_short_place_names():
    from lynch_forecast.places import short_name
    assert short_name("Roshi Khola flood forecast (experimental)") == "Roshi Khola"
    assert short_name("Nakhu Khola") == "Nakhu Khola"
    assert short_name("Flood forecast") == "Flood forecast"


def test_a_late_place_and_a_place_taken_off_the_list(two_places):
    portal = load_portal(str(two_places / "places.yaml"))
    build(portal, now=datetime(2026, 10, 28, 16, 0), log=lambda *a: None)
    cards = json.load(open(portal.out("places.json")))["places"]
    assert cards[0]["late"]                                            # 88 min > 2.5 × 30
    (two_places / "places.yaml").write_text(yaml.safe_dump(
        {"places": ["places/nakhu/forecast.yaml"]}))
    build(load_portal(str(two_places / "places.yaml")), log=lambda *a: None)
    assert not os.path.exists(portal.out("roshi"))


def test_places_file_problems_are_listed(tmp_path):
    a = _place(tmp_path, "roshi", "Roshi")
    (tmp_path / "other").mkdir()
    (tmp_path / "places.yaml").write_text(yaml.safe_dump({
        "titel": "x", "every_minutes": 2, "publish": {"git": {}},
        "places": [a, "places/missing/forecast.yaml", "places/roshi/../roshi/forecast.yaml"]}))
    with pytest.raises(SiteFileError) as err:
        load_portal(str(tmp_path / "places.yaml"))
    text = str(err.value)
    for words in ("did you mean 'title'", "at least 5", "publish:", "file not found",
                  "both in a folder called 'roshi'"):
        assert words in text, words


def test_cli_site_and_status_for_every_place(two_places, capsys, monkeypatch):
    monkeypatch.chdir(two_places)                                      # places.yaml found here
    assert main(["site"]) == 0
    assert os.path.isfile(two_places / "public" / "places.json")
    main(["status"])
    out = capsys.readouterr().out
    assert "── roshi ──" in out and "── nakhu ──" in out


def test_pages_only_publishing_keeps_states_off_git(two_places, monkeypatch):
    from lynch_forecast import publish as pub
    from lynch_forecast import places
    pushed = []
    monkeypatch.setattr(pub, "push_folder", lambda src, remote, branch, *a: pushed.append(branch))
    (two_places / "places.yaml").write_text(yaml.safe_dump({"places": [
        "places/roshi/forecast.yaml"], "publish": {"git": {"remote": "x", "state": False}}}))
    portal = load_portal(str(two_places / "places.yaml"))
    places.build(portal, log=lambda *a: None)
    places.publish(portal, log=lambda *a: None)
    assert pushed == ["gh-pages"]
