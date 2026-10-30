# -*- coding: utf-8 -*-
"""Feed readers of the live forecast, offline: alertnepal's socket.io station frames
(recorded 28 Oct 2026, AWS 496) and its hourly forecast (Nepal time marked Z),
the sticky-session handshake, CSV readers, and the Open-Meteo wrapper on a fake
Open-Meteo server."""

import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from lynch_forecast.sources import Context, all_sources
from lynch_forecast.sources import alertnepal as an
from lynch_forecast.sources.files import read_csv_inflow, read_csv_station, read_gauge_files

DATA = os.path.join(os.path.dirname(__file__), "data")


def _frame():
    with open(os.path.join(DATA, "alertnepal_aws496.txt")) as f:
        return f.read()


def test_station_frame_is_read():
    data = an._event(_frame(), "aws_dashboard")
    st = an.station_from_dashboard(data[0])
    assert st.name == "AWS at Roshi"
    assert st.lat == pytest.approx(27.5736, abs=1e-3) and st.step_s == 600
    assert st.rain.index[0] == datetime(2026, 10, 27, 0, 0)            # UTC, naive
    assert np.all(np.diff(st.rain.index.values).astype("timedelta64[m]").astype(int) == 10)
    assert len(st.rain) == 149 and (st.rain >= 0).all()


def test_hourly_rain_when_10_minute_rain_is_missing():
    st = an._event(_frame(), "aws_dashboard")[0]
    st["observations"] = [o for o in st["observations"] if o["parameter_code"] != "PCPN_10M"]
    got = an.station_from_dashboard(st)
    assert got.step_s == 3600 and len(got.rain) == 25


def test_frames_with_several_packets():
    text = '1:3' + '18:42["other",{"a":1}]' + _frame().split(":", 1)[0] + ":" + \
        _frame().split(":", 1)[1]
    assert an._event(text, "aws_dashboard")[0]["id"] == 496
    assert an._event('5:hello', "aws_dashboard") is None


def test_sticky_session_handshake(monkeypatch):
    """The reader opens a session, sends its request with the session id and polls
    on the same Session (one cookie jar)."""
    calls = []

    class FakeSession:
        def get(self, url, headers=None):
            calls.append(("GET", url))
            if "sid=" not in url:
                op = '0{"sid":"abc123","upgrades":["websocket"],"pingInterval":25000}'
                return f"{len(op)}:{op}2:40"
            if sum(1 for c in calls if c[0] == "GET") == 2:
                return "1:6"                                    # a no-op before the data
            return _frame()

        def post(self, url, data, headers=None):
            calls.append(("POST", url, data))
            return "ok"

    st = an.fetch_dashboard(496, session=FakeSession())
    assert st["id"] == 496
    post = [c for c in calls if c[0] == "POST"][0]
    assert "sid=abc123" in post[1]
    n, msg = post[2].split(":", 1)
    assert int(n) == len(msg) and json.loads(msg[2:])[1]["location"] == {"id": 496}


