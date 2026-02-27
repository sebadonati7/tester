# SIRAYA Healthcare Analytics Dashboard

Interactive analytics dashboard for the SIRAYA medical triage system serving Emilia-Romagna region.

## Features

### Phase 1: Data Layer
- **DataLoader**: Loads and transforms triage logs from SQL file
  - Parses SQL INSERT statements with JSON metadata
  - Aggregates sessions (groups by session_id)
  - Spatial joins with health districts
  - Calculates derived metrics (duration, message count, abandonment)

- **MetricCalculator**: Computes healthcare KPIs
  - **IUI** (Indice di Urgenza Implicita): Analyzes patient urgency keywords vs triage code
  - **Abandonment Rate**: % of incomplete triage sessions
  - **Spatio-Temporal Density**: Heatmap of conditions by district/month/specialty

### Phase 2: UI Layer
- **Interactive Dashboard** built with Streamlit and Plotly
  - Hero metrics: Total triage, avg time, urgent cases %, abandonment rate
  - Choropleth map: Geographic distribution by district
  - Sankey diagram: Patient flow through triage system
  - Temporal heatmap: Request volume by hour/weekday
  - LLM performance scatter: Tokens vs processing time
  - Hierarchical filters: AUSL → Districts → Date range
  - CSV export functionality

## Technology Stack

- **Python 3.12**
- **Streamlit** - UI framework
- **Polars** - Primary data processing (high-performance)
- **Pandas** - UI compatibility layer
- **Plotly** - All visualizations
- **PostgreSQL/SQL** - Data source (simulated via SQL file)

## Data Structure

The system processes triage logs from 8 AUSL (Local Health Units) across 43 health districts in Emilia-Romagna:

- **AUSL ROMAGNA**: 8 districts (Ravenna, Faenza, Lugo, Forlì, Cesena, Rubicone, Rimini, Riccione)
- **AUSL BOLOGNA**: 6 districts
- **AUSL IMOLA**: 1 district
- **AUSL FERRARA**: 3 districts
- **AUSL MODENA**: 7 districts
- **AUSL REGGIO EMILIA**: 6 districts
- **AUSL PARMA**: 4 districts
- **AUSL PIACENZA**: 3 districts

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Key packages installed:
# - polars>=0.20.0
# - pandas>=2.0.0
# - streamlit>=1.40.0
# - plotly>=5.18.0
```

## Usage

### Run the Dashboard

```bash
streamlit run analytics_dashboard.py
```

Then open your browser to http://localhost:8501

### Run Component Tests

```bash
python test_dashboard.py
```

This validates:
- Data loading from SQL file
- Metric calculations
- Visualization data preparation

## Project Structure

```
analytics_dashboard.py          # Main Streamlit dashboard UI
test_dashboard.py              # Component test suite

siraya/
  services/
    analytics_data_loader.py   # Data loading and transformation
    metric_calculator.py       # KPI calculation engine

triage_logs_rows.sql          # Source data (SQL INSERT statements)
distretti_sanitari_er.json    # Health districts mapping
mappa_er.json                 # GeoJSON for Emilia-Romagna map
```

## Key Metrics

1. **Indice di Urgenza Implicita (IUI)**
   - Analyzes keywords like "forte dolore", "sangue", "non respiro"
   - Score 0-10 normalized
   - Flags discrepancies with assigned triage code

2. **Tasso di Abbandono**
   - % of sessions started but not completed
   - Calculated for sessions with detected_intent='triage'

3. **Densità Spaziotemporale**
   - Volume of conditions by district × month × specialty
   - Delta % vs district average

## Development

### Adding New Metrics

Edit `siraya/services/metric_calculator.py` and add methods to the `MetricCalculator` class.

### Adding New Visualizations

Edit `analytics_dashboard.py` and add rendering methods to the `DashboardUI` class.

### Data Source

The dashboard reads from `triage_logs_rows.sql`, which contains INSERT statements with columns:
- id, created_at, session_id
- user_input, bot_response
- detected_intent, triage_code, medical_specialty
- suggested_facility_type
- processing_time_ms, model_version, tokens_used
- metadata (JSON with nested fields)

## Testing

Component tests verify:
- ✅ SQL parsing and data loading (37 sessions)
- ✅ Session aggregation
- ✅ Metric calculations (IUI, abandonment, density)
- ✅ Visualization data preparation

## License

© 2026 SIRAYA Health Navigator - Regione Emilia-Romagna

## Notes

- The dashboard uses Streamlit caching (`@st.cache_data`) for performance
- GeoJSON mapping is loaded from `mappa_er.json`
- District mappings support fuzzy matching for comune names
- All visualizations are responsive and interactive via Plotly
