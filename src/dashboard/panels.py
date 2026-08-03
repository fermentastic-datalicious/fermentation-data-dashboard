"""What the drill-down plots, declared as data rather than code.

One panel per unit-coherent group of variables. Adding a variable is a list
entry here, not a new function anywhere.

Two rules shape the grouping:

- **No dual axes.** Two measures on two y-scales invite the reader to compare
  magnitudes that were never comparable. pH and cumulative base dosing are a
  natural pair conceptually and a terrible pair on one plot (6.8 against 2.0),
  so they get separate panels and share the x-axis instead.
- **Sparse sources draw as markers.** Seven HPLC injections are not a line.
  Joining them implies measurements between the samples that nobody took.

The biomass panel is the deliberate exception to unit-coherence: OD600, dry
cell weight and permittivity carry three different units, but all three are
biomass readings and their *divergence* is the point. A dielectric probe reads
viable cell volume while OD and DCW read everything including dead cells, so
when those traces separate, viability is falling. That is only visible if they
share one axis, so the units go in the trace labels instead.
"""

from dataclasses import dataclass

from ..ingestion.schema import CANONICAL_UNITS

# Categorical slots 1-3 of the validated palette, in fixed order. Never cycled,
# never reordered -- the ordering is the colorblind-safety mechanism, not taste.
# Validated light-mode: worst adjacent CVD dE 9.2, normal-vision dE 27.6.
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a")  # blue, orange, aqua

# Aqua sits at 2.74:1 on the light surface, below the 3:1 mark floor. That is
# legal only with a relief channel, so any trace wearing it must be directly
# labelled. It lands on permittivity, which is the trace the contamination
# story depends on -- it should be labelled regardless.
LOW_CONTRAST_COLORS = frozenset({"#1baf7a"})

# Comparison view. Only two colours are ever used, whatever the cohort size:
# the run under inspection, and everything else. That is what keeps the
# palette's series cap from binding -- a colour-coded overlay tops out at three
# runs, because a fourth slot puts yellow beside orange and they fail the
# normal-vision floor at dE 13.7 against a floor of 15.
HIGHLIGHT_COLOR = SERIES_COLORS[0]
COHORT_COLOR = "#898781"
COHORT_OPACITY = 0.45

# Chart chrome. Muted grid and axes so the data reads first.
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"


@dataclass(frozen=True)
class Trace:
    variable: str
    source: str
    label: str
    mode: str = "lines"  # "lines" for continuous sources, "markers" for sparse

    @property
    def unit(self) -> str:
        return CANONICAL_UNITS[self.variable]

    @property
    def legend_label(self) -> str:
        """Label carrying its own unit, so a shared axis stays honest."""
        return f"{self.label} ({self.unit})" if self.unit else self.label


@dataclass(frozen=True)
class Panel:
    title: str
    axis_label: str
    traces: tuple[Trace, ...]

    @property
    def is_multi_series(self) -> bool:
        return len(self.traces) > 1


# Panels shown by default, top to bottom. Ordered so the biological story reads
# first and the equipment that produced it reads last.
MAIN_PANELS = (
    Panel(
        title="Biomass",
        axis_label="OD600 · g/L · pF/cm",
        traces=(
            Trace("od600", "offline", "OD600", mode="markers"),
            Trace("dcw", "offline", "Dry cell weight", mode="markers"),
            Trace("permittivity", "capacitance", "Permittivity", mode="lines"),
        ),
    ),
    Panel(
        title="Substrate and product",
        axis_label="g/L",
        traces=(
            Trace("glucose", "analytical", "Glucose", mode="markers"),
            Trace("product", "analytical", "Product", mode="markers"),
        ),
    ),
    Panel(
        title="Dissolved oxygen",
        axis_label="% saturation",
        traces=(Trace("DO", "bioreactor", "DO"),),
    ),
    Panel(
        title="pH",
        axis_label="pH",
        traces=(Trace("pH", "bioreactor", "pH"),),
    ),
    Panel(
        title="Base added",
        axis_label="mL (cumulative)",
        traces=(Trace("base_added", "bioreactor", "Base added"),),
    ),
    Panel(
        title="Off-gas",
        axis_label="% of gas stream",
        traces=(
            Trace("offgas_co2", "offgas", "CO2"),
            Trace("offgas_o2", "offgas", "O2"),
        ),
    ),
)

# Equipment actuators. Real but rarely the reason someone opened the run, so
# they live behind a toggle. Each gets its own panel because rpm, L/min, bar
# and degC share no scale worth putting on one axis.
ACTUATOR_PANELS = (
    Panel("Agitation", "rpm", (Trace("agitation", "bioreactor", "Agitation"),)),
    Panel("Gas flow", "L/min", (Trace("gas_flow", "bioreactor", "Gas flow"),)),
    Panel("Pressure", "bar", (Trace("pressure", "bioreactor", "Pressure"),)),
    Panel("Temperature", "degC", (Trace("temperature", "bioreactor", "Temperature"),)),
)


def variable_labels() -> dict[str, str]:
    """Canonical variable -> the human label already given in the panel specs.

    Derived rather than retyped, so a variable cannot end up called one thing
    in the drill-down and another in the comparison view.
    """
    return {
        trace.variable: trace.label
        for panel in MAIN_PANELS + ACTUATOR_PANELS
        for trace in panel.traces
    }


def trace_color(index: int) -> str:
    """Slot colour by position within a panel, assigned in fixed order."""
    return SERIES_COLORS[index % len(SERIES_COLORS)]


def panels_with_data(panels: tuple[Panel, ...], available: set[str]) -> list[Panel]:
    """Drop traces, and then panels, that a given run has no data for.

    Ambr runs carry no `base_added` -- that vessel has no base pump logged --
    so the panel disappears for those runs rather than rendering an empty box.
    A literature-derived run later on will drop far more.
    """
    kept = []
    for panel in panels:
        traces = tuple(t for t in panel.traces if t.variable in available)
        if traces:
            kept.append(Panel(panel.title, panel.axis_label, traces))
    return kept
