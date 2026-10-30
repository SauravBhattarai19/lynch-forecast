# -*- coding: utf-8 -*-
"""The US pieces: design storms from NOAA Atlas 14, the USGS gauge reader and the
forecast whose past stands in for station rain."""

import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from lynch_forecast.design import (ARI_YEARS, DURATIONS_MIN, _pfds_arrays, depths_for,
                                   nested_storm)
from lynch_forecast.sitefile import SiteFileError, parse_site
from lynch_forecast.sources import Context
from lynch_forecast.sources import usgs

# Atlas 14 depths (mm) at Lynch Creek for 10 min … 24 h, 10 and 25 years (Volume 9).
ATLAS = {"durations_min": DURATIONS_MIN[:10], "ari_years": ARI_YEARS,
         "quantiles": [[12, 14, 17, 19, 22, 25, 27, 30, 33, 36],
                       [18, 20, 24, 28, 32, 36, 40, 44, 49, 53],
                       [22, 25, 30, 34, 40, 44, 49, 53, 59, 64],
                       [32, 36, 44, 50, 58, 65, 71, 78, 87, 94],
                       [42, 48, 58, 67, 79, 89, 99, 110, 124, 135],
                       [52, 60, 73, 84, 101, 114, 127, 141, 161, 176],
                       [59, 68, 83, 96, 115, 131, 147, 165, 189, 208],
                       [71, 81, 100, 116, 140, 160, 181, 203, 233, 258],
                       [83, 95, 117, 136, 164, 186, 209, 234, 268, 294],
                       [96, 110, 135, 157, 187, 211, 235, 260, 295, 322]]}


def test_nested_storm_holds_every_duration():
    d = depths_for(ATLAS, 10)
    blocks = nested_storm(d, hours=24, peak_at=0.5)
    assert len(blocks) == 144 and blocks.sum() == pytest.approx(157.0)
    for minutes in (10, 30, 60, 120, 180, 360, 720):
        k = minutes // 10
        wettest = np.convolve(blocks, np.ones(k), "valid").max()
        assert wettest == pytest.approx(d[minutes], rel=1e-6), minutes
    assert int(np.argmax(blocks)) == 72                     # the peak half-way through


def test_return_periods_between_atlas14_ones_on_the_gumbel_scale():
    d10, d20, d25 = (depths_for(ATLAS, y)[1440] for y in (10, 20, 25))
    assert d10 < d20 < d25 and d20 == pytest.approx(179.8, abs=0.1)
    with pytest.raises(ValueError):
        depths_for(ATLAS, 2000)


def test_pfds_answer_is_read():
    text = ("result = 'values';\nquantiles = [['12', '14'], ['18', '20']];\n"
            "upper = [['15', '17'], ['21', '24']];\nvolume = '9';\nregion = 'Southeastern States';\n")
    arrays, meta = _pfds_arrays(text)
    assert arrays["quantiles"].tolist() == [[12, 14], [18, 20]]
    assert meta["volume"] == "9" and meta["region"] == "Southeastern States"


class FakeUSGS:
    def __init__(self, fail_iv=False):
        self.fail_iv, self.urls = fail_iv, []

    def get_json(self, url):
        self.urls.append(url)
        if "waterservices" in url:
            if self.fail_iv:
                raise usgs.FeedError("waterservices did not answer")
            return {"value": {"timeSeries": [{
                "sourceInfo": {"siteName": "LYNCH CREEK AT JACKSON, MS",
                               "geoLocation": {"geogLocation": {"latitude": 32.28, "longitude": -90.21}}},
                "values": [{"value": [
                    {"value": "3.59", "dateTime": "2026-10-29T19:00:00.000-05:00"},
                    {"value": "-999999", "dateTime": "2026-10-29T19:15:00.000-05:00"},
                    {"value": "4.00", "dateTime": "2026-10-29T19:30:00.000-05:00"}]}]}]}}
        return {"features": [{"properties": {"time": "2026-10-30T00:00:00+00:00", "value": "3.59"}}]}


def test_usgs_gage_height_in_metres_utc():
    got = usgs.read_usgs_level({"name": "Lynch", "site": "02486100"},
                               Context(now=datetime(2026, 10, 30, 1, 0)), session=FakeUSGS())
    assert list(got.level.index) == [pd.Timestamp("2026-10-30 00:00"), pd.Timestamp("2026-10-30 00:30")]
    assert got.level.iloc[0] == pytest.approx(3.59 * 0.3048)
    assert got.info["gauge"] == "LYNCH CREEK AT JACKSON, MS" and got.lat == 32.28


def test_usgs_falls_back_to_the_new_api():
    fake = FakeUSGS(fail_iv=True)
    got = usgs.read_usgs_level({"name": "Lynch", "site": "02486100"},
                               Context(now=datetime(2026, 10, 30, 1, 0)), session=fake)
    assert "api.waterdata.usgs.gov" in fake.urls[-1] and len(got.level) == 1


def test_usgs_site_must_be_a_number():
    with pytest.raises(usgs.FeedError):
        usgs.read_usgs_level({"name": "x", "site": "2486100 Lynch"}, Context(now=datetime(2026, 1, 1)))


def _forecast(**extra):
    return dict({"model": "model.yaml", "rain_forecasts": [
        {"name": "NBM", "source": "open_meteo", "model": "ncep_nbm_conus"},
        {"name": "HRRR", "source": "open_meteo", "model": "ncep_hrrr_conus"}]}, **extra)


def test_past_rain_forecast(tmp_path):
    (tmp_path / "model.yaml").write_text("{}\n")
    site = parse_site(_forecast(past_rain_forecast="HRRR"), str(tmp_path / "forecast.yaml"))
    assert (site.main_forecast, site.past_rain_forecast) == ("NBM", "HRRR")
    assert parse_site(_forecast(), str(tmp_path / "forecast.yaml")).past_rain_forecast is None
    with pytest.raises(SiteFileError, match="did you mean 'HRRR'"):
        parse_site(_forecast(past_rain_forecast="HRR"), str(tmp_path / "forecast.yaml"))


def test_hand_basin_map_keeps_its_boxes(tmp_path):
    (tmp_path / "model.yaml").write_text("{}\n")
    one = parse_site(_forecast(basin_map={"method": "hand"}), str(tmp_path / "f.yaml"))
    four = parse_site(_forecast(basin_map={"method": "hand", "boxes": [4, 4]}),
                      str(tmp_path / "f.yaml"))
    assert one.basin_map.boxes == [1, 1] and four.basin_map.boxes == [4, 4]


def test_solver_settings_need_no_new_setup(tmp_path):
    from lynch_forecast.autopilot import model_fingerprint
    (tmp_path / "model.yaml").write_text("DEM_SCALE_M: 10\nIMPLICIT_TOL: 0.0001\n")
    site = parse_site(_forecast(), str(tmp_path / "forecast.yaml"))
    before = model_fingerprint(site)
    (tmp_path / "model.yaml").write_text("DEM_SCALE_M: 10\nIMPLICIT_TOL: 0.001\n")
    assert model_fingerprint(site) == before
    (tmp_path / "model.yaml").write_text("DEM_SCALE_M: 30\nIMPLICIT_TOL: 0.001\n")
    assert model_fingerprint(site) != before
