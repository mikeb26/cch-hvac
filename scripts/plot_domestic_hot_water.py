#!/usr/bin/env python3
"""Create a domestic-hot-water-use infographic from well-detail telemetry.

The script reads tab-delimited ``well-detail_YYMMDD.csv`` files from
``./data/well-detail`` and writes ``./graphs/domestic_hot_water_infographic.png``.
It estimates each calendar day's recirculation flow as that day's first percentile
of FLOWDHWGPM, then treats the remaining flow as domestic hot-water use. It also
writes recirculation charts based on both the first percentile and daily minimum.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

INPUT_DIRECTORY = Path("data/well-detail")
INPUT_PATTERN = "well-detail_[0-9][0-9][0-9][0-9][0-9][0-9].csv"
OUTPUT_FILE = Path("graphs/domestic_hot_water_infographic.png")
RECIRCULATION_OUTPUT_FILE = Path("graphs/domestic_hot_water_recirculation.png")
RECIRCULATION_MINIMUM_OUTPUT_FILE = Path(
    "graphs/domestic_hot_water_recirculation_minimum.png"
)
DATE_COLUMN = "Date"
FLOW_COLUMN = "FLOWDHWGPM"
DATE_FORMAT = "%H:%M:%S %m-%d-%Y"
RECIRCULATION_QUANTILE = 0.01
FULL_DAY_MINIMUM_SPAN = pd.Timedelta(hours=23)
PARTIAL_DAY_GAP = pd.Timedelta(minutes=5)
MULTI_DAY_GAP = pd.Timedelta(days=1)


def warn(message: str) -> None:
    """Print a warning to stderr so it is visible in scheduled-job logs."""
    print(f"WARNING: {message}", file=sys.stderr)


def read_telemetry(files: list[Path]) -> pd.DataFrame:
    """Read usable input files, skipping files that lack required columns."""
    frames: list[pd.DataFrame] = []

    for file_path in files:
        try:
            frame = pd.read_csv(
                file_path,
                sep="\t",
                usecols=lambda column: column in {DATE_COLUMN, FLOW_COLUMN},
            )
        except (OSError, pd.errors.ParserError, ValueError) as error:
            warn(f"Skipping '{file_path}': could not read tab-delimited data ({error})")
            continue

        missing_columns = {DATE_COLUMN, FLOW_COLUMN} - set(frame.columns)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            warn(f"Skipping '{file_path}': missing required column(s): {missing}")
            continue

        frame[DATE_COLUMN] = pd.to_datetime(
            frame[DATE_COLUMN], format=DATE_FORMAT, errors="coerce"
        )
        frame[FLOW_COLUMN] = pd.to_numeric(frame[FLOW_COLUMN], errors="coerce")
        invalid_rows = frame[DATE_COLUMN].isna() | frame[FLOW_COLUMN].isna()
        if invalid_rows.any():
            warn(
                f"'{file_path}' has {invalid_rows.sum():,} row(s) with an invalid "
                "timestamp or FLOWDHWGPM value; those rows are excluded"
            )
            frame = frame.loc[~invalid_rows]

        if not frame.empty:
            frames.append(frame)

    if not frames:
        raise ValueError(
            "No usable telemetry was found. Files must contain tab-delimited "
            f"'{DATE_COLUMN}' and '{FLOW_COLUMN}' columns."
        )

    telemetry = pd.concat(frames, ignore_index=True).sort_values(DATE_COLUMN)
    duplicate_count = telemetry.duplicated(DATE_COLUMN, keep="last").sum()
    if duplicate_count:
        warn(f"Discarding {duplicate_count:,} duplicate timestamp(s), keeping the last value")
        telemetry = telemetry.drop_duplicates(DATE_COLUMN, keep="last")

    return telemetry.set_index(DATE_COLUMN).sort_index()


def calculate_daily_gallons(flow: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return daily estimated DHW gallons and daily recirculation baselines.

    The flow remaining after a per-day first-percentile baseline is integrated with
    the trapezoidal rule over real timestamp intervals. Normal same-day intervals
    are vectorized. The uncommon intervals spanning midnight are split precisely
    at each midnight, preserving the daily totals even across a long telemetry gap.
    """
    day_index = flow.index.normalize()
    baselines = flow.groupby(day_index).quantile(RECIRCULATION_QUANTILE)
    estimated_dhw_flow = (flow - pd.Series(day_index, index=flow.index).map(baselines)).clip(
        lower=0
    )

    timestamps = estimated_dhw_flow.index
    if len(estimated_dhw_flow) < 2:
        return pd.Series(dtype=float), baselines

    start_times = timestamps[:-1]
    end_times = timestamps[1:]
    start_rates = estimated_dhw_flow.iloc[:-1].to_numpy(dtype=float)
    end_rates = estimated_dhw_flow.iloc[1:].to_numpy(dtype=float)
    interval_minutes = (end_times - start_times).total_seconds() / 60.0
    start_days = start_times.normalize()
    same_day = start_days == end_times.normalize()

    totals: defaultdict[pd.Timestamp, float] = defaultdict(float)
    same_day_gallons = (start_rates[same_day] + end_rates[same_day]) * interval_minutes[same_day] / 2
    for day, gallons in zip(start_days[same_day], same_day_gallons):
        totals[day] += gallons

    # Split intervals that cross a midnight. This is normally only a one-second
    # interval, but it also makes estimates internally consistent during gaps.
    for position in np.flatnonzero(~same_day):
        interval_start = start_times[position]
        interval_end = end_times[position]
        total_seconds = (interval_end - interval_start).total_seconds()
        rate_change = end_rates[position] - start_rates[position]
        segment_start = interval_start

        while segment_start < interval_end:
            next_midnight = segment_start.normalize() + pd.Timedelta(days=1)
            segment_end = min(next_midnight, interval_end)
            offset_start = (segment_start - interval_start).total_seconds()
            offset_end = (segment_end - interval_start).total_seconds()
            rate_at_start = start_rates[position] + rate_change * offset_start / total_seconds
            rate_at_end = start_rates[position] + rate_change * offset_end / total_seconds
            totals[segment_start.normalize()] += (
                (rate_at_start + rate_at_end)
                * (segment_end - segment_start).total_seconds()
                / 120.0
            )
            segment_start = segment_end

    return pd.Series(totals, dtype=float).sort_index(), baselines


