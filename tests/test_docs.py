# -*- coding: utf-8 -*-
"""Every YAML block in docs/ and README.md is a valid forecast file or MRRpy settings file."""

import glob
import os
import re

import pytest
import yaml
from MRRpy import Config

from lynch_forecast.places import parse_portal
from lynch_forecast.sitefile import parse_site

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BLOCK = re.compile(r"^([ \t]*)```(\w+)[^\n]*\n(.*?)^\1```[ \t]*$", re.M | re.S)


def _yaml_blocks():
    pages = sorted(glob.glob(os.path.join(ROOT, "docs", "*.md"))) + [os.path.join(ROOT, "README.md")]
    for page in pages:
        for indent, lang, body in _BLOCK.findall(open(page, encoding="utf-8").read()):
            if lang == "yaml":
                yield os.path.relpath(page, ROOT), re.sub(rf"^{indent}", "", body, flags=re.M)


def test_yaml_blocks_are_valid(tmp_path):
    (tmp_path / "model.yaml").write_text("{}\n")
    n = 0
    for page, body in _yaml_blocks():
        data = yaml.safe_load(body)
        assert isinstance(data, dict), f"{page}: a YAML block is not a mapping"
        try:
            if "places" in data:                                  # a places file
                (tmp_path / "places" / "x").mkdir(parents=True, exist_ok=True)
                for i, _ in enumerate(data["places"]):
                    (tmp_path / "places" / "x" / f"{i}.yaml").write_text("{}")
                parse_portal(dict(data, places=[f"places/x/{i}.yaml"
                                                for i in range(len(data["places"]))][:1]),
                             str(tmp_path / "places.yaml"))
            elif "model" in data:                                 # a forecast file
                parse_site(dict(data, model="model.yaml"), str(tmp_path / "forecast.yaml"))
            else:                                                 # MRRpy settings
                Config.from_dict(data)
        except Exception as exc:                                  # noqa: BLE001 — say which
            pytest.fail(f"{page}: invalid block {list(data)}: {exc}")
        n += 1
    assert n >= 3


def test_the_deployed_files_are_valid():
    site = parse_site(yaml.safe_load(open(os.path.join(ROOT, "places", "lynch", "forecast.yaml"))),
                      os.path.join(ROOT, "places", "lynch", "forecast.yaml"))
    assert site.main_forecast == "NBM"
    Config.from_dict(yaml.safe_load(open(os.path.join(ROOT, "places", "lynch", "model.yaml"))))
