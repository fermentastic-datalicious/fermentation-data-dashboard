"""Single-run drill-down: every source for one run, on one timeline."""

import pandas as pd
import streamlit as st

from .charts import build_trend_figure
from .data import (
    available_variables,
    load_observations,
    load_runs,
    load_samples,
    run_conditions,
    run_outcomes,
    source_provenance,
)
from .panels import ACTUATOR_PANELS, MAIN_PANELS, panels_with_data

X_AXIS_CHOICES = {"Elapsed time (h)": "elapsed_h", "Clock time": "timestamp"}


def render(db_path) -> None:
    runs = load_runs(db_path)
    if runs.empty:
        st.error("The run database is empty.")
        return

    run_id, x_axis, show_actuators = _sidebar(runs)
    run = runs.set_index("run_id").loc[run_id]
    obs = load_observations(run_id, db_path)

    _header(run_id, run, obs)

    panels = panels_with_data(MAIN_PANELS, available_variables(obs))
    if show_actuators:
        panels += panels_with_data(ACTUATOR_PANELS, available_variables(obs))

    st.plotly_chart(
        build_trend_figure(obs, panels, x_axis),
        use_container_width=True,
        config={"displaylogo": False},
    )

    _provenance(obs)
    _samples(run_id, db_path)
    _table_view(obs)


def _sidebar(runs: pd.DataFrame) -> tuple[str, str, bool]:
    with st.sidebar:
        st.subheader("Run")
        run_id = st.selectbox(
            "Select a run",
            runs["run_id"],
            format_func=lambda r: _run_option_label(runs, r),
            label_visibility="collapsed",
        )
        st.subheader("Display")
        x_axis = X_AXIS_CHOICES[
            st.radio("X axis", list(X_AXIS_CHOICES), label_visibility="collapsed")
        ]
        show_actuators = st.toggle("Show actuators", value=False)
        st.caption(
            "Agitation, gas flow, pressure and temperature. Each on its own "
            "panel — rpm, L/min, bar and degC share no scale worth combining."
        )
    return run_id, x_axis, show_actuators


def _run_option_label(runs: pd.DataFrame, run_id: str) -> str:
    run = runs.set_index("run_id").loc[run_id]
    return f"{run_id} · {run['strain']} · {run['mode']}"


def _header(run_id: str, run: pd.Series, obs: pd.DataFrame) -> None:
    st.subheader(f"Run {run_id}")
    st.markdown(f"{run_conditions(run)} — {run_outcomes(obs)}")

    # Stated plainly, not as a warning badge. The dashboard shows what the run
    # did; deciding whether it went wrong is the reader's job, and an algorithm
    # flagging it would make that judgement for them.
    if run["anomaly"]:
        st.caption(f"Recorded note on this run: {run['anomaly']}.")


def _provenance(obs: pd.DataFrame) -> None:
    provenance = source_provenance(obs)
    with st.expander(f"{len(provenance)} sources unified for this run", expanded=False):
        st.caption(
            "Each of these was logged by a different instrument on its own "
            "clock and cadence, in a different file format. Two of the six "
            "formats state the run id internally; the rest were resolved from "
            "a header comment, a filename, or a vessel column."
        )
        st.dataframe(provenance, use_container_width=True, hide_index=True)


def _samples(run_id: str, db_path) -> None:
    samples = load_samples(run_id, db_path)
    if samples.empty:
        return
    with st.expander(f"{len(samples)} discrete samples", expanded=False):
        st.caption(
            "Analytical results are timestamped at sample draw, not at "
            "injection — the instrument logged them 1–3 h later, after prep "
            "and queueing."
        )
        st.dataframe(
            samples[
                ["sample_id", "draw_time", "injection_time", "operator", "notes"]
            ],
            use_container_width=True,
            hide_index=True,
        )


def _table_view(obs: pd.DataFrame) -> None:
    """The numbers behind the charts, for anyone the colours do not serve."""
    with st.expander("Data table", expanded=False):
        summary = (
            obs.groupby(["source", "variable", "unit"])["value"]
            .agg(points="size", minimum="min", maximum="max", final="last")
            .reset_index()
        )
        st.dataframe(summary, use_container_width=True, hide_index=True)
