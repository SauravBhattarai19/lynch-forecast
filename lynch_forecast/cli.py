# -*- coding: utf-8 -*-
"""``lynch-forecast …`` — the command line of the live flood forecast."""

import argparse
import os
import sys
import time

WORKFLOW = """\
# Backup runner for the flood forecast (written by 'lynch-forecast github-workflow').
# It runs a cycle only when the main server has not published for {stale} minutes,
# carrying on from the server's last state (the 'state' branch). GitHub runs
# schedules on a best-effort basis and switches them off after 60 days without
# a commit to the repository.
name: flood-forecast-backup
on:
  schedule:
    - cron: "{cron}"
  workflow_dispatch: {{}}
permissions:
  contents: write
concurrency: {{group: flood-forecast, cancel-in-progress: false}}
jobs:
  cycle:
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {{python-version: "3.11"}}
      - run: pip install .            # this repository, with the MRRpy it names
      - name: Configure git for publishing
        run: |
          git config --global user.name "lynch-forecast (backup)"
          git config --global user.email "forecast@localhost"
          git config --global url."https://x-access-token:${{{{ secrets.GITHUB_TOKEN }}}}@github.com/".insteadOf "git@github.com:"
      - run: lynch-forecast run {target} --restore-state --only-if-stale {stale}
"""


def _site(args):
    from . import load_site
    return load_site(args.forecast or "forecast.yaml")


def _cmd_new(args):
    from . import template
    from MRRpy.interactive.render import save_yaml
    from MRRpy.config import Config
    fc = os.path.join(args.folder, "forecast.yaml")
    if os.path.exists(fc) and not args.force:
        print(f"[ERROR] {fc} already exists (use --force to replace it)", file=sys.stderr)
        return 1
    if args.outlet:
        return _new_from_outlet(args, fc)
    os.makedirs(args.folder, exist_ok=True)
    with open(fc, "w") as f:
        f.write(template())
    model = os.path.join(args.folder, "model.yaml")
    if not os.path.exists(model):
        save_yaml(Config(), model, full=False, source="lynch-forecast new")
    print(f"Written: {fc}")
    print(f"         {model}")
    print("Next:")
    print(f"  1. Set the basin in {model} (or: MRRpy wizard --edit {model})")
    print(f"  2. List your stations, forecasts, points and areas in {fc}")
    print(f"  3. lynch-forecast check -f {fc}")
    print(f"  4. lynch-forecast setup -f {fc}")
    print(f"  5. lynch-forecast run -f {fc}")
    return 0


def _new_from_outlet(args, fc):
    from .newplace import add_to_places, known_project, parse_outlet, write_place
    places = args.places or ("places.yaml" if os.path.isfile("places.yaml") else None)
    name = args.name or os.path.basename(os.path.normpath(args.folder)).replace("_", " ").title()
    project = args.gee_project or known_project(places)
    model, fc = write_place(args.folder, name, parse_outlet(args.outlet), args.timezone, project)
    print(f"Written: {model}\n         {fc}")
    added = places and add_to_places(places, fc)
    if added:
        print(f"Added to {places}.")
    print("Next:")
    print(f"  1. Put the towns, bridges or gauges to watch under hydrograph_points in {fc}")
    if not project:
        print(f"  -  Put your Earth Engine project in {model} (GEE_PROJECT)")
    if places:
        print("  2. Nothing else: a running 'lynch-forecast serve' (the service) sets it up "
              "and publishes it by itself. Follow it with: lynch-forecast doctor")
        print(f"     (or set it up now: lynch-forecast setup -f {fc})")
    else:
        print(f"  2. lynch-forecast setup -f {fc}   then   lynch-forecast run -f {fc}")
    return 0


def _cmd_check(args):
    from .check import check_site
    out = check_site(_site(args))
    bad = [x["name"] for k in ("stations", "forecasts", "points", "inflows")
           for x in out.get(k, []) if not x.get("ok")]
    if bad:
        print(f"Problems with: {', '.join(bad)}")
    return 1 if bad else 0


