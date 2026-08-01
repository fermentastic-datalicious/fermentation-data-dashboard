"""Auxiliary online sensors: off-gas analyzer and capacitance probe.

Both are continuous but independently logged from the bioreactor control
system -- own timestamp base, own start lag, own cadence -- so joining
them back to the bioreactor timeline is a genuine ingestion problem
rather than a simple concat.
"""

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .process_model import RunParams

AMBIENT_O2_PCT = 20.9
AMBIENT_CO2_PCT = 0.04
CAP_SLOPE = 1.8  # pF/cm per g/L viable biomass


def write_offgas_csv(
    df: pd.DataFrame, params: RunParams, out_dir: Path, cadence_min: float = 0.5, start_lag_min: float = 5.0
) -> Path:
    """BlueSens-style off-gas CO2/O2 analyzer export (semicolon-delimited)."""
    rng = np.random.default_rng(params.seed + 5000)
    clock_offset_min = rng.uniform(-3.0, 3.0)  # unsynced analyzer clock vs bioreactor clock

    step_h = cadence_min / 60.0
    sample_h = np.arange(start_lag_min / 60.0, params.duration_h, step_h)
    our = np.interp(sample_h, df["elapsed_h"], df["OUR_gLh"])
    cer = np.interp(sample_h, df["elapsed_h"], df["CER_gLh"])
    flow = np.interp(sample_h, df["elapsed_h"], df["gas_flow_Lpm"])

    o2_pct = AMBIENT_O2_PCT - (our / np.maximum(flow, 0.1)) * 0.9
    co2_pct = AMBIENT_CO2_PCT + (cer / np.maximum(flow, 0.1)) * 1.1

    timestamps = [
        params.start_time + timedelta(hours=float(h), minutes=clock_offset_min) for h in sample_h
    ]

    rows = pd.DataFrame(
        {
            "Time_stamp": [t.strftime("%Y-%m-%d %H:%M:%S") for t in timestamps],
            "CO2_percent": np.clip(co2_pct + rng.normal(0, 0.05, len(sample_h)), 0, None).round(3),
            "O2_percent": np.clip(o2_pct + rng.normal(0, 0.1, len(sample_h)), 0, None).round(3),
            "Flow_In_Lpm": np.clip(flow + rng.normal(0, 0.02, len(sample_h)), 0, None).round(3),
        }
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{params.run_id}_offgas.csv"
    rows.to_csv(path, index=False, sep=";")
    return path


def write_capacitance_csv(
    df: pd.DataFrame, params: RunParams, out_dir: Path, cadence_min: float = 2.0, start_lag_min: float = 8.0
) -> Path:
    """Capacitance/dielectric probe export, own timestamp base."""
    rng = np.random.default_rng(params.seed + 6000)
    clock_offset_min = rng.uniform(-4.0, 4.0)

    step_h = cadence_min / 60.0
    sample_h = np.arange(start_lag_min / 60.0, params.duration_h, step_h)
    xv = np.interp(sample_h, df["elapsed_h"], df["biomass_viable_gL"])
    permittivity = CAP_SLOPE * xv

    timestamps = [
        params.start_time + timedelta(hours=float(h), minutes=clock_offset_min) for h in sample_h
    ]

    rows = pd.DataFrame(
        {
            "Timestamp": [t.strftime("%Y-%m-%d %H:%M:%S") for t in timestamps],
            "Permittivity_pF_cm": np.clip(permittivity + rng.normal(0, 0.05, len(sample_h)), 0, None).round(3),
            "Frequency_kHz": 1000,
        }
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{params.run_id}_capacitance.csv"
    rows.to_csv(path, index=False)
    return path
