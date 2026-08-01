"""Time-tolerant joins across sources that never shared a clock.

Four data sources, four different notions of when "now" is:

- the bioreactor control system logs on a fixed few-second-to-minute cadence
- the off-gas analyzer and capacitance probe each free-run on their own clock,
  offset by a few minutes and started at a different moment
- HPLC and offline biomass are sample-triggered, landing wherever someone
  happened to pull a sample

No exact timestamp match exists between any two of them, so joining means
nearest-match within a tolerance. Doing it here, at read time, rather than
baking a resampled grid into storage keeps the stored data lossless and lets
the dashboard change its mind about the tolerance without re-ingesting.
"""

import pandas as pd

# The auxiliary sensors run within a few minutes of the control system clock,
# so 5 minutes catches the true match without reaching across real structure.
# Sample-triggered sources are sparse and irregular; they get more room.
CONTINUOUS_TOLERANCE = pd.Timedelta(minutes=5)
SAMPLE_TOLERANCE = pd.Timedelta(minutes=10)

CONTINUOUS_SOURCES = ("bioreactor", "offgas", "capacitance")
SAMPLE_SOURCES = ("analytical", "offline")


def _run_start(run_obs: pd.DataFrame) -> pd.Timestamp:
    """Recover the run's t=0 from any row, since elapsed_h is measured from it."""
    row = run_obs.iloc[0]
    return pd.Timestamp(row["timestamp"]) - pd.Timedelta(hours=float(row["elapsed_h"]))


def pivot_source(obs: pd.DataFrame, run_id: str, source: str) -> pd.DataFrame:
    """One source, one run, long -> wide on its own native timestamps."""
    subset = obs[(obs["run_id"] == run_id) & (obs["source"] == source)]
    if subset.empty:
        return pd.DataFrame(columns=["timestamp"])
    wide = (
        subset.pivot_table(index="timestamp", columns="variable", values="value", aggfunc="mean")
        .reset_index()
        .sort_values("timestamp")
    )
    wide.columns.name = None
    return wide


def _attach_samples(
    base: pd.DataFrame, samples: pd.DataFrame, tolerance: pd.Timedelta
) -> pd.DataFrame:
    """Place each sparse sample on the single reference row nearest to it.

    Samples drawn closer together than the reference cadence would compete for
    the same row; the first one wins and the collision is not silently merged
    into an average.
    """
    snapped = pd.merge_asof(
        samples,
        base[["timestamp"]].rename(columns={"timestamp": "ref_timestamp"}),
        left_on="timestamp",
        right_on="ref_timestamp",
        direction="nearest",
        tolerance=tolerance,
    )
    snapped = snapped.dropna(subset=["ref_timestamp"]).drop_duplicates(
        subset=["ref_timestamp"], keep="first"
    )
    snapped = snapped.drop(columns=["timestamp"]).rename(columns={"ref_timestamp": "timestamp"})
    return base.merge(snapped, on="timestamp", how="left")


def align_to_reference(
    obs: pd.DataFrame,
    run_id: str,
    reference: str = "bioreactor",
    continuous_tolerance: pd.Timedelta = CONTINUOUS_TOLERANCE,
    sample_tolerance: pd.Timedelta = SAMPLE_TOLERANCE,
) -> pd.DataFrame:
    """Join every source for one run onto the bioreactor timeline.

    The control system is the reference because it is the densest and the one
    whose clock the run is nominally described by. Anything outside tolerance
    stays NaN rather than being stretched to the closest available point, so a
    gap reads as a gap instead of a flat line.

    The two source families are matched in opposite directions, which matters:

    - Continuous auxiliary sensors are dense like the reference, so each
      reference row pulls its nearest sensor reading.
    - Sample-triggered sources are sparse, so each *sample* is snapped onto its
      nearest reference row instead. Pulling them the other way would repeat a
      single HPLC point across every reference row inside the tolerance window
      -- roughly twenty copies at a one-minute cadence, which then double-counts
      in any average and stacks markers on any scatter.

    Returned frame is wide: one row per bioreactor timestamp, one column per
    variable, plus `elapsed_h` for plotting runs of different lengths together.
    """
    base = pivot_source(obs, run_id, reference)
    if base.empty:
        raise ValueError(f"run {run_id!r} has no {reference} data to align against")

    for source in CONTINUOUS_SOURCES:
        if source == reference:
            continue
        other = pivot_source(obs, run_id, source)
        if other.empty:
            continue
        base = pd.merge_asof(
            base, other, on="timestamp", direction="nearest", tolerance=continuous_tolerance
        )

    for source in SAMPLE_SOURCES:
        other = pivot_source(obs, run_id, source)
        if other.empty:
            continue
        base = _attach_samples(base, other, sample_tolerance)

    start = _run_start(obs[obs["run_id"] == run_id])
    base.insert(0, "run_id", run_id)
    base.insert(2, "elapsed_h", (base["timestamp"] - start).dt.total_seconds() / 3600.0)
    return base.reset_index(drop=True)


def align_runs(obs: pd.DataFrame, run_ids: list[str] | None = None, **kwargs) -> pd.DataFrame:
    """Aligned frames for several runs, stacked -- the multi-run comparison view."""
    run_ids = run_ids or sorted(obs["run_id"].unique())
    return pd.concat(
        [align_to_reference(obs, run_id, **kwargs) for run_id in run_ids], ignore_index=True
    )


def resample_continuous(
    obs: pd.DataFrame, run_id: str, variables: list[str] | None = None, freq: str = "5min"
) -> pd.DataFrame:
    """Continuous streams averaged onto a regular grid, indexed by elapsed hours.

    For overlaying runs that were logged at different cadences -- DASGIP every
    minute, Ambr every two -- where the raw point density otherwise makes the
    comparison unreadable.
    """
    subset = obs[(obs["run_id"] == run_id) & (obs["source"].isin(CONTINUOUS_SOURCES))]
    if variables:
        subset = subset[subset["variable"].isin(variables)]
    if subset.empty:
        return pd.DataFrame()

    wide = subset.pivot_table(
        index="timestamp", columns="variable", values="value", aggfunc="mean"
    )
    resampled = wide.resample(freq).mean()

    start = _run_start(subset)
    resampled.insert(0, "elapsed_h", (resampled.index - start).total_seconds() / 3600.0)
    resampled.columns.name = None
    return resampled.reset_index()
