"""The common intermediate schema every source is normalized into.

One numeric measurement per row:

    run_id, timestamp, elapsed_h, source, variable, value, unit, source_file

Vendor column names are mapped to canonical variables through explicit tables
rather than pattern matching. It is more typing, but the mapping *is* the
deliverable -- it documents exactly what "pH" means across four instruments,
and it is the first thing a client asks to see.
"""

from dataclasses import dataclass

import pandas as pd

OBSERVATION_COLUMNS = [
    "run_id",
    "timestamp",
    "elapsed_h",
    "source",
    "variable",
    "value",
    "unit",
    "source_file",
]

SAMPLE_COLUMNS = [
    "sample_id",
    "run_id",
    "draw_time",
    "injection_time",
    "operator",
    "dilution_factor",
    "method",
    "notes",
]

SOURCES = ("bioreactor", "analytical", "offline", "offgas", "capacitance")

# Canonical unit per variable. Any parser emitting a variable must emit it in
# this unit -- that is the whole point of the layer.
CANONICAL_UNITS = {
    "pH": "",
    "DO": "%",
    "temperature": "degC",
    "agitation": "rpm",
    "gas_flow": "L/min",
    "pressure": "bar",
    "base_added": "mL",
    "glucose": "g/L",
    "product": "g/L",
    "glucose_peak_area": "counts",
    "product_peak_area": "counts",
    "glucose_rt": "min",
    "product_rt": "min",
    "od600": "",
    "dcw": "g/L",
    "offgas_co2": "%",
    "offgas_o2": "%",
    "offgas_flow": "L/min",
    "permittivity": "pF/cm",
}


@dataclass(frozen=True)
class ColumnMap:
    """Maps one vendor column onto a canonical variable.

    `scale` converts the vendor's unit into the canonical one -- the reason
    this field exists at all is DASGIP logging gas flow in L/h while Ambr logs
    L/min. Without the 1/60 the two systems are off by 60x on a shared axis.
    """

    variable: str
    scale: float = 1.0

    @property
    def unit(self) -> str:
        return CANONICAL_UNITS[self.variable]


# DASGIP-style export: units are baked into the column names.
DASGIP_COLUMNS = {
    "PV_pH": ColumnMap("pH"),
    "PV_DO2 [%]": ColumnMap("DO"),
    "PV_Temp [degC]": ColumnMap("temperature"),
    "PV_Stirrer [rpm]": ColumnMap("agitation"),
    "PV_Gasflow [L/h]": ColumnMap("gas_flow", scale=1.0 / 60.0),
    "PV_Pressure [bar]": ColumnMap("pressure"),
    "Base_Total [mL]": ColumnMap("base_added"),
}

# Ambr-style export: columns are "<Vessel_N>_<measurement>", so these keys are
# the suffix left after the vessel prefix is stripped.
AMBR_MEASUREMENTS = {
    "pH": ColumnMap("pH"),
    "DO_pct": ColumnMap("DO"),
    "Temp_C": ColumnMap("temperature"),
    "Stirrer_rpm": ColumnMap("agitation"),
    "Gasflow_Lpm": ColumnMap("gas_flow"),
    "Pressure_bar": ColumnMap("pressure"),
}

# HPLC peak table: one row per analyte per injection. The concentration is the
# headline number; retention time and peak area are kept as a QC trace so the
# chromatography can be sanity-checked rather than silently trusted.
HPLC_ANALYTES = {
    "Glucose": "glucose",
    "Product": "product",
}
HPLC_PEAK_COLUMNS = {
    "Amount": "",  # -> the analyte's own variable name
    "Area": "_peak_area",
    "RT_min": "_rt",
}

OFFLINE_COLUMNS = {
    "OD600_Corrected": ColumnMap("od600"),
    "DCW_g_L": ColumnMap("dcw"),
}

OFFGAS_COLUMNS = {
    "CO2_percent": ColumnMap("offgas_co2"),
    "O2_percent": ColumnMap("offgas_o2"),
    "Flow_In_Lpm": ColumnMap("offgas_flow"),
}

# Frequency_kHz is a fixed instrument setting, not a measurement -- dropped.
CAPACITANCE_COLUMNS = {
    "Permittivity_pF_cm": ColumnMap("permittivity"),
}


def empty_observations() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in OBSERVATION_COLUMNS})


def build_observations(
    run_id: str,
    timestamps: pd.Series,
    source: str,
    variable: str,
    values: pd.Series,
    unit: str,
    source_file: str,
) -> pd.DataFrame:
    """Assemble one variable's worth of observations, dropping unmeasured points.

    Rows with a null value are dropped rather than carried as NaN: a blank
    DCW cell in a hand-entered sheet means "not measured", and the long format
    represents that by the row's absence.
    """
    frame = pd.DataFrame(
        {
            "run_id": run_id,
            "timestamp": pd.to_datetime(timestamps).reset_index(drop=True),
            "elapsed_h": pd.NA,  # filled in by normalize, which knows run start times
            "source": source,
            "variable": variable,
            "value": pd.to_numeric(values, errors="coerce").reset_index(drop=True),
            "unit": unit,
            "source_file": source_file,
        }
    )
    return frame.dropna(subset=["value", "timestamp"])[OBSERVATION_COLUMNS]


def validate_observations(obs: pd.DataFrame) -> pd.DataFrame:
    """Fail loudly on anything that would quietly corrupt the dashboard later."""
    missing = [c for c in OBSERVATION_COLUMNS if c not in obs.columns]
    if missing:
        raise ValueError(f"observations missing columns: {missing}")

    bad_sources = sorted(set(obs["source"]) - set(SOURCES))
    if bad_sources:
        raise ValueError(f"unknown source values: {bad_sources}")

    bad_variables = sorted(set(obs["variable"]) - set(CANONICAL_UNITS))
    if bad_variables:
        raise ValueError(f"unregistered variables: {bad_variables}")

    # A variable carrying two different units is the exact bug this layer exists
    # to prevent, so check it explicitly instead of trusting the parsers.
    for variable, group in obs.groupby("variable"):
        units = set(group["unit"])
        expected = CANONICAL_UNITS[variable]
        if units != {expected}:
            raise ValueError(f"{variable} has units {sorted(units)}, expected {expected!r}")

    if obs["value"].isna().any():
        raise ValueError("observations contain null values")
    if obs["timestamp"].isna().any():
        raise ValueError("observations contain null timestamps")

    return obs