def _cmd_setup(args):
    from .autopilot import record_manual
    from .prepare import SetupError, latest_flood_run, map_dems, setup_site
    site = _site(args)
    try:
        if args.maps_only:
            map_dems(site, latest_flood_run(site))
        else:
            setup_site(site, force_terrain=args.terrain, warm=not args.no_warm_up,
                       parallel=not args.serial)
    except Exception as exc:                              # noqa: BLE001 — recorded, then shown
        record_manual(site, False, exc, maps_only=args.maps_only)
        if not isinstance(exc, SetupError):
            raise
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if args.maps_only or not args.no_warm_up:
        record_manual(site, True, maps_only=args.maps_only)
    return 0


def _targets(args):
    """(places file or None, [(place id, site)]): -p, else -f, else places.yaml or
    forecast.yaml in this folder."""
    from . import load_site
    from .places import load_portal, sites
    places = getattr(args, "places", None)
    if not places and not args.forecast and os.path.isfile("places.yaml"):
        places = "places.yaml"
    if places:
        portal = load_portal(places)
        return portal, [(p.id, site) for p, site in sites(portal)]
    site = load_site(args.forecast or "forecast.yaml")
    return None, [(None, site)]


def _run_once(site, args, git=None, subdir="", publish=True):
    from .cycle import run_cycle
    if getattr(args, "restore_state", False):
        from .publish import restore_state
        restore_state(site, git=git, subdir=subdir)
    return run_cycle(site, publish=publish and not args.no_publish, parallel=not args.serial,
                     only_if_stale=args.only_if_stale)


def _pairs(portal, items):
    by_id = {p.id: p for p in portal.places}
    return [(by_id[pid], site) for pid, site in items if pid in by_id]


def _round(args, portal, items, everyone=None):
    """One update of every place in *items*, then (several places) the site of
    *everyone* (default: every place's file read again) → exit code."""
    if portal is None:
        st = _run_once(items[0][1], args)
        return 0 if st.get("ok") or st.get("skipped") else 1
    from . import places
    bad, ran = [], False
    for pid, site in items:
        print(f"── {pid} ──")
        try:
            st = _run_once(site, args, git=(portal.publish or {}).get("git"), subdir=pid,
                           publish=False)
            ran = ran or not st.get("skipped")
            if not (st.get("ok") or st.get("skipped")):
                bad.append(pid)
        except Exception as exc:                          # noqa: BLE001 — the others go on
            print(f"[ERROR] {pid}: {exc}", file=sys.stderr)
            bad.append(pid)
    if ran or not args.only_if_stale:
        pairs = None if everyone is None else _pairs(portal, everyone)
        places.build(portal, items=pairs)
        if not args.no_publish:
            places.publish(portal, items=pairs)
    if bad:
        print(f"Updates failed for: {', '.join(bad)}", file=sys.stderr)
    return 1 if bad else 0


def _cmd_run(args):
    try:
        return _round(args, *_targets(args))
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


