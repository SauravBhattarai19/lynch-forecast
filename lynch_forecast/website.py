# -*- coding: utf-8 -*-
"""website.py — copy the page (index.html, app.js, style.css) into ``site/``.

The page itself never changes between cycles; it reads ``data/latest.json`` and
the files next to it (products.py).  ``site/`` is a plain static folder: open it
from any web server, GitHub Pages or a shared drive."""

import os
import shutil

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
FILES = ("index.html", "app.js", "style.css", "leaflet.css")


def write(site):
    out = site.dir("site")
    os.makedirs(os.path.join(out, "data"), exist_ok=True)
    for name in FILES:
        shutil.copyfile(os.path.join(WEB_DIR, name), os.path.join(out, name))
    open(os.path.join(out, ".nojekyll"), "w").close()      # GitHub Pages: serve as is
    return out
