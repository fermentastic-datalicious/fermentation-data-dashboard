"""Auxiliary online sensors: off-gas analyzer and capacitance probe.

Continuous like the bioreactor, but logged by separate boxes with their own
clocks, their own cadences, and their own start lags. Neither file records
which run it belongs to -- the filename is the only link -- and neither clock
is synced to the control system, so these streams can only be joined back to
the run timeline with a tolerance. That join lives in `align`.
"""

from pathlib import Path

import pandas as pd

from ..run_registry import RunRegistry, run_id_from_filename
from ..schema import CAPACITANCE_COLUMNS, OFFGAS_COLUMNS, build_observations

OFFGAS_SOURCE = "offgas"
CAPACITANCE_SOURCE = "capacitance"


def _parse_sensor_file(
    path: Path,
    registry: RunRegistry,
    filename_suffix: str,
    timestamp_column: str,
    columns: dict,
    source: str,
    sep: str = ",",
) -> pd.DataFrame:
    run_id = run_id_from_filename(path, filename_suffix)
    registry.get(run_id)

    df = pd.read_csv(path, sep=sep)
    timestamps = pd.to_datetime(df[timestamp_column])

    frames = []
    for column, mapping in columns.items():
        if column not in df.columns:
            continue
        frames.append(
            build_observations(
                run_id=run_id,
                timestamps=timestamps,
                source=source,
                variable=mapping.variable,
                values=df[column] * mapping.scale,
                unit=mapping.unit,
                source_file=path.name,
            )
        )
    return pd.concat(frames, ignore_index=True)


def parse_offgas_file(path: Path, registry: RunRegistry) -> pd.DataFrame:
    """BlueSens-style CO2/O2 analyzer export -- semicolon-delimited."""
    return _parse_sensor_file(
        path,
        registry,
        filename_suffix="_offgas",
        timestamp_column="Time_stamp",
        columns=OFFGAS_COLUMNS,
        source=OFFGAS_SOURCE,
        sep=";",
    )


def parse_capacitance_file(path: Path, registry: RunRegistry) -> pd.DataFrame:
    """Dielectric probe export; the constant excitation frequency is dropped."""
    return _parse_sensor_file(
        path,
        registry,
        filename_suffix="_capacitance",
        timestamp_column="Timestamp",
        columns=CAPACITANCE_COLUMNS,
        source=CAPACITANCE_SOURCE,
    )
