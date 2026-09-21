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


# --- run-level provenance and conditions ---------------------------------
# Observations say what was measured; these say where a run came from and
# under what conditions it ran. Both are needed before a literature-derived
# run can sit in the same database as a generated one.

# Every run carries exactly one of these, set explicitly by whichever path
# created it. There is deliberately no default: a default is how an unflagged
# row gets in, and synthetic and literature runs must never be silently mixed.
DATA_ORIGINS = ("synthetic", "literature")

# `not_reported` is a finding -- the paper was read and does not state it.
# `not_checked` means nobody looked. Collapsing the two would make a gap in a
# paper indistinguishable from a gap in the transcription.
CONDITION_STATUSES = ("reported", "not_reported", "not_checked")

RUN_CONDITION_COLUMNS = [
    "run_id",
    "field",
    "value_text",
    "value_num",
    "unit",
    "status",
    "evidence",
]

PUBLICATION_COLUMNS = [
    "publication_id",
    "doi",
    "first_author",
    "year",
    "title",
    "journal",
    "peer_reviewed",
    "tier",
    "criteria_version",
    "verified_date",
    "report_path",
]

# Canonical condition fields, grouped as in the screening protocol's
# conditions checklist. Stored long in `run_conditions`, one row per field, so
# adding a field is a registry entry rather than a migration -- the list is
# expected to change once real papers are transcribed against it.
#
# Values are kept in the paper's own words and units. Biomass in particular is
# reported as OD600, gDCW/L or g wet weight/L, and those are not
# interchangeable; any conversion happens at read time, never at entry.
CONDITION_FIELDS = {
    # organism and inoculum
    "organism": "organism",
    "strain": "organism",
    "genotype": "organism",
    "inoculum_density": "organism",
    "inoculum_volume_pct": "organism",
    # vessel and scale
    "vessel_type": "vessel",
    "working_volume": "vessel",
    "total_volume": "vessel",
    "mode": "vessel",
    # medium
    "medium_type": "medium",
    "carbon_source": "medium",
    "carbon_source_initial_conc": "medium",
    "nitrogen_source": "medium",
    "key_salts": "medium",
    "trace_elements": "medium",
    "antifoam": "medium",
    # feed (fed-batch and continuous only)
    "feed_strategy": "feed",
    "feed_composition": "feed",
    "feed_concentration": "feed",
    "feed_start_time": "feed",
    "feed_rate": "feed",
    "mu_setpoint": "feed",
    "dilution_rate": "feed",
    # control setpoints
    "temperature_setpoint": "control",
    "ph_setpoint": "control",
    "ph_titrant": "control",
    "do_setpoint": "control",
    "do_cascade": "control",
    "aeration_rate": "control",
    "agitation": "control",
    "pressure": "control",
    # induction (recombinant only)
    "inducer": "induction",
    "inducer_concentration": "induction",
    "induction_point": "induction",
    "post_induction_temperature": "induction",
    # outcomes
    "final_biomass": "outcome",
    "max_biomass": "outcome",
    "product_titer": "outcome",
    "yield_product_substrate": "outcome",
    "yield_product_biomass": "outcome",
    "volumetric_productivity": "outcome",
    "mu_max": "outcome",
    "process_duration": "outcome",
    # statistics
    "biological_replicates": "statistics",
    "error_bars": "statistics",
}


def validate_run_conditions(conditions: pd.DataFrame) -> pd.DataFrame:
    """Enforce the screening protocol's evidence rule on transcribed conditions.

    The database repeats the status and evidence checks as CHECK constraints;
    doing them here as well means a bad transcription fails with a message
    naming the field, not a bare IntegrityError at insert.
    """
    missing = [c for c in RUN_CONDITION_COLUMNS if c not in conditions.columns]
    if missing:
        raise ValueError(f"run_conditions missing columns: {missing}")

    bad_fields = sorted(set(conditions["field"]) - set(CONDITION_FIELDS))
    if bad_fields:
        raise ValueError(f"unregistered condition fields: {bad_fields}")

    bad_statuses = sorted(set(conditions["status"]) - set(CONDITION_STATUSES))
    if bad_statuses:
        raise ValueError(f"unknown condition statuses: {bad_statuses}")

    duplicated = conditions[conditions.duplicated(["run_id", "field"], keep=False)]
    if not duplicated.empty:
        pairs = sorted(set(zip(duplicated["run_id"], duplicated["field"])))
        raise ValueError(f"condition recorded more than once: {pairs}")

    # A reported value with no citation is indistinguishable from an inferred
    # one, and an inferred value is a fabricated data point.
    reported = conditions[conditions["status"] == "reported"]
    for column in ("value_text", "evidence"):
        blank = reported[reported[column].isna() | (reported[column].astype(str).str.strip() == "")]
        if not blank.empty:
            pairs = sorted(zip(blank["run_id"], blank["field"]))
            raise ValueError(f"reported conditions without {column}: {pairs}")

    return conditions
