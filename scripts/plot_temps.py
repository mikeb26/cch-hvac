#!/usr/bin/env python3
"""
Plot the most recent five days of BLDGHWRTEMPAVG and BLDGHWSTEMPAVG,
plus outside temperature from bigpic*.csv telemetry files in ./data.
Saves the plot to ./graphs/temperature_plot.png.
"""
import glob
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Find all CSV files matching the pattern
glob_pattern = 'data/bigpic*.csv'
files = sorted(glob.glob(glob_pattern))

if not files:
    raise FileNotFoundError(f"No files found matching pattern {glob_pattern}")

# Date parsing settings
date_col = 'Date'
date_format = '%H:%M:%S %m-%d-%Y'
temperature_cols = ['BLDGHWRTEMPAVG', 'BLDGHWSTEMPAVG']
outside_temp_col = 'OATEMP'
required_cols = [date_col, *temperature_cols, outside_temp_col]

# Load and concatenate data
dfs = []
for f in files:
    df = pd.read_csv(
        f,
        sep='\t',
        usecols=lambda column: column in required_cols,
    )
    missing_cols = set(required_cols) - set(df.columns)
    if missing_cols:
        missing = ', '.join(sorted(missing_cols))
        raise KeyError(f"File '{f}' is missing required column(s): {missing}")

    df[date_col] = pd.to_datetime(df[date_col], format=date_format)
    df.set_index(date_col, inplace=True)
    dfs.append(df)

# Combine all dataframes and sort by time index
data = pd.concat(dfs).sort_index()

# Restrict the plot to the final five 24-hour periods represented in the data.
latest_timestamp = data.index.max()
start_timestamp = latest_timestamp - pd.Timedelta(days=5)
data = data.loc[data.index >= start_timestamp]

data_subset = data[temperature_cols + [outside_temp_col]].copy()
data_subset.rename(columns={outside_temp_col: 'Outside Temp Avg'}, inplace=True)
plot_cols = temperature_cols + ['Outside Temp Avg']

# Plot
title = 'Building HW Return & Supply Temps and Outside Temp Over Time'
fig, ax = plt.subplots(figsize=(12, 6))

data_subset[plot_cols].plot(ax=ax, title=title)

# Label the maximum and minimum supply temperature (BLDGHWSTEMPAVG) for each day
supply_series = data_subset['BLDGHWSTEMPAVG'].dropna()
if not supply_series.empty:
    supply_color = ax.get_lines()[1].get_color()
    last_day = max(supply_series.index.date)

    for day, daily_supply in supply_series.groupby(supply_series.index.date):
        extrema = []
#        if day != last_day:
        extrema.append(('Max', daily_supply.idxmax(), daily_supply.max(), (10, 12)))
        extrema.append(('Min', daily_supply.idxmin(), daily_supply.min(), (10, -22)))

        for label, timestamp, temperature, offset in extrema:
            label_date = f'{timestamp.month}/{timestamp.day}'
            ax.annotate(
                f'{label} {label_date}: {temperature:.1f}°F',
                xy=(timestamp, temperature),
                xytext=offset,
                textcoords='offset points',
                color=supply_color,
                arrowprops=dict(arrowstyle='->', color=supply_color),
                bbox=dict(boxstyle='round,pad=0.2', fc='white', ec=supply_color, alpha=0.8),
            )

# Format x-axis with labels every 6 hours, including M/D and time
ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
ax.xaxis.set_major_formatter(mdates.DateFormatter('%-m/%-d %-I%p'))
plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

ax.set_xlabel('Time')
ax.set_ylabel('Temperature (°F)')
legend_labels = ['Return Temp Avg', 'Supply Temp Avg']
legend_labels.append('Outside Temp Avg')
ax.legend(legend_labels)
plt.tight_layout()

# Save the figure
output_file = Path('graphs/temperature_plot.png')
output_file.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(output_file)
print(f"Plot saved to {output_file}")
