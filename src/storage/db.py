"""SQLite run database.

Three tables: `runs` (the manifest), `observations` (the common schema, one
numeric measurement per row), and `samples` (per-sample metadata that has no
business sitting in a numeric value column).

The load is a full rebuild rather than an upsert. The data is synthetic and
regenerable in seconds, so incremental-merge logic would be complexity with
nothing to show for it.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from ..ingestion.schema import OBSERVATION_COLUMNS, SAMPLE_COLUMNS

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "processed" / "runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id                  TEXT PRIMARY KEY,
    vessel_id               TEXT NOT NULL,
    system                  TEXT NOT NULL,
    mode                    TEXT NOT NULL,
    strain                  TEXT,
    start_time              TIMESTAMP NOT NULL,
    duration_h              REAL,
    volume_L                REAL,
    anomaly                 TEXT,
    final_biomass_total_gL  REAL,
    final_product_gL        REAL
);

CREATE TABLE IF NOT EXISTS observations (
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    timestamp    TIMESTAMP NOT NULL,
    elapsed_h    REAL NOT NULL,
    source       TEXT NOT NULL,
    variable     TEXT NOT NULL,
    value        REAL NOT NULL,
    unit         TEXT NOT NULL,
    source_file  TEXT
);

CREATE TABLE IF NOT EXISTS samples (
    sample_id        TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    draw_time        TIMESTAMP,
    injection_time   TIMESTAMP,
    operator         TEXT,
    dilution_factor  REAL,
    method           TEXT,
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_run_variable_time
    ON observations(run_id, variable, timestamp);
CREATE INDEX IF NOT EXISTS idx_obs_run_source
    ON observations(run_id, source);
CREATE INDEX IF NOT EXISTS idx_samples_run
    ON samples(run_id);
"""

TABLES = ("observations", "samples", "runs")

BUILD_COMMAND = "python -m src.storage.build_db --rebuild"


class DatabaseNotBuiltError(FileNotFoundError):
    """The run database has not been built yet.

    Covers both "the file is not there" and "the file is there but has no
    schema". Either way the fix is the same command, so callers get one thing
    to catch. Subclasses FileNotFoundError to match how a missing run manifest
    is reported in `ingestion.run_registry`.
    """


def _not_built(detail: str) -> DatabaseNotBuiltError:
    return DatabaseNotBuiltError(f"{detail} Build it first: {BUILD_COMMAND}")


@contextmanager
def connect(db_path: Path | str = DB_PATH, create: bool = False):
    """Connection with foreign keys on -- they are off by default in SQLite.

    Opening a missing database is an error rather than a silent create.
    `sqlite3.connect` will happily conjure an empty file, which turns a
    "you haven't built the database yet" mistake into a confusing "no such
    table" several calls later, and litters the disk on the way. Only the
    loader passes `create=True`; every reader gets the safe default.
    """
    path = str(db_path)
    if path != ":memory:":
        if create:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        elif not Path(path).exists():
            # Raised before sqlite3.connect, so no stray file is left behind.
            raise _not_built(f"No run database at {path}.")

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _database_path(conn: sqlite3.Connection) -> str:
    """The file backing this connection, for use in error messages."""
    for _, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return file or ":memory:"
    return "unknown"


def require_schema(conn: sqlite3.Connection) -> None:
    """Fail with the build command if the expected tables are not present.

    Guards the case `connect` cannot catch: a database file that exists but
    was never loaded, or was only partly built.
    """
    present = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    missing = [table for table in TABLES if table not in present]
    if missing:
        raise _not_built(
            f"Run database at {_database_path(conn)} is missing "
            f"table(s): {', '.join(sorted(missing))}."
        )


def init_db(conn: sqlite3.Connection, rebuild: bool = False) -> None:
    if rebuild:
        for table in TABLES:  # ordered child-first so the foreign keys stay satisfied
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.executescript(SCHEMA)


def write_all(
    conn: sqlite3.Connection,
    runs: pd.DataFrame,
    observations: pd.DataFrame,
    samples: pd.DataFrame,
) -> None:
    """Write runs first, then the tables that reference them."""
    runs.to_sql("runs", conn, if_exists="append", index=False)
    observations[OBSERVATION_COLUMNS].to_sql(
        "observations", conn, if_exists="append", index=False, chunksize=10_000
    )
    samples[SAMPLE_COLUMNS].to_sql("samples", conn, if_exists="append", index=False)


def read_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    require_schema(conn)
    return pd.read_sql_query("SELECT * FROM runs ORDER BY run_id", conn, parse_dates=["start_time"])


def read_observations(
    conn: sqlite3.Connection,
    run_ids: list[str] | None = None,
    sources: list[str] | None = None,
    variables: list[str] | None = None,
) -> pd.DataFrame:
    """Filtered read of the common schema, parameterized to keep SQL out of callers."""
    require_schema(conn)

    clauses, params = [], []
    for column, values in (("run_id", run_ids), ("source", sources), ("variable", variables)):
        if values:
            clauses.append(f"{column} IN ({','.join('?' * len(values))})")
            params.extend(values)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    query = (
        f"SELECT {', '.join(OBSERVATION_COLUMNS)} FROM observations{where} "
        "ORDER BY run_id, source, variable, timestamp"
    )
    return pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp"])


def read_samples(conn: sqlite3.Connection, run_ids: list[str] | None = None) -> pd.DataFrame:
    require_schema(conn)

    where, params = "", []
    if run_ids:
        where = f" WHERE run_id IN ({','.join('?' * len(run_ids))})"
        params = run_ids
    return pd.read_sql_query(
        f"SELECT * FROM samples{where} ORDER BY run_id, sample_id",
        conn,
        params=params,
        parse_dates=["draw_time", "injection_time"],
    )


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES
    }
