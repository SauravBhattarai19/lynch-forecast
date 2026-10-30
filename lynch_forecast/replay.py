# -*- coding: utf-8 -*-
"""
replay.py — ``lynch-forecast replay``: rehearse a past flood on a fake clock.

The forecast runs cycle after cycle from *start* to *end* as if it were live,
each seeing only the data that existed at its clock time.  Use station sources
that hold the past (``csv`` or ``gauge_files``, e.g. satellite rain prepared for
the event) and forecasts as they were issued (``open_meteo`` with
``api: single_run``).  It runs in ``replay/<start>/`` with its own states and
archive, sharing ``static/``, and writes ``replay_summary.csv``: the highest
expected flow at every point, for every version and cycle.
"""

import dataclasses
import os
import shutil
from datetime import datetime, timedelta

import pandas as pd

from .cycle import STAMP, run_cycle
from .sitefile import NO_RAIN


def replay(site, start, end, every_hours=3.0, warm_up_days=None, log=print, parallel=True):
    start = start.replace(second=0, microsecond=0)
    rdir = site.dir("replay", start.strftime(STAMP))
    os.makedirs(rdir, exist_ok=True)
    web = dict(site.website)
    web.pop("publish", None)
    rsite = dataclasses.replace(site, folder=rdir, website=web)
    if not os.path.lexists(rsite.dir("static")):
        os.symlink(site.dir("static"), rsite.dir("static"), target_is_directory=True)
    for f in site.rain_forecasts:
        if f.source == "open_meteo" and f.options.get("api", "forecast") != "single_run":
            log(f"  Note: '{f.name}' reads today's forecast service, not the forecast issued "
                "then; set api: single_run for a fair rehearsal.")
    from .prepare import warm_up
    warm_up(rsite, now=start, days=warm_up_days, log=log, parallel=parallel,
            record_flood=False)
    rows = []
    t = start
    while t <= end:
        st = run_cycle(rsite, now=t, log=log, publish=False, parallel=parallel)
        keep = os.path.join(rdir, "by_cycle", t.strftime(STAMP))
        if os.path.isdir(rsite.dir("site", "data")):
            shutil.rmtree(keep, ignore_errors=True)
            shutil.copytree(rsite.dir("site", "data"), keep)
            latest = os.path.join(keep, "latest.json")
            if os.path.isfile(latest):
                import json
                with open(latest) as f:
                    js = json.load(f)
                for p in js.get("points", []):
                    for v, pk in p.get("peaks", {}).items():
                        rows.append({"cycle_utc": t, "point": p["name"], "version": v,
                                     "peak_m3s": pk["q"], "peak_utc": pk["utc"],
                                     "level": pk.get("level") or ""})
        log(f"  Replay {t:%Y-%m-%d %H:%M}: {'ok' if st.get('ok') else 'FAILED'}")
        t += timedelta(hours=float(every_hours))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(rdir, "replay_summary.csv"), index=False)
    return df


def parse_time(text):
    t = datetime.fromisoformat(str(text).strip().replace("Z", ""))
    return t.replace(tzinfo=None)


__all__ = ["replay", "parse_time", "NO_RAIN"]
