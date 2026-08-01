"""Tests for the SQLite run database.

Mostly round-trip: what comes back out has to be what went in, because every
downstream chart reads from here rather than from the parsers.
"""

import pandas as pd
import pytest

from src.storage.db import (
    connect,
    init_db,
    read_observations,
    read_runs,
    read_samples,
    table_counts,
    write_all,
)


@pytest.fixture(scope="module")
def db(normalized, tmp_path_factory):
    path = tmp_path_factory.mktemp("storage") / "runs.db"
    with connect(path) as conn:
        init_db(conn, rebuild=True)
        write_all(conn, normalized.runs, normalized.observations, normalized.samples)
    return path


def test_all_rows_land_in_the_database(db, normalized):
    with connect(db) as conn:
        counts = table_counts(conn)
    assert counts["runs"] == len(normalized.runs)
    assert counts["observations"] == len(normalized.observations)
    assert counts["samples"] == len(normalized.samples)


def test_observations_round_trip_unchanged(db, normalized):
    with connect(db) as conn:
        stored = read_observations(conn)

    expected = normalized.observations.sort_values(
        ["run_id", "source", "variable", "timestamp"]
    ).reset_index(drop=True)
    stored = stored.sort_values(["run_id", "source", "variable", "timestamp"]).reset_index(drop=True)

    assert list(stored.columns) == list(expected.columns)
    assert len(stored) == len(expected)
    pd.testing.assert_series_equal(stored["value"], expected["value"], check_dtype=False)
    pd.testing.assert_series_equal(
        stored["timestamp"], expected["timestamp"], check_dtype=False
    )
    assert stored["variable"].tolist() == expected["variable"].tolist()
    assert stored["unit"].tolist() == expected["unit"].tolist()


def test_samples_keep_their_two_timestamps(db, normalized):
    with connect(db) as conn:
        stored = read_samples(conn)
    assert len(stored) == len(normalized.samples)
    paired = stored.dropna(subset=["draw_time", "injection_time"])
    assert (paired["injection_time"] > paired["draw_time"]).all()


def test_filters_narrow_the_read(db):
    with connect(db) as conn:
        subset = read_observations(conn, run_ids=["R1"], sources=["bioreactor"], variables=["pH"])
        everything = read_observations(conn)

    assert set(subset["run_id"]) == {"R1"}
    assert set(subset["variable"]) == {"pH"}
    assert 0 < len(subset) < len(everything)


def test_run_metadata_survives_the_trip(db):
    with connect(db) as conn:
        runs = read_runs(conn)
    assert set(runs["run_id"]) == {"R1", "R2", "R3", "A1", "A2", "A3", "A4"}
    assert runs.loc[runs["run_id"] == "R3", "anomaly"].iloc[0] == "contamination"
    assert set(runs["system"]) == {"dasgip", "ambr"}


def test_observations_cannot_reference_an_unknown_run(db):
    """Foreign keys are off by default in SQLite; `connect` turns them on."""
    import sqlite3

    with connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO observations "
                "(run_id, timestamp, elapsed_h, source, variable, value, unit, source_file) "
                "VALUES ('GHOST', '2026-03-02 08:00:00', 0.0, 'bioreactor', 'pH', 7.0, '', 'x.csv')"
            )


def test_rebuild_is_idempotent(db, normalized):
    """Re-running the loader must not double the data."""
    with connect(db) as conn:
        init_db(conn, rebuild=True)
        write_all(conn, normalized.runs, normalized.observations, normalized.samples)
        counts = table_counts(conn)
    assert counts["observations"] == len(normalized.observations)
