"""Orchestrates the ingestion layer: raw file tree -> one validated long frame.

Everything that needs to know about more than one source at a time lives
here. The parsers stay pure and single-format; this module handles discovery,
dispatch, the cross-source timestamp correction, and the final validation
pass before anything reaches storage.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .parsers.analytical import parse_hplc_file, parse_hplc_samples
from .parsers.auxiliary import parse_capacitance_file, parse_offgas_file
from .parsers.bioreactor import parse_ambr_file, parse_dasgip_file
from .parsers.offline import parse_biomass_file, parse_biomass_samples
from .run_registry import DATA_RAW, RunRegistry
from .schema import (
    OBSERVATION_COLUMNS,
    SAMPLE_COLUMNS,
    empty_observations,
    validate_observations,
)


@dataclass
class NormalizedData:
    runs: pd.DataFrame
    observations: pd.DataFrame
    samples: pd.DataFrame

    def summary(self) -> str:
        by_source = self.observations.groupby("source").size().sort_values(ascending=False)
        lines = [
            f"{len(self.runs)} runs, "
            f"{len(self.observations):,} observations, "
            f"{len(self.samples)} samples",
            "  observations by source:",
        ]
        lines += [f"    {source:<12} {count:>8,}" for source, count in by_source.items()]
        return "\n".join(lines)


def discover_files(data_raw: Path = DATA_RAW) -> dict[str, list[Path]]:
    """Map each source layout to the files present on disk."""
    return {
        "dasgip": sorted((data_raw / "bioreactor" / "dasgip").glob("*.csv")),
        "ambr": sorted((data_raw / "bioreactor" / "ambr").glob("*_ambr.csv")),
        "hplc": sorted((data_raw / "analytical").glob("*_hplc.csv")),
        "biomass": sorted((data_raw / "offline").glob("*_biomass.csv")),
        "offgas": sorted((data_raw / "auxiliary" / "offgas").glob("*_offgas.csv")),
        "capacitance": sorted((data_raw / "auxiliary" / "capacitance").glob("*_capacitance.csv")),
    }


def _parse_observations(files: dict[str, list[Path]], registry: RunRegistry) -> pd.DataFrame:
    parsers = {
        "dasgip": parse_dasgip_file,
        "ambr": parse_ambr_file,
        "hplc": parse_hplc_file,
        "biomass": parse_biomass_file,
        "offgas": parse_offgas_file,
        "capacitance": parse_capacitance_file,
    }
    frames = [
        parsers[layout](path, registry) for layout, paths in files.items() for path in paths
    ]
    if not frames:
        return empty_observations()
    return pd.concat(frames, ignore_index=True)


def build_samples(files: dict[str, list[Path]]) -> pd.DataFrame:
    """Merge the two halves of each sample's identity into one table.

    The biomass sheet knows when a sample was drawn; the HPLC peak table knows
    when it was injected and by which method. They share a sample id, so an
    outer join gives one row per physical sample with both timestamps.
    """
    biomass = [parse_biomass_samples(p) for p in files["biomass"]]
    hplc = [parse_hplc_samples(p) for p in files["hplc"]]

    biomass_df = pd.concat(biomass, ignore_index=True) if biomass else pd.DataFrame()
    hplc_df = pd.concat(hplc, ignore_index=True) if hplc else pd.DataFrame()

    if biomass_df.empty:
        samples = hplc_df
    elif hplc_df.empty:
        samples = biomass_df
    else:
        samples = biomass_df.merge(
            hplc_df.drop(columns=["run_id"]), on="sample_id", how="outer"
        )

    for column in SAMPLE_COLUMNS:
        if column not in samples.columns:
            samples[column] = pd.NA
    return samples[SAMPLE_COLUMNS].sort_values(["run_id", "sample_id"]).reset_index(drop=True)


def backdate_analytical(obs: pd.DataFrame, samples: pd.DataFrame) -> pd.DataFrame:
    """Move analytical observations from injection time to sample draw time.

    An HPLC row is stamped when the vial entered the instrument, which is one
    to three hours after the sample left the vessel. Taken at face value, every
    glucose and product point sits visibly to the right of the DO and base
    curves that explain it.

    The correction joins on (run_id, injection_time), which the samples table
    resolves back to a draw time. The draw time itself is hand-entered to the
    minute, so this is not exact -- but a few minutes of transcription jitter
    is a different order of problem from a systematic multi-hour lag.

    Rows whose injection time has no matching sample keep their original
    timestamp and raise a warning rather than being dropped.
    """
    analytical = obs["source"] == "analytical"
    if not analytical.any():
        return obs

    # De-duplicated so the merge below is strictly one-to-one and cannot change
    # the row count it is being assigned back onto.
    lookup = (
        samples.dropna(subset=["injection_time", "draw_time"])[
            ["run_id", "injection_time", "draw_time"]
        ]
        .drop_duplicates(subset=["run_id", "injection_time"])
    )
    if lookup.empty:
        warnings.warn("No sample draw times available; analytical data stays at injection time.")
        return obs

    obs = obs.copy()
    merged = obs.loc[analytical, ["run_id", "timestamp"]].merge(
        lookup,
        left_on=["run_id", "timestamp"],
        right_on=["run_id", "injection_time"],
        how="left",
    )
    draw_times = merged["draw_time"].to_numpy()

    unresolved = merged["draw_time"].isna().sum()
    if unresolved:
        warnings.warn(
            f"{unresolved} analytical rows had no matching sample draw time "
            "and remain stamped at injection time."
        )

    corrected = pd.Series(draw_times, index=obs.index[analytical])
    obs.loc[analytical, "timestamp"] = corrected.fillna(obs.loc[analytical, "timestamp"])
    return obs


def add_elapsed_hours(obs: pd.DataFrame, registry: RunRegistry) -> pd.DataFrame:
    """Hours since each run's start -- computed once here, not in every query."""
    obs = obs.copy()
    obs["elapsed_h"] = pd.concat(
        [registry.elapsed_hours(run_id, group["timestamp"]) for run_id, group in obs.groupby("run_id")]
    ).sort_index()
    return obs


def normalize_all(data_raw: Path = DATA_RAW, registry: RunRegistry | None = None) -> NormalizedData:
    """Parse every raw source into the common schema, validated and ready to store."""
    registry = registry or RunRegistry.load(data_raw / "run_manifest.csv")
    files = discover_files(data_raw)

    observations = _parse_observations(files, registry)
    samples = build_samples(files)

    observations = backdate_analytical(observations, samples)
    observations = add_elapsed_hours(observations, registry)
    observations = observations.sort_values(["run_id", "source", "variable", "timestamp"])
    observations = observations.reset_index(drop=True)[OBSERVATION_COLUMNS]

    validate_observations(observations)
    _validate_referential_integrity(observations, samples, registry)

    return NormalizedData(runs=registry.manifest, observations=observations, samples=samples)


def _validate_referential_integrity(
    obs: pd.DataFrame, samples: pd.DataFrame, registry: RunRegistry
) -> None:
    known = set(registry.run_ids)
    for name, frame in (("observations", obs), ("samples", samples)):
        orphans = sorted(set(frame["run_id"].dropna()) - known)
        if orphans:
            raise ValueError(f"{name} reference run_ids missing from the manifest: {orphans}")
