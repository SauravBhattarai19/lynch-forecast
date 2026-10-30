# -*- coding: utf-8 -*-
"""
models.py — one model run of a forecast version, in its own folder.

Each version runs in ``cycles/<cycle>/<version>/``: links to every file setup
saved in ``static/`` (terrain, land cover, soil, …), its own rain files, inflow
files and outputs.  Runs go to separate processes (started fresh, with a set
number of CPU threads each) so the versions of a cycle run side by side.
"""

import contextlib
import io
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from .offline import earth_engine_blocked, thread_limit, threads_here

# Files and folders a run writes: never linked from static/.
RUN_OUTPUTS = {"hydrograph.csv", "mass_balance.csv", "gauges.csv", "partition.csv",
               "state", "inundation", "fields", "openmeteo", "imerg", "rain", "inflows",
               "areas", "run.log", "mrrpy_run.log"}


def link_static(static_dir, run_dir):
    """Link every file setup saved into *run_dir* (copies where links fail)."""
    import shutil
    os.makedirs(run_dir, exist_ok=True)
    for name in os.listdir(static_dir):
        if name in RUN_OUTPUTS or name.startswith(".") or name.endswith((".log", ".part")):
            continue
        src, dst = os.path.join(static_dir, name), os.path.join(run_dir, name)
        if os.path.lexists(dst):
            continue
        try:
            os.symlink(src, dst, target_is_directory=os.path.isdir(src))
        except OSError:                                    # e.g. Windows without rights
            (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)


def run_model(cfg_dict, clock_start, log_path):
    """
    initialise_grid → run_time_loop → save_hydrograph for one version, with
    Earth Engine off.  The model starts with the settings' own EVENT_START_UTC
    (the soil date fixed at setup) and the clock time of the run (*clock_start*)
    is set after start-up, for the saved states.  Returns a small dict.
    """
    from MRRpy.config import Config
    from MRRpy.core.routing import router

    t0 = time.time()
    out = {"ok": False, "seconds": 0.0, "error": None, "log": log_path}
    with open(log_path, "w") as logf, contextlib.redirect_stdout(logf), \
            contextlib.redirect_stderr(logf), earth_engine_blocked():
        try:
            cfg = Config.from_dict(cfg_dict)
            grid = router.initialise_grid(cfg)
            cfg.EVENT_START_UTC = clock_start
            hyd = router.run_time_loop(grid, cfg)
            router.save_hydrograph(hyd, cfg)
            out["ok"] = True
        except Exception as exc:                           # noqa: BLE001 — reported
            traceback.print_exc()
            out["error"] = f"{type(exc).__name__}: {exc}"
    out["seconds"] = round(time.time() - t0, 1)
    return out


def run_many(jobs, workers, threads, parallel=True, log=print):
    """
    Run *jobs* ({name: (cfg_dict, clock_start, log_path)}) → {name: result}.
    With *parallel*, in up to *workers* fresh processes of *threads* threads.
    """
    results = {}
    if not parallel or len(jobs) == 1 and workers <= 1:
        for name, args in jobs.items():
            with threads_here(threads):
                results[name] = run_model(*args)
            log(f"  {name}: {'done' if results[name]['ok'] else 'FAILED'} "
                f"in {results[name]['seconds']:g} s")
        return results
    n = max(1, min(len(jobs), workers or len(jobs)))
    with thread_limit(threads), ProcessPoolExecutor(n, mp_context=get_context("spawn")) as ex:
        futs = {name: ex.submit(run_model, *args) for name, args in jobs.items()}
        for name, fut in futs.items():
            try:
                results[name] = fut.result()
            except Exception as exc:                       # the process itself died
                results[name] = {"ok": False, "seconds": 0.0, "error": str(exc),
                                 "log": jobs[name][2]}
            log(f"  {name}: {'done' if results[name]['ok'] else 'FAILED'} "
                f"in {results[name]['seconds']:g} s")
    return results


def last_lines(path, n=6):
    """The end of a run log, for an error message."""
    try:
        with open(path, errors="replace") as f:
            return "".join(io.StringIO(f.read()).readlines()[-n:]).rstrip()
    except OSError:
        return ""
