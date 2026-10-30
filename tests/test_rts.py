# -*- coding: utf-8 -*-
"""The RTS (rts.wscada.net) station reader and the sign-in file, offline: a fake
RTS server shaped like the real one (read 28 Oct 2026)."""

import json
import os
import subprocess
import urllib.parse
from datetime import datetime, timedelta

import pytest

from lynch_forecast.credentials import credentials
from lynch_forecast.sources import Context, all_sources
from lynch_forecast.sources import rts
from lynch_forecast.sources._http import FeedError

NOW = datetime(2026, 10, 28, 14, 4)


class FakeRts:
    """Answers like RTS: a token from auth/login, a station, its series and readings."""

    def __init__(self, password="right"):
        self.password, self.calls, self.issued = password, [], 0
        self.expire_next = False

    def post(self, url, data, headers=None):
        self.calls.append(("POST", url))
        body = json.loads(data)
        if body["password"] != self.password:
            raise FeedError("auth/login did not answer (HTTP 401 Unauthorized)", code=401)
        self.issued += 1
        return json.dumps({"token": f"t{self.issued}"})

    def get_json(self, url, headers=None):
        self.calls.append(("GET", url))
        if headers.get("Authorization") != f"Bearer t{self.issued}" or self.expire_next:
            self.expire_next = False
            raise FeedError("expired (HTTP 401 Unauthorized)", code=401)
        path, _, query = url[len(rts.SERVER):].partition("?")
        if path == "api/station/":
            return [{"id": 468, "name": "AWS at Roshi", "latitude": 27.5736, "longitude": 85.4873}]
        if path == "api/station/468":
            return {"id": 468, "name": "AWS at Roshi", "latitude": 27.5736, "longitude": 85.4873,
                    "data_source": [{"parameters": [
                        {"parameter_id": 453, "parameter_code": "PCPN_10M"},
                        {"parameter_id": 528, "parameter_code": "SM_10_10M"}]}]}
        if path == "api/station/468/data-series":
            return [{"id": 7238, "parameter_id": 453}, {"id": 7204, "parameter_id": 528}]
        if path == "api/station/471":
            return {"id": 471, "name": "Roshi Khola at Panauti", "latitude": 27.5700,
                    "longitude": 85.5389, "data_source": [{"parameters": [
                        {"parameter_id": 600, "parameter_code": "WTR_LVL"}]}]}
        if path == "api/station/471/data-series":
            return [{"id": 7300, "parameter_id": 600}]
        if path == "api/observation/index":
            q = urllib.parse.parse_qs(query)
            a = datetime.fromisoformat(q["date_from"][0].rstrip("Z"))
            b = datetime.fromisoformat(q["date_to"][0].rstrip("Z"))
            t = a.replace(minute=a.minute - a.minute % 10, second=0) + timedelta(minutes=10)
            out = []
            while t <= b:
                value = {"7238": "0.2", "7300": "1342.8"}.get(q["series_id"][0], "0.31")
                flag = 1 if t == datetime(2026, 10, 28, 12, 0) else 0      # one flagged reading
                out.append({"time": t.strftime("%Y-%m-%dT%H:%M:00.000Z"), "value": value,
                            "qc_flag": flag, "series_id": q["series_id"][0]})
                t += timedelta(minutes=10)
            return list(reversed(out))                                       # newest first, as RTS
        raise AssertionError(url)


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setenv("RTS_USERNAME", "me")
    monkeypatch.setenv("RTS_PASSWORD", "right")
    rts._TOKENS.clear()


def _read(tmp_path, fake, now=NOW, archived=None, **spec):
    ctx = Context(now=now, cache_dir=str(tmp_path), archived=archived or {})
    return rts.read_station(dict({"name": "AWS at Roshi", "station": 468}, **spec), ctx,
                            client=rts.Rts(session=fake))[0]


def test_reads_rain_and_soil_in_utc(signed_in, tmp_path):
    fake = FakeRts()
    st = _read(tmp_path, fake, backfill_days=2)
    assert (st.name, st.step_s, st.lat) == ("AWS at Roshi", 600, 27.5736)
    assert st.rain.index.is_monotonic_increasing and st.rain.index.max() == datetime(2026, 10, 28, 14, 0)
    assert datetime(2026, 10, 28, 12, 0) not in st.rain.index                # flagged: left out
    assert len(st.rain) == 2 * 144 - 1 and st.rain.eq(0.2).all()
    assert set(st.extra) == {"SM_10_10M"}                                    # SM_30 not at this station
    assert "rts" in all_sources()["station"]


