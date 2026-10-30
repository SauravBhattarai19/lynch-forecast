# -*- coding: utf-8 -*-
"""
publish.py — put the page online after a cycle.

``website: publish:`` in the forecast file picks how:

* ``{git: {remote: git@github.com:USER/REPO.git}}`` — the page goes to the
  ``gh-pages`` branch (GitHub Pages serves it) and the newest saved states,
  the archive and status.json to a ``state`` branch, each as one fresh commit
  pushed with --force so the repository stays small.  A backup runner (GitHub
  Actions) fetches ``state`` to carry on where the server stopped
  (:func:`restore_state`).
* ``{command: "rsync -a {site}/ host:/var/www/flood/"}`` — any command;
  ``{site}`` is the page folder and ``{folder}`` the forecast folder.

With several places (places.py) the whole site is published at once instead,
and the ``state`` branch holds one folder per place.
"""

import glob
import os
import shlex
import shutil
import subprocess

from . import states as _states

KEEP_STATES = 3


def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def push_folder(src, remote, branch, work, message):
    """Push the files of *src* as one orphan commit to *remote* *branch*."""
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(src, work, symlinks=False)
    _git(["init", "-q"], work)
    _git(["checkout", "-q", "-b", branch], work)
    _git(["add", "-A"], work)
    _git(["-c", "user.name=lynch-forecast", "-c", "user.email=forecast@localhost",
          "commit", "-q", "-m", message], work)
    _git(["push", "-q", "--force", remote, f"{branch}:{branch}"], work)


def state_bundle(site, dst):
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(os.path.join(dst, "states"))
    for t, p in _states.list_states(site)[-KEEP_STATES:]:
        shutil.copy2(p, os.path.join(dst, "states", os.path.basename(p)))
    if os.path.isdir(site.dir("archive")):
        shutil.copytree(site.dir("archive"), os.path.join(dst, "archive"))
    if os.path.isfile(site.dir("status.json")):
        shutil.copy2(site.dir("status.json"), dst)
    return dst


def run_command(command, site, folder):
    """Run a publish command with {site} and {folder} filled in."""
    cmd = command.format(site=shlex.quote(site), folder=shlex.quote(folder))
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"publish command failed: {(r.stderr or r.stdout).strip()}")


def publish(site, log=print):
    pub = site.website.get("publish") or {}
    status = site.dir("status.json")
    message = "forecast " + (open(status).read(40) if os.path.isfile(status) else "")
    message = message.splitlines()[0][:72]
    if "command" in pub:
        run_command(pub["command"], site=site.dir("site"), folder=site.folder)
        log("  Published with the command.")
        return
    git = pub["git"]
    remote = git["remote"]
    work = site.dir(".publish")
    push_folder(site.dir("site"), remote, git.get("pages_branch", "gh-pages"),
                 os.path.join(work, "pages"), message)
    bundle = state_bundle(site, os.path.join(work, "state_src"))
    push_folder(bundle, remote, git.get("state_branch", "state"),
                 os.path.join(work, "state"), message)
    log(f"  Published to {remote} (gh-pages and state).")


def restore_state(site, log=print, git=None, subdir=""):
    """Fetch the ``state`` branch and copy its states and archive into the folder
    (for a backup runner that starts from an empty checkout).  With several
    places, *git* is the places file's and each place is in its *subdir*."""
    git = git or (site.website.get("publish") or {}).get("git")
    if not git:
        raise RuntimeError("restore needs website: publish: git: remote: in the forecast file")
    work = site.dir(".publish", "restore")
    shutil.rmtree(work, ignore_errors=True)
    r = subprocess.run(["git", "clone", "-q", "--depth", "1", "-b",
                        git.get("state_branch", "state"), git["remote"], work],
                       capture_output=True, text=True)
    if r.returncode:
        log(f"  No state branch to restore from ({r.stderr.strip()}); starting fresh.")
        return 0
    work = os.path.join(work, subdir) if subdir else work
    n = 0
    os.makedirs(site.dir("states"), exist_ok=True)
    for p in glob.glob(os.path.join(work, "states", "*.npz")):
        dst = site.dir("states", os.path.basename(p))
        if not os.path.exists(dst):
            shutil.copy2(p, dst)
            n += 1
    if os.path.isdir(os.path.join(work, "archive")):
        shutil.copytree(os.path.join(work, "archive"), site.dir("archive"), dirs_exist_ok=True)
    if os.path.isfile(os.path.join(work, "status.json")):
        shutil.copy2(os.path.join(work, "status.json"), site.dir("status.json"))
    log(f"  Restored {n} state(s) and the archive from the state branch.")
    return n
