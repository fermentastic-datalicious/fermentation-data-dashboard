"""Tests for the ingestion layer.

The interesting failures here are silent ones -- a unit left unconverted, a
vessel column mapped to the wrong run, a timestamp off by hours. None of them
raise on their own; they just produce a plausible-looking wrong chart. So most
of these tests assert cross-source agreement rather than mechanics.
"""

import pandas as pd
import pytest

from src.ingestion.align import align_to_reference, resample_continuous
from src.ingestion.normalize import build_samples
from src.ingestion.parsers import (
    parse_ambr_file,
    parse_biomass_file,
    parse_capacitance_file,
    parse_dasgip_file,
    parse_hplc_file,
    parse_offgas_file,
)
from src.ingestion.run_registry import UnknownRunError, RunRegistry, run_id_from_filename
from src.ingestion.schema import CANONICAL_UNITS, OBSERVATION_COLUMNS, SOURCES

PARSERS = {
    "dasgip": parse_dasgip_file,
    "ambr": parse_ambr_file,
    "hplc": parse_hplc_file,
    "biomass": parse_biomass_file,
    "offgas": parse_offgas_file,
    "capacitance": parse_capacitance_file,
}


# --- schema conformance -------------------------------------------------


@pytest.mark.parametrize("layout", list(PARSERS))
def test_every_parser_returns_the_common_schema(layout, files, registry):
    for path in files[layout]:
        obs = PARSERS[layout](path, registry)
        assert list(obs.columns) == OBSERVATION_COLUMNS
        assert not obs.empty
        assert obs["source"].isin(SOURCES).all()
        assert obs["variable"].isin(CANONICAL_UNITS).all()
        assert not obs["value"].isna().any()
        assert not obs["timestamp"].isna().any()


def test_units_are_canonical_everywhere(normalized):
    for variable, group in normalized.observations.groupby("variable"):
        assert set(group["unit"]) == {CANONICAL_UNITS[variable]}


# --- unit normalization -------------------------------------------------


def test_gas_flow_is_comparable_across_systems(normalized):
    """DASGIP logs L/h, Ambr logs L/min. Miss the conversion and they differ 60x.

    Both systems are configured around the same ~0.8 L/min in the generator, so
    if the scaling is right their ranges overlap; if it is not, they do not
    come close.
    """
    gas = normalized.observations.query("variable == 'gas_flow'")
    by_run = gas.groupby("run_id")["value"].mean()
    dasgip = by_run[["R1", "R2", "R3"]]
    ambr = by_run[["A1", "A2", "A3", "A4"]]
    assert dasgip.between(0.5, 1.5).all()
    assert abs(dasgip.mean() - ambr.mean()) < 0.1


# --- run identity -------------------------------------------------------


def test_ambr_wide_file_unpivots_into_one_run_per_vessel(files, registry):
    obs = parse_ambr_file(files["ambr"][0], registry)
    assert sorted(obs["run_id"].unique()) == ["A1", "A2", "A3", "A4"]
    for _, group in obs.groupby("run_id"):
        assert len(group["variable"].unique()) == 6


def test_dasgip_run_id_comes_from_the_header_not_the_filename(files, registry):
    from src.ingestion.parsers.bioreactor import read_dasgip_header

    path = next(p for p in files["dasgip"] if p.stem == "R1")
    assert read_dasgip_header(path)["Batch ID"] == "R1"
    assert parse_dasgip_file(path, registry)["run_id"].unique().tolist() == ["R1"]


def test_auxiliary_run_id_comes_from_the_filename():
    assert run_id_from_filename(pd.io.common.Path("R1_offgas.csv"), "_offgas") == "R1"
    with pytest.raises(ValueError):
        run_id_from_filename(pd.io.common.Path("mystery.csv"), "_offgas")


def test_unknown_run_is_rejected_rather_than_passed_through(registry):
    with pytest.raises(UnknownRunError):
        registry.get("NOPE")
    with pytest.raises(UnknownRunError):
        registry.by_vessel("ambr", "Vessel_99")


def test_every_observation_belongs_to_a_manifest_run(normalized):
    known = set(normalized.runs["run_id"])
    assert set(normalized.observations["run_id"]) <= known
    assert set(normalized.samples["run_id"]) <= known


# --- HPLC back-dating ---------------------------------------------------


