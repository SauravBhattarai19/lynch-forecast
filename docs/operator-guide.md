# Operator guide

This guide is for the person who keeps the flood forecast running. No
programming is needed. Once it is installed, the forecast runs by itself. You
change what it does by editing a few text files (YAML), and it picks up each
change on its own.

## What runs, and where

One computer runs `lynch-forecast serve` all the time, as a service. It needs
Linux or macOS (Windows: inside WSL), with internet. Every 6 hours
(`every_minutes: 360`) it does
this for each place listed in `places.yaml`:

1. reads the rain stations;
2. fetches the weather-model rain forecasts;
3. runs the MRRpy model once for each forecast;
4. draws the flood maps;
5. writes the web pages and publishes them.

The site is a front page with every place, plus one page for each place.

Each round of Lynch Creek takes 30 to 60 minutes on the current server, a large
one shared with other forecasts: the six 96-hour runs on the 10 m grid, then the
1 m map of the whole basin. A smaller computer takes longer. That is fine as long
as a round finishes well within the 6 hours. Do not run the design storms
(`lynch-forecast design`) at the same time as a round on a busy computer: both
then slow down many times over.

## Install (once)

```bash
git clone https://github.com/SauravBhattarai19/lynch-forecast.git
cd lynch-forecast
scripts/install.sh
```

The script makes a Python environment in `.venv`, installs the forecast and the
MRRpy model, and runs the check (`doctor`). It needs Python 3.10 or newer and
git. Then sign in once to three services:

| What for | Command |
|---|---|
| Earth Engine. Needed to **set up** a place (terrain, soil, land cover); the hourly updates do not use it. | `.venv/bin/MRRpy earth-engine-login --project YOUR-PROJECT` |
| Station passwords, if a station needs one (RTS) | `scripts/save-credentials.sh rts` |
| Publishing: this computer must be able to `git push` to the repository named under `publish:` in `places.yaml` | `gh auth login`, or an SSH key added to GitHub |

Then start it for good:

```bash
.venv/bin/lynch-forecast service install
```

From then on, it starts when the computer starts and starts again if it ever
crashes. To run while nobody is logged in, it needs "linger". The install
switches it on, or prints the one command an administrator must run.

Moving from another computer? Copy each `places/<place>/archive/` folder
across before starting. The stations only serve about a day of history, so
this archive is the only long record of their rain. Everything else is remade
by itself.

## Every day: nothing

Look at the site now and then. Each place's card says when it was last
updated. **Late** means it has not updated for more than 2½ rounds; then run:

```bash
.venv/bin/lynch-forecast doctor
```

It checks everything and says what to fix, in words. Each line is `ok`,
`WARN` (it works, but have a look) or `FIX` (it will not work until fixed).

## Changing what it does: edit a file

Every round, `serve` reads `places.yaml` and every place's files again.

| You want to … | Edit | When it takes effect |
|---|---|---|
| **add a place** | run `lynch-forecast new places/NAME --name "Name" --outlet LAT,LON`, then add its points to its `forecast.yaml` | next round: it is set up by itself (about 10 minutes), then it appears on the site |
| remove a place | `places.yaml`: delete its line | next round |
| add a point with a hydrograph | the place's `forecast.yaml`, under `hydrograph_points` | next round |
| set or change warning levels | the same: `warning_m3s`, `danger_m3s` | next round |
| show a river gauge's measured level under a point | the place's `forecast.yaml`, under `river_gauges` | next round |
| add a close-up flood map | `forecast.yaml`, under `flood_areas` | its DEM is fetched by itself; the map appears from the next round |
| add a rain station | `forecast.yaml`, under `rain_stations` | next round |
| run a place on forecasts only (no live stations) | `forecast.yaml`: `rain_stations: []`, which is what `new` writes. The rain before now is then the main forecast model's own, and the page says so | next round |
| add or drop a weather model | `forecast.yaml`, under `rain_forecasts` | next round |
| forecast further ahead | `forecast.yaml`: `forecast_hours` | next round |
| update more or less often | `places.yaml`: `every_minutes` | after the current round |
| title, notice, where it is published | `places.yaml` | next round |
| change the model itself (basin, runoff, routing) | the place's `model.yaml` | the place is set up again by itself. Its old saved states no longer fit and are moved to `old/` |

