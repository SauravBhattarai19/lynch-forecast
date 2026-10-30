# -*- coding: utf-8 -*-
"""
Sign-ins for feeds that need one, never kept in the repository.

``credentials("RTS")`` → (username, password), from the environment variables
``RTS_USERNAME`` / ``RTS_PASSWORD`` when set (GitHub Actions secrets), else from
the file ``scripts/save-credentials.sh`` writes:
``~/.config/lynch-forecast/credentials.env`` (or ``$LYNCH_FORECAST_CREDENTIALS``),
raw ``KEY=value`` lines.
"""

import os

DEFAULT_FILE = os.path.join("~", ".config", "lynch-forecast", "credentials.env")


def credentials_file():
    return os.path.expanduser(os.environ.get("LYNCH_FORECAST_CREDENTIALS") or DEFAULT_FILE)


def _read(path):
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip()] = value
    return values


def credentials(prefix):
    """(username, password) for *prefix* (e.g. ``"RTS"``), or a plain-words error."""
    from .sources._http import FeedError
    prefix = prefix.upper()
    user = os.environ.get(f"{prefix}_USERNAME")
    password = os.environ.get(f"{prefix}_PASSWORD")
    if not (user and password):
        saved = _read(credentials_file())
        user = user or saved.get(f"{prefix}_USERNAME")
        password = password or saved.get(f"{prefix}_PASSWORD")
    if not (user and password):
        raise FeedError(f"no {prefix} sign-in: run scripts/save-credentials.sh "
                        f"{prefix.lower()} (or set {prefix}_USERNAME and {prefix}_PASSWORD)")
    return user, password