def test_analytical_data_is_moved_back_to_sample_draw_time(normalized, files):
    """Raw HPLC rows sit 1-3 h after the draw; ingested ones should not."""
    raw = pd.concat([pd.read_csv(p) for p in files["hplc"]], ignore_index=True)
    raw_injection = pd.to_datetime(raw["Injection_DateTime"])

    analytical = normalized.observations.query("source == 'analytical'")
    assert analytical["timestamp"].max() < raw_injection.max()

    samples = build_samples(files).dropna(subset=["draw_time", "injection_time"])
    lag_h = (samples["injection_time"] - samples["draw_time"]).dt.total_seconds() / 3600
    assert lag_h.min() > 0.5, "fixture no longer has an injection lag to correct"

    # Every analytical timestamp should now coincide with a recorded draw time.
    draw_times = set(samples["draw_time"])
    assert set(analytical["timestamp"]) <= draw_times


def test_elapsed_hours_start_at_zero_and_span_the_run(normalized, registry):
    """The control system defines the run's clock; other sources drift around it.

    Bioreactor logging starts at t=0 and stops at the nominal duration exactly.
    Sample draws are scheduled with jitter and the auxiliary sensors run on
    unsynced clocks, so both can land slightly outside that window -- a little
    slop is correct here, and a lot would mean a timestamp was misparsed.
    """
    for run_id, group in normalized.observations.groupby("run_id"):
        duration = registry.get(run_id).duration_h
        control = group.query("source == 'bioreactor'")
        assert control["elapsed_h"].min() == pytest.approx(0.0, abs=0.02)
        assert control["elapsed_h"].max() == pytest.approx(duration, abs=0.05)

        assert group["elapsed_h"].min() > -0.5
        assert group["elapsed_h"].max() < duration + 1.0


# --- alignment ----------------------------------------------------------


def test_aligned_frame_carries_all_sources_for_a_run(normalized):
    wide = align_to_reference(normalized.observations, "R1")
    for column in ("pH", "DO", "offgas_co2", "permittivity", "glucose", "od600"):
        assert wide[column].notna().any(), f"{column} did not survive the join"


def test_sparse_samples_appear_once_each_not_smeared_across_the_window(normalized):
    """A tolerance-window join in the naive direction duplicates each sample."""
    obs = normalized.observations
    n_samples = obs.query("run_id == 'R1' and variable == 'glucose'").shape[0]
    wide = align_to_reference(obs, "R1")
    assert wide["glucose"].notna().sum() == n_samples


def test_matched_rows_stay_within_tolerance(normalized):
    """Off-gas runs on its own clock; the join must not reach past the tolerance."""
    obs = normalized.observations
    tolerance = pd.Timedelta(minutes=5)
    wide = align_to_reference(obs, "R1", continuous_tolerance=tolerance)
    offgas_times = obs.query("run_id == 'R1' and variable == 'offgas_co2'")["timestamp"]

    matched = wide.loc[wide["offgas_co2"].notna(), "timestamp"]
    gaps = matched.map(lambda t: (offgas_times - t).abs().min())
    assert gaps.max() <= tolerance


def test_capacitance_tracks_offline_biomass_after_the_join(normalized):
    """A scrambled join breaks this: the two measure the same thing, separately.

    Permittivity is an online proxy for viable cell density and DCW is the
    offline gravimetric measurement, logged by different instruments on
    different clocks. If they still agree after aligning, the join lined up
    the right rows.
    """
    wide = align_to_reference(normalized.observations, "R1")
    paired = wide[["permittivity", "dcw"]].dropna()
    assert len(paired) >= 5
    assert paired.corr().iloc[0, 1] > 0.9


def test_contamination_run_still_reads_as_contaminated(normalized):
    """R3's injected anomaly should survive ingestion intact and on-timeline."""
    wide = align_to_reference(normalized.observations, "R3")
    before = wide.query("elapsed_h < 20")
    after = wide.query("elapsed_h > 28")
    assert after["DO"].mean() < before["DO"].mean()
    assert after["base_added"].max() > before["base_added"].max()


def test_resampling_puts_runs_of_different_cadence_on_one_grid(normalized):
    obs = normalized.observations
    dasgip = resample_continuous(obs, "R1", variables=["DO", "pH"], freq="15min")
    ambr = resample_continuous(obs, "A1", variables=["DO", "pH"], freq="15min")
    for frame in (dasgip, ambr):
        assert list(frame.columns) == ["timestamp", "elapsed_h", "DO", "pH"]
    spacing = dasgip["elapsed_h"].diff().dropna()
    assert spacing.round(4).nunique() == 1


# --- registry -----------------------------------------------------------


def test_missing_manifest_gives_an_actionable_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="generate_all"):
        RunRegistry.load(tmp_path / "run_manifest.csv")
