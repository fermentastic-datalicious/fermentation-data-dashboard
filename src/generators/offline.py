"""Manual offline biomass measurements (OD600, dry cell weight).

No native file format in real workflows -- simulated as a simple
hand-entered spreadsheet-style CSV, with the mild messiness (occasional
blank note, one missing DCW value) typical of manual entry.
"""

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .process_model import RunParams

OD_SLOPE = 3.0  # OD600 per g/L total biomass
OPERATORS = ["JL", "MK", "RT"]
NOTES_POOL = ["", "", "", "", "foaming observed", "sample slightly delayed", "duplicate reading taken"]


def _interp(df: pd.DataFrame, col: str, t_h: float) -> float:
    return float(np.interp(t_h, df["elapsed_h"], df[col]))


def write_biomass_csv(df: pd.DataFrame, params: RunParams, sample_times_h: list[float], out_dir: Path) -> Path:
    rng = np.random.default_rng(params.seed + 4000)
    rows = []
    for i, t_h in enumerate(sample_times_h, start=1):
        draw_time = params.start_time + timedelta(hours=t_h, minutes=float(rng.uniform(0, 10)))
        true_biomass = max(0.0, _interp(df, "biomass_total_gL", t_h))
        true_od = true_biomass * OD_SLOPE

        dilution = 1.0
        for candidate in (5.0, 20.0, 50.0, 100.0):
            if true_od / dilution > 0.8:
                dilution = candidate
        measured_od = (true_od / dilution) * rng.normal(1.0, 0.04)
        od_corrected = measured_od * dilution

        dcw = true_biomass * rng.normal(0.95, 0.05)
        dcw_str = "" if rng.random() < 0.02 else round(max(0.0, dcw), 3)

        rows.append(
            {
                "Date": draw_time.strftime("%Y-%m-%d"),
                "Time": draw_time.strftime("%H:%M"),
                "Run_ID": params.run_id,
                "Sample_ID": f"{params.run_id}-S{i:03d}",
                "OD600_Raw": round(max(0.0, measured_od), 3),
                "Dilution_Factor": dilution,
                "OD600_Corrected": round(max(0.0, od_corrected), 3),
                "DCW_g_L": dcw_str,
                "Operator": rng.choice(OPERATORS),
                "Notes": rng.choice(NOTES_POOL),
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{params.run_id}_biomass.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path
