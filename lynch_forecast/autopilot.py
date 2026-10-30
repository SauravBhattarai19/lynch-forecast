# -*- coding: utf-8 -*-
"""
autopilot.py — what ``lynch-forecast serve`` does by itself, so that running the
forecast only means editing YAML files:

* **Settings are read again every round.**  A file with a mistake is reported
  and the last good version of it is kept, so one typo never stops the others.
* **A new place is set up by itself** (terrain, Earth Engine layers, warm-up,
  flood-map DEMs), after the other places have updated, and gets its first
  forecast straight away.
* **Changed model settings are set up again.**  The old ``static/`` and
  ``states/`` no longer fit the model, so they are moved to ``old/<time>/``.
* **A flood area or basin map added or changed gets its DEMs.**

What each place was prepared for is kept in ``static/setup.json``: fingerprints
of the model settings and of the flood maps, or the error of the last attempt,
which is tried again after ``RETRY_HOURS``.
"""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta

RETRY_HOURS = 1.0
STAMP = "%Y-%m-%dT%H:%MZ"


def _utcnow():
    return datetime.utcnow().replace(second=0, microsecond=0)


def _hash(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


# Solver accuracy and reporting: they change how a run steps, not the terrain, the
# layers or what a saved state means, so changing them needs no new setup.
RUN_ONLY = ("IMPLICIT_TOL", "IMPLICIT_MAX_ITERS", "IMPLICIT_CFL_TARGET", "MASS_BALANCE_REPORT",
            "BACKEND")


def model_fingerprint(site):
    """The model settings as read (comments, order, the soil date and the
    RUN_ONLY settings left out)."""
    import yaml
    with open(site.model, encoding="utf-8") as f:
        text = f.read()
    if site.model.endswith(".py"):
        return _hash(text)
    data = yaml.safe_load(text) or {}
    if isinstance(data, dict):
        data.pop("EVENT_START_UTC", None)          # written by setup itself
        for key in RUN_ONLY:
            data.pop(key, None)
    return _hash(data)


def maps_fingerprint(site):
    """What the flood maps' DEMs depend on."""
    areas = [[a.name, list(a.box), a.method, a.dem, a.dem_scale_m] for a in site.flood_areas]
    bm = site.basin_map
    basin = None if bm is None else [bm.name, bm.method, bm.boxes, bm.overlap_km, bm.dem,
                                     bm.dem_scale_m, bm.box_cells]
    return _hash({"areas": areas, "basin_map": basin})


def record_path(site):
    return site.dir("static", "setup.json")


def read_record(site):
    try:
        with open(record_path(site), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(site, rec):
    p = record_path(site)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".part", "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(p + ".part", p)


def _was_set_up(site):
    """Set up before setup.json existed: terrain and at least one saved state."""
    from .cycle import model_config
    from .states import list_states
    try:
        return os.path.isfile(model_config(site).ROUTING_DEM_PATH) and bool(list_states(site))
    except Exception:                               # noqa: BLE001 — a bad model is 'not set up'
        return False


@dataclass
class State:
    need: str          # '' (nothing to do) | 'setup' | 'maps' | 'wait'
    can_run: bool      # set up for these model settings: cycles may run
    note: str = ""


def place_state(site, now=None):
    """What *site* needs before (or besides) its next cycle → :class:`State`."""
    now = now or _utcnow()
    rec = read_record(site)
    model_fp, maps_fp = model_fingerprint(site), maps_fingerprint(site)
    if rec is None and _was_set_up(site):
        rec = {"model": model_fp, "maps": maps_fp, "set_up_utc": None,
               "note": "set up before setup.json existed"}
        _save(site, rec)
    rec = rec or {}
    can_run = rec.get("model") == model_fp
    if not can_run:
        need = "setup"
    elif rec.get("maps") != maps_fp:
        if not site.has_maps():                   # maps taken away: nothing to prepare
            rec["maps"] = maps_fp
            _save(site, rec)
            return State("", True)
        need = "maps"
    else:
        return State("", True)
    failed = rec.get("failed") or {}
    if (failed.get("what") == need and failed.get("model") == model_fp
            and failed.get("maps") == maps_fp):
        since = now - datetime.strptime(failed["utc"], STAMP)
        if since < timedelta(hours=RETRY_HOURS):
            left = timedelta(hours=RETRY_HOURS) - since
            return State("wait", can_run, f"{need} failed at {failed['utc']} UTC "
                         f"(tried again in {left.seconds // 60} min): {failed['error']}")
    why = ("new place" if not rec.get("model") else "model settings changed") \
        if need == "setup" else "flood maps added or changed"
    return State(need, can_run, why)


def _move_aside(site, log):
    """Keep the old static/ and states/ in old/<time>/ (they fit the old model)."""
    stamp = _utcnow().strftime("%Y%m%dT%H%MZ")
    for d in ("static", "states"):
        src = site.dir(d)
        if os.path.isdir(src):
            dst = site.dir("old", stamp, d)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            log(f"  {d}/ moved to {os.path.relpath(dst, site.folder)} (it fits the old model)")


def prepare(site, need, log=print, parallel=True):
    """Do what :func:`place_state` said (*need* 'setup' or 'maps') → True if done.

    The outcome is recorded in static/setup.json either way."""
    from .prepare import latest_flood_run, map_dems, setup_site
    rec = read_record(site) or {}
    now = _utcnow().strftime(STAMP)
    try:
        if need == "setup":
            if rec.get("model"):                    # set up before, for other settings
                _move_aside(site, log)
            rec = {}
            setup_site(site, log=log, parallel=parallel)
            rec["set_up_utc"] = now
        else:
            map_dems(site, latest_flood_run(site), log)
            rec.pop("failed", None)
        # after setup: it may have written into the model file (the basin's box)
        rec.update(model=model_fingerprint(site), maps=maps_fingerprint(site), maps_utc=now)
        _save(site, rec)
        return True
    except Exception as exc:                        # noqa: BLE001 — recorded, retried later
        rec["failed"] = {"what": need, "utc": now, "error": str(exc).strip()[:600],
                         "model": model_fingerprint(site), "maps": maps_fingerprint(site)}
        _save(site, rec)
        log(f"[ERROR] {need} of '{site.name}' failed: {exc}")
        return False


def record_manual(site, ok, error=None, maps_only=False):
    """'lynch-forecast setup' run by hand: record it the same way."""
    rec = read_record(site) or {}
    now = _utcnow().strftime(STAMP)
    if ok:
        rec.pop("failed", None)
        if not maps_only:
            rec["set_up_utc"] = now
            rec["model"] = model_fingerprint(site)
        rec.update(maps=maps_fingerprint(site), maps_utc=now)
    else:
        rec["failed"] = {"what": "maps" if maps_only else "setup", "utc": now,
                         "error": str(error).strip()[:600], "model": model_fingerprint(site),
                         "maps": maps_fingerprint(site)}
    _save(site, rec)


class Settings:
    """The places file and every forecast file, read again each round; a file with
    a mistake keeps its last good version (reported in :attr:`problems`)."""

    def __init__(self, places=None, forecast=None):
        self.places_path, self.forecast_path = places, forecast
        self.portal, self.sites, self.problems = None, {}, []

    def read(self):
        """→ (portal or None, [(place id or None, site)])."""
        from . import load_site
        from .places import load_portal
        self.problems = []
        if self.places_path:
            try:
                self.portal = load_portal(self.places_path)
            except Exception as exc:                # noqa: BLE001 — keep the last good one
                if self.portal is None:
                    raise
                self.problems.append(f"{self.places_path}: {exc} (the last good version "
                                     "is used)")
            fresh = {}
            for p in self.portal.places:
                try:
                    fresh[p.id] = load_site(p.forecast)
                except Exception as exc:            # noqa: BLE001
                    if p.id in self.sites:
                        fresh[p.id] = self.sites[p.id]
                        self.problems.append(f"{p.forecast}: {exc} (the last good version "
                                             "is used)")
                    else:
                        self.problems.append(f"{p.forecast}: {exc} (left out until fixed)")
            self.sites = fresh
            return self.portal, [(pid, s) for pid, s in self.sites.items()]
        try:
            self.sites = {None: load_site(self.forecast_path or "forecast.yaml")}
        except Exception as exc:                    # noqa: BLE001
            if not self.sites:
                raise
            self.problems.append(f"{exc} (the last good version is used)")
        return None, list(self.sites.items())
