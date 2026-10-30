# Example: Lynch Creek, Jackson, Mississippi

Lynch Creek drains south-west Jackson (Hinds County): the West branch from
I-220 and Dixon Road, then past Robinson Street, Ellis Avenue, the USGS gauge
at Valley Street, Jackson State University and University Boulevard, to the
Pearl River below South Gallatin Street. It is about 38 km² of city, much of it
paved, and it rises within an hour or two of heavy rain. Its gauge, USGS
02486100, has peak flows back to 1953. The NWS forecasts the same gauge as
LYNM6.

This forecast copies the Roshi Khola one (Nepal) to a US county creek. The
model and its physics are the same. Only the place, the terrain and the rain
change:

| | Roshi Khola | Lynch Creek |
|---|---|---|
| Routing terrain | FABDEM 90 m | USGS 3DEP 1/3 arc-second, 10 m, NHDPlus creeks burned in |
| Flood-map terrain | the model grid, 30 m | USGS 3DEP 1 m lidar (MS_NatchezTrace_2016) |
| Past rain | two rain stations (RTS) | no station: HRRR's own hourly runs |
| Rain forecasts | ECMWF, GFS, ICON, alertnepal | NBM (main), HRRR, GFS, ECMWF, ICON, no more rain |
| River gauge on the page | RTS 471 at Panauti | USGS 02486100 at Valley Street |
| Warning levels | none | NWS action stage; the gauge's 10-year flood |
| Extra page | | 10- and 20-year design storms (NOAA Atlas 14) |

## The terrain: `dem/` and `model.yaml`

The 3DEP 10 m DEM is good bare earth, but it has every road and railway
embankment and none of the culverts under them. Below the gauge, the DEM sends
Lynch Creek north along the ditch beside I-55 and the railway, into a far
bigger basin (352 km² instead of 38). So `scripts/make_dem.py` burns the
NHDPlus V2 creeks into it:

```bash
python scripts/make_dem.py --box -90.31 32.255 -90.165 32.365 --crs EPSG:32615 \
    --comid 15756806 --out places/lynch/dem/lynch_3dep10m_burned.tif
```

It reads the 3DEP tile from the USGS's own store (the 1/3 arc-second tile
n33w091), reprojects it to 10 m in UTM 15N and fetches the creeks above the
outlet's NHDPlus line (COMID 15756806) from the USGS NLDI. Then it lowers the
cells along each creek 3 m below the lowest ground within two cells, never
rising downstream. That is 4,059 cells on 20 lines, written beside the DEM in
`dem_source.json`. With the creeks burned in:

| | Model (10 m) | USGS |
|---|---|---|
| Basin at the outlet | 37.9 km² | 38.1 km² (NLDI; 96 % overlap) |
| Basin at the gauge | 31.0 km² | 31.34 km² |

The flood maps use the 1 m lidar as it is, with nothing burned in, so the
culverts under roads are not in them either.

### The 1 m lidar: `dem/lynch_3dep1m.tif`

Earth Engine's `USGS/3DEP/1m` keeps only the newest survey of each place. Here
that is MS_NRCS_East_2018, which covers 0.1 % of the basin, and its downloads
give 0 m where it has nothing: 70–87 % of each 1 m download was a 90 m pit,
and the 2-D maps on it had 27 m of water moving at 400 m/s. So the 1 m DEM is
built from the USGS's own tiles of the MS_NatchezTrace_2016 survey, which covers
the whole basin:

```bash
# the four tiles, 1.7 GB (windowed reads over the web are slow for these files)
for t in x75y358 x75y359 x76y358 x76y359; do
  curl -sO https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/MS_NatchezTrace_2016/TIFF/USGS_one_meter_${t}_MS_NatchezTrace_2016.tif &
done; wait
python scripts/make_lidar.py --box -90.303 32.258 -90.174 32.362 --crs EPSG:32615 \
    --project MS_NatchezTrace_2016 --only-project --tiles-dir . \
    --out places/lynch/dem/lynch_3dep1m.tif --also 4
```

It is 147 million cells: 97.8 % from the survey, and the rest of the box (its
edges, outside the basin) filled from the 10 m DEM. At 300 MB it is not in git;
`lynch_3dep1m.json` says how it was made, and the 4 m copy
(`lynch_3dep4m.tif`, the mean of the 1 m cells) is kept.

The rest of `model.yaml` is the Roshi physics: Green-Ampt infiltration
(HiHydroSoil) with impervious land (local climate zones), and implicit
diffusive-wave routing at Courant 2. The bankfull flow is the median of the
gauge's 69 yearly peaks, 104 m³/s at 31.3 km², scaled by drainage area along the
creeks. The model is not calibrated on Lynch Creek.

## The forecast: `forecast.yaml`

There is no rain station to read. The rain before now is HRRR's: its hourly
3 km runs, kept by Open-Meteo, are the same past for every version. After now,
each version has its own model's forecast, and NBM (the NWS National Blend,
2.5 km) is the main one: it draws the flood maps. Each update reads 70 rain
points from Open-Meteo, so about 1,700 calls a day, well inside the free
10,000.

Seven hydrograph points follow the creek down. Their warning and danger levels
come from the gauge:

| Level | At the gauge | Where it comes from |
|---|---|---|
| Warning | 62.5 m³/s (2,210 cfs) | NWS action stage at LYNM6, 12 ft, read on USGS rating 5.0 |
| Danger | 165.5 m³/s (5,845 cfs) | the 10-year flood: log-Pearson III of 69 yearly peaks, 1953–2024 |

At Robinson Street, Ellis Avenue, University Boulevard and South Gallatin
Street they are the gauge's levels scaled by (drainage area / 31.0 km²)^0.75.
They are placeholders, not real levels. The two points up the West branch have
none.

The page draws the gauge's measured water level (USGS gage height, in
metres) under its hydrograph, from USGS WaterServices.

The whole basin is mapped every update on the 1 m lidar by HAND (height above
the nearest creek), in 4 × 4 overlapping boxes so each stays under MRRpy's 30
million cells. HAND is quick and fair along the main valleys. It knows nothing of
culverts, storm drains or the Pearl River's backwater.

## The design storms: `design.yaml`

```bash
lynch-forecast design -f places/lynch/forecast.yaml
```

This runs the 10-year and 20-year 24-hour storms of NOAA Atlas 14 (Volume 9,
version 2) at the middle of the basin. The storms start from the forecast's
newest saved state, so the soil is as wet as the forecast has it. Each storm is
nested: every duration from 10 minutes to 24 hours holds its Atlas 14 depth.

| Storm | 24 h | Wettest hour | Wettest 10 minutes |
|---|---|---|---|
| 10-year | 157 mm (6.18 in) | 67 mm | 28 mm |
| 20-year | 180 mm (7.08 in) | 76 mm | 32 mm |

Atlas 14 has no 20-year depth. It is read between the 10- and 25-year ones on
the Gumbel scale. Both storms are mapped by HAND over the whole basin, as in the
forecast, and by 2-D simulation at 1 m in four boxes along the creek:

- West branch at I-220 and Dixon Road;
- Robinson Street to Ellis Avenue;
- Valley Street gauge to University Boulevard;
- State Street to the Pearl River.

The page, at `design/` under the place's page, compares each storm's flow at
the gauge with the gauge's own flood of the same return period. A storm of a
given return period need not give the flood of that return period, but it
should be near it.
