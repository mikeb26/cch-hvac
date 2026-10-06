#!/usr/bin/env python3
"""Plot completed heat-pump run durations from tab-delimited ``bigpic*.csv`` exports.

A heat pump is on whenever its HP<N>STATE value is nonzero. Missing state values
are forward-filled, so they retain the most recently observed state. Only runs
with both an observed 0->nonzero start and a later observed nonzero->0 end are
included: runs already active at the beginning of the data and still active at
the end are deliberately excluded.

The script writes ``heat_pump_runtime_histogram.png``. The image is an
infographic containing aggregate and per-pump statistics alongside a compact
histogram. Durations are measured from the timestamp of the observed start
transition to that of the observed stop transition.
"""
import glob

import matplotlib.pyplot as plt
import pandas as pd


GLOB_PATTERN = "data/bigpic*.csv"
DATE_COLUMN = "Date"
DATE_FORMAT = "%H:%M:%S %m-%d-%Y"
HEAT_PUMP_COLUMNS = [f"HP{number}STATE" for number in range(1, 7)]
OUTPUT_FILE = "graphs/heat_pump_runtime_histogram.png"
HISTOGRAM_BIN_EDGES = [0, 5, 10, 15, 30, 60, 120, 240, 480]
HISTOGRAM_BIN_LABELS = [
    "0–5m",
    "5m–10m",
    "10–15m",
    "15m–30m",
    "30m–60m",
    "60m–120m",
    "120m–240m",
    "240m–480m",
    ">480m",
]
LOW_END_HISTOGRAM_BIN_EDGES = list(range(21))
LOW_END_HISTOGRAM_BIN_LABELS = [f"{minute}-{minute + 1}m" for minute in range(20)]


def load_telemetry(files):
    """Load only timestamps and heat-pump states, sorted chronologically."""
    required_columns = [DATE_COLUMN] + HEAT_PUMP_COLUMNS
    dataframes = []

    for filename in files:
        header = pd.read_csv(filename, sep="\t", nrows=0)
        missing_columns = [
            column for column in required_columns if column not in header.columns
        ]
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise KeyError(f"File '{filename}' is missing required column(s): {missing}")

        frame = pd.read_csv(filename, sep="\t", usecols=required_columns)
        frame[DATE_COLUMN] = pd.to_datetime(
            frame[DATE_COLUMN], format=DATE_FORMAT, errors="coerce"
        )
        invalid_dates = frame[DATE_COLUMN].isna().sum()
        if invalid_dates:
            raise ValueError(
                f"File '{filename}' contains {invalid_dates} invalid "
                f"'{DATE_COLUMN}' timestamp(s); expected format {DATE_FORMAT}."
            )

        # Invalid state text is treated as missing telemetry, retaining the
        # previous observed state just like an empty state value.
        for column in HEAT_PUMP_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        dataframes.append(frame)

    return pd.concat(dataframes, ignore_index=True).sort_values(
        DATE_COLUMN, kind="stable"
    )


def completed_run_durations(data, state_column):
    """Return timedeltas for completed runs of one heat pump.

    Forward filling preserves a pump's state over missing records. A start is
    valid only after an observed off state, which automatically omits a run
    already active at the beginning of the measurement window. A duration is
    retained only if a later observed off state closes it.
    """
    effective_state = data[state_column].ffill()
    state_is_known = effective_state.notna()
    is_running = effective_state.ne(0)
    was_running = is_running.shift()

    # These can occur only at an observed value because forward filling does
    # not change state at a missing value.
    starts = state_is_known & is_running & was_running.eq(False)
    ends = state_is_known & is_running.eq(False) & was_running.eq(True)

    run_ids = starts.cumsum()
    timestamps = data[DATE_COLUMN]
    start_times = pd.Series(
        timestamps.loc[starts].to_numpy(), index=run_ids.loc[starts].to_numpy()
    )
    end_times = pd.Series(
        timestamps.loc[ends].to_numpy(), index=run_ids.loc[ends].to_numpy()
    )

    # Joining by run ID discards an initial unmatched stop and an unmatched
    # final start, leaving only runs observed from start through stop.
    return (end_times - start_times).dropna()


def summarize(durations):
    """Return runtime statistics in minutes for a timedelta Series."""
    minutes = durations.dt.total_seconds() / 60
    if minutes.empty:
        return None

    return {
        "count": len(minutes),
        "total": minutes.sum(),
        "mean": minutes.mean(),
        "median": minutes.median(),
        "min": minutes.min(),
        "max": minutes.max(),
    }


