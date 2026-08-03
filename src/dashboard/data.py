"""Reads for the dashboard, cached, plus the summaries the view displays.

Queries are cached per run because a drill-down re-runs the whole script on
every widget interaction -- changing the x-axis toggle should not re-read
50,000 rows from SQLite.

Everything here returns the long common schema untouched. Pivoting happens per
panel at render time, so no shape decision is baked in this far down.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from ..ingestion.align import SAMPLE_SOURCES as SPARSE_SOURCES
from ..ingestion.align import resample_continuous
from ..storage import DB_PATH, connect, read_observations, read_runs, read_samples

SOURCE_LABELS = {
    "bioreactor": "Bioreactor control system",
    "analytical": "HPLC (analytical)",
    "offline": "Offline biomass (manual)",
    "offgas": "Off-gas analyser",
    "capacitance": "Capacitance probe",
}


@st.cache_data(show_spinner=False)
def load_runs(db_path: Path = DB_PATH) -> pd.DataFrame:
    with connect(db_path) as conn:
        return read_runs(conn)


@st.cache_data(show_spinner=False)
def load_observations(run_id: str, db_path: Path = DB_PATH) -> pd.DataFrame:
    with connect(db_path) as conn:
        return read_observations(conn, run_ids=[run_id])


@st.cache_data(show_spinner=False)
def load_samples(run_id: str, db_path: Path = DB_PATH) -> pd.DataFrame:
    with connect(db_path) as conn:
        return read_samples(conn, run_ids=[run_id])


@st.cache_data(show_spinner=False)
def load_cohort_observations(run_ids: tuple[str, ...], db_path: Path = DB_PATH) -> pd.DataFrame:
    """Every run in a cohort, in one long frame.

    Takes a tuple rather than a list so the cache key is hashable.
    """
    with connect(db_path) as conn:
        return read_observations(conn, run_ids=list(run_ids))


def available_variables(obs: pd.DataFrame) -> set[str]:
    return set(obs["variable"].unique())


def comparable_traces(
    obs: pd.DataFrame, variable: str, run_ids: tuple[str, ...], freq: str = "5min"
) -> dict[str, pd.DataFrame]:
    """One run-keyed frame per run, ready to overlay for a single variable.

    Continuous variables are put on a common grid: the runs were logged at
    different cadences -- 60 s on DASGIP, 120 s on Ambr, 30 s on the off-gas
    analyser -- and a comparison wants them commensurate rather than at
    whatever rate each box happened to use. It also cuts an overlay of seven
    off-gas traces from ~80,000 points to ~3,000.

    Sparse, sample-triggered variables are left exactly as recorded. Resampling
    seven HPLC injections onto a five-minute grid would invent a dense series
    out of nothing.
    """
    subset = obs[obs["variable"] == variable]
    if subset.empty:
        return {}

    sparse = subset["source"].iloc[0] in SPARSE_SOURCES
    traces = {}
    for run_id in run_ids:
        run_obs = subset[subset["run_id"] == run_id].sort_values("elapsed_h")
        if run_obs.empty:
            continue
        traces[run_id] = (
            run_obs[["elapsed_h", "value"]]
            if sparse
            else _resample_one(run_obs, variable, freq)
        )
    return traces


def _resample_one(run_obs: pd.DataFrame, variable: str, freq: str) -> pd.DataFrame:
    resampled = resample_continuous(run_obs, run_obs["run_id"].iloc[0], [variable], freq)
    if resampled.empty:
        return run_obs[["elapsed_h", "value"]]
    return resampled[["elapsed_h", variable]].rename(columns={variable: "value"}).dropna()


def cohort_outcomes(runs: pd.DataFrame, run_ids: tuple[str, ...]) -> pd.DataFrame:
    """Endpoint summary for a cohort — the table that gets R3 wrong.

    Included deliberately, because it is what most run reviews stop at, and
    because on this cohort it ranks the contaminated run first. The caption in
    the view says so; the traces below it do the correcting.
    """
    cohort = runs[runs["run_id"].isin(run_ids)].copy()
    return cohort[
        [
            "run_id",
            "vessel_id",
            "duration_h",
            "final_biomass_total_gL",
            "final_product_gL",
            "anomaly",
        ]
    ].rename(
        columns={
            "run_id": "Run",
            "vessel_id": "Vessel",
            "duration_h": "Duration (h)",
            "final_biomass_total_gL": "Final biomass (g/L)",
            "final_product_gL": "Final product (g/L)",
            "anomaly": "Recorded note",
        }
    )


def divergence_from_cohort(
    obs: pd.DataFrame, variable: str, run_ids: tuple[str, ...], highlighted: str
) -> dict[str, float]:
    """How far the highlighted run sits from the rest, over the second half.

    Plain description, not detection: the median of the other runs is the
    reference, and the number reported is simply the difference. No threshold,
    no verdict, no colour coding. A reader decides whether 32 percentage points
    of dissolved oxygen matters -- and for a different variable it might not.
    """
    others = [r for r in run_ids if r != highlighted]
    if not others:
        return {}

    subset = obs[obs["variable"] == variable]
    late = subset[subset["elapsed_h"] >= subset["elapsed_h"].max() / 2]
    if late.empty:
        return {}

    focus = late[late["run_id"] == highlighted]["value"]
    rest = late[late["run_id"].isin(others)]["value"]
    if focus.empty or rest.empty:
        return {}

    return {
        "highlighted": float(focus.mean()),
        "cohort_median": float(rest.median()),
        "difference": float(focus.mean() - rest.median()),
    }


def source_provenance(obs: pd.DataFrame) -> pd.DataFrame:
    """One row per source: where it came from and how often it logged.

    This is the view's evidence that the ingestion layer did something. Five
    instruments, four unsynced clocks, cadences from 30 s to hours -- all of it
    invisible once the data is unified, which is exactly why it is worth
    showing explicitly rather than trusting the reader to imagine it.
    """
    rows = []
    for source, group in obs.groupby("source"):
        # Cadence is measured on one variable, not the whole source: every
        # variable from a source shares its timestamps, so pooling them would
        # report a cadence of zero.
        one_variable = group[group["variable"] == group["variable"].iloc[0]]
        gaps = one_variable["timestamp"].sort_values().diff().dt.total_seconds()
        rows.append(
            {
                "Source": SOURCE_LABELS.get(source, source),
                "Files": ", ".join(sorted(group["source_file"].unique())),
                "Variables": group["variable"].nunique(),
                "Points": len(group),
                "Cadence": _describe_cadence(gaps.median()),
                "First reading": one_variable["timestamp"].min(),
                "Last reading": one_variable["timestamp"].max(),
            }
        )
    return pd.DataFrame(rows).sort_values("Points", ascending=False, ignore_index=True)


def _describe_cadence(seconds: float) -> str:
    if pd.isna(seconds):
        return "single reading"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def run_conditions(run: pd.Series) -> str:
    """What the run was, as one line.

    Deliberately not a row of metric tiles: seven of them across the width
    left every value truncated ("fed-ba...", "37.58 ..."), and none of this is
    a headline number anyway. It is the context you read once before looking
    at the charts.
    """
    return " · ".join(
        [
            run["strain"],
            run["mode"],
            f"{run['vessel_id']} ({run['system']})",
            f"{run['volume_L']:g} L",
            f"{run['duration_h']:g} h",
        ]
    )


def run_outcomes(obs: pd.DataFrame) -> str:
    """Final biomass and titer, where the run recorded them."""
    parts = []
    for variable, label in (("dcw", "Final biomass"), ("product", "Final product")):
        series = obs[obs["variable"] == variable].sort_values("elapsed_h")
        if not series.empty:
            unit = series["unit"].iloc[-1]
            parts.append(f"{label} **{series['value'].iloc[-1]:.2f}** {unit}")
    return " · ".join(parts)
