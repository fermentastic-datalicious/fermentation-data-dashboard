"""Fermentation Data Dashboard — Streamlit entry point.

Run locally:   streamlit run streamlit_app.py

On a fresh checkout there is no data: `data/` is gitignored in full because
both the synthetic sources and the database are regenerable. The app builds
them on first boot, which takes a few seconds and happens once per container.
"""

import streamlit as st

from src.dashboard import drilldown
from src.dashboard.bootstrap import ensure_database

st.set_page_config(
    page_title="Fermentation Data Dashboard",
    page_icon="🧫",
    layout="wide",
)


@st.cache_resource(show_spinner="Generating synthetic sources and building the run database…")
def _database():
    return ensure_database()


def main() -> None:
    db_path = _database()

    st.title("Fermentation Data Dashboard")
    st.caption(
        "Four kinds of bioprocess source system — control loggers, analytical "
        "instruments, manual sample sheets and auxiliary sensors — normalised "
        "into one queryable run database. All data is synthetic and generated "
        "by this repo."
    )

    drilldown.render(db_path)


if __name__ == "__main__":
    main()