def identify_partial_days(flow: pd.Series, all_days: pd.DatetimeIndex) -> pd.Series:
    """Flag days lacking near-full coverage or containing a substantial data gap."""
    grouped = flow.groupby(flow.index.normalize())
    first_seen = grouped.apply(lambda values: values.index.min())
    last_seen = grouped.apply(lambda values: values.index.max())
    observed_span = last_seen - first_seen
    partial = (observed_span < FULL_DAY_MINIMUM_SPAN).reindex(all_days, fill_value=True)

    # Attribute a gap to each calendar day it touches. This also flags days that
    # have interpolated volume but no actual observations at all.
    gaps = flow.index.to_series().diff()
    for current_time, gap in gaps.dropna().items():
        if gap <= PARTIAL_DAY_GAP:
            continue
        gap_start = current_time - gap
        for day in pd.date_range(gap_start.normalize(), current_time.normalize(), freq="D"):
            partial.loc[day] = True

    return partial.astype(bool)


def print_gap_warnings(timestamps: pd.DatetimeIndex) -> None:
    """Warn prominently about gaps longer than a day, which are interpolated."""
    gaps = timestamps.to_series().diff()
    long_gaps = gaps[gaps > MULTI_DAY_GAP]
    for gap_end, duration in long_gaps.items():
        gap_start = gap_end - duration
        warn(
            "Multi-day telemetry gap of "
            f"{duration} from {gap_start:%Y-%m-%d %H:%M:%S} to "
            f"{gap_end:%Y-%m-%d %H:%M:%S}; gallons are interpolated across it"
        )


def format_gallons(value: float) -> str:
    return f"{value:,.0f} gal"


