"""Vendor-style bioreactor control system file writers.

Two file patterns are generated, matching common real-world exports:
- DASGIP-style: one CSV per vessel, with a small metadata header block.
- Ambr-style: multiple vessels sharing one wide CSV, one row per timestamp.
"""

import csv
from pathlib import Path

import numpy as np
import pandas as pd

from .process_model import RunParams


def _add_noise(rng: np.random.Generator, series: pd.Series, std: float) -> pd.Series:
    return series + rng.normal(0, std, size=len(series))


def write_dasgip_file(df: pd.DataFrame, params: RunParams, out_dir: Path) -> Path:
    rng = np.random.default_rng(params.seed + 1000)
    out = pd.DataFrame(
        {
            "Time [h]": df["elapsed_h"].round(4),
            "Timestamp": df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "PV_pH": _add_noise(rng, df["pH"], 0.01).round(3),
            "PV_DO2 [%]": _add_noise(rng, df["DO_pct"], 0.3).clip(0, 100).round(2),
            "PV_Temp [degC]": _add_noise(rng, df["temp_C"], 0.02).round(2),
            "PV_Stirrer [rpm]": _add_noise(rng, df["agitation_rpm"], 2.0).round(1),
            "PV_Gasflow [L/h]": _add_noise(rng, df["gas_flow_Lpm"] * 60.0, 0.5).round(2),
            "PV_Pressure [bar]": _add_noise(rng, df["pressure_bar"], 0.005).round(4),
            "Base_Total [mL]": df["cum_base_mL"].round(2),
        }
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{params.run_id}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["# DASGIP Control System Export"])
        writer.writerow([f"# Batch ID: {params.run_id}"])
        writer.writerow([f"# Vessel: {params.vessel_id}"])
        writer.writerow([f"# Strain: {params.strain}"])
        writer.writerow([f"# Export Date: {df['timestamp'].iloc[-1].strftime('%Y-%m-%d %H:%M:%S')}"])
    out.to_csv(path, mode="a", index=False)
    return path


def write_ambr_file(
    dfs: list[pd.DataFrame], params_list: list[RunParams], out_dir: Path, batch_id: str, resample_every: int = 2
) -> Path:
    merged = None
    for df, params in zip(dfs, params_list):
        rng = np.random.default_rng(params.seed + 2000)
        sampled = df.iloc[::resample_every].reset_index(drop=True)
        prefix = params.vessel_id
        block = pd.DataFrame(
            {
                "Timestamp": sampled["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
                f"{prefix}_pH": _add_noise(rng, sampled["pH"], 0.01).round(3),
                f"{prefix}_DO_pct": _add_noise(rng, sampled["DO_pct"], 0.3).clip(0, 100).round(2),
                f"{prefix}_Temp_C": _add_noise(rng, sampled["temp_C"], 0.02).round(2),
                f"{prefix}_Stirrer_rpm": _add_noise(rng, sampled["agitation_rpm"], 2.0).round(1),
                f"{prefix}_Gasflow_Lpm": _add_noise(rng, sampled["gas_flow_Lpm"], 0.01).round(3),
                f"{prefix}_Pressure_bar": _add_noise(rng, sampled["pressure_bar"], 0.005).round(4),
            }
        )
        if merged is None:
            merged = block
        else:
            merged = merged.merge(block, on="Timestamp", how="outer")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{batch_id}_ambr.csv"
    merged.to_csv(path, index=False)
    return path
