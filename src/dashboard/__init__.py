"""Streamlit dashboard over the run database.

    streamlit run streamlit_app.py
"""

from .bootstrap import database_is_ready, ensure_database

__all__ = ["database_is_ready", "ensure_database"]
