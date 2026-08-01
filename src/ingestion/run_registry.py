"""Run identity: which physical run does this file (or column) belong to?

Only two of the six source formats state their run_id inside the file. The
rest hide it in a header comment, a filename, or a column prefix that has to
be looked up by vessel. That is normal -- instruments are configured by
whoever set them up that morning -- so run resolution gets its own module and
every resolved id is checked against the manifest.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
MANIFEST_PATH = DATA_RAW / "run_manifest.csv"


class UnknownRunError(KeyError):
    """Raised when a file resolves to a run that is not in the manifest."""


@dataclass(frozen=True)
class Run:
    run_id: str
    vessel_id: str
    system: str
    mode: str
    strain: str
    start_time: datetime
    duration_h: float
    volume_L: float
    anomaly: str


class RunRegistry:
    """The manifest, plus the lookups each parser needs to identify its run."""

    def __init__(self, manifest: pd.DataFrame):
        self._manifest = manifest
        self._runs = {
            row.run_id: Run(
                run_id=row.run_id,
                vessel_id=row.vessel_id,
                system=row.system,
                mode=row.mode,
                strain=row.strain,
                start_time=row.start_time.to_pydatetime(),
                duration_h=float(row.duration_h),
                volume_L=float(row.volume_L),
                anomaly=row.anomaly,
            )
            for row in manifest.itertuples()
        }
        self._by_vessel = {(r.system, r.vessel_id): r.run_id for r in self._runs.values()}

    @classmethod
    def load(cls, manifest_path: Path = MANIFEST_PATH) -> "RunRegistry":
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No run manifest at {manifest_path}. "
                "Generate the synthetic sources first: python -m src.generators.generate_all"
            )
        manifest = pd.read_csv(manifest_path, parse_dates=["start_time"])
        manifest["anomaly"] = manifest["anomaly"].fillna("")
        return cls(manifest)

    @property
    def manifest(self) -> pd.DataFrame:
        return self._manifest.copy()

    @property
    def run_ids(self) -> list[str]:
        return list(self._runs)

    def get(self, run_id: str) -> Run:
        """Look up a run, rejecting ids the manifest has never heard of.

        Raising here rather than passing the id through is deliberate: an
        unrecognized run_id means orphan rows that join to nothing and quietly
        vanish from every dashboard view.
        """
        try:
            return self._runs[run_id]
        except KeyError:
            raise UnknownRunError(
                f"run_id {run_id!r} is not in the manifest (known: {sorted(self._runs)})"
            ) from None

    def vessels_for_system(self, system: str) -> list[str]:
        """Vessel ids belonging to one control system, longest first.

        Ordered longest-first so that matching a column prefix against them
        cannot let `Vessel_1` shadow a hypothetical `Vessel_10`.
        """
        vessels = [r.vessel_id for r in self._runs.values() if r.system == system]
        return sorted(set(vessels), key=len, reverse=True)

    def by_vessel(self, system: str, vessel_id: str) -> Run:
        """Resolve a run from its system and vessel -- the Ambr wide-file case.

        The Ambr export names its columns `Vessel_1_pH` and never mentions a
        batch or run anywhere, so the vessel is the only handle available.
        """
        try:
            run_id = self._by_vessel[(system, vessel_id)]
        except KeyError:
            raise UnknownRunError(
                f"no {system} run recorded for vessel {vessel_id!r}"
            ) from None
        return self._runs[run_id]

    def elapsed_hours(self, run_id: str, timestamps: pd.Series) -> pd.Series:
        """Hours since the run started -- the x-axis for any multi-run overlay."""
        start = pd.Timestamp(self.get(run_id).start_time)
        return (pd.to_datetime(timestamps) - start).dt.total_seconds() / 3600.0


def run_id_from_filename(path: Path, suffix: str) -> str:
    """Pull the run_id off filenames like `R1_offgas.csv` (suffix `_offgas`).

    The off-gas analyzer and capacitance probe write nothing that identifies
    the run -- no header, no column -- so the filename is genuinely the only
    link back to the bioreactor. Fragile in real life, and worth naming as
    such rather than hiding behind a regex somewhere.
    """
    stem = path.stem
    if not stem.endswith(suffix):
        raise ValueError(f"{path.name} does not match the expected `<run_id>{suffix}.csv` pattern")
    return stem[: -len(suffix)]
