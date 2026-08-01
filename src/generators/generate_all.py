"""Orchestrator: simulate every run and write all synthetic source files.

Run with:  python -m src.generators.generate_all
"""

from pathlib import Path

import numpy as np
import pandas as pd

from .analytical import generate_sample_schedule, write_hplc_style
from .auxiliary import write_capacitance_csv, write_offgas_csv
from .bioreactor import write_ambr_file, write_dasgip_file
from .offline import write_biomass_csv
from .process_model import RunParams, simulate_run
from .run_definitions import ambr_runs, dasgip_runs

DATA_RAW = Path(__file__).resolve().parents[2] / "data" / "raw"


def _write_sample_triggered_sources(df: pd.DataFrame, params: RunParams) -> None:
    rng = np.random.default_rng(params.seed + 7000)
    sample_times_h = generate_sample_schedule(params, rng)
    write_hplc_style(df, params, sample_times_h, DATA_RAW / "analytical")
    write_biomass_csv(df, params, sample_times_h, DATA_RAW / "offline")


def _write_auxiliary_sources(df: pd.DataFrame, params: RunParams) -> None:
    write_offgas_csv(df, params, DATA_RAW / "auxiliary" / "offgas")
    write_capacitance_csv(df, params, DATA_RAW / "auxiliary" / "capacitance")


def main() -> None:
    manifest_rows = []

    # DASGIP-style: one vessel per file
    dasgip_dfs = []
    for params in dasgip_runs():
        df = simulate_run(params)
        dasgip_dfs.append(df)
        write_dasgip_file(df, params, DATA_RAW / "bioreactor" / "dasgip")
        _write_sample_triggered_sources(df, params)
        _write_auxiliary_sources(df, params)
        manifest_rows.append(_manifest_row(params, df, "dasgip"))

    # Ambr-style: all vessels combined into one multi-vessel file
    ambr_params = ambr_runs()
    ambr_dfs = [simulate_run(p) for p in ambr_params]
    write_ambr_file(ambr_dfs, ambr_params, DATA_RAW / "bioreactor" / "ambr", batch_id="AMBR-BATCH-01")
    for df, params in zip(ambr_dfs, ambr_params):
        _write_sample_triggered_sources(df, params)
        _write_auxiliary_sources(df, params)
        manifest_rows.append(_manifest_row(params, df, "ambr"))

    manifest_path = DATA_RAW / "run_manifest.csv"
    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False)
    print(f"Wrote {len(manifest_rows)} runs. Manifest: {manifest_path}")


def _manifest_row(params: RunParams, df: pd.DataFrame, system: str) -> dict:
    return {
        "run_id": params.run_id,
        "vessel_id": params.vessel_id,
        "system": system,
        "mode": params.mode,
        "strain": params.strain,
        "start_time": params.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_h": params.duration_h,
        "volume_L": params.volume_L,
        "anomaly": params.anomaly or "",
        "final_biomass_total_gL": round(float(df["biomass_total_gL"].iloc[-1]), 3),
        "final_product_gL": round(float(df["product_gL"].iloc[-1]), 3),
    }


if __name__ == "__main__":
    main()
