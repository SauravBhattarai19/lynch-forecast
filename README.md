# Lynch Creek flood forecast, Jackson MS (experimental)

A live flood forecast for Lynch Creek in Jackson, Hinds County, Mississippi.
Every 6 hours it fetches the latest weather-model rain (NBM, HRRR, GFS, ECMWF and
ICON), runs the [MRRpy](https://github.com/SauravBhattarai19/MRRpy) model 72
hours ahead on the USGS 3DEP 10 m terrain, and maps the flood depth on the USGS
3DEP 1 m lidar. A second page runs the 10-year and 20-year design storms of NOAA
Atlas 14 through the same model, with 2-D flood maps at 4 m (the 1 m lidar averaged)
and the 1 m HAND map of the whole basin.

It is the [Roshi Khola forecast](https://github.com/SauravBhattarai19/Roshi-Forecast)
(Nepal) moved to a US county creek, to show the same system working on county
data.

> [!WARNING]
> **This is a research test, not a flood warning.** Follow the National Weather
> Service (Jackson, MS) and Hinds County Emergency Management. The model is not
> calibrated on Lynch Creek; its flows and maps are a model's, not a flood
> study's, and culverts and storm drains are not in the terrain.

## What it covers

| | |
|---|---|
| Basin | Lynch Creek to just above the Pearl River, 37.9 km² (14.6 mi²); the USGS gauge 02486100 at Valley Street drains 31.3 km² |
| Routing terrain | USGS 3DEP 1/3 arc-second DEM at 10 m (379,000 cells), the NHDPlus creeks burned in ([why](docs/lynch.md#the-terrain-dem-and-modelyaml)) |
| Flood maps | USGS 3DEP 1 m lidar (NatchezTrace 2016, [`scripts/make_lidar.py`](docs/lynch.md#the-1-m-lidar-demlynch_3dep1mtif)): every creek of the basin by HAND each update; 2-D at 4 m for the design storms |
| Rain | no station: HRRR's hourly runs before now; after now NBM (the main forecast), HRRR, GFS, ECMWF, ICON and "no more rain", each its own version, through [Open-Meteo](https://open-meteo.com) |
| River flow | seven points from the West branch at I-220 to South Gallatin Street, in m³/s, with the gauge's measured level from USGS |
| Warning levels | warning: the NWS action stage at the gauge (LYNM6, 12 ft = 2,210 cfs); danger: the gauge's 10-year flood (5,845 cfs) |
| Design storms | 10- and 20-year, 24-hour, NOAA Atlas 14 (157 and 180 mm), checked against the gauge's own 10- and 20-year floods |
| Model | MRRpy: Green-Ampt infiltration with impervious land, implicit diffusive-wave routing |

[docs/lynch.md](docs/lynch.md) gives the set-up and where every number comes
from.

## How it is split

All the hydrology and hydraulics are the **MRRpy library's**: rain onto the
basin, infiltration, runoff, river routing and the flood maps. This repository
holds no model code. It only feeds MRRpy and shows what MRRpy gives:

| Here (`lynch_forecast/`) | In MRRpy |
|---|---|
| reading the forecasts and the USGS gauge (`sources/`) | rain onto the grid, Open-Meteo rain (`MRRpy.core.precip`) |
| building each version's rain files (`rainfiles.py`) | runoff: Green-Ampt, impervious (`MRRpy.core.runoff`) |
| keeping saved states and choosing the start (`states.py`) | saving and loading a model state (`RESTART_FROM`, `SAVE_STATE_AT_HOURS`) |
| running the versions side by side (`models.py`, `cycle.py`) | routing (`MRRpy.core.routing`) |
| the design storms: Atlas 14, the storm, the gauge's floods (`design.py`) | flood maps, HAND and 2-D (`MRRpy.core.inundation`) |
| the web pages and publishing (`products.py`, `website.py`, `web/`) | |
| the burned 10 m DEM (`scripts/make_dem.py`) | |

The forecast code is general. Another creek needs only its own `model.yaml`
and `forecast.yaml` (`lynch-forecast new`).

## Install

On the computer that will run it (Linux or macOS):

```bash
git clone https://github.com/SauravBhattarai19/lynch-forecast.git
cd lynch-forecast
scripts/install.sh                          # .venv with this package and its MRRpy
.venv/bin/lynch-forecast service install    # runs by itself from now on
.venv/bin/lynch-forecast doctor             # what is ready, what to fix
```

**Handing it over? Read the [operator guide](docs/operator-guide.md).** It
covers the sign-ins, what to edit for each change, and what to do when
something is wrong.

The forecast needs an MRRpy with restart files, Open-Meteo rain and flood maps
for several areas. MRRpy's
[`forecast-support`](https://github.com/SauravBhattarai19/MRRpy/tree/forecast-support)
branch has all three. The install takes MRRpy from it (developers:
`pip install -e ".[setup,test]"`). `lynch-forecast` checks the MRRpy it runs on
and says what is missing. Setting a place up uses Google Earth Engine (soil,
land cover); the updates do not. The 1 m lidar comes from the USGS directly.

## Running it

```bash
lynch-forecast check -f places/lynch/forecast.yaml    # try every feed; where each point falls
lynch-forecast setup -f places/lynch/forecast.yaml    # once: terrain, layers, 30-day warm-up, 1 m DEMs
lynch-forecast serve                                  # keeps going: every 6 hours, then the site published
lynch-forecast run                                    # one update now
lynch-forecast design -f places/lynch/forecast.yaml   # the 10- and 20-year storms, once (design.yaml)
lynch-forecast site                                   # only rebuild the site (public/)
lynch-forecast doctor                                 # is everything ready and running? what to fix
```

The site is `public/`: a front page with every place in `places.yaml`, and a
page for each place under `public/<place>/`. Lynch Creek's has

- a map of every creek, coloured by flow; click any creek for its forecast;
- the 1 m flood map of the whole basin; click it for the depth;
- the hydrographs of each forecast, with the USGS gauge's level under them;
- day-by-day tiles;
- a link to the design storms, `public/lynch/design/`.

Each update publishes the site to this repository's `gh-pages` branch (`publish:` in
`places.yaml`; [how](docs/how-it-works.md#publishing-on-github-pages-with-github-as-a-backup)):
<https://sauravbhattarai19.github.io/lynch-forecast/>, Lynch Creek under `lynch/`.

| File or folder | What it is | In git? |
|---|---|---|
| `places.yaml` | the places, the site's title, where it is published | yes |
| `places/lynch/forecast.yaml` | the forecasts, points, gauge and maps | yes |
| `places/lynch/model.yaml` | the MRRpy settings | yes |
| `places/lynch/design.yaml` | the design storms and their 2-D areas | yes |
| `places/lynch/dem/` | the burned 10 m DEM, the NHDPlus creeks burned into it, how it was made | yes |
| `places/lynch/design/atlas14.json`, `usgs_02486100_peaks.rdb` | NOAA Atlas 14 depths and the gauge's yearly peaks | yes |
| `docs/` | [operator guide](docs/operator-guide.md), [how it works](docs/how-it-works.md), [the forecast file](docs/forecast-file.md), [Lynch Creek](docs/lynch.md) | yes |
| `places/lynch/static/`, `states/`, `archive/`, `cycles/`, `site/`, `design/runs/` | terrain, saved states, gauge archive, each update's runs, the pages | no: made on the server |
| `public/` | the whole web site | no: made on the server, published to `gh-pages` |

## Credits

The model is [MRRpy](https://github.com/SauravBhattarai19/MRRpy). Terrain: USGS
3D Elevation Program (1/3 arc-second and 1 m lidar). Creeks: NHDPlus V2 through
the USGS NLDI. River data: USGS 02486100, Lynch Creek at Jackson, MS, and the
NWS (LYNM6). Design rain: NOAA Atlas 14, Volume 9. Weather-model rain comes from
[Open-Meteo](https://open-meteo.com) (CC BY 4.0), from NOAA (NBM, HRRR, GFS),
ECMWF and DWD (ICON). Map data: © OpenStreetMap contributors.
