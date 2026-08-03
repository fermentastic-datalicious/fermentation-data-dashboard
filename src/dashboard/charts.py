"""Builds the stacked trend figure.

One figure with shared x-axis rather than one figure per panel, because the
whole question a drill-down answers is "what else was happening at that
moment". Linked zoom and a unified crosshair only exist within a single
figure; separate figures would put the reader back to comparing by eye across
independently-scaled plots.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..ingestion.schema import CANONICAL_UNITS
from .panels import (
    BASELINE,
    COHORT_COLOR,
    COHORT_OPACITY,
    GRIDLINE,
    HIGHLIGHT_COLOR,
    INK_MUTED,
    INK_PRIMARY,
    INK_SECONDARY,
    LOW_CONTRAST_COLORS,
    SURFACE,
    Panel,
    trace_color,
    variable_labels,
)

# Above this many points a WebGL trace renders far more smoothly than SVG.
# Off-gas on a 96 h run is 11,510 points per variable; downsampling would have
# meant averaging away the DO crash this view exists to show, so the renderer
# changes instead of the data.
WEBGL_THRESHOLD = 2000

PANEL_HEIGHT_PX = 165
# Plot area inside one panel, once the subplot title and spacing are taken out.
# Only used to keep end-labels from printing on top of each other.
PLOT_AREA_PX = 120
MIN_LABEL_GAP_PX = 14

X_LABELS = {"elapsed_h": "Elapsed time (h)", "timestamp": "Clock time"}


def build_trend_figure(
    obs: pd.DataFrame, panels: list[Panel], x_axis: str = "elapsed_h"
) -> go.Figure:
    """Stack one subplot per panel, sharing the x-axis."""
    if not panels:
        return go.Figure()

    figure = make_subplots(
        rows=len(panels),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.055 / max(1, len(panels) / 4),
        subplot_titles=[p.title for p in panels],
    )

    for row, panel in enumerate(panels, start=1):
        to_label = []
        for index, spec in enumerate(panel.traces):
            series = obs[obs["variable"] == spec.variable].sort_values(x_axis)
            if series.empty:
                continue
            color = trace_color(index)
            figure.add_trace(
                _make_trace(series, spec, color, panel, x_axis),
                row=row,
                col=1,
            )
            # Direct labels carry identity here -- there is no legend. A single
            # trace is named by its panel title, so only multi-series panels
            # need labelling, plus any colour too faint against the surface to
            # identify a line on its own.
            if panel.is_multi_series or color in LOW_CONTRAST_COLORS:
                to_label.append((spec, series))

        _label_series_ends(figure, to_label, row, x_axis)

        figure.update_yaxes(
            title_text=panel.axis_label,
            title_font=dict(size=11, color=INK_SECONDARY),
            tickfont=dict(size=10, color=INK_MUTED),
            gridcolor=GRIDLINE,
            zeroline=False,
            linecolor=BASELINE,
            row=row,
            col=1,
        )

    _style_figure(figure, panels, x_axis)
    return figure


def build_comparison_figure(
    traces_by_variable: dict[str, dict[str, pd.DataFrame]],
    highlighted: str,
    sparse_variables: set[str] | None = None,
) -> go.Figure:
    """One run against the rest of its cohort, across several variables.

    The cohort is drawn first, in thin grey, so the highlighted run lands on
    top of it rather than behind. Grey is not a series colour -- it is
    deliberately anonymous, because the question is "does this run sit inside
    the others" and naming each of the others would answer a question nobody
    asked.

    Only two colours appear regardless of cohort size, which is the point: a
    colour-coded overlay tops out at three runs on this palette, and cohorts
    are already four.
    """
    variables = list(traces_by_variable)
    if not variables:
        return go.Figure()

    sparse_variables = sparse_variables or set()
    labels = variable_labels()
    figure = make_subplots(
        rows=len(variables),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.055 / max(1, len(variables) / 4),
        subplot_titles=[labels.get(v, v) for v in variables],
    )

    for row, variable in enumerate(variables, start=1):
        runs = traces_by_variable[variable]
        markers = variable in sparse_variables

        for run_id, frame in runs.items():
            if run_id == highlighted:
                continue
            figure.add_trace(
                _cohort_trace(frame, run_id, markers), row=row, col=1
            )

        focus = runs.get(highlighted)
        if focus is not None and not focus.empty:
            figure.add_trace(
                _highlight_trace(focus, highlighted, markers), row=row, col=1
            )
            last = focus.iloc[-1]
            figure.add_annotation(
                x=last["elapsed_h"],
                y=last["value"],
                text=highlighted,
                showarrow=False,
                xanchor="left",
                xshift=8,
                font=dict(size=11, color=HIGHLIGHT_COLOR),
                row=row,
                col=1,
            )

        figure.update_yaxes(
            title_text=CANONICAL_UNITS.get(variable, "") or None,
            title_font=dict(size=11, color=INK_SECONDARY),
            tickfont=dict(size=10, color=INK_MUTED),
            gridcolor=GRIDLINE,
            zeroline=False,
            linecolor=BASELINE,
            row=row,
            col=1,
        )

    _style_comparison(figure, variables)
    return figure


def _cohort_trace(frame: pd.DataFrame, run_id: str, markers: bool) -> go.Scatter:
    """One of the runs being compared against. Anonymous by design."""
    scatter = go.Scattergl if len(frame) > WEBGL_THRESHOLD else go.Scatter
    shared = dict(
        x=frame["elapsed_h"],
        y=frame["value"],
        name=run_id,
        showlegend=False,
        opacity=COHORT_OPACITY,
        # Kept out of hover: reading the crosshair should tell you about the
        # run you selected, not list every other run at that instant.
        hoverinfo="skip",
    )
    if markers:
        return go.Scatter(**shared, mode="markers", marker=dict(size=6, color=COHORT_COLOR))
    return scatter(**shared, mode="lines", line=dict(width=1, color=COHORT_COLOR))


def _highlight_trace(frame: pd.DataFrame, run_id: str, markers: bool) -> go.Scatter:
    scatter = go.Scattergl if len(frame) > WEBGL_THRESHOLD else go.Scatter
    shared = dict(
        x=frame["elapsed_h"],
        y=frame["value"],
        name=run_id,
        showlegend=False,
        hovertemplate=f"<b>{run_id}</b> %{{y:.3g}}<extra></extra>",
    )
    if markers:
        return go.Scatter(
            **shared,
            mode="markers",
            marker=dict(size=9, color=HIGHLIGHT_COLOR, line=dict(width=2, color=SURFACE)),
        )
    return scatter(**shared, mode="lines", line=dict(width=2, color=HIGHLIGHT_COLOR))


def _style_comparison(figure: go.Figure, variables: list[str]) -> None:
    figure.update_layout(
        height=PANEL_HEIGHT_PX * len(variables) + 120,
        margin=dict(l=70, r=90, t=40, b=45),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(
            family='system-ui, -apple-system, "Segoe UI", sans-serif',
            color=INK_PRIMARY,
        ),
        showlegend=False,
        hovermode="x unified",
        hoverlabel=dict(bgcolor=SURFACE, font_size=11),
    )
    figure.update_xaxes(
        showgrid=True, gridcolor=GRIDLINE, linecolor=BASELINE,
        tickfont=dict(size=10, color=INK_MUTED),
    )
    figure.update_xaxes(
        title_text=X_LABELS["elapsed_h"],
        title_font=dict(size=11, color=INK_SECONDARY),
        row=len(variables),
        col=1,
    )
    for annotation in figure.layout.annotations[: len(variables)]:
        annotation.update(font=dict(size=12, color=INK_PRIMARY), x=0, xanchor="left")


def _make_trace(
    series: pd.DataFrame, spec, color: str, panel: Panel, x_axis: str
) -> go.Scatter:
    markers_only = spec.mode == "markers"
    hover_unit = f" {spec.unit}" if spec.unit else ""
    common = dict(
        x=series[x_axis],
        y=series["value"],
        name=spec.legend_label,
        # No legend anywhere in this figure. One legend beside a six-panel
        # stack cannot sit next to the panel it describes, and it made the
        # palette look wrong: slot colours restart per panel, so the legend
        # showed blue against both "OD600" and "Glucose" as though they were
        # the same thing. Labels at the end of each line say it in place.
        showlegend=False,
        hovertemplate=f"<b>{spec.label}</b> %{{y:.3g}}{hover_unit}<extra></extra>",
    )

    if markers_only:
        # Sparse, sample-triggered data. Drawn as points because a line between
        # two HPLC injections six hours apart asserts values nobody measured.
        return go.Scatter(
            **common,
            mode="markers",
            marker=dict(size=9, color=color, line=dict(width=2, color=SURFACE)),
        )

    scatter = go.Scattergl if len(series) > WEBGL_THRESHOLD else go.Scatter
    return scatter(**common, mode="lines", line=dict(width=2, color=color))


def _label_series_ends(figure, to_label: list, row: int, x_axis: str) -> None:
    """Name each trace at its right-hand end, nudged apart where they collide.

    Two traces can finish at nearly the same value -- on the contaminated run
    permittivity lands on 11 and dry cell weight on 9.7 -- and their labels
    then print on top of each other. Since these labels are the only identity
    in the figure, an unreadable one loses the series entirely.

    Positions are resolved in pixels rather than data units so the minimum gap
    holds regardless of what each panel's axis happens to span.
    """
    if not to_label:
        return

    values = pd.concat([series["value"] for _, series in to_label])
    low, high = float(values.min()), float(values.max())
    span = (high - low) or 1.0

    placed = []
    for spec, series in to_label:
        last = series.iloc[-1]
        y = float(last["value"])
        # Distance down from the top of the plot area.
        depth = (1 - (y - low) / span) * PLOT_AREA_PX
        placed.append({"spec": spec, "x": last[x_axis], "y": y, "depth": depth})

    placed.sort(key=lambda entry: entry["depth"])
    for above, below in zip(placed, placed[1:]):
        crowding = MIN_LABEL_GAP_PX - (below["depth"] - above["depth"])
        if crowding > 0:
            below["depth"] += crowding

    for entry in placed:
        original = (1 - (entry["y"] - low) / span) * PLOT_AREA_PX
        figure.add_annotation(
            x=entry["x"],
            y=entry["y"],
            text=entry["spec"].label,
            showarrow=False,
            xanchor="left",
            xshift=8,
            # Plotly shifts upward on positive values; depth grows downward.
            yshift=original - entry["depth"],
            font=dict(size=10, color=INK_SECONDARY),
            row=row,
            col=1,
        )


def _style_figure(figure: go.Figure, panels: list[Panel], x_axis: str) -> None:
    figure.update_layout(
        height=PANEL_HEIGHT_PX * len(panels) + 120,
        # The right margin holds the end-labels now that the legend is gone.
        margin=dict(l=70, r=130, t=40, b=45),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(
            family='system-ui, -apple-system, "Segoe UI", sans-serif',
            color=INK_PRIMARY,
        ),
        showlegend=False,
        # One crosshair across every panel: the fastest way to answer "what was
        # the pH when DO crashed" without reading two charts.
        hovermode="x unified",
        hoverlabel=dict(bgcolor=SURFACE, font_size=11),
    )
    figure.update_xaxes(
        showgrid=True,
        gridcolor=GRIDLINE,
        linecolor=BASELINE,
        tickfont=dict(size=10, color=INK_MUTED),
    )
    figure.update_xaxes(
        title_text=X_LABELS.get(x_axis, x_axis),
        title_font=dict(size=11, color=INK_SECONDARY),
        row=len(panels),
        col=1,
    )
    # Subplot titles come out at 16px bold by default, which competes with the
    # data. Demote them to quiet section headings.
    for annotation in figure.layout.annotations[: len(panels)]:
        annotation.update(
            font=dict(size=12, color=INK_PRIMARY), x=0, xanchor="left"
        )
