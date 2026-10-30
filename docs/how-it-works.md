# Live flood forecast

`lynch-forecast` runs the MRRpy model as a **live flood forecast**. Every half
hour it reads the latest rain from your rain stations and adds rain forecasts. It carries the basin on
from where the last update left it and writes:

- **hydrographs**: river flow at any number of points, one line for each rain
  forecast, with your warning and danger levels;
- **flood maps**: the deepest water expected in any number of areas;
- **a web page** with all of it, in plain words and tables. You can put it on
  GitHub Pages or any web server.

> [!WARNING]
> **A model, not a warning service.** A forecast made this way is experimental.
> Before anyone acts on it, compare it with past floods (see
> [Rehearse a past flood](#rehearse-a-past-flood)). Always point people to the
> official warnings. The page says this at the top.

A place needs **two files**:

- `model.yaml`: the usual MRRpy settings (DEM, outlet, runoff, routing), the
  same file `MRRpy run` uses;
- `forecast.yaml`: a short file listing the rain stations, the forecasts, the
  inflow points, the hydrograph points and the flood areas. See
  [The forecast file](forecast-file.md).

## Getting started

```bash
lynch-forecast new places/lynch/     # writes places/lynch/forecast.yaml and model.yaml
#   edit model.yaml (or: MRRpy wizard --edit places/lynch/model.yaml) and forecast.yaml
lynch-forecast check -f places/lynch/forecast.yaml    # try every feed; where each point falls
lynch-forecast setup -f places/lynch/forecast.yaml    # once: terrain, layers, warm-up, area DEMs
lynch-forecast run   -f places/lynch/forecast.yaml    # one update of this place alone
```

## Several places, one site

`places.yaml` lists the places. `run`, `serve`, `status` and `github-workflow`
then work on all of them, and `public/` gets one site for them all:

```yaml
title: Flood forecasts (experimental)
every_minutes: 30
places:
  - places/lynch/forecast.yaml
  - places/nakhu/forecast.yaml
publish: {git: {remote: https://github.com/USER/REPO.git}}   # optional
```

```bash
lynch-forecast serve -p places.yaml   # each place in turn every every_minutes (60 here), then the site
lynch-forecast site  -p places.yaml   # only rebuild public/ (--publish to put it online)
```

The front page (`public/index.html`) shows each place's basin on a map and a
card with its last update, the rain expected and the highest flow at each
point, in words and a table. Each place's own page is at `public/<place>/`,
the place being its folder's name, and links back to the front page. A place
that fails one update keeps its last page, and the others still update. With
several places the `state` branch keeps one folder per place.

`check` prints each rain station's last report and each forecast's rain. For
each hydrograph point it prints the river cell the model will use, with its
drainage area and how far it moved. It also writes `check_map.html`, which
shows each point as written and as used. Fix the coordinates until every point
sits on the right river.

`setup` does the slow, once-only work:

- It prepares the terrain into `static/`.
- It saves every Earth Engine layer the model uses (land cover, soil, …).
- It checks that the model starts the same with Earth Engine switched off.
  Updates never use Earth Engine, so they cannot stop to wait for a sign-in.
- It runs `warm_up_days` of past rain to wet the soil.
- It downloads each flood area's DEM.

## What one update does

1. It reads every rain station and keeps the readings in `archive/`. The live
   feeds keep only a day or so, and the archive keeps everything. The **last
   report** is the latest time every working station has reported. A station
   silent for over an hour counts as missing.
2. It starts from the model state saved about `look_back_hours` (12 h) before
   the last report. So it runs those hours again with any rain that arrived
   late.
3. For each rain forecast it builds that forecast's rain: measured station
   rain up to the last report, and the forecast after it. One station gives
   the same rain everywhere. Several are spread by distance. A 10-minute
   interval no station reported is filled from the main forecast, or with
   zero rain (`missing_rain`), and the page says "estimated".
4. It runs all forecast **versions** side by side, each to `forecast_hours`
   ahead. The main version saves a state every hour up to the last report,
   for the next update. One more version assumes no more rain falls.
5. It draws the flood maps of every area from the main version.
6. It writes the web page's files into `site/` and publishes them.

Nothing on the page goes blank when something fails:

- If a forecast feed fails, its last good forecast (up to 12 h old) stands in.
  If there is none, that version is left out.
- If the main run fails, the page keeps the last results and says since when.

Each update writes `status.json`, which `lynch-forecast status` reads.

The model must be able to carry on from a saved state
([6.8](https://mrrpy.readthedocs.io/en/latest/manual/outputs/#68-carrying-on-from-a-saved-state)), so `check` refuses:

- `RUNOFF_SOURCE: scs_cn`: it adds up rain from the start and never recovers;
- `raster` runoff;
- the `dynamic` routing method;
- per-zone saturation excess (set `VSA_PER_POLYGON: false`), because every
  forecast has its own rain points.

Physical runoff with Green-Ampt infiltration and impervious land suits it best.

## The folder

| Folder or file | Holds |
|---|---|
| `static/` | terrain and layers from setup; `static/areas/<area>/` the area DEMs |
| `archive/` | every station reading and inflow; recent forecasts |
| `states/` | saved states: all of the last 2 days, then one a day |
| `cycles/<time>/<version>/` | each update's runs: rain files, hydrographs, logs |
| `site/` | the web page: `index.html` and `data/`, `maps/` |
| `status.json` | what the last update did |

## Running it on a server

Any computer that stays on will do. One update of a basin of 75,000 cells
takes a few minutes. Run it from cron, two minutes after each half hour:

```bash
2,32 * * * *  cd /srv/lynch-forecast && lynch-forecast run -p places.yaml >> cycles.log 2>&1
```

You can also run `lynch-forecast serve` in `tmux`, or as a systemd service. A
lock file stops two updates from running in the same folder at once.

## Publishing on GitHub Pages, with GitHub as a backup

Use a small repository of its own for the forecast, and set:

```yaml
name: My river flood forecast
model: model.yaml
rain_stations:
  - {name: My gauge, source: csv, path: gauge.csv, lat: 27.57, lon: 85.49}
website:
  title: My river flood forecast (experimental)
  publish: {git: {remote: git@github.com:USER/my-river-forecast.git}}
```

Each update pushes the page to the `gh-pages` branch. It pushes the newest
states and the archive to a `state` branch. Each push is one fresh commit, so
the repository stays small. In the repository settings, serve Pages from
`gh-pages`.

GitHub can also run the forecast, but only as a **backup**:

- its schedules start late or are skipped when GitHub is busy;
- they stop after 60 days without a commit;
- there is no GPU;
- the free minutes are limited on private repositories.

To set up the backup:

```bash
lynch-forecast github-workflow -p places.yaml
```

This writes `.github/workflows/forecast-backup.yml`. Commit it with
`places.yaml` and each place's `forecast.yaml`, `model.yaml` and `static/`. Every half hour it checks whether
the server has updated in the last 45 minutes. If it has, it stops at once. If
not, it fetches the `state` branch and runs the update itself.

## Rehearse a past flood

```bash
lynch-forecast replay -f places/lynch/forecast.yaml --start "2026-10-01 00:00" --end "2026-10-03 00:00" --every 3
```

This runs the updates one after another on a fake clock. Each update sees only
the data that existed at its time. Use station sources that hold the past, for
example `gauge_files` with satellite rain prepared for the storm. Use forecasts
as they were issued: `open_meteo` with `api: single_run` (for 2024 storms,
`model: ecmwf_ifs`; Open-Meteo keeps no 2024 runs of the other models). The rehearsal gets
its own folder `replay/<start>/`. `replay_summary.csv` holds the highest
expected flow at every point, for every forecast and update. Use it to set
warning levels from the model's own flows, and to see how early the model
would have warned.

## Adding a data source

Each kind of feed is a small function registered by name. The function returns
pandas tables, so a new station network or forecast service is one file. See
the docstring of `lynch_forecast.sources`. Built in:

- rain stations: `alertnepal`, `csv`, `gauge_files`;
- rain forecasts: `open_meteo` (any of its weather models), `alertnepal_forecast`,
  `csv`;
- inflows: `csv`;
- river levels (`river_gauges`): `usgs`, `rts`, `csv`.
