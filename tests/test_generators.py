import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.generators.process_model import simulate_run
from src.generators.run_definitions import all_runs


def test_all_runs_stay_physically_valid():
    for params in all_runs():
        df = simulate_run(params)
        assert df["substrate_gL"].min() >= 0
        assert df["biomass_viable_gL"].min() >= 0
        assert df["biomass_dead_gL"].min() >= 0
        assert df["DO_pct"].between(0, 100).all()
        assert df["pH"].between(4, 9).all()
        assert not df.isna().any().any()


def test_contamination_run_shows_viable_biomass_decline():
    params = next(p for p in all_runs() if p.anomaly == "contamination")
    df = simulate_run(params)
    peak_viable = df["biomass_viable_gL"].max()
    final_viable = df["biomass_viable_gL"].iloc[-1]
    assert final_viable < peak_viable * 0.8
