"""Multi-run comparison: one run against the rest of its cohort.

The drill-down answers "what happened in this run". It cannot answer "was this
run normal", because one run has nothing to be normal against. That is the
question a process engineer actually opens a dashboard to ask.
"""

import pandas as pd
import streamlit as st

from ..ingestion.align import SAMPLE_SOURCES
from .charts import build_comparison_figure
from .cohorts import build_cohorts
from .data import (
    cohort_outcomes,
    comparable_traces,
    divergence_from_cohort,
    load_cohort_observations,
    load_runs,
)
from .panels import variable_labels

# The three variables on which R3 separates from its cohort. A first-time
# viewer landing on the defaults should see the finding without configuring
# anything; everything else is available in the picker.
DEFAULT_VARIABLES = ["DO", "offgas_co2", "permittivity"]


def render(db_path) -> None:
    runs = load_runs(db_path)
    if runs.empty:
        st.error("The run database is empty.")
        return

    cohorts = build_cohorts(runs)
    cohort, highlighted, variables = _sidebar(cohorts)

    obs = load_cohort_observations(cohort.run_ids, db_path)
    labels = variable_labels()

    st.subheader(cohort.label)
    st.caption(
        f"Held constant across these runs: {cohort.held_constant}. "
        "Runs are only ever compared within a cohort — overlaying a CHO "
        "fed-batch on an *E. coli* batch would be easy to draw and impossible "
        "to interpret."
    )

    _outcomes(runs, cohort, highlighted)

    if not variables:
        st.info("Choose at least one variable to compare.")
        return

    traces = {v: comparable_traces(obs, v, cohort.run_ids) for v in variables}
    traces = {v: t for v, t in traces.items() if t}
    sparse = {
        v for v in variables
        if not obs[obs["variable"] == v].empty
        and obs[obs["variable"] == v]["source"].iloc[0] in SAMPLE_SOURCES
    }

    st.plotly_chart(
        build_comparison_figure(traces, highlighted, sparse_variables=sparse),
        use_container_width=True,
        config={"displaylogo": False},
    )

    _divergence(obs, cohort, highlighted, variables, labels)


def _sidebar(cohorts) -> tuple:
    with st.sidebar:
        st.subheader("Cohort")
        cohort = st.selectbox(
            "Cohort",
            cohorts,
            format_func=lambda c: c.label,
            label_visibility="collapsed",
        )
        st.subheader("Highlight")
        highlighted = st.radio(
            "Run", cohort.run_ids, label_visibility="collapsed", horizontal=True
        )
        st.caption("Every other run in the cohort is drawn behind, in grey.")

        st.subheader("Variables")
        labels = variable_labels()
        variables = st.multiselect(
            "Variables",
            list(labels),
            default=DEFAULT_VARIABLES,
            format_func=lambda v: labels.get(v, v),
            label_visibility="collapsed",
        )
    return cohort, highlighted, variables


def _outcomes(runs: pd.DataFrame, cohort, highlighted: str) -> None:
    outcomes = cohort_outcomes(runs, cohort.run_ids)
    st.dataframe(
        outcomes.style.apply(
            lambda row: [
                "font-weight: 600" if row["Run"] == highlighted else "" for _ in row
            ],
            axis=1,
        ),
        use_container_width=True,
        hide_index=True,
    )

    # The point of showing this table at all. On the E. coli cohort the
    # contaminated run has the highest final biomass, because dry cell weight
    # counts dead cells -- so a review that stops at endpoints ranks it first.
    best = outcomes.loc[outcomes["Final biomass (g/L)"].idxmax()]
    if best["Recorded note"]:
        st.caption(
            f"Read on endpoints alone, **{best['Run']}** has the highest final "
            f"biomass in this cohort — and it is the run carrying a recorded "
            f"note of *{best['Recorded note']}*. Dry cell weight counts dead "
            "cells, so contamination can raise it. The trajectories below say "
            "something different."
        )


def _divergence(obs, cohort, highlighted, variables, labels) -> None:
    rows = []
    for variable in variables:
        stats = divergence_from_cohort(obs, variable, cohort.run_ids, highlighted)
        if stats:
            rows.append(
                {
                    "Variable": labels.get(variable, variable),
                    f"{highlighted} (mean, 2nd half)": round(stats["highlighted"], 2),
                    "Rest of cohort (median)": round(stats["cohort_median"], 2),
                    "Difference": round(stats["difference"], 2),
                }
            )
    if not rows:
        return

    with st.expander(f"How far {highlighted} sits from the rest", expanded=False):
        st.caption(
            "Plain arithmetic over the second half of each run — no threshold "
            "and no verdict. Whether a difference matters depends on the "
            "variable, and that judgement belongs to whoever ran the "
            "experiment."
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