def create_infographic(daily: pd.DataFrame) -> None:
    """Draw and save the daily-volume chart plus period summary."""
    full_days = daily.loc[~daily["partial"] & daily["gallons"].notna(), "gallons"]

    figure = plt.figure(figsize=(16, 10), constrained_layout=True)
    grid = figure.add_gridspec(2, 1, height_ratios=[3.5, 1])
    axis = figure.add_subplot(grid[0])
    text_axis = figure.add_subplot(grid[1])

    full_mask = ~daily["partial"] & daily["gallons"].notna()
    axis.bar(
        daily.index[full_mask],
        daily.loc[full_mask, "gallons"],
        width=0.82,
        color="#1976A2",
        label="Full telemetry coverage",
    )
    axis.set_title("Estimated Domestic Hot-Water Use", loc="left", fontsize=22, fontweight="bold")
    axis.set_ylabel("Estimated DHW use (gallons per day)")
    axis.set_xlabel("Date")
    axis.grid(axis="y", alpha=0.25)
    axis.set_axisbelow(True)
    axis.legend(frameon=False, loc="upper left")
    axis.yaxis.set_major_locator(mticker.MultipleLocator(1000))
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=12))
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))

    if not full_days.empty:
        # "Median day" means the full-coverage day whose use is closest to the
        # median daily use. In a tie, pandas chooses the earliest such day.
        median_day = (full_days - full_days.median()).abs().idxmin()
        median_gallons = full_days.loc[median_day]
        axis.scatter(
            [median_day],
            [median_gallons],
            s=85,
            color="#FFFFFF",
            edgecolor="#0D4765",
            linewidth=2,
            zorder=4,
        )
        axis.annotate(
            f"Median-use day\n{median_day:%b %-d, %Y}\n{format_gallons(median_gallons)}",
            xy=(median_day, median_gallons),
            xytext=(14, 20),
            textcoords="offset points",
            ha="left",
            va="bottom",
            fontsize=10,
            arrowprops={"arrowstyle": "-", "color": "#0D4765", "lw": 1.2},
            bbox={"boxstyle": "round,pad=0.45", "fc": "white", "ec": "#0D4765", "alpha": 0.96},
            zorder=5,
        )
    else:
        axis.text(
            0.5,
            0.5,
            "No days have full telemetry coverage",
            ha="center",
            va="center",
            transform=axis.transAxes,
            color="#555555",
            fontsize=14,
        )

    text_axis.axis("off")
    period_start = daily.index.min().strftime("%b %-d, %Y")
    period_end = daily.index.max().strftime("%b %-d, %Y")
    baseline_days = daily.loc[full_mask, "recirculation_gpm"].dropna()
    baseline_text = (
        f"Median daily recirculation estimate: {baseline_days.median():.2f} GPM"
        if not baseline_days.empty
        else "Recirculation estimate unavailable"
    )
    if full_days.empty:
        summary_text = "Daily average: not available\nDaily minimum: not available\nDaily maximum: not available"
        statistics_note = "No full-coverage days were available for daily statistics."
        headline = "No full-coverage daily average"
        annual_estimate_text = "Estimated annual total: not available"
    else:
        daily_average = full_days.mean()
        estimated_annual_total = daily_average * 365
        summary_text = (
            f"Daily minimum: {format_gallons(full_days.min())}\n"
            f"Daily maximum: {format_gallons(full_days.max())}"
        )
        statistics_note = f"Daily statistics use {len(full_days)} full-coverage day(s)."
        headline = f"Average daily total: {format_gallons(daily_average)}"
        annual_estimate_text = (
            f"Estimated annual total: {format_gallons(estimated_annual_total)}"
        )

    text_axis.text(
        0.01,
        0.76,
        headline,
        fontsize=25,
        fontweight="bold",
        color="#1976A2",
        transform=text_axis.transAxes,
    )
    text_axis.text(
        0.01,
        0.47,
        annual_estimate_text,
        fontsize=17,
        fontweight="bold",
        color="#1976A2",
        transform=text_axis.transAxes,
    )
    text_axis.text(
        0.01,
        0.20,
        f"Estimated DHW use after recirculation removal, {period_start} – {period_end}",
        fontsize=12,
        transform=text_axis.transAxes,
    )
    text_axis.text(
        0.42,
        0.76,
        summary_text,
        fontsize=15,
        linespacing=1.6,
        transform=text_axis.transAxes,
    )
    text_axis.text(
        0.42,
        0.16,
        f"{statistics_note}\n"
        f"{baseline_text}. Recirculation is removed using each day's FLOWDHWGPM 1st percentile.",
        fontsize=10.5,
        color="#444444",
        transform=text_axis.transAxes,
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(OUTPUT_FILE, dpi=180, bbox_inches="tight")
    plt.close(figure)


def create_recirculation_chart(
    daily: pd.DataFrame,
    column: str,
    method_label: str,
    output_file: Path,
) -> None:
    """Draw a daily recirculation-flow estimate using the requested method.

    Partial or gapped days are represented by NaN values. Matplotlib therefore
    leaves a visible break in the line instead of connecting estimates across
    telemetry that does not cover a full calendar day.
    """
    full_mask = ~daily["partial"] & daily[column].notna()
    recirculation = daily[column].where(full_mask)
    full_day_recirculation = recirculation.dropna()

    figure, axis = plt.subplots(figsize=(16, 7), constrained_layout=True)
    axis.plot(
        recirculation.index,
        recirculation,
        color="#4B7F52",
        linewidth=1.8,
        marker="o",
        markersize=3.5,
        label=f"Daily {method_label} FLOWDHWGPM",
    )
    axis.set_title(
        f"Estimated Domestic Hot-Water Recirculation Flow ({method_label})",
        loc="left",
        fontsize=20,
        fontweight="bold",
    )
    axis.set_ylabel("Estimated recirculation flow (GPM)")
    axis.set_xlabel("Date")
    axis.grid(axis="y", alpha=0.25)
    axis.set_axisbelow(True)
    axis.legend(frameon=False, loc="upper left")
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=12))
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))

    if full_day_recirculation.empty:
        axis.text(
            0.5,
            0.5,
            "No days have full telemetry coverage",
            ha="center",
            va="center",
            transform=axis.transAxes,
            color="#555555",
            fontsize=14,
        )
    else:
        median_recirc = full_day_recirculation.median()
        axis.text(
            0.01,
            0.02,
            f"Median full-day estimate: {median_recirc:.2f} GPM\n"
            f"Each point is that calendar day's FLOWDHWGPM {method_label}.",
            transform=axis.transAxes,
            fontsize=10.5,
            color="#333333",
            va="bottom",
            bbox={"boxstyle": "round,pad=0.4", "fc": "white", "ec": "#BBBBBB", "alpha": 0.94},
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_file, dpi=180, bbox_inches="tight")
    plt.close(figure)


