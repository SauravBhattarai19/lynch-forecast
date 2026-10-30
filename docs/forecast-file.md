# The forecast file

`forecast.yaml` lists what a [live forecast](how-it-works.md) reads, runs and shows.
`lynch-forecast new` writes a commented one. `lynch-forecast check` reports
every problem at once, in plain words. A misspelt key gets "did you mean".
Relative paths are read from the forecast file's folder, and times are UTC.

```yaml
name: Lynch Creek flood forecast
model: model.yaml
display_timezone: America/Chicago
forecast_hours: 72
rain_stations: []
rain_forecasts:
  - {name: NBM, source: open_meteo, model: ncep_nbm_conus, spacing_m: 3000}
  - {name: HRRR, source: open_meteo, model: ncep_hrrr_conus, spacing_m: 3000}
main_forecast: NBM
past_rain_forecast: HRRR
hydrograph_points:
  - {name: Lynch Creek gauge at Valley Street (USGS 02486100), lat: 32.28448, lon: -90.21492, warning_m3s: 62.5, danger_m3s: 165.5}
river_gauges:
  - {name: Lynch Creek at Jackson (USGS 02486100), source: usgs, site: "02486100", point: Lynch Creek gauge at Valley Street (USGS 02486100)}
basin_map: {method: hand, boxes: [4, 4], overlap_km: 0.5, dem: usgs_3dep_1m, dem_scale_m: 1}
website:
  title: Lynch Creek flood forecast, Jackson MS (experimental)
```

## Main settings

