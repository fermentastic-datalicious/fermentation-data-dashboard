"""Tests for the data layer behind the drill-down.

No browser here. What is worth testing is the part that fails silently: a
panel naming a variable that does not exist renders an empty box rather than
raising, and nobody notices until someone looks at the screenshot.
"""

import pandas as pd
import pytest

from src.dashboard.bootstrap import database_is_ready, ensure_database
from src.dashboard.charts import (
    MIN_LABEL_GAP_PX,
    PLOT_AREA_PX,
    WEBGL_THRESHOLD,
    build_trend_figure,
)
from src.dashboard.data import source_provenance
from src.dashboard.panels import (
    ACTUATOR_PANELS,
    LOW_CONTRAST_COLORS,
    MAIN_PANELS,
    SERIES_COLORS,
    panels_with_data,
)
from src.ingestion.schema import CANONICAL_UNITS, SOURCES
from src.storage import DB_PATH, connect, read_observations

ALL_PANELS = MAIN_PANELS + ACTUATOR_PANELS


@pytest.fixture(scope="module")
def r1() -> pd.DataFrame:
    with connect(DB_PATH) as conn:
        return read_observations(conn, run_ids=["R1"])


@pytest.fixture(scope="module")
def a1() -> pd.DataFrame:
    with connect(DB_PATH) as conn:
        return read_observations(conn, run_ids=["A1"])


# --- panel spec integrity ------------------------------------------------
# A typo in the panel spec produces an empty chart, not an error. These catch
# it at test time instead.


@pytest.mark.parametrize("panel", ALL_PANELS, ids=lambda p: p.title)
def test_panel_variables_are_registered(panel):
    for trace in panel.traces:
        assert trace.variable in CANONICAL_UNITS, f"{trace.variable} is not a canonical variable"


@pytest.mark.parametrize("panel", ALL_PANELS, ids=lambda p: p.title)
def test_panel_sources_are_real(panel):
    for trace in panel.traces:
        assert trace.source in SOURCES


@pytest.mark.parametrize("panel", ALL_PANELS, ids=lambda p: p.title)
def test_panel_units_come_from_the_registry(panel):
    """Units are never retyped into the panel spec -- they resolve from schema."""
    for trace in panel.traces:
        assert trace.unit == CANONICAL_UNITS[trace.variable]


def test_no_variable_appears_in_two_panels():
    seen = [t.variable for panel in ALL_PANELS for t in panel.traces]
    assert len(seen) == len(set(seen)), "a variable is plotted twice"


# --- graceful degradation ------------------------------------------------


def test_ambr_run_drops_the_base_panel(a1):
    """Ambr vessels log no base pump, so the panel disappears rather than
    rendering an empty box."""
    available = set(a1["variable"])
    assert "base_added" not in available

    titles = [p.title for p in panels_with_data(MAIN_PANELS, available)]
    assert "Base added" not in titles
    assert "pH" in titles, "dropping one panel must not take its neighbours"


def test_dasgip_run_keeps_every_main_panel(r1):
    titles = [p.title for p in panels_with_data(MAIN_PANELS, set(r1["variable"]))]
    assert titles == [p.title for p in MAIN_PANELS]


def test_a_run_with_no_data_yields_no_panels():
    assert panels_with_data(MAIN_PANELS, set()) == []


def test_empty_panel_list_still_builds_a_figure():
    assert len(build_trend_figure(pd.DataFrame(), []).data) == 0


# --- rendering choices ---------------------------------------------------


def test_sparse_sources_draw_as_markers_not_lines(r1):
    """Seven HPLC injections joined by a line would assert values nobody
    measured."""
    figure = build_trend_figure(r1, panels_with_data(MAIN_PANELS, set(r1["variable"])))
    sparse = [t for t in figure.data if t.name.startswith(("Glucose", "Product", "OD600", "Dry cell"))]
    assert sparse, "expected the sample-triggered traces to be present"
    assert all(t.mode == "markers" for t in sparse)


def test_large_traces_use_webgl(a1):
    """A1 off-gas is ~11.5k points per variable; SVG stalls, WebGL does not."""
    figure = build_trend_figure(a1, panels_with_data(MAIN_PANELS, set(a1["variable"])))
    for trace in figure.data:
        expected = "Scattergl" if len(trace.x) > WEBGL_THRESHOLD else "Scatter"
        assert type(trace).__name__ == expected, f"{trace.name} n={len(trace.x)}"


def test_figure_carries_no_legend(r1):
    """Slot colours restart per panel, so one shared legend showed blue against
    both OD600 and Glucose as though they were the same series. Identity is
    carried by end-labels instead."""
    figure = build_trend_figure(r1, panels_with_data(MAIN_PANELS, set(r1["variable"])))
    assert figure.layout.showlegend is False
    assert all(trace.showlegend is False for trace in figure.data)


