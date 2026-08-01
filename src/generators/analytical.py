"""HPLC-style peak table generator (discrete, sample-triggered data).

Mimics a Chromeleon/Empower-style peak table export: one row per analyte
per injection, long format, tied to sample draw time rather than a
regular clock.
"""

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .process_model import RunParams

ANALYTES = {
    # name: (retention_time_min, area_slope, height_frac)
    "Glucose": (4.2, 5.0e4, 0.42),
    "Product": (9.5, 4.2e4, 0.38),
}


def generate_sample_schedule(
    params: RunParams, rng: np.random.Generator, interval_h: float = 6.0, jitter_min: float = 15.0
) -> list[float]:
    times = []
    t = 0.0
    while t <= params.duration_h:
        jitter = rng.normal(0, jitter_min / 60.0)
        times.append(max(0.0, round(t + jitter, 3)))
        t += interval_h
    return times


def _interp(df: pd.DataFrame, col: str, t_h: float) -> float:
    return float(np.interp(t_h, df["elapsed_h"], df[col]))


def write_hplc_style(df: pd.DataFrame, params: RunParams, sample_times_h: list[float], out_dir: Path) -> Path:
    rng = np.random.default_rng(params.seed + 3000)
    rows = []
    injection_id = 1
    for t_h in sample_times_h:
        draw_time = params.start_time + timedelta(hours=t_h)
        prep_lag_h = rng.uniform(1.0, 3.0)  # sample prep + instrument queue delay
        injection_time = draw_time + timedelta(hours=prep_lag_h)

        conc_by_analyte = {
            "Glucose": max(0.0, _interp(df, "substrate_gL", t_h)),
            "Product": max(0.0, _interp(df, "product_gL", t_h)),
        }
        for name, (rt, area_slope, height_frac) in ANALYTES.items():
            conc = conc_by_analyte[name]
            conc_noisy = max(0.0, conc * rng.normal(1.0, 0.04))
            area = max(0.0, area_slope * conc_noisy * rng.normal(1.0, 0.02))
            height = max(0.0, area * height_frac * rng.normal(1.0, 0.03))
            rt_actual = rt + rng.normal(0, 0.02)
            rows.append(
                {
                    "Injection_ID": injection_id,
                    "Sample_Name": f"{params.run_id}-S{injection_id:03d}",
                    "Run_ID": params.run_id,
                    "Injection_DateTime": injection_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "Peak_Name": name,
                    "RT_min": round(rt_actual, 3),
                    "Area": round(area, 1),
                    "Height": round(height, 1),
                    "Amount": round(conc_noisy, 4),
                    "Units": "g/L",
                    "Method": "HPLC-RID-Organics-v3",
                }
            )
        injection_id += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{params.run_id}_hplc.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path