Lines to copy:

```text
hydrograph_points:
  - {name: Town bridge, lat: 27.5849, lon: 85.5214, warning_m3s: 150, danger_m3s: 300}
flood_areas:
  - {name: Town, box: [85.50, 27.58, 85.54, 27.61], method: 2d}   # west, south, east, north
rain_stations:
  - {name: AWS at Nala, source: rts, station: 469}
```

Every setting is explained in [the forecast file](forecast-file.md).

**A mistake in a file does no harm.** The forecast keeps using the last good
version of that file, and `doctor` and the log say what is wrong. To check a
place's file before relying on it, run
`lynch-forecast check -f places/NAME/forecast.yaml`. This tries every feed and
shows where each point falls on the rivers.

### A new place from its outlet

```bash
.venv/bin/lynch-forecast new places/nakhu --name "Nakhu Khola" --outlet 27.62,85.29
```

The outlet is the point on the river that the basin drains to. The command
writes `places/nakhu/model.yaml` and `forecast.yaml` and adds the place to
`places.yaml`. The model uses the same physics as Lynch Creek (and the Roshi
Khola before it): Green-Ampt infiltration, implicit diffusive routing, on 90 m
bare-earth terrain unless you give it a DEM of your own (Lynch Creek: 3DEP
10 m with the creeks burned in, `scripts/make_dem.py`).

The new place is forecast-only: it has no rain stations (`rain_stations: []`),
so the rain before now is the main forecast model's own. Add its stations there
if it has some; the Bagmati at Khokana place runs without any.

The model starts with a small box around the outlet. Setup downloads the
terrain and widens the box by itself until the whole basin fits. The place
starts with the outlet as its only point and with a flood map of the whole
basin. Add the towns and bridges to watch, with their warning levels, under
`hydrograph_points`.

## What it does by itself

- reads the settings again every round, keeping the last good version of a file
  with a mistake;
- sets up new places and changed models, and fetches the DEMs of new flood maps.
  A failed setup is tried again an hour later;
- keeps going when a feed fails. A failed forecast is replaced by the last good
  one (up to 12 hours old). A station silent for over an hour counts as missing,
  and the forecast-model rain fills the gap; the page says so;
- starts again after a crash or a restart of the computer (the service);
- deletes old runs and old saved states.

## When something is wrong

| What you see | Why | What to do |
|---|---|---|
| a card says **Late** | `serve` stopped or is stuck | `lynch-forecast service status`, then `lynch-forecast service logs`; `lynch-forecast service restart` |
| `FIX Earth Engine` | the sign-in expired | `.venv/bin/MRRpy earth-engine-login --project YOUR-PROJECT` |
| `FIX Sign-in: RTS` | the password is missing or changed | `scripts/save-credentials.sh rts` |
| `FIX Publishing` | this computer cannot push | `gh auth login` (or an SSH key on GitHub) |
| `FIX Settings` | a file has a mistake | the line says which file and what; fix it |
| a new place never appears | its setup fails | `doctor` shows the error. To see it all: `lynch-forecast setup -f places/NAME/forecast.yaml` |
| a station shows **Silent** | the station is not reporting | nothing; the forecast-model rain fills in |

## Updating the software

```bash
git pull
scripts/install.sh
.venv/bin/lynch-forecast service restart
```

## The commands

| Command | What it does |
|---|---|
| `lynch-forecast doctor` | is everything ready and running? what to fix |
| `lynch-forecast service install / status / logs / restart / uninstall` | the service that keeps `serve` running |
| `lynch-forecast new places/NAME --outlet LAT,LON` | a new place |
| `lynch-forecast check -f places/NAME/forecast.yaml` | try every feed of a place; where its points fall |
| `lynch-forecast status` | each place's last update and its stations |
| `lynch-forecast run` | one update of every place now |
| `lynch-forecast setup -f places/NAME/forecast.yaml` | set a place up by hand (`serve` does it by itself) |
