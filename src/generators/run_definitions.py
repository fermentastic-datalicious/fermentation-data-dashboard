"""Roster of synthetic runs used to generate the demo dataset.

R1-R3: DASGIP-style system, one vessel per file, simple batch mode.
R3 includes a mid-run contamination anomaly (DO crash, growth stall) so
the dashboard has something interesting to flag.

A1-A4: Ambr-style parallel mini-bioreactor system, fed-batch mode, all
four vessels logged together in one multi-vessel file (a small DoE-style
screen with mu_max jitter across vessels).
"""

from datetime import datetime

import numpy as np

from .process_model import RunParams

_BASE_START = datetime(2026, 3, 2, 8, 0, 0)


def _jitter(rng: np.random.Generator, base: float, pct: float) -> float:
    return base * (1 + rng.normal(0, pct))


def dasgip_runs() -> list[RunParams]:
    runs = []
    configs = [
        ("R1", "V1", 42, None, None),
        ("R2", "V2", 43, None, None),
        ("R3", "V3", 44, "contamination", 20.0),
    ]
    for run_id, vessel, seed, anomaly, anomaly_time in configs:
        rng = np.random.default_rng(seed)
        runs.append(
            RunParams(
                run_id=run_id,
                vessel_id=vessel,
                system="dasgip",
                mode="batch",
                strain="E. coli BL21",
                start_time=_BASE_START,
                duration_h=36.0,
                volume_L=5.0,
                seed=seed,
                mu_max=_jitter(rng, 0.35, 0.05),
                Yxs=_jitter(rng, 0.5, 0.04),
                S0=_jitter(rng, 20.0, 0.05),
                anomaly=anomaly,
                anomaly_time_h=anomaly_time,
            )
        )
    return runs


def ambr_runs() -> list[RunParams]:
    runs = []
    configs = [
        ("A1", "Vessel_1", 51),
        ("A2", "Vessel_2", 52),
        ("A3", "Vessel_3", 53),
        ("A4", "Vessel_4", 54),
    ]
    for run_id, vessel, seed in configs:
        rng = np.random.default_rng(seed)
        runs.append(
            RunParams(
                run_id=run_id,
                vessel_id=vessel,
                system="ambr",
                mode="fed-batch",
                strain="CHO-K1",
                start_time=_BASE_START,
                duration_h=96.0,
                volume_L=0.25,
                seed=seed,
                mu_max=_jitter(rng, 0.10, 0.15),
                Yxs=_jitter(rng, 0.45, 0.05),
                S0=_jitter(rng, 8.0, 0.05),
                feed_start_h=24.0,
                feed_rate_Lph=0.001,
                feed_conc_gL=400.0,
                DO_setpoint=40.0,
                pH_setpoint=7.0,
                Temp_setpoint=36.8,
            )
        )
    return runs


def all_runs() -> list[RunParams]:
    return dasgip_runs() + ambr_runs()
