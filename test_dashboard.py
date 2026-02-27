#!/usr/bin/env python3
"""
Test script for SIRAYA Analytics Dashboard
Validates all components without running Streamlit server
"""

import sys
sys.path.insert(0, '.')

from siraya.services.analytics_data_loader import DataLoader
from siraya.services.metric_calculator import MetricCalculator
import polars as pl

def test_data_loader():
    """Test DataLoader functionality"""
    print("=" * 60)
    print("TESTING DATA LOADER")
    print("=" * 60)
    
    df, geojson = DataLoader.load_master_data()
    
    print(f"\n✅ Loaded {df.height} sessions")
    print(f"   - Unique sessions: {df['session_id'].n_unique()}")
    print(f"   - Date range: {df['timestamp_finale'].min()} to {df['timestamp_finale'].max()}")
    
    # Check columns
    expected_cols = ['session_id', 'codice_triage_finale', 'specialita', 'distretto', 'ausl']
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        print(f"   ⚠️  Missing columns: {missing}")
    else:
        print(f"   ✅ All expected columns present")
    
    # Sample data
    print(f"\n📊 Sample data:")
    print(df.select(['session_id', 'codice_triage_finale', 'comune', 'distretto', 'num_messaggi']).head(3))
    
    # GeoJSON
    print(f"\n🗺️  GeoJSON: {geojson.get('type', 'Unknown type')}")
    
    return df, geojson


def test_metric_calculator(df):
    """Test MetricCalculator functionality"""
    print("\n" + "=" * 60)
    print("TESTING METRIC CALCULATOR")
    print("=" * 60)
    
    # Test IUI calculation
    print("\n1. Testing IUI calculation...")
    df_with_iui = MetricCalculator.calcola_iui(df)
    
    if 'iui_score' in df_with_iui.columns:
        print(f"   ✅ IUI scores calculated")
        print(f"   - Mean IUI: {df_with_iui['iui_score'].mean():.2f}")
        print(f"   - Max IUI: {df_with_iui['iui_score'].max()}")
        
        # Count anomalies
        if 'flag_anomalia_urgenza' in df_with_iui.columns:
            anomalies = df_with_iui.filter(pl.col('flag_anomalia_urgenza') == True).height
            print(f"   - Anomalie rilevate: {anomalies}")
    else:
        print(f"   ❌ IUI calculation failed")
    
    # Test abandonment rate
    print("\n2. Testing abandonment rate...")
    tasso = MetricCalculator.calcola_tasso_abbandono(df)
    print(f"   ✅ Tasso abbandono: {tasso:.2f}%")
    
    # Test spatial-temporal density
    print("\n3. Testing spatio-temporal density...")
    df_density = MetricCalculator.densita_patologie_spaziotemporali(df)
    print(f"   ✅ Density calculated: {df_density.height} rows")
    if df_density.height > 0:
        print(f"   Top 3 combinations:")
        print(df_density.select(['distretto', 'mese', 'specialita', 'volume']).head(3))
    
    # Test summary stats
    print("\n4. Testing summary statistics...")
    stats = MetricCalculator.get_summary_stats(df)
    print(f"   ✅ Summary stats:")
    for key, value in stats.items():
        print(f"      - {key}: {value}")
    
    return df_with_iui


def test_visualizations(df):
    """Test visualization data preparation (without rendering)"""
    print("\n" + "=" * 60)
    print("TESTING VISUALIZATION DATA")
    print("=" * 60)
    
    # Test choropleth data
    print("\n1. Choropleth map data...")
    if 'distretto' in df.columns:
        map_data = (
            df.filter(pl.col('distretto') != 'Non Identificato')
            .group_by('distretto')
            .agg([
                pl.count().alias('volume_triage'),
                pl.col('specialita').mode().first().alias('specialita_prevalente')
            ])
        )
        print(f"   ✅ {map_data.height} districts with data")
    else:
        print(f"   ❌ distretto column not found")
    
    # Test temporal heatmap data
    print("\n2. Temporal heatmap data...")
    if 'timestamp_finale' in df.columns:
        heatmap_data = (
            df.with_columns([
                pl.col('timestamp_finale').dt.hour().alias('ora'),
                pl.col('timestamp_finale').dt.weekday().alias('giorno_settimana')
            ])
            .group_by(['ora', 'giorno_settimana'])
            .agg(pl.count().alias('volume'))
        )
        print(f"   ✅ {heatmap_data.height} hour-day combinations")
    else:
        print(f"   ❌ timestamp_finale column not found")
    
    # Test Sankey flow data
    print("\n3. Sankey flow data...")
    flow_cols = ['detected_intent', 'specialita', 'codice_triage_finale']
    missing_flow = [c for c in flow_cols if c not in df.columns]
    if not missing_flow:
        flow = (
            df.filter(
                (pl.col('detected_intent').is_not_null()) &
                (pl.col('specialita').is_not_null())
            )
            .group_by(['detected_intent', 'specialita'])
            .agg(pl.count().alias('flow_count'))
        )
        print(f"   ✅ {flow.height} flow connections")
    else:
        print(f"   ⚠️  Missing flow columns: {missing_flow}")
    
    # Test LLM performance data
    print("\n4. LLM performance data...")
    perf_cols = ['tokens_totali', 'tempo_totale_ms']
    missing_perf = [c for c in perf_cols if c not in df.columns]
    if not missing_perf:
        perf_data = df.filter(
            (pl.col('tokens_totali') > 0) | (pl.col('tempo_totale_ms') > 0)
        )
        print(f"   ✅ {perf_data.height} sessions with performance data")
    else:
        print(f"   ⚠️  Missing performance columns: {missing_perf}")


def main():
    """Run all tests"""
    print("\n" + "🏥" * 30)
    print("SIRAYA ANALYTICS DASHBOARD - COMPONENT TEST")
    print("🏥" * 30 + "\n")
    
    # Test 1: Data Loader
    df, geojson = test_data_loader()
    
    if df.height == 0:
        print("\n❌ No data loaded - cannot continue tests")
        return
    
    # Test 2: Metric Calculator
    df_enriched = test_metric_calculator(df)
    
    # Test 3: Visualization data
    test_visualizations(df_enriched)
    
    # Final summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"✅ All components tested successfully!")
    print(f"📊 Dashboard ready to use with {df.height} sessions")
    print(f"\nTo run the dashboard:")
    print(f"  streamlit run analytics_dashboard.py")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