def statistics_line(label, durations):
    """Return the concise runtime-statistics line formerly printed to stdout."""
    statistics = summarize(durations)
    if statistics is None:
        return f"{label}: no completed runs observed"

    return (
        f"{label}: {statistics['count']} completed run(s); "
        f"total {statistics['total']:.3f} min; "
        f"mean {statistics['mean']:.3f} min; "
        f"median {statistics['median']:.3f} min; "
        f"min {statistics['min']:.3f} min; "
        f"max {statistics['max']:.3f} min"
    )


def plot_infographic(all_durations, statistics, start_date, end_date):
    """Write runtime statistics and a supporting histogram as an infographic."""
    minutes = all_durations.dt.total_seconds() / 60
    fig = plt.figure(figsize=(14, 12), constrained_layout=True)
    layout = fig.add_gridspec(3, 1, height_ratios=(3, 2, 2))
    statistics_axis = fig.add_subplot(layout[0])
    histogram_axis = fig.add_subplot(layout[1])
    low_end_histogram_axis = fig.add_subplot(layout[2])

    fig.suptitle("Heat-Pump Completed Run Summary", fontsize=18, fontweight="bold")
    statistics_axis.axis("off")
    statistics_axis.text(
        0.03,
        0.92,
        (
            "Completed-run statistics "
            f"({start_date:%Y-%m-%d} to {end_date:%Y-%m-%d})"
        ),
        fontsize=14,
        fontweight="bold",
        va="top",
        transform=statistics_axis.transAxes,
    )
    statistics_axis.text(
        0.03,
        0.80,
        "\n".join(statistics),
        family="monospace",
        fontsize=11,
        linespacing=1.8,
        va="top",
        transform=statistics_axis.transAxes,
        bbox={"boxstyle": "round,pad=0.8", "facecolor": "#f3f6f9", "edgecolor": "#b7c4d0"},
    )

    if minutes.empty:
        for axis in (histogram_axis, low_end_histogram_axis):
            axis.text(
                0.5,
                0.5,
                "No completed heat-pump runs observed",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_xticks([])
            axis.set_yticks([])
    else:
        duration_buckets = pd.cut(
            minutes,
            bins=HISTOGRAM_BIN_EDGES + [float("inf")],
            labels=HISTOGRAM_BIN_LABELS,
            include_lowest=True,
        )
        bucket_counts = duration_buckets.value_counts(sort=False)
        histogram_axis.bar(
            HISTOGRAM_BIN_LABELS,
            bucket_counts,
            edgecolor="black",
            alpha=0.8,
            color="#4c78a8",
        )
        histogram_axis.set_xlabel("Completed run duration")
        histogram_axis.set_ylabel("Number of runs")
        histogram_axis.tick_params(axis="x", rotation=30)

        low_end_minutes = minutes.loc[minutes.between(0, 20, inclusive="both")]
        low_end_buckets = pd.cut(
            low_end_minutes,
            bins=LOW_END_HISTOGRAM_BIN_EDGES,
            labels=LOW_END_HISTOGRAM_BIN_LABELS,
            include_lowest=True,
        )
        low_end_bucket_counts = low_end_buckets.value_counts(sort=False)
        low_end_histogram_axis.bar(
            LOW_END_HISTOGRAM_BIN_LABELS,
            low_end_bucket_counts,
            edgecolor="black",
            alpha=0.8,
            color="#f58518",
        )
        low_end_histogram_axis.set_xlabel("Completed run duration")
        low_end_histogram_axis.set_ylabel("Number of runs")
        low_end_histogram_axis.tick_params(axis="x", rotation=45, labelsize=8)

    histogram_axis.set_title("Distribution of completed run durations", fontsize=12)
    low_end_histogram_axis.set_title(
        "Completed run durations from 0 to 20 minutes (1-minute buckets)",
        fontsize=12,
    )
#    fig.text(
#        0.5,
#        0.01,
#        f"Histogram saved to {OUTPUT_FILE}",
#        ha="center",
#        fontsize=9,
#        color="#4d5966",
#    )
    fig.savefig(OUTPUT_FILE)
    plt.close(fig)


def main():
    files = sorted(glob.glob(GLOB_PATTERN))
    if not files:
        raise FileNotFoundError(f"No files found matching pattern {GLOB_PATTERN}")

    data = load_telemetry(files)
    per_pump_durations = {
        column: completed_run_durations(data, column) for column in HEAT_PUMP_COLUMNS
    }
    all_durations = pd.concat(per_pump_durations.values(), ignore_index=True)

    statistics = [statistics_line("All heat pumps", all_durations)]
    statistics.extend(
        statistics_line(column.removesuffix("STATE"), durations)
        for column, durations in per_pump_durations.items()
    )
    plot_infographic(
        all_durations,
        statistics,
        data[DATE_COLUMN].min(),
        data[DATE_COLUMN].max(),
    )


if __name__ == "__main__":
    main()
