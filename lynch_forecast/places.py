# -*- coding: utf-8 -*-
"""
places.py — several places, one web site.

``places.yaml`` lists the places, each a folder with its own ``forecast.yaml``
and ``model.yaml``::

    title: Flood forecasts (experimental)
    places:
      - places/lynch/forecast.yaml
      - places/hanging_moss/forecast.yaml
    publish: {git: {remote: https://github.com/USER/REPO.git}}   # optional;
                                                    # state: false → pages only

``build`` writes the site to ``public/`` (``output:``):

* ``index.html`` — every place on a map and as a card, in plain words;
* ``places.json`` — what the cards show;
* ``<place>/`` — that place's own page (a copy of its ``site/``), the place
  being the name of its folder.

A place's own page links back to the front page when it sits in one.
"""

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import yaml

from .sitefile import SiteFileError, _did_you_mean, load_site, slug

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
PORTAL_FILES = (("portal.html", "index.html"), ("portal.js", "portal.js"),
                ("style.css", "style.css"), ("leaflet.css", "leaflet.css"))
KEYS = {"title": "the front page's title", "notice": "a line added to the notice",
        "places": "the places: each a forecast.yaml",
        "every_minutes": "minutes between rounds of updates (serve)",
        "output": "the folder the site is written to (default public)",
        "publish": "where the site goes: {git: {remote: …}} or {command: …}"}


@dataclass
class Place:
    id: str                       # the folder name, also the place's web address
    forecast: str                 # its forecast.yaml (absolute)


@dataclass
class Portal:
    path: str
    folder: str
    title: str = "Flood forecasts (experimental)"
    notice: str = ""
    every_minutes: int = 30
    output: str = "public"
    places: list = field(default_factory=list)
    publish: dict = field(default_factory=dict)

    def out(self, *parts):
        return os.path.join(self.folder, self.output, *parts)


def parse_portal(data, path="places.yaml"):
    """A :class:`Portal` from the YAML mapping, or SiteFileError listing every problem."""
    folder = os.path.dirname(os.path.abspath(path))
    errors = []
    if not isinstance(data, dict):
        raise SiteFileError(f"{path}: expected a mapping (title:, places:, …)")
    for key in data:
        if key not in KEYS:
            errors.append(f"unknown key '{key}'{_did_you_mean(key, KEYS)}")
    places, seen = [], {}
    raw = data.get("places") or []
    if not isinstance(raw, list) or not raw:
        errors.append("places: list at least one forecast file, e.g. "
                      "places: [places/lynch/forecast.yaml]")
        raw = []
    for item in raw:
        fc = os.path.normpath(os.path.join(folder, str(item)))
        pid = slug(os.path.basename(os.path.dirname(fc)) or "place")
        if not os.path.isfile(fc):
            errors.append(f"places: file not found: {item}")
        elif pid in seen:
            errors.append(f"places: {item} and {seen[pid]} are both in a folder called "
                          f"'{pid}'; give each place its own folder")
        else:
            seen[pid] = item
            places.append(Place(pid, fc))
    try:
        every = int(data.get("every_minutes", 30))
        if every < 5:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("every_minutes: a whole number of minutes, at least 5")
        every = 30
    pub = data.get("publish") or {}
    if pub and not (isinstance(pub, dict) and ("command" in pub or
                                              isinstance(pub.get("git"), dict)
                                              and pub["git"].get("remote"))):
        errors.append("publish: {git: {remote: …}} or {command: \"…\"}")
    if errors:
        raise SiteFileError(f"{path} has {len(errors)} problem(s):\n"
                            + "\n".join(f"  • {e}" for e in errors))
    return Portal(path=os.path.abspath(path), folder=folder,
                  title=str(data.get("title") or Portal.title),
                  notice=str(data.get("notice") or ""), every_minutes=every,
                  output=str(data.get("output") or "public"), places=places, publish=pub)


