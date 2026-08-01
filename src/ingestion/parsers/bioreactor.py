"""Bioreactor control system exports, in both file patterns.

DASGIP-style: one file per vessel, a `#`-commented metadata header, units
embedded in the column names, gas flow in L/h.

Ambr-style: four vessels sharing one wide file, one row per timestamp, no
run or batch identifier anywhere in the file, gas flow in L/min.

Same measurements, same underlying process, two entirely different shapes --
which is the case for building a common schema in the first place.
"""

from pathlib import Path

import pandas as pd

from ..run_registry import RunRegistry
from ..schema import AMBR_MEASUREMENTS, DASGIP_COLUMNS, build_observations

SOURCE = "bioreactor"


def read_dasgip_header(path: Path) -> dict[str, str]:
    """Parse the leading `# Key: value` block into a dict.

    Read line by line rather than with pandas' `comment=` so the metadata is
    captured instead of discarded -- the batch id lives in there, and it is
    the only in-file statement of which run this is.
    """
    meta: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            if not line.startswith("#"):
                break
            body = line.lstrip("#").strip()
            if ":" in body:
                key, _, value = body.partition(":")
                meta[key.strip()] = value.strip()
    return meta


def _count_header_lines(path: Path) -> int:
    with open(path) as f:
        return sum(1 for line in f if line.startswith("#"))


def parse_dasgip_file(path: Path, registry: RunRegistry) -> pd.DataFrame:
    """One vessel per file; run_id from the `# Batch ID:` header."""
    meta = read_dasgip_header(path)
    run_id = meta.get("Batch ID", path.stem)
    registry.get(run_id)  # reject anything not in the manifest before parsing further

    df = pd.read_csv(path, skiprows=_count_header_lines(path))
    timestamps = pd.to_datetime(df["Timestamp"])

    frames = []
    for column, mapping in DASGIP_COLUMNS.items():
        if column not in df.columns:
            continue
        frames.append(
            build_observations(
                run_id=run_id,
                timestamps=timestamps,
                source=SOURCE,
                variable=mapping.variable,
                values=df[column] * mapping.scale,
                unit=mapping.unit,
                source_file=path.name,
            )
        )
    return pd.concat(frames, ignore_index=True)


def _split_vessel_column(column: str, vessel_ids: list[str]) -> tuple[str, str] | None:
    """Split `Vessel_1_DO_pct` into ("Vessel_1", "DO_pct").

    Matched against the manifest's known vessel ids rather than by splitting on
    "_", because both the vessel prefix and the measurement suffix contain
    underscores and there is no way to guess the boundary otherwise.
    """
    for vessel_id in vessel_ids:
        prefix = f"{vessel_id}_"
        if column.startswith(prefix):
            return vessel_id, column[len(prefix) :]
    return None


def parse_ambr_file(path: Path, registry: RunRegistry, system: str = "ambr") -> pd.DataFrame:
    """Unpivot a multi-vessel wide file into one run per vessel.

    The file itself never names a run, so each column's vessel prefix is looked
    up in the manifest to recover the run_id.
    """
    df = pd.read_csv(path)
    timestamps = pd.to_datetime(df["Timestamp"])
    vessel_ids = registry.vessels_for_system(system)

    frames = []
    for column in df.columns:
        if column == "Timestamp":
            continue
        split = _split_vessel_column(column, vessel_ids)
        if split is None:
            continue
        vessel_id, measurement = split
        mapping = AMBR_MEASUREMENTS.get(measurement)
        if mapping is None:
            continue
        run = registry.by_vessel(system, vessel_id)
        frames.append(
            build_observations(
                run_id=run.run_id,
                timestamps=timestamps,
                source=SOURCE,
                variable=mapping.variable,
                values=df[column] * mapping.scale,
                unit=mapping.unit,
                source_file=path.name,
            )
        )

    if not frames:
        raise ValueError(f"{path.name}: no columns matched any manifest vessel for system {system!r}")
    return pd.concat(frames, ignore_index=True)
