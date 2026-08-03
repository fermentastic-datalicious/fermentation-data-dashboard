"""Tests for the multi-run comparison view.

Two things here are worth guarding. The two-colour invariant is what keeps the
palette's series cap from binding as cohorts grow, and it would be easy to
"improve" by giving each run its own colour. And the resampling that makes the
overlay readable could, at a coarser grid, quietly average away the very
divergence the view exists to show.
"""

import pandas as pd
import pytest

from src.dashboard.charts import build_comparison_figure
from src.dashboard.cohorts import COHORT_KEYS, build_cohorts, cohort_for_run
from src.dashboard.data import (
    cohort_outcomes,
    comparable_traces,
    divergence_from_cohort,
)
from src.dashboard.panels import COHORT_COLOR, HIGHLIGHT_COLOR, variable_labels
from src.storage import DB_PATH, connect, read_observations, read_runs

DASGIP = ("R1", "R2", "R3")
AMBR = ("A1", "A2", "A3", "A4")


@pytest.fixture(scope="module")
def runs() -> pd.DataFrame:
    with connect(DB_PATH) as conn:
        return read_runs(conn)


@pytest.fixture(scope="module")
def dasgip_obs() -> pd.DataFrame:
    with connect(DB_PATH) as conn:
        return read_observations(conn, run_ids=list(DASGIP))


# --- cohorts -------------------------------------------------------------


def test_cohorts_derive_from_the_manifest(runs):
    cohorts = build_cohorts(runs)
    assert len(cohorts) == 2
    assert {c.run_ids for c in cohorts} == {DASGIP, AMBR}


def test_no_cohort_mixes_organisms_or_modes(runs):
    """The whole point of a cohort: overlaying CHO fed-batch on E. coli batch
    would be easy to draw and impossible to interpret."""
    indexed = runs.set_index("run_id")
    for cohort in build_cohorts(runs):
        members = indexed.loc[list(cohort.run_ids)]
        for key in COHORT_KEYS:
            assert members[key].nunique() == 1, f"cohort spans several {key} values"


def test_every_run_belongs_to_exactly_one_cohort(runs):
    cohorts = build_cohorts(runs)
    assigned = [r for c in cohorts for r in c.run_ids]
    assert sorted(assigned) == sorted(runs["run_id"])
    assert len(assigned) == len(set(assigned))
    assert cohort_for_run(cohorts, "R3").run_ids == DASGIP


def test_unknown_run_has_no_cohort(runs):
    with pytest.raises(KeyError):
        cohort_for_run(build_cohorts(runs), "does-not-exist")


# --- the two-colour invariant -------------------------------------------


@pytest.mark.parametrize("highlighted", DASGIP)
def test_only_two_colours_regardless_of_who_is_highlighted(dasgip_obs, highlighted):
    """A colour-coded overlay tops out at three runs on this palette, because a
    fourth slot puts yellow beside orange below the normal-vision floor.
    Highlight-one uses two colours whatever the cohort size, which is what
    keeps that cap from ever binding."""
    traces = {v: comparable_traces(dasgip_obs, v, DASGIP) for v in ("DO", "offgas_co2")}
    figure = build_comparison_figure(traces, highlighted)

    colours = {
        (t.line.color if t.mode == "lines" else t.marker.color) for t in figure.data
    }
    assert colours == {HIGHLIGHT_COLOR, COHORT_COLOR}


def test_highlighted_run_is_drawn_last_so_it_sits_on_top(dasgip_obs):
    traces = {"DO": comparable_traces(dasgip_obs, "DO", DASGIP)}
    figure = build_comparison_figure(traces, "R3")
    assert figure.data[-1].name == "R3"
    assert figure.data[-1].line.color == HIGHLIGHT_COLOR


def test_only_the_highlighted_run_is_labelled_and_hoverable(dasgip_obs):
    traces = {"DO": comparable_traces(dasgip_obs, "DO", DASGIP)}
    figure = build_comparison_figure(traces, "R3")

    labelled = [a.text for a in figure.layout.annotations if a.text == "R3"]
    assert len(labelled) == 1, "exactly one run label per panel"

    for trace in figure.data:
        if trace.name == "R3":
            assert trace.hoverinfo != "skip"
        else:
            assert trace.hoverinfo == "skip", "cohort runs must stay out of the crosshair"


def test_a_single_run_cohort_renders_without_a_grey_layer(dasgip_obs):
    traces = {"DO": comparable_traces(dasgip_obs, "DO", ("R1",))}
    figure = build_comparison_figure(traces, "R1")
    assert len(figure.data) == 1
    assert figure.data[0].line.color == HIGHLIGHT_COLOR


def test_comparison_figure_carries_no_legend(dasgip_obs):
    traces = {"DO": comparable_traces(dasgip_obs, "DO", DASGIP)}
    figure = build_comparison_figure(traces, "R3")
    assert figure.layout.showlegend is False


def test_empty_input_still_builds_a_figure():
    assert len(build_comparison_figure({}, "R1").data) == 0


# --- resampling ----------------------------------------------------------


def test_resampling_shrinks_continuous_traces(dasgip_obs):
    raw = len(dasgip_obs[(dasgip_obs["variable"] == "offgas_co2") & (dasgip_obs["run_id"] == "R1")])
    resampled = comparable_traces(dasgip_obs, "offgas_co2", DASGIP)["R1"]
    assert raw > 4000
    assert len(resampled) < raw / 5


def test_sparse_variables_are_never_resampled(dasgip_obs):
    """Seven HPLC injections onto a five-minute grid would invent a dense
    series out of nothing."""
    for variable in ("dcw", "od600", "glucose", "product"):
        raw = dasgip_obs[(dasgip_obs["variable"] == variable) & (dasgip_obs["run_id"] == "R1")]
        assert len(comparable_traces(dasgip_obs, variable, DASGIP)["R1"]) == len(raw)


@pytest.mark.parametrize(
    "variable,expect_lower",
    [("DO", True), ("offgas_co2", False), ("permittivity", True)],
)
def test_resampling_preserves_r3_divergence(dasgip_obs, variable, expect_lower):
    """Guards the finding itself: if a future coarser grid averaged R3 back
    into the cohort, the view would silently stop working."""
    stats = divergence_from_cohort(dasgip_obs, variable, DASGIP, "R3")
    assert stats, f"no divergence computed for {variable}"
    if expect_lower:
        assert stats["difference"] < 0
    else:
        assert stats["difference"] > 0


def test_a_healthy_run_sits_close_to_its_cohort(dasgip_obs):
    """The counterpart: R1 should not look like an outlier."""
    r1 = divergence_from_cohort(dasgip_obs, "DO", DASGIP, "R1")
    r3 = divergence_from_cohort(dasgip_obs, "DO", DASGIP, "R3")
    assert abs(r1["difference"]) < abs(r3["difference"]) / 5


# --- the misleading table ------------------------------------------------


def test_endpoint_table_ranks_the_contaminated_run_first(runs):
    """Not a bug. This is why the view shows trajectories and says so in the
    caption -- a review that stops at endpoints gets this cohort backwards."""
    outcomes = cohort_outcomes(runs, DASGIP)
    best = outcomes.loc[outcomes["Final biomass (g/L)"].idxmax()]
    assert best["Run"] == "R3"
    assert best["Recorded note"] == "contamination"


def test_variable_labels_cover_every_default(dasgip_obs):
    from src.dashboard.comparison import DEFAULT_VARIABLES

    labels = variable_labels()
    for variable in DEFAULT_VARIABLES:
        assert variable in labels, f"{variable} has no human label"
        assert not dasgip_obs[dasgip_obs["variable"] == variable].empty
