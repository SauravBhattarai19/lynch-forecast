# -*- coding: utf-8 -*-
"""
service.py — ``lynch-forecast service install|status|logs|uninstall``: keep
``lynch-forecast serve`` running on this computer, starting with it and again
after a crash.

* With systemd (most Linux): a user service, ``~/.config/systemd/user/<name>.service``.
  For it to run while nobody is logged in, the user needs "linger"
  (``loginctl enable-linger``), which install tries and otherwise explains.
* Without it (macOS, WSL without systemd, …): a cron line every 10 minutes that
  starts ``serve`` unless it is already running (serve keeps a lock, so there is
  never a second one).
"""

import os
import shlex
import shutil
import subprocess
import sys

NAME = "lynch-forecast"
CRON_MARK = "# lynch-forecast serve (service install)"

UNIT = """\
# Written by 'lynch-forecast service install'. Keeps the flood forecast running:
# it starts with the computer and again after a crash.
# Remove it with: lynch-forecast service uninstall
[Unit]
Description=Flood forecast (lynch-forecast serve in {folder})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={folder}
ExecStart={command}
Restart=always
RestartSec=60
Environment=PYTHONUNBUFFERED=1
Environment=PATH={path}

[Install]
WantedBy=default.target
"""


def _run(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def serve_command(target):
    """The command the service runs: this Python, this package, *target* (-p/-f …)."""
    return [sys.executable, "-m", "lynch_forecast", "serve"] + list(target)


def unit_path(name=NAME):
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "systemd", "user", f"{name}.service")


def has_systemd():
    """Can this user run systemd user services right now?"""
    if not shutil.which("systemctl"):
        return False
    return _run(["systemctl", "--user", "show-environment"]).returncode == 0


def unit_text(folder, target):
    path = os.pathsep.join(dict.fromkeys(
        [os.path.dirname(sys.executable)] + os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep)))
    return UNIT.format(folder=folder, command=shlex.join(serve_command(target)), path=path)


def cron_line(folder, target):
    log = os.path.join(folder, "serve.log")
    return (f"*/10 * * * * cd {shlex.quote(folder)} && {shlex.join(serve_command(target))} "
            f">> {shlex.quote(log)} 2>&1 {CRON_MARK}")


def _crontab():
    out = _run(["crontab", "-l"])
    return out.stdout if out.returncode == 0 else ""


def _set_crontab(text):
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def install(folder, target, name=NAME, log=print, dry_run=False):
    """Install and start the service → 0, or 1 with the reason."""
    folder = os.path.abspath(folder)
    if has_systemd():
        unit = unit_text(folder, target)
        path = unit_path(name)
        if dry_run:
            log(f"Would write {path}:\n{unit}")
            return 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(unit)
        for cmd in (["daemon-reload"], ["enable", "--now", f"{name}.service"]):
            r = _run(["systemctl", "--user"] + cmd)
            if r.returncode:
                log(f"[ERROR] systemctl --user {' '.join(cmd)}: {r.stderr.strip()}")
                return 1
        log(f"Installed and started: {path}")
        user = os.environ.get("USER") or ""
        linger = _run(["loginctl", "show-user", user, "-p", "Linger"]).stdout.strip()
        if linger != "Linger=yes":
            r = _run(["loginctl", "enable-linger", user])
            if r.returncode:
                log("For it to run while nobody is logged in, ask an administrator to run:\n"
                    f"  sudo loginctl enable-linger {user}")
            else:
                log("It also runs while nobody is logged in (linger is on).")
        log("Check it with: lynch-forecast service status   (logs: lynch-forecast service logs)")
        return 0
    if not shutil.which("crontab"):
        log("[ERROR] Neither systemd nor cron is available. Keep this running instead "
            f"(e.g. in tmux, or at start-up):\n  cd {folder} && "
            f"{shlex.join(serve_command(target))}")
        return 1
    line = cron_line(folder, target)
    if dry_run:
        log(f"Would add to crontab:\n{line}")
        return 0
    rest = [ln for ln in _crontab().splitlines() if CRON_MARK not in ln]
    _set_crontab("\n".join(rest + [line]) + "\n")
    log("Installed a cron line (starts serve within 10 minutes, and again if it stops):\n"
        f"  {line}\nLogs go to {os.path.join(folder, 'serve.log')}.")
    return 0


def uninstall(name=NAME, log=print):
    done = False
    path = unit_path(name)
    if os.path.isfile(path) and shutil.which("systemctl"):
        _run(["systemctl", "--user", "disable", "--now", f"{name}.service"])
        os.remove(path)
        _run(["systemctl", "--user", "daemon-reload"])
        log(f"Stopped and removed {path}")
        done = True
    if shutil.which("crontab"):
        text = _crontab()
        if CRON_MARK in text:
            _set_crontab("\n".join(ln for ln in text.splitlines() if CRON_MARK not in ln) + "\n")
            log("Removed the cron line (a serve it started keeps going until stopped).")
            done = True
    if not done:
        log("No service was installed.")
    return 0


def state(name=NAME):
    """(kind, running, words): kind 'systemd', 'cron' or None."""
    if os.path.isfile(unit_path(name)) and shutil.which("systemctl"):
        active = _run(["systemctl", "--user", "is-active", f"{name}.service"]).stdout.strip()
        enabled = _run(["systemctl", "--user", "is-enabled", f"{name}.service"]).stdout.strip()
        return "systemd", active == "active", f"systemd user service: {active}, {enabled}"
    if shutil.which("crontab") and CRON_MARK in _crontab():
        return "cron", None, "cron line installed (runs serve every 10 minutes unless running)"
    return None, False, "not installed (lynch-forecast service install)"


def restart(folder, name=NAME, log=print):
    """Start serve again (after an update of the software): systemd restarts it; with
    cron, the running serve is stopped and cron starts the new one within 10 minutes."""
    kind, _, _ = state(name)
    if kind == "systemd":
        r = _run(["systemctl", "--user", "restart", f"{name}.service"])
        log("Restarted." if not r.returncode else f"[ERROR] {r.stderr.strip()}")
        return r.returncode
    lock = os.path.join(os.path.abspath(folder), ".serve.lock")
    try:
        with open(lock) as f:
            pid = int(f.read().strip())
        os.kill(pid, 15)
        log(f"Stopped serve (process {pid})" + ("; cron starts it again within 10 minutes."
                                               if kind == "cron" else "."))
        return 0
    except (OSError, ValueError):
        log("No running serve found here.")
        return 1


def status(name=NAME, log=print):
    kind, running, words = state(name)
    log(f"Service: {words}")
    if kind == "systemd":
        log(_run(["systemctl", "--user", "status", "--no-pager", "-n", "0",
                  f"{name}.service"]).stdout.rstrip())
    return 0 if kind and running is not False else 1


def logs(folder, lines=60, name=NAME, log=print):
    kind, _, _ = state(name)
    if kind == "systemd":
        out = _run(["journalctl", "--user", "-u", f"{name}.service", "-n", str(lines),
                    "--no-pager"])
        log(out.stdout.rstrip() or out.stderr.rstrip())
        return 0
    path = os.path.join(os.path.abspath(folder), "serve.log")
    if os.path.isfile(path):
        with open(path, errors="replace") as f:
            log("".join(f.readlines()[-lines:]).rstrip())
        return 0
    log("No logs: the service is not installed here.")
    return 1
