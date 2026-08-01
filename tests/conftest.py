import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.normalize import discover_files, normalize_all  # noqa: E402
from src.ingestion.run_registry import DATA_RAW, RunRegistry  # noqa: E402


@pytest.fixture(scope="session")
def registry() -> RunRegistry:
    return RunRegistry.load()


@pytest.fixture(scope="session")
def files() -> dict:
    return discover_files(DATA_RAW)


@pytest.fixture(scope="session")
def normalized():
    """Full ingest, parsed once and shared -- it reads ~300k rows off disk."""
    return normalize_all()
