# -*- coding: utf-8 -*-
"""
states.py — the saved model states a forecast carries on from.

The main version of each cycle saves the model state every hour up to the last
station report.  They are kept as ``states/state_<YYYYmmddTHHMM>Z.npz`` (the
clock time the state belongs to).  The next cycle starts from the newest one at
least ``look_back_hours`` old, so it runs those hours again with whatever
station rain arrived late.  All states of the last two days are kept, then one
a day (00:00 UTC) for ``keep_states_days``.
"""

import glob
import os
import shutil
from datetime import datetime, timedelta

STAMP = "%Y%m%dT%H%MZ"
KEEP_ALL_HOURS = 48


def state_path(site, t):
    return site.dir("states", f"state_{t.strftime(STAMP)}.npz")


def list_states(site):
    """[(time, path)] of the kept states, oldest first."""
    out = []
    for p in glob.glob(site.dir("states", "state_*.npz")):
        try:
            out.append((datetime.strptime(os.path.basename(p)[6:-4], STAMP), p))
        except ValueError:
            continue
    return sorted(out)


def pick_start(site, target):
    """The newest kept state at or before *target* → (time, path), or None."""
    best = None
    for t, p in list_states(site):
        if t <= target:
            best = (t, p)
    return best


def keep(site, state_dir, run_start, until):
    """Copy a run's states (``state_<hours>h.npz``) up to clock time *until*
    into the site's states folder → list of the clock times kept."""
    os.makedirs(site.dir("states"), exist_ok=True)
    kept = []
    for p in sorted(glob.glob(os.path.join(state_dir, "state_*h.npz"))):
        try:
            hours = float(os.path.basename(p)[6:-5])
        except ValueError:
            continue
        t = run_start + timedelta(hours=hours)
        t = t.replace(second=0, microsecond=0)
        if t > until:
            continue
        dst = state_path(site, t)
        tmp = dst + ".part.npz"
        shutil.copyfile(p, tmp)
        os.replace(tmp, dst)
        kept.append(t)
    return kept


def prune(site, now):
    """Drop states no longer needed (see the module docstring)."""
    states = list_states(site)
    if not states:
        return []
    newest = states[-1][0]
    cut_all = now - timedelta(hours=KEEP_ALL_HOURS)
    cut_daily = now - timedelta(days=float(site.keep_states_days))
    gone = []
    for t, p in states:
        if t == newest or t >= cut_all:
            continue
        if t >= cut_daily and t.hour == 0 and t.minute == 0:
            continue
        os.remove(p)
        gone.append(t)
    return gone
