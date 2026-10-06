# AGENTS.md

## Repository purpose

This repository holds the configuration of the PLC managing CCH's HVAC
system. Additionally it contains a handful of scripts for downloading
and analyzing metrics generated from the PLC.

## Repository layout

- `/scripts/download_bigpics.sh`: metrics retrieval script.
- `/scripts/plot_temps.py`: standalone temperature plotting script.
- `/scripts/plot_heat_pump_runtimes.py`: standalone heat pump runtime histogram
- `/data/bigpic_YYMMDD.csv`: ETL'd metrics from the PLC; not committed
- `/graphs/*.png`: Generated graphs; not committed

## Input data contract

Despite the `.csv` extension, telemetry files are tab-delimited and have a header row. The example contains one-second observations and these columns:

- `Date`: timestamp formatted as `%H:%M:%S %m-%d-%Y` (for example, `00:00:00 06-27-2026`). Parse the value in this column rather than inferring a date from the filename.
- `BLDGHWRTEMPAVG`: building hot-water return temperature average.
- `BLDGHWSTEMPAVG`: building hot-water supply temperature average.
- `WELLINTEMP`, `WELLRETTEMP`: well-loop temperatures.
- `WELLPRESSAVG`: average well pressure.
- `HP1STATE` through `HP6STATE`: heat-pump state values.
- `WWPSTATE`, `WWP1SPDPCNT`: water/well pump telemetry.
- `DHWOUTTEMP`: domestic-hot-water outlet temperature.
- `OATEMP`: outside-air temperature.
- `CURRENTPHASE1`: phase-1 current.

The plotting code treats temperature values as degrees Fahrenheit.

### Heat-pump state semantics

For every `HP<N>STATE` column:

- `0` means heat pump N was not running.
- Any nonzero value means heat pump N was running.

Treat heat-pump state as an on/off condition using `value != 0`. Do not assume that the magnitude of a nonzero value indicates output, stage, speed, or load. Do not apply this rule to `WWPSTATE` or other columns without additional domain information.

## Development guidelines

- Preserve tab-separated input handling (`sep='\t'`) and the timestamp format unless the data contract is intentionally changed.
- Keep support for multiple `bigpic*.csv` files: concatenate them and sort by parsed timestamp before analysis.
- Validate required columns and fail with a useful message rather than silently producing misleading output.
- Account for missing values when calculating statistics or state transitions.
- Prefer vectorized pandas operations over row-by-row loops for full-size telemetry files.
- Avoid loading unnecessary columns or making extra full-data copies when adding analyses; production files may be large.
- Keep generated artifacts and real `bigpic*.csv` data out of version control.
- If heat-pump activity is added to a plot or calculation, derive it from `HP1STATE` through `HP6STATE` with nonzero checks, not numeric sums of their raw state codes.

## Running the script

Use Python 3 with `pandas` and `matplotlib` installed. Place one or more real exports named `bigpic*.csv` in the repository root, then run:

```text
python3 ./scripts/plot_temps.py
```

The script writes `./graphs/temperature_plot.png` from the repository root.

## Verification

There is currently no automated test suite. After modifying data parsing or plotting:

1. Run a syntax check with `python3 -m py_compile ./scripts/plot_temps.py`.
2. Run the script against representative tab-delimited input.
3. Confirm `temperature_plot.png` is created and inspect it for sensible ordering, labels, and values.
4. For any heat-pump logic, explicitly verify that zero maps to off and several different nonzero codes all map to on.
