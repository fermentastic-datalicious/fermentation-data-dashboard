# Fermentation Data Dashboard (Portfolio Demo)

A standalone demo showing a generalized pipeline for turning raw
fermentation/bioprocess data into a unified dashboard. Built entirely from
scratch with synthetic data — no proprietary code, formats, or data.

## Data landscape modeled

1. **Bioreactor control systems** — continuous time series (pH, DO, temp,
   agitation, gas flow, pressure), one-file-per-vessel and multi-vessel
   patterns.
2. **Analytical instruments** — discrete, sample-triggered peak tables
   (HPLC/LC-DAD/GC-MS style).
3. **Offline/manual measurements** — biomass (OD600, DCW), spreadsheet-style
   CSV.
4. **Auxiliary online sensors** — independently logged continuous data
   (off-gas analyzers, capacitance probes) joined back to the run timeline.

## Architecture

- Common intermediate schema: `run_id, timestamp, source, variable, value, unit`
  (plus `elapsed_h` and `source_file` for plotting and provenance)
- Ingestion layer: per-source parsers into the common schema, time-tolerant joins
- Storage: SQLite run database
- Dashboard: Streamlit + Plotly

### What the ingestion layer actually resolves

Each source arrives in a different shape, and normalizing them is most of the work:

| Source | The problem | Where it's handled |
|---|---|---|
| DASGIP `R*.csv` | `#` header block, units in column names, **gas flow in L/h** | `parsers/bioreactor.py` |
| Ambr `*_ambr.csv` | 4 vessels in one wide file, no run id anywhere | `parsers/bioreactor.py` |
| HPLC `*_hplc.csv` | timestamped at *injection*, 1–3 h after the sample was drawn | `normalize.backdate_analytical` |
| Biomass `*_biomass.csv` | split `Date`/`Time` columns, blank cells for unmeasured DCW | `parsers/offline.py` |
| Off-gas / capacitance | own unsynced clocks, own cadences, run id only in the filename | `parsers/auxiliary.py`, `align.py` |

Two conversions matter most. Gas flow is normalized to L/min, without which DASGIP
runs read 60× higher than Ambr runs on a shared axis. And HPLC points are moved
back to their sample draw time, without which every glucose and product reading
sits hours to the right of the DO and base curves that explain it.

Cross-source joins happen at **read time** (`align.py`, `merge_asof` with a
tolerance), so storage stays lossless and the dashboard can retune the tolerance
without re-ingesting. Continuous sensors are matched onto the control-system
timeline; sparse samples are snapped the other way, onto their single nearest
control row, so one HPLC point does not become twenty.

## Project layout

```
src/generators/   synthetic data generators, one per source type
src/ingestion/    parsers/normalizers into the common schema
  schema.py         canonical variables, units, vendor column maps
  run_registry.py   manifest + run identity resolution
  parsers/          one module per source family
  normalize.py      discovery, dispatch, cross-source corrections
  align.py          time-tolerant joins
src/storage/      SQLite schema + read/write helpers
src/dashboard/    Streamlit app
data/raw/         generated synthetic source files (gitignored)
data/processed/   runs.db (gitignored)
tests/
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python -m src.generators.generate_all      # write the synthetic raw sources
python -m src.storage.build_db --rebuild   # normalize + load into data/processed/runs.db
pytest -q
```

Reading it back:

```python
from src.storage import connect, read_observations
from src.ingestion import align_to_reference

with connect() as conn:
    obs = read_observations(conn, run_ids=["R1"])

wide = align_to_reference(obs, "R1")   # all five sources on one timeline
```

Current dataset: 7 runs, ~307k observations, 89 samples (~45 MB database).

## Deployment target

Streamlit Community Cloud.