def main() -> None:
    files = sorted(INPUT_DIRECTORY.glob(INPUT_PATTERN))
    if not files:
        raise FileNotFoundError(
            f"No files found matching '{INPUT_DIRECTORY / INPUT_PATTERN}'."
        )

    telemetry = read_telemetry(files)
    flow = telemetry[FLOW_COLUMN]
    print_gap_warnings(flow.index)

    daily_gallons, baselines = calculate_daily_gallons(flow)
    all_days = pd.date_range(flow.index.min().normalize(), flow.index.max().normalize(), freq="D")
    daily = pd.DataFrame(index=all_days)
    daily.index.name = "date"
    daily["gallons"] = daily_gallons.reindex(all_days, fill_value=0.0)
    daily["recirculation_gpm"] = baselines.reindex(all_days)
    daily["recirculation_minimum_gpm"] = (
        flow.groupby(flow.index.normalize()).min().reindex(all_days)
    )
    daily["partial"] = identify_partial_days(flow, all_days)

    full_coverage_days = ~daily["partial"] & daily["gallons"].notna()
    zero_usage_days = daily.index[full_coverage_days & daily["gallons"].eq(0)]
    for day in zero_usage_days:
        baseline = daily.at[day, "recirculation_gpm"]
        warn(
            f"Full-coverage day {day:%Y-%m-%d} computed zero estimated DHW "
            f"consumption; its FLOWDHWGPM 1st-percentile recirculation estimate "
            f"was {baseline:.3f} GPM"
        )

    full_day_gallons = daily.loc[full_coverage_days, "gallons"]
    create_infographic(daily)
    create_recirculation_chart(
        daily,
        "recirculation_gpm",
        "1st-percentile",
        RECIRCULATION_OUTPUT_FILE,
    )
    create_recirculation_chart(
        daily,
        "recirculation_minimum_gpm",
        "minimum",
        RECIRCULATION_MINIMUM_OUTPUT_FILE,
    )
    partial_days = daily["partial"].sum()
    print(f"Read {len(flow):,} observations from {len(files)} file(s).")
    if not full_day_gallons.empty:
        daily_average = full_day_gallons.mean()
        print(f"Average daily estimated domestic hot-water use: {format_gallons(daily_average)}")
        print(f"Estimated annual domestic hot-water use: {format_gallons(daily_average * 365)}")
    print(f"Marked {partial_days:,} of {len(daily):,} day(s) as partial or gapped.")
    print(f"Infographic saved to {OUTPUT_FILE}")
    print(f"Recirculation chart saved to {RECIRCULATION_OUTPUT_FILE}")
    print(f"Minimum recirculation chart saved to {RECIRCULATION_MINIMUM_OUTPUT_FILE}")


if __name__ == "__main__":
    main()