def test_reads_go_back_from_the_archive_s_newest_reading(signed_in, tmp_path):
    fake = FakeRts()
    _read(tmp_path, fake, backfill_days=40)                                  # empty archive:
    first = [c for c in fake.calls if "observation" in c[1]]                 # two 31-day pieces
    assert len(first) == 2 * 2
    fake.calls.clear()
    later = NOW + timedelta(minutes=30)
    st = _read(tmp_path, fake, now=later, archived={"AWS at Roshi": datetime(2026, 10, 28, 14, 0)},
               backfill_days=40)
    asked = [urllib.parse.parse_qs(c[1].split("?", 1)[1]) for c in fake.calls if "observation" in c[1]]
    assert len(asked) == 2 and asked[0]["date_from"] == ["2026-10-28T11:00:00Z"]   # 3 h overlap
    assert not [c for c in fake.calls if c[1].endswith("data-series")]       # station kept a day
    assert st.rain.index.min() > NOW - timedelta(hours=4)
    fake.calls.clear()                                                       # nothing archived
    _read(tmp_path, fake, now=later, backfill_days=2)                        # (e.g. 'check'):
    asked = [urllib.parse.parse_qs(c[1].split("?", 1)[1]) for c in fake.calls if "observation" in c[1]]
    assert asked[0]["date_from"] == ["2026-10-26T14:34:00Z"]                 # the whole back-fill


def test_reads_a_river_gauge_s_water_level(signed_in, tmp_path):
    fake = FakeRts()
    ctx = Context(now=NOW, cache_dir=str(tmp_path))
    got = rts.read_level({"name": "Panauti gauge", "station": 471, "backfill_days": 1}, ctx,
                         client=rts.Rts(session=fake))
    assert got.level.eq(1342.8).all() and got.level.index.max() == datetime(2026, 10, 28, 14, 0)
    assert got.info == {"gauge": "Roshi Khola at Panauti", "station": 471} and got.lat == 27.57
    regs = all_sources()                                                     # the readers
    assert regs["level"]["rts"].func is rts.read_level                       # the forecast
    assert regs["station"]["rts"].func is rts.read_station                   # file reaches
    with pytest.raises(FeedError, match="no WTR_LVL series"):                # a rain station
        rts.read_level({"name": "x", "station": 468}, ctx, client=rts.Rts(session=fake))


def test_one_sign_in_and_again_when_it_expires(signed_in, tmp_path):
    fake = FakeRts()
    _read(tmp_path, fake, backfill_days=1)
    assert sum(c[0] == "POST" for c in fake.calls) == 1
    fake.expire_next = True
    _read(tmp_path / "other", fake, backfill_days=1)
    assert sum(c[0] == "POST" for c in fake.calls) == 2


def test_wrong_password_says_so_without_the_password(monkeypatch, tmp_path):
    monkeypatch.setenv("RTS_USERNAME", "me")
    monkeypatch.setenv("RTS_PASSWORD", "wrong-secret")
    rts._TOKENS.clear()
    with pytest.raises(FeedError, match="refused the sign-in") as err:
        _read(tmp_path, FakeRts())
    assert "wrong-secret" not in str(err.value)


def test_credentials_from_the_file_or_the_environment(monkeypatch, tmp_path):
    f = tmp_path / "credentials.env"
    f.write_text("GSS_USERNAME=g\nRTS_USERNAME=alice\nRTS_PASSWORD=p@ss w=rd\n")
    monkeypatch.setenv("LYNCH_FORECAST_CREDENTIALS", str(f))
    monkeypatch.delenv("RTS_USERNAME", raising=False)
    monkeypatch.delenv("RTS_PASSWORD", raising=False)
    assert credentials("rts") == ("alice", "p@ss w=rd")
    monkeypatch.setenv("RTS_USERNAME", "bob")
    monkeypatch.setenv("RTS_PASSWORD", "env")
    assert credentials("RTS") == ("bob", "env")                               # Actions secrets win
    with pytest.raises(FeedError, match="save-credentials.sh gss"):
        credentials("GSS")


def test_save_credentials_script_refuses_without_a_terminal(tmp_path):
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts",
                          "save-credentials.sh")
    out = subprocess.run(["bash", script], input="me\nx\nx\n", capture_output=True, text=True,
                         env=dict(os.environ, LYNCH_FORECAST_CREDENTIALS=str(tmp_path / "c.env")))
    assert out.returncode == 1 and "terminal" in out.stderr
    assert not (tmp_path / "c.env").exists()
