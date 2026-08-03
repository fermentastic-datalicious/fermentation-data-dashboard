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


def available_variables(obs: pd.DataFrame) -> set[str]:
    return set(obs["variable"].unique())


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
