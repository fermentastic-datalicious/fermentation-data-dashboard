"""HPLC peak tables -- discrete, sample-triggered analytical data.

One row per analyte per injection. Two things to know about the timestamps:
the file records when the vial was *injected*, not when the sample was drawn
from the vessel, and the two differ by the sample prep and instrument queue
time. Back-dating is a cross-source operation and happens in `normalize`;
this parser reports injection time faithfully and hands over the sample
metadata needed to do the correction.
"""

from pathlib import Path

import pandas as pd

from ..run_registry import RunRegistry
from ..schema import CANONICAL_UNITS, HPLC_ANALYTES, HPLC_PEAK_COLUMNS, build_observations

SOURCE = "analytical"


def parse_hplc_file(path: Path, registry: RunRegistry) -> pd.DataFrame:
    """Peak table -> observations, timestamped at injection time.

    Concentration is the headline number, but retention time and peak area are
    carried through as well: they are what tells you whether a suspicious
    concentration is real biology or a chromatography problem.
    """
    df = pd.read_csv(path)
    injection_time = pd.to_datetime(df["Injection_DateTime"])

    frames = []
    for run_id, run_rows in df.groupby("Run_ID"):
        registry.get(str(run_id))
        for peak_name, analyte_rows in run_rows.groupby("Peak_Name"):
            variable_base = HPLC_ANALYTES.get(str(peak_name))
            if variable_base is None:
                continue  # an analyte the schema does not track
            for column, variable_suffix in HPLC_PEAK_COLUMNS.items():
                if column not in analyte_rows.columns:
                    continue
                variable = f"{variable_base}{variable_suffix}"
                frames.append(
                    build_observations(
                        run_id=str(run_id),
                        timestamps=injection_time.loc[analyte_rows.index],
                        source=SOURCE,
                        variable=variable,
                        values=analyte_rows[column],
                        unit=CANONICAL_UNITS[variable],
                        source_file=path.name,
                    )
                )
    return pd.concat(frames, ignore_index=True)


def parse_hplc_samples(path: Path) -> pd.DataFrame:
    """Injection-level metadata, one row per sample.

    `Sample_Name` here is the same identity as `Sample_ID` in the offline
    biomass sheet -- that shared key is what makes back-dating possible.
    """
    df = pd.read_csv(path)
    samples = (
        df.groupby("Sample_Name")
        .agg(
            run_id=("Run_ID", "first"),
            injection_time=("Injection_DateTime", "first"),
            method=("Method", "first"),
        )
        .reset_index()
        .rename(columns={"Sample_Name": "sample_id"})
    )
    samples["injection_time"] = pd.to_datetime(samples["injection_time"])
    return samples