def test_forecast_times_are_nepal_time_marked_z():
    js = json.load(open(os.path.join(DATA, "alertnepal_qpf.json")))
    now = datetime(2026, 10, 28, 3, 27)               # recorded at 03:27 UTC = 09:12 Nepal
    s, lat, lon, how = an.forecast_from_response(js, now)
    assert "Nepal" in how
    # first hour 09:00 Nepal = 03:15 UTC; an hour's rain is labelled with its end
    assert s.index[0] == datetime(2026, 10, 28, 4, 15)
    real_utc = dict(js, forecast=[dict(r, datetime=(datetime(2026, 10, 28, 3) + timedelta(
        hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")) for i, r in enumerate(js["forecast"])])
    s2, *_, how2 = an.forecast_from_response(real_utc, now)
    assert how2 == "UTC" and s2.index[0] == datetime(2026, 10, 28, 4, 0)


def test_csv_station_in_local_time(tmp_path):
    p = tmp_path / "g.csv"
    pd.DataFrame({"when": ["2026-03-01 05:45", "2026-03-01 06:00", "2026-03-01 06:15"],
                  "mm": [1.0, 2.0, 3.0]}).to_csv(p, index=False)
    spec = {"name": "G", "source": "csv", "path": str(p), "lat": 27.0, "lon": 85.0,
            "time_column": "when", "value_column": "mm", "timezone": "Asia/Kathmandu"}
    st = read_csv_station(spec, Context(now=datetime(2026, 3, 1, 0, 15)))[0]
    assert list(st.rain.index) == [datetime(2026, 3, 1, 0, 0), datetime(2026, 3, 1, 0, 15)]
    assert st.step_s == 900                                           # nothing after "now"


def test_csv_inflow_and_gauge_files(tmp_path, basin_cfg):
    p = tmp_path / "q.csv"
    pd.DataFrame({"time_utc": ["2026-03-01 00:00", "2026-03-01 01:00"],
                  "Q_m3s": [5.0, -1.0]}).to_csv(p, index=False)
    q = read_csv_inflow({"name": "q", "path": str(p)}, Context(now=datetime(2026, 3, 2))).flow
    assert list(q) == [5.0]                                           # negative readings dropped
    pd.DataFrame({"gauge_id": ["A"], "name": ["a"], "easting_m": [330450.0],
                  "northing_m": [3060450.0]}).to_csv(tmp_path / "gg.csv", index=False)
    pd.DataFrame({"time_s": [0, 1800, 3600], "A": [0.0, 2.0, 1.0]}).to_csv(
        tmp_path / "tt.csv", index=False)
    sts = read_gauge_files({"name": "IMERG", "gauges": str(tmp_path / "gg.csv"),
                            "timeseries": str(tmp_path / "tt.csv"),
                            "start_utc": "2024-09-27 00:00", "crs": "EPSG:32645"},
                           Context(now=datetime(2024, 9, 27, 1, 0)))
    # rows give the rain from their time on; stations label rain by its end
    assert sts[0].step_s == 1800 and list(sts[0].rain) == [0.0, 2.0]
    assert sts[0].rain.index[0] == datetime(2024, 9, 27, 0, 30)
    assert 26 < sts[0].lat < 28


@pytest.fixture
def basin_cfg(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box
    from MRRpy import Config
    gj = str(tmp_path / "watershed.geojson")
    gpd.GeoDataFrame(geometry=[box(85.25, 27.65, 85.35, 27.75)], crs="EPSG:4326").to_file(gj)
    cfg = Config(OUTPUT_DIR=str(tmp_path / "static"), TARGET_CRS_EPSG="EPSG:32645")
    cfg.update_output_paths()
    cfg.WATERSHED_GEOJSON = gj
    cfg.DEM_PATH = ""
    return cfg


class FakeOpenMeteo:
    """Answers like Open-Meteo's forecast API: 0.1° cells, 2 mm every hour."""

    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
        d0 = datetime.fromisoformat(q["start_date"]).replace(tzinfo=timezone.utc)
        d1 = datetime.fromisoformat(q["end_date"]).replace(tzinfo=timezone.utc) + timedelta(days=1)
        times = list(range(int(d0.timestamp()), int(d1.timestamp()), 3600))
        out = []
        for la, lo in zip(map(float, q["latitude"].split(",")), map(float, q["longitude"].split(","))):
            out.append({"latitude": round(np.floor(la * 10) / 10 + 0.05, 5),
                        "longitude": round(np.floor(lo * 10) / 10 + 0.05, 5),
                        "hourly": {"time": times, "precipitation": [2.0] * len(times)}})
        return out if len(out) > 1 else out[0]


def test_open_meteo_wrapper_uses_mrrpy_source_and_its_cache(basin_cfg, monkeypatch, tmp_path):
    from MRRpy.core.precip import openmeteo as om
    fake = FakeOpenMeteo()
    monkeypatch.setattr(om, "_http_get_json", fake)
    src = all_sources()["forecast"]["open_meteo"].func
    ctx = Context(now=datetime(2026, 3, 1, 12, 5), cfg=basin_cfg, cache_dir=str(tmp_path / "c"),
                  start=datetime(2026, 3, 1, 0), end=datetime(2026, 3, 3, 13), log=lambda *_: None)
    spec = {"name": "ECMWF", "source": "open_meteo", "model": "ecmwf_ifs025"}
    fc = src(spec, ctx)
    assert len(fc.points) >= 1 and fc.step_s == 3600
    assert fc.rain.index.min() <= datetime(2026, 3, 1, 1) and fc.rain.index.max() >= ctx.end
    assert (fc.rain.loc[datetime(2026, 3, 1, 5)] == 2.0).all()
    assert "models=ecmwf_ifs025" in fake.urls[0]
    n = len(fake.urls)
    src(spec, ctx)                                    # the same window: cached, no call
    assert len(fake.urls) == n
    ctx.start += timedelta(hours=1)
    ctx.end += timedelta(hours=1)
    src(spec, ctx)                                    # the window moved an hour: fetched
    assert len(fake.urls) > n


def test_single_run_takes_the_run_out_by_then():
    from lynch_forecast.sources.open_meteo import issued_run
    assert issued_run(datetime(2024, 9, 27, 10, 30)) == datetime(2024, 9, 27, 0)
    assert issued_run(datetime(2024, 9, 27, 13, 0), delay_hours=7) == datetime(2024, 9, 27, 6)
    assert issued_run(datetime(2024, 9, 27, 3, 0), run_hours=(0, 12)) == datetime(2024, 9, 26, 12)