def test_every_multi_series_trace_is_directly_labelled(r1):
    """With no legend, an unlabelled trace in a multi-series panel has no
    identity at all."""
    panels = panels_with_data(MAIN_PANELS, set(r1["variable"]))
    figure = build_trend_figure(r1, panels)
    labelled = {a.text for a in figure.layout.annotations}

    for panel in panels:
        if not panel.is_multi_series:
            continue
        for trace in panel.traces:
            assert trace.label in labelled, f"{trace.label} has no identity"


def test_low_contrast_series_are_directly_labelled(r1):
    """Aqua sits at 2.74:1 on the light surface, below the 3:1 mark floor.
    That is legal only with a relief channel, so it must carry a direct label."""
    panels = panels_with_data(MAIN_PANELS, set(r1["variable"]))
    figure = build_trend_figure(r1, panels)
    labelled = {a.text for a in figure.layout.annotations}

    for panel in panels:
        for index, trace in enumerate(panel.traces):
            if SERIES_COLORS[index % len(SERIES_COLORS)] in LOW_CONTRAST_COLORS:
                assert trace.label in labelled, f"{trace.label} needs a direct label"


def test_colliding_end_labels_are_pushed_apart():
    """On R3 permittivity finishes at 11.0 and dry cell weight at 9.7; their
    labels printed on top of each other before this was handled."""
    with connect(DB_PATH) as conn:
        r3 = read_observations(conn, run_ids=["R3"])

    panels = panels_with_data(MAIN_PANELS, set(r3["variable"]))
    figure = build_trend_figure(r3, panels)

    biomass_panel = next(p for p in panels if p.title == "Biomass")
    labels = {t.label for t in biomass_panel.traces}
    annotations = [a for a in figure.layout.annotations if a.text in labels]
    assert len(annotations) == len(labels)

    # Reproduce the placement model: depth downward from the top of the plot
    # area, after the nudge each annotation actually received.
    values = r3[r3["variable"].isin({t.variable for t in biomass_panel.traces})]["value"]
    low, high = float(values.min()), float(values.max())
    span = (high - low) or 1.0

    depths = sorted(
        (1 - (a.y - low) / span) * PLOT_AREA_PX - (a.yshift or 0) for a in annotations
    )
    gaps = [b - a for a, b in zip(depths, depths[1:])]
    assert all(gap >= MIN_LABEL_GAP_PX - 1e-6 for gap in gaps), f"labels overlap: {gaps}"


def test_well_separated_labels_are_left_alone():
    """The nudge only applies where labels would actually collide."""
    with connect(DB_PATH) as conn:
        r1 = read_observations(conn, run_ids=["R1"])

    panels = [p for p in panels_with_data(MAIN_PANELS, set(r1["variable"]))
              if p.title == "Substrate and product"]
    figure = build_trend_figure(r1, panels)

    # Glucose ends at 0 and product near 6.2 -- far apart, so no shifting.
    assert all((a.yshift or 0) == 0 for a in figure.layout.annotations if a.text)


def test_palette_slots_are_assigned_in_fixed_order():
    """The slot ordering is the colourblind-safety mechanism, not taste."""
    assert SERIES_COLORS == ("#2a78d6", "#eb6834", "#1baf7a")
    assert LOW_CONTRAST_COLORS <= set(SERIES_COLORS)


def test_x_axis_toggle_changes_the_plotted_values(r1):
    panels = panels_with_data(MAIN_PANELS, set(r1["variable"]))
    elapsed = build_trend_figure(r1, panels, x_axis="elapsed_h")
    clock = build_trend_figure(r1, panels, x_axis="timestamp")
    assert elapsed.data[0].x[0] != clock.data[0].x[0]


# --- provenance ----------------------------------------------------------


def test_provenance_reports_every_source_with_its_own_cadence(r1):
    provenance = source_provenance(r1)
    assert len(provenance) == r1["source"].nunique()
    assert (provenance["Points"] > 0).all()
    # Cadence is measured per variable, not pooled across a source -- pooling
    # would report every source as logging instantaneously.
    assert not provenance["Cadence"].str.startswith("0 s").any()


# --- first boot ----------------------------------------------------------


def test_ensure_database_builds_from_nothing(tmp_path):
    db = tmp_path / "nested" / "runs.db"
    assert not database_is_ready(db)

    ensure_database(db)

    assert database_is_ready(db)
    with connect(db) as conn:
        assert len(read_observations(conn, run_ids=["R1"])) > 0


def test_ensure_database_is_idempotent(tmp_path):
    db = tmp_path / "runs.db"
    ensure_database(db)
    first = db.stat().st_mtime_ns

    ensure_database(db)  # must not rebuild

    assert db.stat().st_mtime_ns == first
