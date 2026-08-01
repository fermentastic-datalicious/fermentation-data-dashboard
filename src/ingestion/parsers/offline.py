"""Hand-entered offline biomass measurements (OD600, dry cell weight).

A spreadsheet rather than an instrument export, with the corresponding
messiness: date and time in separate columns, time to the minute only, and
the occasional blank cell where a measurement was not taken.

This file also happens to be the only source that records when a sample was
actually drawn from the vessel, which makes it the reference for correcting
HPLC injection times in `normalize`.
"""

from pathlib import Path

import pandas as pd

from ..run_registry import RunRegistry
from ..schema import OFFLINE_COLUMNS, build_observations

SOURCE = "offline"


def _draw_times(df: pd.DataFrame) -> pd.Series:
    """Recombine the split Date and Time columns into real timestamps."""
    return pd.to_datetime(df["Date"].astype(str) + " " + df["Time"].astype(str))


def parse_biomass_file(path: Path, registry: RunRegistry) -> pd.DataFrame:
    """OD600 and DCW at sample draw times.

    Blank DCW cells become dropped rows, not NaN values: in a long schema
    "not measured" is expressed by the row's absence, and a NaN sitting in a
    value column will eventually be plotted or averaged by something.
    """
    df = pd.read_csv(path)
    timestamps = _draw_times(df)

    frames = []
    for run_id, run_rows in df.groupby("Run_ID"):
        registry.get(str(run_id))
        for column, mapping in OFFLINE_COLUMNS.items():
            if column not in run_rows.columns:
                continue
            frames.append(
                build_observations(
                    run_id=str(run_id),
                    timestamps=timestamps.loc[run_rows.index],
                    source=SOURCE,
                    variable=mapping.variable,
                    values=pd.to_numeric(run_rows[column], errors="coerce") * mapping.scale,
                    unit=mapping.unit,
                    source_file=path.name,
                )
            )
    return pd.concat(frames, ignore_index=True)


def parse_biomass_samples(path: Path) -> pd.DataFrame:
    """Sample-level metadata, including the draw time other sources lack.

    Operator and free-text notes ("foaming observed") do not belong in a
    numeric value column, but they are exactly the context someone wants when
    a point looks wrong -- so they live here.
    """
    df = pd.read_csv(path)
    samples = pd.DataFrame(
        {
            "sample_id": df["Sample_ID"],
            "run_id": df["Run_ID"],
            "draw_time": _draw_times(df),
            "operator": df["Operator"],
            "dilution_factor": pd.to_numeric(df["Dilution_Factor"], errors="coerce"),
            "notes": df["Notes"].fillna(""),
        }
    )
    return samples