def load_portal(path):
    if not os.path.isfile(path):
        raise SiteFileError(f"places file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return parse_portal(yaml.safe_load(f), path)


def sites(portal):
    """[(Place, Site)] — every place's forecast file, read and checked."""
    return [(p, load_site(p.forecast)) for p in portal.places]


# ── The front page ───────────────────────────────────────────────────────────

def short_name(name):
    """'Roshi Khola flood forecast (experimental)' → 'Roshi Khola', for lists of places."""
    short = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    short = re.sub(r"\s+flood\s+forecasts?$", "", short, flags=re.I)
    return short.strip() or name


def _card(place, site, every_minutes, now):
    """What the front page says about one place (from its latest.json)."""
    latest_path = site.dir("site", "data", "latest.json")
    card = {"id": place.id, "name": site.website.get("title") or site.name,
            "short": short_name(site.name), "timezone": site.display_timezone,
            "has_forecast": False}
    if not os.path.isfile(latest_path):
        return card
    with open(latest_path, encoding="utf-8") as f:
        latest = json.load(f)
    cycle = datetime.strptime(latest["cycle_utc"], "%Y-%m-%dT%H:%MZ")
    main = latest.get("main")
    points = []
    rank = {None: 0, "normal": 1, "warning": 2, "danger": 3}
    for pt in latest.get("points", []):
        peaks = {v: p for v, p in (pt.get("peaks") or {}).items() if p and p.get("q") is not None}
        fc = {v: p for v, p in peaks.items() if v != "No more rain"}
        forecasts = [p["q"] for p in fc.values()]
        m = peaks.get(main) or {}
        worst = max((p.get("level") for p in fc.values()), key=lambda x: rank.get(x, 0),
                    default=None)
        points.append({"id": pt.get("id"), "name": pt["name"], "main_q": m.get("q"),
                       "main_utc": m.get("utc"), "level": m.get("level"), "now_q": pt.get("now_q"),
                       "low_q": min(forecasts) if forecasts else None,
                       "high_q": max(forecasts) if forecasts else None,
                       "worst": worst, "n_forecasts": len(fc),
                       "n_above_warning": sum(p.get("level") in ("warning", "danger")
                                              for p in fc.values()),
                       "has_levels": pt.get("warning_m3s") is not None
                       or pt.get("danger_m3s") is not None})
    basin_map = next((a for a in latest.get("areas", []) if a.get("basin") and a.get("ok")), None)
    card.update(has_forecast=True, name=latest.get("title") or card["name"],
                cycle_utc=latest["cycle_utc"], t_obs_utc=latest.get("t_obs_utc"),
                main=main, forecast_hours=latest.get("forecast_hours"), points=points,
                late=(now - cycle) > timedelta(minutes=2.5 * every_minutes),
                rain_next_mm=(latest.get("rain") or {}).get("next_mm", {}).get(main),
                worst=max((p["worst"] for p in points), key=lambda x: rank.get(x, 0), default=None),
                flooded_km2=basin_map.get("flooded_km2") if basin_map else None,
                stations=len(latest.get("stations", [])),
                stations_ok=sum(bool(x.get("ok")) for x in latest.get("stations", [])))
    return card


def build(portal, now=None, log=print, items=None):
    """Write the whole site to public/ → its folder.  *items*: the [(Place, Site)]
    already read (default: every place's file read now)."""
    now = now or datetime.utcnow()
    out = portal.out()
    os.makedirs(out, exist_ok=True)
    cards = []
    for place, site in (sites(portal) if items is None else items):
        src, dst = site.dir("site"), portal.out(place.id)
        if os.path.isdir(src):
            tmp = dst + ".part"
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp)
            shutil.rmtree(dst, ignore_errors=True)
            os.replace(tmp, dst)
        cards.append(_card(place, site, portal.every_minutes, now))
        log(f"  Place {place.id}: " + ("page copied" if os.path.isdir(src)
                                      else "no forecast yet"))
    ids = {p.id for p in portal.places}
    for name in os.listdir(out):                     # places taken off the list
        full = os.path.join(out, name)
        if os.path.isdir(full) and name not in ids and \
                os.path.isfile(os.path.join(full, "data", "latest.json")):
            shutil.rmtree(full)
    with open(os.path.join(out, "places.json.part"), "w", encoding="utf-8") as f:
        json.dump({"title": portal.title, "notice": portal.notice,
                   "built_utc": now.strftime("%Y-%m-%dT%H:%MZ"), "places": cards}, f, indent=1)
    os.replace(os.path.join(out, "places.json.part"), os.path.join(out, "places.json"))
    for src, dst in PORTAL_FILES:
        shutil.copyfile(os.path.join(WEB_DIR, src), os.path.join(out, dst))
    open(os.path.join(out, ".nojekyll"), "w").close()
    log(f"Site written: {out} ({len(cards)} place(s))")
    return out


def publish(portal, log=print, items=None):
    """Put public/ online, and every place's newest states (git only)."""
    from . import publish as pub
    p = portal.publish
    if not p:
        log("  Not published: places.yaml has no publish:.")
        return
    if "command" in p:
        pub.run_command(p["command"], site=portal.out(), folder=portal.folder)
        log("  Published with the command.")
        return
    git = p["git"]
    work = os.path.join(portal.folder, ".publish")
    pub.push_folder(portal.out(), git["remote"], git.get("pages_branch", "gh-pages"),
                    os.path.join(work, "pages"), f"forecast {datetime.utcnow():%Y-%m-%d %H:%M} UTC")
    if git.get("state") is False:
        log(f"  Published to {git['remote']} (gh-pages; states kept on this computer).")
        return
    bundle = os.path.join(work, "state_src")
    shutil.rmtree(bundle, ignore_errors=True)
    for place, site in (sites(portal) if items is None else items):
        pub.state_bundle(site, os.path.join(bundle, place.id))
    pub.push_folder(bundle, git["remote"], git.get("state_branch", "state"),
                    os.path.join(work, "state"), "states")
    log(f"  Published to {git['remote']} (gh-pages and state).")
