"""Mechanistic ground-truth simulator for a single fermentation run.

This produces a fine-grained (1-minute) "true state" time series that all
of the per-source generators (bioreactor logger, HPLC, offline biomass,
off-gas/capacitance) sample from. Sharing one ground truth keeps the
synthetic sources internally consistent, the way real data from one
physical run would be.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class RunParams:
    run_id: str
    vessel_id: str
    system: str  # "dasgip" or "ambr"
    mode: str  # "batch" or "fed-batch"
    strain: str
    start_time: datetime
    duration_h: float
    volume_L: float
    seed: int

    # Growth / metabolism kinetics
    mu_max: float = 0.35  # 1/h
    Ks: float = 0.15  # g/L (substrate half-saturation)
    Yxs: float = 0.5  # gX/gS
    Yps_growth: float = 0.15  # gP/gX (growth-associated product)
    Yps_nongrowth: float = 0.02  # gP/gX/h (non-growth-associated)
    ms: float = 0.02  # gS/gX/h maintenance
    qO2max: float = 0.4  # gO2/gX/h at mu_max
    qO2_maint: float = 0.02  # gO2/gX/h maintenance respiration
    RQ: float = 1.0  # respiratory quotient, CER/OUR
    kd: float = 0.004  # 1/h baseline death rate

    S0: float = 20.0  # g/L initial substrate
    X0: float = 0.15  # g/L initial viable biomass

    # Control setpoints
    DO_setpoint: float = 40.0  # %
    DO_deadband: float = 3.0
    pH_setpoint: float = 6.8
    pH_deadband: float = 0.05
    Temp_setpoint: float = 37.0

    agit_min: float = 200.0  # rpm
    agit_max: float = 1200.0  # rpm
    flow_base: float = 0.8  # L/min
    pressure_base: float = 1.05  # bar (absolute)
    kLa_coeff: float = 0.006  # 1/h per rpm, linearized

    # Fed-batch feed (ignored if mode == "batch")
    feed_start_h: Optional[float] = None
    feed_rate_Lph: Optional[float] = None
    feed_conc_gL: Optional[float] = None

    # Anomaly injection
    anomaly: Optional[str] = None  # e.g. "contamination"
    anomaly_time_h: Optional[float] = None

    dt_h: float = 1.0 / 60.0  # 1-minute integration step


def _anomaly_effects(t_h: float, p: RunParams) -> tuple[float, float, float]:
    """Returns (death_rate_multiplier, extra_OUR_gLh, acid_rate_multiplier)."""
    if p.anomaly == "contamination" and p.anomaly_time_h is not None and t_h >= p.anomaly_time_h:
        onset_progress = min(1.0, (t_h - p.anomaly_time_h) / 4.0)  # ramps in over ~4h
        return 1.0 + 7.0 * onset_progress, 3.0 * onset_progress, 1.0 + 2.0 * onset_progress
    return 1.0, 0.0, 1.0


def simulate_run(p: RunParams) -> pd.DataFrame:
    rng = np.random.default_rng(p.seed)
    n_steps = int(p.duration_h / p.dt_h) + 1

    S = p.S0
    Xv = p.X0
    Xd = 0.0
    P = 0.0
    DO = p.DO_setpoint + 20.0  # starts above setpoint before demand ramps up
    pH = p.pH_setpoint
    Agit = p.agit_min
    CumBase = 0.0

    rows = []
    for i in range(n_steps):
        t_h = i * p.dt_h
        kd_mult, extra_OUR, acid_mult = _anomaly_effects(t_h, p)

        DO_limitation = min(1.0, max(0.0, DO / 20.0))
        mu = p.mu_max * (S / (p.Ks + S + 1e-9)) * DO_limitation
        kd_eff = p.kd * kd_mult

        qO2 = p.qO2max * (mu / p.mu_max) + p.qO2_maint
        OUR = qO2 * Xv + extra_OUR  # g O2 / L / h
        CER = p.RQ * OUR

        # DO cascade: agitation ramps to hold DO at setpoint, up to agit_max
        kLa = p.kLa_coeff * Agit  # 1/h, linearized mass transfer coefficient
        OUR_pct_h = OUR / 0.075  # g O2/L/h -> %sat/h, using ~7.5 mg/L at 100% sat
        dDO = (kLa * (100.0 - DO) - OUR_pct_h) * p.dt_h
        DO = float(np.clip(DO + dDO + rng.normal(0, 0.15), 0.0, 100.0))

        do_error = p.DO_setpoint - DO
        if do_error > p.DO_deadband:
            agit_target = p.agit_max
        elif do_error < -p.DO_deadband:
            agit_target = p.agit_min
        else:
            agit_target = Agit
        Agit = float(np.clip(Agit + (agit_target - Agit) * min(1.0, p.dt_h * 3.0), p.agit_min, p.agit_max))

        Flow = p.flow_base * (1 + 0.1 * (Agit - p.agit_min) / (p.agit_max - p.agit_min)) + rng.normal(0, 0.01)
        Pressure = p.pressure_base + rng.normal(0, 0.01)

        # pH: metabolic acid production drifts pH down; bang-bang base dosing corrects it
        pH -= 0.006 * acid_mult * qO2 * Xv * p.dt_h
        if pH < p.pH_setpoint - p.pH_deadband:
            dose_mL = 0.5
            pH += 0.02
            CumBase += dose_mL
        Temp = p.Temp_setpoint + rng.normal(0, 0.03)

        # feed (fed-batch only); feed volume assumed negligible vs vessel volume
        feed_S = 0.0
        if p.mode == "fed-batch" and p.feed_start_h is not None and t_h >= p.feed_start_h:
            feed_S = (p.feed_rate_Lph * p.feed_conc_gL) / p.volume_L

        dS = (-mu / p.Yxs * Xv - p.ms * Xv) * p.dt_h + feed_S * p.dt_h
        dXv = (mu - kd_eff) * Xv * p.dt_h
        dXd = kd_eff * Xv * p.dt_h
        dP = (p.Yps_growth * mu + p.Yps_nongrowth) * Xv * p.dt_h

        S = max(0.0, S + dS)
        Xv = max(0.0, Xv + dXv)
        Xd = max(0.0, Xd + dXd)
        P = max(0.0, P + dP)

        rows.append(
            (
                t_h,
                p.start_time + timedelta(hours=t_h),
                S,
                Xv,
                Xd,
                Xv + Xd,
                P,
                DO,
                pH,
                Temp,
                Agit,
                Flow,
                Pressure,
                CumBase,
                OUR,
                CER,
            )
        )

    return pd.DataFrame(
        rows,
        columns=[
            "elapsed_h",
            "timestamp",
            "substrate_gL",
            "biomass_viable_gL",
            "biomass_dead_gL",
            "biomass_total_gL",
            "product_gL",
            "DO_pct",
            "pH",
            "temp_C",
            "agitation_rpm",
            "gas_flow_Lpm",
            "pressure_bar",
            "cum_base_mL",
            "OUR_gLh",
            "CER_gLh",
        ],
    )
