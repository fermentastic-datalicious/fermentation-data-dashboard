"""Ingestion layer: raw vendor files -> the common intermediate schema.

    from src.ingestion import normalize_all
    data = normalize_all()          # .runs, .observations, .samples
"""

from .align import align_runs, align_to_reference, resample_continuous
from .normalize import NormalizedData, normalize_all
from .run_registry import RunRegistry
from .schema import CANONICAL_UNITS, OBSERVATION_COLUMNS, SAMPLE_COLUMNS, SOURCES

__all__ = [
    "CANONICAL_UNITS",
    "NormalizedData",
    "OBSERVATION_COLUMNS",
    "RunRegistry",
    "SAMPLE_COLUMNS",
    "SOURCES",
    "align_runs",
    "align_to_reference",
    "normalize_all",
    "resample_continuous",
]
