"""Tests for the SQLite run database.

Mostly round-trip: what comes back out has to be what went in, because every
downstream chart reads from here rather than from the parsers.
"""

import sqlite3

import pandas as pd
import pytest

from src.storage.db import (
    BUILD_COMMAND,
    DatabaseNotBuiltError,
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
    with connect(path, create=True) as conn:
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


# --- unbuilt database ---------------------------------------------------
# The database is not in version control, so a fresh clone has none. That
# path has to fail in a way that says what to run, and without scattering
# empty database files around while it does.


def test_reading_a_database_that_was_never_built_names_the_build_command(tmp_path):
    missing = tmp_path / "runs.db"

    with pytest.raises(DatabaseNotBuiltError) as excinfo:
        with connect(missing) as conn:
            read_observations(conn)

    message = str(excinfo.value)
    assert BUILD_COMMAND in message
    assert str(missing) in message


def test_opening_a_missing_database_leaves_no_stray_file(tmp_path):
    """sqlite3.connect would silently create one; connect() must not."""
    missing = tmp_path / "runs.db"

    with pytest.raises(DatabaseNotBuiltError):
        with connect(missing):
            pass

    assert not missing.exists()
    assert list(tmp_path.iterdir()) == []


def test_missing_parent_directory_is_not_created_on_read(tmp_path):
    nested = tmp_path / "data" / "processed" / "runs.db"

    with pytest.raises(DatabaseNotBuiltError):
        with connect(nested):
            pass

    assert not nested.parent.exists()


def test_an_empty_database_file_still_reports_what_to_run(tmp_path):
    """The file exists but was never loaded -- a half-finished build."""
    empty = tmp_path / "runs.db"
    sqlite3.connect(empty).close()
    assert empty.exists()

    with connect(empty) as conn:
        for read in (read_observations, read_runs, read_samples):
            with pytest.raises(DatabaseNotBuiltError, match="missing table"):
                read(conn)


def test_a_partly_built_database_is_rejected(tmp_path):
    """Only some tables present -- reads must not return misleading emptiness."""
    partial = tmp_path / "runs.db"
    with connect(partial, create=True) as conn:
        conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")

    with connect(partial) as conn:
        with pytest.raises(DatabaseNotBuiltError, match="observations"):
            read_observations(conn)


def test_creating_is_opt_in_and_still_works(tmp_path):
    created = tmp_path / "nested" / "runs.db"
    with connect(created, create=True) as conn:
        init_db(conn)
    assert created.exists()

    with connect(created) as conn:  # now openable without create
        assert read_observations(conn).empty


def test_a_built_database_reads_without_complaint(db):
    """The guard must not fire on the normal path."""
    with connect(db) as conn:
        assert not read_observations(conn, run_ids=["R1"]).empty


def test_rebuild_is_idempotent(db, normalized):
    """Re-running the loader must not double the data."""
    with connect(db) as conn:
        init_db(conn, rebuild=True)
        write_all(conn, normalized.runs, normalized.observations, normalized.samples)
        counts = table_counts(conn)
    assert counts["observations"] == len(normalized.observations)
