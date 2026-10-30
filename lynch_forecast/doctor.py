# -*- coding: utf-8 -*-
"""
doctor.py — ``lynch-forecast doctor``: is this computer ready to run the
forecast by itself, and is it doing so?  Each line is ``ok``, ``WARN`` (works,
but look) or ``FIX`` (will not work until fixed), with what to do.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime

OK, WARN, FIX = "ok", "WARN", "FIX"


def _git_reaches(remote):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -o BatchMode=yes")
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", remote], capture_output=True,
                           text=True, timeout=30, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1]


def _age(utc_text):
    """Minutes since a UTC time written '2026-10-28 19:02' (or ISO with T/Z)."""
    try:
        t = datetime.fromisoformat(str(utc_text).replace("T", " ").rstrip("Z"))
    except (TypeError, ValueError):
        return None
    return (datetime.utcnow() - t).total_seconds() / 60.0


def checks(places=None, forecast=None, feeds=False):
    """→ [(level, what, words)]."""
    from . import __version__, missing_mrrpy_features
    from .autopilot import Settings, place_state
    out = []
    add = lambda level, what, words: out.append((level, what, words))   # noqa: E731

    v = sys.version_info
    add(OK if v >= (3, 10) else FIX, "Python", f"{v.major}.{v.minor}.{v.micro} at "
        f"{sys.executable}" + ("" if v >= (3, 10) else " — needs 3.10 or newer"))
    try:
        import MRRpy
        missing = missing_mrrpy_features()
        add(FIX if missing else OK, "MRRpy", f"{MRRpy.__version__}" + (
            " lacks: " + "; ".join(missing) + " — run scripts/install.sh" if missing else
            f" (lynch-forecast {__version__})"))
    except ImportError as exc:
        add(FIX, "MRRpy", f"not installed ({exc}) — run scripts/install.sh")
        return out

    settings = Settings(places, forecast)
    try:
        portal, items = settings.read()
    except Exception as exc:                        # noqa: BLE001 — reported in words
        add(FIX, "Settings", str(exc))
        return out
    what = (f"{settings.places_path}: {len(items)} place(s)" if portal else
            f"{settings.forecast_path or 'forecast.yaml'}")
    add(FIX if settings.problems else OK, "Settings", what + "".join(
        "\n        " + p for p in settings.problems))

    states = {pid: place_state(site) for pid, site in items}
    need_ee = any(s.need in ("setup", "maps", "wait") for s in states.values())
    from MRRpy.config import Config
    projects = set()
    for _, site in items:
        try:
            projects.add(Config.from_file(site.model).GEE_PROJECT or None)
        except Exception:                           # noqa: BLE001 — Settings reported it
            pass
    for project in sorted(projects, key=str):
        try:
            from MRRpy.gee.auth import status as ee_status
            ok, words = ee_status(project)
        except Exception as exc:                    # noqa: BLE001
            ok, words = False, str(exc)
        level = OK if ok else (FIX if need_ee else WARN)
        add(level, "Earth Engine", words + ("" if ok else
            "\n        Needed to set up a place (not for the half-hourly updates). Sign in "
            "once with: MRRpy earth-engine-login" + (f" --project {project}" if project else "")))

    prefixes = sorted({str(e.options.get("sign_in") or "RTS") for _, site in items
                       for e in site.rain_stations if e.source == "rts"})
    if prefixes:
        from .credentials import credentials, credentials_file
        for prefix in prefixes:
            try:
                credentials(prefix)
                add(OK, "Sign-in", f"{prefix}: saved ({credentials_file()} or the environment)")
            except Exception:                       # noqa: BLE001
                add(FIX, "Sign-in", f"{prefix}: missing — run scripts/save-credentials.sh "
                    f"{prefix.lower()}")

    publish = (portal.publish if portal else (items[0][1].website.get("publish") if items
                                              else None)) or {}
    git = publish.get("git") if isinstance(publish, dict) else None
    if git and git.get("remote"):
        if not shutil.which("git"):
            add(FIX, "Publishing", "git is not installed")
        else:
            ok, why = _git_reaches(git["remote"])
            add(OK if ok else FIX, "Publishing", f"git {git['remote']}: " + (
                "reachable (pushing uses this computer's git sign-in)" if ok else
                f"not reachable ({why}). Pushing needs a sign-in for it "
                "on this computer: an SSH key added to GitHub, or 'gh auth login'"))
    elif publish.get("command") if isinstance(publish, dict) else False:
        add(OK, "Publishing", f"by command: {publish['command']}")
    else:
        add(WARN, "Publishing", "no publish: setting — the pages are only written to "
            + (portal.out() if portal else "site/"))

    folder = os.path.abspath(portal.folder if portal else (items[0][1].folder if items else "."))
    free = shutil.disk_usage(folder).free / 1e9
    add(OK if free > 20 else WARN if free > 5 else FIX, "Disk",
        f"{free:,.0f} GB free in {folder}" + ("" if free > 5 else " — runs and maps need room"))

    from .cycle import last_status
    for pid, site in items:
        st, name = states[pid], pid or site.name
        if st.need in ("setup", "wait") and not st.can_run:
            add(WARN if st.need == "setup" else FIX, f"Place {name}",
                ("not set up yet (" + st.note + "): serve sets it up by itself, or run "
                 f"lynch-forecast setup -f {os.path.relpath(site.path)}") if st.need == "setup"
                else st.note)
            continue
        last = last_status(site)
        if not last:
            add(WARN, f"Place {name}", "set up, but no update has run yet")
            continue
        mins = _age(last.get("cycle_utc"))
        late = mins is not None and mins > 2.5 * site.every_minutes
        words = (f"last update {mins:.0f} min ago, " if mins is not None else "") + \
            ("ok" if last.get("ok") else "FAILED: " + "; ".join(
                f"{s['step']}: {s.get('note', '')}" for s in last.get("steps", [])
                if not s.get("ok"))[:300])
        if st.need:
            words += f"; {st.need}: {st.note}"
        add(FIX if not last.get("ok") else WARN if late or st.need else OK, f"Place {name}",
            words + (" — late: is serve running?" if late else ""))
        if feeds:
            from .check import check_site
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                res = check_site(site)
            bad = [x["name"] for k in ("stations", "forecasts", "inflows")
                   for x in res.get(k, []) if not x.get("ok")]
            add(FIX if bad else OK, f"Feeds {name}", "not working: " + ", ".join(bad) if bad
                else "every station and forecast answered")

    from .service import state
    kind, running, words = state()
    add(OK if kind and running is not False else WARN, "Service", words)
    return out


def report(places=None, forecast=None, feeds=False, log=print):
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)    # Google's Python notices
    rows = checks(places, forecast, feeds)
    log(f"lynch-forecast doctor — {os.getcwd()}")
    for level, what, words in rows:
        log(f"  {level:<4}  {what}: {words}")
    fixes = sum(1 for r in rows if r[0] == FIX)
    log("All good." if not fixes else f"{fixes} thing(s) to fix (FIX above).")
    return 1 if fixes else 0