def _serve_lock(folder):
    """Hold a lock while serving, so a second serve here stops (→ the open file)."""
    try:
        import fcntl
    except ImportError:                                   # Windows: no lock
        return None
    f = open(os.path.join(folder, ".serve.lock"), "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.seek(0)
        pid = f.read().strip()
        f.close()
        raise RuntimeError("'lynch-forecast serve' is already running here"
                           + (f" (process {pid})" if pid else "")
                           + "; see: lynch-forecast service status")
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def tend(args, portal, items, log=print):
    """One round of serve: update the places that are ready, then set up new or
    changed places and flood maps (autopilot.py) → exit code."""
    from .autopilot import place_state, prepare
    ready, todo = [], []
    for pid, site in items:
        st = place_state(site)
        if st.can_run:
            ready.append((pid, site))
        if st.need in ("setup", "maps"):
            todo.append((pid, site, st))
        elif st.need == "wait":
            log(f"  {pid or site.name}: waiting — {st.note}")
    code = _round(args, portal, ready, items) if ready else 0
    for pid, site, st in todo:
        log(f"── {pid or site.name}: " + ("setting up" if st.need == "setup" else
                                        "preparing the flood maps' DEMs") + f" ({st.note}) ──")
        if not prepare(site, st.need, log=log, parallel=not args.serial):
            code = 1
        elif st.need == "setup":                           # its first forecast now
            code = max(code, _round(args, portal, [(pid, site)], items))
    return code


def _cmd_serve(args):
    from .autopilot import Settings
    for stream in (sys.stdout, sys.stderr):              # logs to a file or tee: line by line
        try:
            stream.reconfigure(line_buffering=True)
        except AttributeError:
            pass
    places = args.places or (None if args.forecast else
                             "places.yaml" if os.path.isfile("places.yaml") else None)
    settings = Settings(places, args.forecast)
    try:
        portal, items = settings.read()
        lock = _serve_lock(portal.folder if portal else items[0][1].folder)  # noqa: F841
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print("Serving: every round reads the settings again, updates each place, and sets "
          "up new or changed places by itself. Stop with Ctrl+C.")
    while True:
        t0 = time.time()
        try:
            portal, items = settings.read()
            for problem in settings.problems:
                print(f"[ERROR] {problem}", file=sys.stderr)
            tend(args, portal, items)
        except Exception as exc:                          # noqa: BLE001 — keep serving
            print(f"[ERROR] round failed: {exc}", file=sys.stderr)
        minutes = portal.every_minutes if portal else items[0][1].every_minutes
        every = 60.0 * minutes
        wait = every - ((time.time() - 120.0) % every)      # 2 min after each half hour
        print(f"Next round in {wait / 60:.0f} min (this one took {time.time() - t0:.0f} s).",
              flush=True)
        time.sleep(wait)


def _cmd_doctor(args):
    from .doctor import report
    places = args.places or (None if args.forecast else
                             "places.yaml" if os.path.isfile("places.yaml") else None)
    return report(places, args.forecast, feeds=args.feeds)


def _cmd_service(args):
    from . import service
    if args.action == "install":
        if args.places or (not args.forecast and os.path.isfile("places.yaml")):
            path = os.path.abspath(args.places or "places.yaml")
            target = ["-p", path]
        else:
            path = os.path.abspath(args.forecast or "forecast.yaml")
            target = ["-f", path]
        if not os.path.isfile(path):
            print(f"[ERROR] {path} not found", file=sys.stderr)
            return 1
        if args.no_publish:
            target.append("--no-publish")
        return service.install(os.path.dirname(path), target, name=args.name,
                               dry_run=args.dry_run)
    if args.action == "status":
        return service.status(args.name)
    if args.action == "logs":
        return service.logs(os.getcwd(), lines=args.lines, name=args.name)
    if args.action == "restart":
        return service.restart(os.getcwd(), name=args.name)
    return service.uninstall(args.name)


def _cmd_site(args):
    from . import places
    portal, _ = _targets(args)
    if portal is None:
        print("[ERROR] 'site' needs a places file (-p places.yaml)", file=sys.stderr)
        return 1
    places.build(portal)
    if args.publish:
        places.publish(portal)
    return 0


def _cmd_replay(args):
    from .replay import parse_time, replay
    df = replay(_site(args), parse_time(args.start), parse_time(args.end),
                every_hours=args.every, warm_up_days=args.warm_up_days,
                parallel=not args.serial)
    if len(df):
        print(df.groupby(["point", "version"])["peak_m3s"].max().round(1).to_string())
    return 0


def _cmd_design(args):
    from .design import run_design
    s = run_design(_site(args), parallel=not args.serial, maps=not args.no_maps)
    for pt in s["points"]:
        print(f"  {pt['name']}: " + ", ".join(f"{k} {v['q']:.1f} m³/s" for k, v in pt["peaks"].items()))
    return 0


def _status_one(site, json_too):
    import json
    from . import archive
    from .cycle import last_status
    st = last_status(site)
    if not st:
        print("No cycle has run yet.")
        return 1
    print(f"Last cycle {st['cycle_utc']} UTC: {'ok' if st.get('ok') else 'FAILED'} "
          f"in {st.get('seconds', '?')} s; station rain to {st.get('t_obs_utc')} UTC")
    for s in st.get("steps", []):
        print(f"  {'ok  ' if s['ok'] else 'FAIL'} {s['step']}  {s.get('note', '')}")
    for v, r in st.get("versions", {}).items():
        print(f"  version {v}: {'ran' if r.get('ok') else 'FAILED ' + str(r.get('error'))}")
    for key, (meta, table) in archive.stations(site).items():
        print(f"  station {meta['name']}: last report {meta.get('last_report_utc')} UTC, "
              f"{meta.get('rows')} rows kept")
    if json_too:
        print(json.dumps(st, indent=1))
    return 0 if st.get("ok") else 1


def _cmd_status(args):
    portal, items = _targets(args)
    code = 0
    for pid, site in items:
        if portal:
            print(f"── {pid} ──")
        code = max(code, _status_one(site, args.json))
    return code


def _cmd_workflow(args):
    portal, items = _targets(args)
    if portal:
        folder, minutes = portal.folder, portal.every_minutes
        target = "-p " + os.path.relpath(portal.path, folder)
    else:
        site = items[0][1]
        folder, minutes = site.folder, site.every_minutes
        target = "-f " + os.path.relpath(site.path, folder)
    out = args.output or os.path.join(folder, ".github", "workflows", "forecast-backup.yml")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    minute = (minutes // 4) or 7
    cron = f"{minute},{minute + 30} * * * *" if minutes == 30 else f"{minute} * * * *"
    with open(out, "w") as f:
        f.write(WORKFLOW.format(cron=cron, stale=args.stale, target=target))
    print(f"Written: {out}")
    print("Commit it, with each place's forecast.yaml, model.yaml and static/, to the "
          "repository named in publish: git: remote:.")
    return 0


def build_parser():
    from MRRpy import __version__ as mrrpy_version
    from . import __version__
    p = argparse.ArgumentParser(prog="lynch-forecast",
                                description="A live flood forecast built on the MRRpy model: "
                                            "see 'lynch-forecast new' to start.")
    p.add_argument("--version", action="version",
                   version=f"lynch-forecast {__version__} (MRRpy {mrrpy_version})")
    fs = p.add_subparsers(dest="command", required=True)

    def with_file(name, help, func, places=False):
        q = fs.add_parser(name, help=help)
        q.add_argument("-f", "--forecast", default=None,
                       help="one place's forecast file (default: forecast.yaml)")
        if places:
            q.add_argument("-p", "--places", default=None,
                           help="a places file: every place in it, and the site of them all "
                                "(default: places.yaml when there is one here)")
        q.set_defaults(func=func)
        return q

    q = fs.add_parser("new", help="a new place: its forecast file and model file "
                                  "(with --outlet, ready to run)")
    q.add_argument("folder", help="folder for the new place, e.g. places/nakhu")
    q.add_argument("--outlet", metavar="LAT,LON",
                   help="the river point the basin drains to, e.g. 27.44,85.81: writes a "
                        "ready model (setup finds the basin around it)")
    q.add_argument("--name", help="the place's name, e.g. 'Nakhu Khola' (default: the folder)")
    q.add_argument("--timezone", help="times on its page, e.g. Asia/Kathmandu "
                                      "(default: Nepal's in Nepal, else UTC)")
    q.add_argument("--gee-project", help="Earth Engine project (default: another place's)")
    q.add_argument("-p", "--places", default=None,
                   help="also add it to this places file (default: places.yaml here)")
    q.add_argument("--force", action="store_true", help="replace an existing forecast.yaml")
    q.set_defaults(func=_cmd_new)
    with_file("check", "try every feed and show where each point falls on the rivers",
              _cmd_check)
    q = with_file("setup", "prepare the site once: terrain, layers, warm-up, area DEMs",
                  _cmd_setup)
    q.add_argument("--terrain", action="store_true", help="prepare the terrain again")
    q.add_argument("--no-warm-up", action="store_true", help="skip the warm-up run")
    q.add_argument("--maps-only", action="store_true",
                   help="only prepare the flood maps' DEMs (after adding an area or "
                        "basin_map), from the newest run")
    q.add_argument("--serial", action="store_true", help="run models in this process")
    for name, help, func in (("run", "update the forecast now (one place, or every place)", _cmd_run),
                             ("serve", "keep going: update every every_minutes, set up new places by itself",
                              _cmd_serve)):
        q = with_file(name, help, func, places=True)
        q.add_argument("--no-publish", action="store_true", help="do not publish the page")
        q.add_argument("--serial", action="store_true", help="run the versions one by one "
                                                             "in this process")
        q.add_argument("--only-if-stale", type=float, default=None, metavar="MIN",
                       help="do nothing if a cycle finished less than MIN minutes ago")
        q.add_argument("--restore-state", action="store_true",
                       help="first fetch the newest states from the git 'state' branch")
    q = with_file("replay", "rehearse a past flood on a fake clock", _cmd_replay)
    q.add_argument("--start", required=True, help="first cycle, UTC (YYYY-MM-DD HH:MM)")
    q.add_argument("--end", required=True, help="last cycle, UTC")
    q.add_argument("--every", type=float, default=3.0, help="hours between cycles (default 3)")
    q.add_argument("--warm-up-days", type=float, default=None,
                   help="days of rain before the first cycle (default: warm_up_days)")
    q.add_argument("--serial", action="store_true", help="run models in this process")
    q = with_file("design", "design storms (design.yaml beside the forecast file, e.g. the "
                            "10- and 20-year storms of NOAA Atlas 14): runs, flood maps, page",
                  _cmd_design)
    q.add_argument("--no-maps", action="store_true", help="only the runs, no flood maps")
    q.add_argument("--serial", action="store_true", help="run models in this process")
    q = with_file("status", "the last cycle and how fresh each station is", _cmd_status,
                  places=True)
    q.add_argument("--json", action="store_true", help="also print status.json")
    q = with_file("github-workflow", "write the GitHub Actions backup workflow", _cmd_workflow,
                  places=True)
    q.add_argument("-o", "--output", default=None, help="where to write it")
    q.add_argument("--stale", type=float, default=45.0,
                   help="minutes without a cycle before the backup runs (default 45)")
    q = with_file("doctor", "is this computer ready, and is the forecast running? "
                            "(what to fix, in words)", _cmd_doctor, places=True)
    q.add_argument("--feeds", action="store_true", help="also try every station and forecast")
    q = fs.add_parser("service", help="keep 'serve' running on this computer: install, "
                                      "status, logs, restart, uninstall")
    q.add_argument("action", choices=("install", "status", "logs", "restart", "uninstall"))
    q.add_argument("-f", "--forecast", default=None, help="one place's forecast file")
    q.add_argument("-p", "--places", default=None,
                   help="the places file (default: places.yaml here)")
    q.add_argument("--no-publish", action="store_true",
                   help="install: only write the pages, never publish them")
    q.add_argument("--dry-run", action="store_true", help="install: only show what it would do")
    q.add_argument("--lines", type=int, default=60, help="logs: how many lines (default 60)")
    q.add_argument("--name", default="lynch-forecast", help=argparse.SUPPRESS)
    q.set_defaults(func=_cmd_service)
    q = with_file("site", "write the web site of every place (front page + each place's page)",
                  _cmd_site, places=True)
    q.add_argument("--publish", action="store_true", help="also publish it (publish: in "
                                                          "the places file)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command not in ("new", "service", "doctor"):
        from . import missing_mrrpy_features
        missing = missing_mrrpy_features()
        if missing:
            print("[ERROR] The installed MRRpy lacks what the forecast needs: "
                  + "; ".join(missing) + ". Install the MRRpy named in README.md.",
                  file=sys.stderr)
            return 1
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
