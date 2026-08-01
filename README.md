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
- Ingestion layer: per-source parsers into the common schema, time-tolerant joins
- Storage: SQLite run database
- Dashboard: Streamlit + Plotly

## Project layout

```
src/generators/   synthetic data generators, one per source type
src/ingestion/     parsers/normalizers into the common schema
src/storage/       SQLite schema + read/write helpers
src/dashboard/     Streamlit app
data/raw/          generated synthetic source files (gitignored)
data/processed/    normalized/joined output (gitignored)
tests/
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Deployment target

Streamlit Community Cloud.