| Key | Default | Meaning |
|---|---|---|
| `name` | Flood forecast | a title for the forecast |
| `model` | (needed) | the MRRpy settings file; its `OUTPUT_DIR` is not used: setup works in `static/` |
| `folder` | the forecast file's folder | where `static/`, `archive/`, `states/`, `cycles/` and `site/` go |
| `display_timezone` | UTC | times on the web page, e.g. `America/Chicago` |
| `every_minutes` | 30 | minutes between updates (`serve`) |
| `forecast_hours` | 48 | how far ahead each update forecasts |
| `look_back_hours` | 12 | hours of station rain each update runs again |
| `warm_up_days` | 30 | days of past rain setup runs to wet the soil |
| `missing_rain` | `forecast_model` | a 10-minute interval no station reported: the main forecast's rain, or `zero` |
| `station_silent_minutes` | 60 | a station silent this long counts as missing |
| `main_forecast` | the first forecast | the forecast that draws the flood maps |
| `past_rain_forecast` | `main_forecast` | with no station: the forecast whose own past is the rain before now, the same for every version (HRRR's hourly runs at Lynch Creek) |
| `no_rain_version` | true | also run a version with no more rain |
| `workers` | 0 | model runs at the same time (0 = all versions at once) |
| `threads_per_run` | 8 | CPU threads for each run |
| `keep_cycles_days` | 3 | days to keep each update's folder |
| `keep_states_days` | 30 | days to keep the daily saved states |

## Rain stations: `rain_stations`

Each entry has a `name`, a `source` and that source's keys.

Stations stand for the whole basin: the rain at each model cell is spread from
them by distance. List them only where they are spread well enough for that.
With `rain_stations: []`, the rain before now is the main forecast model's own
(`missing_rain`), and the page says so. The Roshi Khola does this: its three
stations all lie in the quarter of the basin above the Panauti gauge, which
runs on them as its own place (`places/panauti`).

| Source | Keys | Reads |
|---|---|---|
| `alertnepal` | `station` (number), `parameter` (`PCPN_10M`) | an automatic weather station of the [alertnepal](https://alertnepal.online) dashboard (Nepal). These are the dashboard's own interfaces, not a published API: ask the alertnepal team before you run a forecast on them |
| `rts` | `station` (number), `parameter` (`PCPN_10M`), `extra` (soil moisture), `backfill_days` (35), `sign_in` (`RTS`) | a station of the [RTS](https://rts.wscada.net) time-series system, which needs a sign-in: save it with `scripts/save-credentials.sh rts`. `python -m lynch_forecast.sources.rts` lists the stations it can read. The first reading goes back `backfill_days`, so the warm-up runs on measured rain; later ones ask only for what is new. Readings flagged by RTS's quality check are left out |
| `csv` | `path` (file or web address), `lat`, `lon`; `time_column` (`time_utc`), `value_column` (`rain_mm`), `timezone`, `step_minutes` | one gauge: the mm that fell in the interval ending at each time |
| `gauge_files` | `gauges`, `timeseries`, `start_utc`; `crs` | many gauges in MRRpy's own [rain-gauge files](https://mrrpy.readthedocs.io/en/latest/manual/precipitation/), e.g. satellite rain prepared for a rehearsal |

## Rain forecasts: `rain_forecasts`

Each forecast runs as its own version, with its own line on every chart.

| Source | Keys | Reads |
|---|---|---|
| `open_meteo` | `model`, e.g. `ecmwf_ifs025`, `gfs_seamless`, `icon_seamless`; `spacing_m` (10000), `buffer_m`; `api` (`forecast`, or `single_run` for a rehearsal), `run_hours`, `delay_hours` | a weather model through MRRpy's [Open-Meteo source](https://mrrpy.readthedocs.io/en/latest/manual/precipitation/): each model cell over the basin becomes a rain point. It is fetched again when its window moves, at most once an hour. At 10 km spacing that is about 25 points per model, well inside Open-Meteo's free 10,000 calls a day |
| | | For a rehearsal, Open-Meteo keeps past runs of only some models: the 2024 ECMWF runs are under `ecmwf_ifs` (9 km), not `ecmwf_ifs025` |
| `alertnepal_forecast` | `points`: `[[lat, lon], …]` (default: the middle of the basin) | alertnepal's hourly rain forecast (240 h). Its times are Nepal time marked as UTC, and are converted |
| `csv` | `path`, `lat`, `lon` (as the `csv` station) | a forecast at one point from a file |

## Inflows: `inflows`

These are rivers that enter the model at its edge, each an
[inflow hydrograph](https://mrrpy.readthedocs.io/en/latest/manual/inflows/).

| Key | Meaning |
|---|---|
| `name`, `lat`, `lon` | the point; it moves to the nearest river cell |
| `source: csv`, `path` | the readings: `time_utc` and `Q_m3s` |
| `ahead` | after the last reading: `hold` it, or `recede` |
| `recede_hours` | how fast it recedes: 24 h falls to 37 % in a day |

## Hydrograph points: `hydrograph_points`

| Key | Meaning |
|---|---|
| `name`, `lat`, `lon` | the point; it moves to the river cell of largest drainage within `snap_radius_cells` (2). A larger radius slides a point down its river, so put the point on the river and keep the radius small |
| `warning_m3s`, `danger_m3s` | optional levels. The page says "above warning level" or "above danger level" in words |

The outlet gets a hydrograph too, unless a point is already within 500 m of it.

## River gauges: `river_gauges`

A river gauge's measured water level, drawn in its own panel under a point's
hydrograph, with its own scale, so the forecast can be checked against the river.
The model never uses it. Each entry has a `name`, a `source`, that source's keys
and `point`: the name of the hydrograph point it is drawn under (put that point
at the gauge). The readings are kept in `archive/levels/`.

| Source | Keys | Reads |
|---|---|---|
| `rts` | `station` (number), `parameter` (`WTR_LVL`), `backfill_days` (35), `sign_in` (`RTS`) | a river gauge of the RTS system (471 is the Roshi Khola at Panauti) |
| `usgs` | `site` (the USGS number, as text: `"02486100"`), `parameter` (`00065`, gage height), `backfill_days` (35) | a USGS gauge, from WaterServices, or the new Water Data API when that fails; feet are turned into metres |
| `csv` | `path` (file or web address); `time_column` (`time_utc`), `value_column` (`level_m`), `timezone` | the water level at each time |

The page shows the gauge's own reading, in metres. It is not the depth: the
RTS gauge at Panauti reads about 1342.7 m at low flow. It also says how far the
level is above its lowest reading of the last 30 days, and how it changed in
the last hour.

```text
hydrograph_points:
  - {name: Panauti gauge (RTS 471), lat: 27.56998, lon: 85.53894, snap_radius_cells: 3}
river_gauges:
  - {name: Roshi Khola at Panauti, source: rts, station: 471, point: Panauti gauge (RTS 471)}
```

## Flood areas: `flood_areas`

| Key | Meaning |
|---|---|
| `name` | the area's name |
| `box` | `[west, south, east, north]` in degrees |
| `method` | `hand` (seconds) or `2d` (more exact, about a minute): see [6.3](https://mrrpy.readthedocs.io/en/latest/manual/outputs/#63-flood-depth-and-extent-maps) |
| `dem` | `auto`, `model_grid`, a DEM name or a GeoTIFF, as `INUNDATION_DEM` |
| `dem_scale_m` | the resolution of a downloaded DEM |

## The whole basin: `basin_map`

One more flood map, of every river in the basin, drawn in boxes side by side
and joined. `basin_map: 2d` (or `hand`) uses the defaults.

| Key | Default | Meaning |
|---|---|---|
| `name` | Whole basin | its name on the page |
| `method` | `2d` | `2d` or `hand` |
| `boxes` | `auto` for 2-D, `[1, 1]` for HAND | `[columns, rows]`; give it for HAND on a 1 m DEM, so each box stays under MRRpy's 30 million cells |
| `overlap_km` | 0.5 | how far the boxes overlap; the deeper water is kept where they do |
| `dem`, `dem_scale_m` | as for flood areas | Lynch Creek: `usgs_3dep_1m`, 1 |

## The web page: `website`

| Key | Meaning |
|---|---|
| `title` | the page title |
| `notice` | a line added to the notice at the top, e.g. who runs it |
| `links` | a list of `{name, url, text}`: cards under the flood maps, e.g. the design-storm page (`url: design/`) |
| `publish` | `{git: {remote: …}}` pushes to GitHub Pages ([how](how-it-works.md#publishing-on-github-pages-with-github-as-a-backup)); `{command: "…"}` runs any command (`{site}` is the page folder) |

## Design storms: `design.yaml`

`lynch-forecast design -f places/<place>/forecast.yaml` runs storms of a
chosen return period, once, from the newest saved state, and writes a page of
their own to `site/design/` (published with the place, under `design/`). It
reads `design.yaml` beside the forecast file:

| Key | Default | Meaning |
|---|---|---|
| `atlas14` | `design/atlas14.json` | NOAA Atlas 14 depths at the middle of the basin; fetched from NOAA HDSC when the file is missing |
| `duration_hours` | 24 | the storm's length |
| `peak_at` | 0.5 | where the most intense 10 minutes fall (0.5 = half-way) |
| `lead_hours`, `after_hours` | 1, 24 | dry hours before the storm; hours run after it |
| `pad_hours` | 6 | 2-D maps: hours simulated before the flood rises and after it falls (MRRpy's own is 24; at 1 m a simulated hour takes about 2 minutes on 32 cores) |
| `start_from` | `latest` | the saved state to start from: `latest`, or a state file, e.g. `states/state_20261030T0000Z.npz` |
| `storms` | (needed) | `{name, years}` each; years between Atlas 14's are read on the Gumbel scale |
| `gauge_peaks`, `gauge_point` | none | a USGS peak-flow file (RDB) and the point at its gauge: the page compares the storm's flow with the gauge's flood of the same return period (log-Pearson III) |
| `flood_areas` | none | as in the forecast file; `method: 2d` by default. Each runs in its own process |
| `basin_map` | true | also draw the forecast's `basin_map` for each storm |

The storm is nested: every duration from 10 minutes to the whole storm holds
its Atlas 14 depth, the blocks set out alternately around the peak. The same
rain falls on the whole basin, with no areal reduction.
