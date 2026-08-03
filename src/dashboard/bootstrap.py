"""Make sure there is a database to read, building one if there is not.

`data/` is gitignored in full, so a fresh deploy -- Streamlit Community Cloud
included -- starts with no raw sources and no database. Rather than committing
a 45 MB binary that changes completely on every rebuild, the app generates the
synthetic sources and ingests them on first boot. Measured at 4.4 s and 232 MB
peak, against roughly 1 GB available on the free tier.

This is the one place allowed to build the database as a side effect of a read.
Everything else fails loudly instead -- see `storage.db.require_schema`.
"""

import logging
from pathlib import Path

from ..generators import generate_all
from ..ingestion.run_registry import DATA_RAW
from ..storage import DB_PATH, DatabaseNotBuiltError, connect, require_schema
from ..storage import build_db

log = logging.getLogger(__name__)


def database_is_ready(db_path: Path = DB_PATH) -> bool:
    """True if the database exists *and* carries its schema.

    A file on disk is not enough: an abandoned or partial build leaves a
    database with no tables, which fails later and further from the cause.
    """
    try:
        with connect(db_path) as conn:
            require_schema(conn)
    except DatabaseNotBuiltError:
        return False
    return True


def ensure_database(db_path: Path = DB_PATH, data_raw: Path = DATA_RAW) -> Path:
    """Return a path to a populated database, building one if needed.

    Idempotent and safe to call on every run -- it only does work when the
    database is genuinely missing, so the caller can wrap it in
    `st.cache_resource` and stop thinking about it.
    """
    if database_is_ready(db_path):
        return db_path

    log.info("No run database at %s -- generating synthetic sources", db_path)
    if not (data_raw / "run_manifest.csv").exists():
        generate_all.main()

    log.info("Ingesting into %s", db_path)
    build_db.build(db_path=db_path, data_raw=data_raw, rebuild=True)
    return db_path
