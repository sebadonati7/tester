"""
SIRAYA Health Navigator - Metric Calculator
Phase 1: KPI Calculations

This module provides:
- MetricCalculator class for calculating medical KPIs
- Indice di Urgenza Implicita (IUI) analysis
- Abandonment rate calculation
- Spatio-temporal disease density analysis
"""

import polars as pl
from typing import Dict, List
import re


class MetricCalculator:
    """
    Calcola KPI clinici e tecnici.
    
    Features:
    - Implicit Urgency Index (IUI)
    - Abandonment rate
    - Spatio-temporal density heatmaps
    """
    
    @staticmethod
    def calcola_iui(df: pl.DataFrame) -> pl.DataFrame:
        """
        KPI 1: Indice di Urgenza Implicita (IUI)
        
        Analizza sentiment del user_input per rilevare discrepanze tra
        urgenza percepita dal paziente e codice triage assegnato.
        
        ALGORITMO SEMPLIFICATO:
        - Conta parole chiave di urgenza ("forte dolore", "non respiro", "sangue")
        - Score 0-10
        - Confronta con mapping codice triage (ROSSO=10, ARANCIONE=7, ...)
        - Delta > 3 → FLAG ANOMALIA
        
        Args:
            df: DataFrame con colonna user_input_completo e codice_triage_finale
            
        Returns:
            DataFrame arricchito con colonne IUI
        """
        # Define urgency keywords
        URGENCY_KEYWORDS = [
            r'forte',
            r'acuto',
            r'insopportabile',
            r'grave',
            r'sangue',
            r'svenuto',
            r'non respiro',
            r'non riesco a respirare',
            r'dolore fortissimo',
            r'dolore lancinante',
            r'febbre alta',
            r'perdita di coscienza',
            r'trauma',
            r'incidente',
        ]
        
        # Combine patterns
        pattern = '|'.join(URGENCY_KEYWORDS)
        
        # Count urgency keywords
        df = df.with_columns([
            pl.col('user_input_completo').fill_null('').str.to_lowercase()
              .str.count_matches(pattern)
              .alias('keywords_urgenza'),
        ])
        
        # Map triage code to numeric score
        df = df.with_columns([
            pl.when(pl.col('codice_triage_finale') == 'ROSSO')
              .then(10)
              .when(pl.col('codice_triage_finale') == 'ARANCIONE')
              .then(7)
              .when(pl.col('codice_triage_finale') == 'GIALLO')
              .then(5)
              .when(pl.col('codice_triage_finale') == 'VERDE')
              .then(3)
              .when(pl.col('codice_triage_finale') == 'BIANCO')
              .then(1)
              .when(pl.col('codice_triage_finale') == '1')
              .then(3)
              .when(pl.col('codice_triage_finale') == '2')
              .then(5)
              .when(pl.col('codice_triage_finale') == '3')
              .then(7)
              .otherwise(1)
              .alias('score_triage'),
        ])
        
        # Calculate IUI score (normalized 0-10)
        df = df.with_columns([
            (pl.col('keywords_urgenza') * 2).clip(0, 10).alias('iui_score'),
        ])
        
        # Flag anomalies where IUI >> Score Triage
        df = df.with_columns([
            (pl.col('iui_score') - pl.col('score_triage') > 3).alias('flag_anomalia_urgenza'),
        ])
        
        return df
    
    @staticmethod
    def calcola_tasso_abbandono(df: pl.DataFrame) -> float:
        """
        KPI 2: Tasso di Abbandono
        
        % di sessioni iniziate con intent='triage' ma senza codice finale
        
        Args:
            df: DataFrame con colonne detected_intent e sessione_abbandonata
            
        Returns:
            Percentuale di abbandono (0-100)
        """
        if df.height == 0:
            return 0.0
        
        # Filter sessions with triage intent
        triage_sessions = df.filter(
            (pl.col('detected_intent') == 'triage') |
            (pl.col('detected_intent').is_null())  # Include null intents
        )
        
        total = triage_sessions.height
        
        if total == 0:
            return 0.0
        
        # Count abandoned sessions
        abandoned = triage_sessions.filter(
            pl.col('sessione_abbandonata') == True
        ).height
        
        return round((abandoned / total) * 100, 2)
    
    @staticmethod
    def densita_patologie_spaziotemporali(df: pl.DataFrame) -> pl.DataFrame:
        """
        KPI 3: Heatmap Distretto × Mese × Specialità
        
        Calcola la densità di patologie per area geografica e periodo temporale.
        
        Args:
            df: DataFrame con colonne distretto, mese, specialita
            
        Returns:
            DataFrame aggregato con volumi e delta percentuale
            
        Columns:
        | distretto | mese | specialita | volume | delta_percentuale |
        """
        if df.height == 0:
            # Return empty DataFrame with expected schema
            return pl.DataFrame(schema={
                'distretto': pl.Utf8,
                'mese': pl.Int32,
                'specialita': pl.Utf8,
                'volume': pl.UInt32,
                'delta_percentuale': pl.Float64,
            })
        
        # Filter out null values
        df_clean = df.filter(
            pl.col('distretto').is_not_null() &
            pl.col('specialita').is_not_null() &
            (pl.col('distretto') != 'Non Identificato')
        )
        
        # Group by district, month, and specialty
        df_agg = df_clean.group_by(['distretto', 'mese', 'specialita']).agg([
            pl.count().alias('volume'),
        ])
        
        # Calculate delta percentage vs average for that district
        # First get monthly average per district
        df_agg = df_agg.with_columns([
            (pl.col('volume').cast(pl.Float64)).alias('volume_float')
        ])
        
        # Calculate average by district across all months/specialties
        df_district_avg = df_clean.group_by('distretto').agg([
            (pl.count().cast(pl.Float64) / pl.col('mese').n_unique()).alias('avg_volume_per_month')
        ])
        
        # Join averages
        df_agg = df_agg.join(df_district_avg, on='distretto', how='left')
        
        # Calculate delta percentage
        df_agg = df_agg.with_columns([
            (((pl.col('volume_float') / pl.col('avg_volume_per_month')) - 1) * 100)
            .fill_null(0)
            .alias('delta_percentuale')
        ])
        
        # Select final columns
        df_result = df_agg.select([
            'distretto',
            'mese',
            'specialita',
            'volume',
            'delta_percentuale'
        ])
        
        # Sort by volume descending
        df_result = df_result.sort('volume', descending=True)
        
        return df_result
    
    @staticmethod
    def get_summary_stats(df: pl.DataFrame) -> Dict:
        """
        Calculate summary statistics for the dataset.
        
        Args:
            df: DataFrame with session data
            
        Returns:
            Dictionary with key statistics
        """
        if df.height == 0:
            return {
                'total_sessions': 0,
                'avg_processing_time_ms': 0,
                'avg_tokens': 0,
                'urgent_cases_percent': 0,
                'abandonment_rate': 0,
            }
        
        stats = {
            'total_sessions': df.height,
            'avg_processing_time_ms': df['tempo_totale_ms'].mean() if 'tempo_totale_ms' in df.columns else 0,
            'avg_tokens': df['tokens_totali'].mean() if 'tokens_totali' in df.columns else 0,
        }
        
        # Calculate urgent cases percentage
        if 'codice_triage_finale' in df.columns:
            urgent = df.filter(
                pl.col('codice_triage_finale').is_in(['ROSSO', 'ARANCIONE', '3', '2'])
            ).height
            stats['urgent_cases_percent'] = round((urgent / df.height) * 100, 2)
        else:
            stats['urgent_cases_percent'] = 0
        
        # Abandonment rate
        stats['abandonment_rate'] = MetricCalculator.calcola_tasso_abbandono(df)
        
        return stats
