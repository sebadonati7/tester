"""
SIRAYA Health Navigator - Analytics Data Loader
Phase 1: Data Layer Implementation

This module provides:
- DataLoader class for loading and transforming triage logs data
- Session aggregation and enrichment
- Spatial joins with health districts
- KPI calculations for medical analytics
"""

import polars as pl
import streamlit as st
import json
import re
from datetime import datetime, timedelta
from typing import Tuple, Dict, List, Optional, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# DISTRICT MAPPING (FALLBACK)
# ============================================================================

DISTRETTI_ER = {
    "AUSL ROMAGNA": [
        "Ravenna", "Faenza", "Lugo", "Forlì", 
        "Cesena - Valle Savio", "Rubicone", "Rimini", "Riccione"
    ],
    "AUSL BOLOGNA": [
        "Bologna Città", "Pianura Est", "Pianura Ovest",
        "Reno, Lavino e Samoggia", "San Lazzaro di Savena", 
        "Appennino Bolognese"
    ],
    "AUSL IMOLA": ["Imola"],
    "AUSL FERRARA": ["Centro-Nord", "Sud-Est", "Ovest"],
    "AUSL MODENA": [
        "Modena", "Carpi", "Mirandola", "Sassuolo",
        "Pavullo nel Frignano", "Vignola", "Castelfranco Emilia"
    ],
    "AUSL REGGIO EMILIA": [
        "Reggio Emilia", "Guastalla", "Correggio",
        "Montecchio", "Scandiano", "Castelnovo ne' Monti"
    ],
    "AUSL PARMA": [
        "Parma", "Fidenza", "Sud-Est", "Valli Taro e Ceno"
    ],
    "AUSL PIACENZA": [
        "Città di Piacenza", "Levante", "Ponente"
    ]
}


class DataLoader:
    """
    Gestisce l'estrazione e caching dei dati medicali.
    
    Features:
    - Parsing SQL file with triage logs
    - JSON metadata extraction and parsing
    - Session aggregation (last state per session)
    - Spatial join with health districts
    - Derived column calculations
    """
    
    @staticmethod
    @st.cache_data(ttl=3600, show_spinner=False)
    def load_master_data() -> Tuple[pl.DataFrame, Dict]:
        """
        Carica i dati dal SQL fornito (triage_logs_rows.sql) e li arricchisce.
        
        VINCOLI CRITICI:
        1. Parsing del campo 'metadata' (JSON string) → struct Polars
        2. Aggregazione per session_id (ultimo stato conversazione)
        3. Join spaziale con distretti_sanitari_er.json
        4. Calcolo colonne derivate (durata_conversazione, num_messaggi_sessione)
        
        Returns:
            df_master (Polars): Dataset aggregato
            geojson_er (Dict): Confini geografici distretti
        """
        try:
            # Load SQL file and parse
            df = DataLoader._load_sql_file()
            
            # Parse metadata JSON
            df = DataLoader._parse_metadata(df)
            
            # Aggregate by session
            df_sessions = DataLoader._aggregate_sessions(df)
            
            # Load districts and perform spatial join
            districts_data = DataLoader._load_districts()
            df_enriched = DataLoader._enrich_with_districts(df_sessions, districts_data)
            
            # Load GeoJSON for map visualization
            geojson = DataLoader._load_geojson()
            
            logger.info(f"✅ Loaded {df_enriched.height} sessions successfully")
            return df_enriched, geojson
            
        except Exception as e:
            logger.error(f"❌ Error loading master data: {e}")
            # Return empty dataframe with expected schema
            return DataLoader._create_empty_dataframe(), {}
    
    @staticmethod
    def _load_sql_file() -> pl.DataFrame:
        """
        Load and parse the SQL INSERT statements from triage_logs_rows.sql.
        
        Returns:
            Polars DataFrame with raw log data
        """
        sql_path = Path(__file__).parent.parent.parent / "triage_logs_rows.sql"
        
        if not sql_path.exists():
            logger.warning(f"SQL file not found: {sql_path}")
            return DataLoader._create_empty_dataframe()
        
        # Read SQL file
        with open(sql_path, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        
        # Extract the VALUES section (everything after "VALUES ")
        values_match = re.search(r'VALUES\s+(.+)', sql_content, re.DOTALL)
        if not values_match:
            logger.warning("No VALUES found in SQL file")
            return DataLoader._create_empty_dataframe()
        
        values_section = values_match.group(1)
        
        # Split by "), (" to get individual tuples
        # First, temporarily replace escaped quotes to avoid confusion
        values_section = values_section.replace("''", "<<<ESCAPED_QUOTE>>>")
        
        # Split by finding balanced parentheses manually to avoid ReDoS
        # This is safer than using complex regex with nested quantifiers
        tuples = []
        current_tuple = ""
        paren_depth = 0
        in_quotes = False
        i = 0
        
        while i < len(values_section):
            char = values_section[i]
            
            # Handle quotes
            if char == "'" and (i == 0 or values_section[i-1] != '\\'):
                in_quotes = not in_quotes
                current_tuple += char
            # Track parentheses only outside quotes
            elif char == '(' and not in_quotes:
                paren_depth += 1
                if paren_depth == 1:
                    current_tuple = ""  # Start new tuple
                else:
                    current_tuple += char
            elif char == ')' and not in_quotes:
                paren_depth -= 1
                if paren_depth == 0:
                    # Complete tuple found
                    tuples.append(current_tuple)
                    current_tuple = ""
                else:
                    current_tuple += char
            elif paren_depth > 0:
                # Inside a tuple
                current_tuple += char
            
            i += 1
        
        matches = tuples
        
        rows = []
        for match in matches:
            # Restore escaped quotes
            match = match.replace("<<<ESCAPED_QUOTE>>>", "''")
            
            # Split by comma, but respect quoted strings and JSON objects
            parts = DataLoader._split_sql_values(match)
            
            if len(parts) >= 16:  # Ensure we have all columns
                row_dict = {
                    'id': DataLoader._clean_value(parts[0]),
                    'created_at': DataLoader._clean_value(parts[1]),
                    'session_id': DataLoader._clean_value(parts[2]),
                    'user_input': DataLoader._clean_value(parts[3]),
                    'bot_response': DataLoader._clean_value(parts[4]),
                    'detected_intent': DataLoader._clean_value(parts[5]),
                    'triage_code': DataLoader._clean_value(parts[6]),
                    'medical_specialty': DataLoader._clean_value(parts[7]),
                    'suggested_facility_type': DataLoader._clean_value(parts[8]),
                    'reasoning': DataLoader._clean_value(parts[9]),
                    'estimated_wait_time': DataLoader._clean_value(parts[10]),
                    'processing_time_ms': DataLoader._clean_value(parts[11]),
                    'model_version': DataLoader._clean_value(parts[12]),
                    'tokens_used': DataLoader._clean_value(parts[13]),
                    'client_ip': DataLoader._clean_value(parts[14]),
                    'metadata': DataLoader._clean_value(parts[15])
                }
                rows.append(row_dict)
        
        if not rows:
            logger.warning("No data rows extracted from SQL file")
            return DataLoader._create_empty_dataframe()
        
        # Create Polars DataFrame
        df = pl.DataFrame(rows)
        
        # Convert data types
        # Handle timestamps - created_at column is a string like '2026-01-25 22:29:17.979328+00'
        # We need to parse it carefully
        df = df.with_columns([
            pl.col('id').cast(pl.Utf8),
            # Parse timestamp: remove timezone suffix and parse, then add timezone
            pl.when(pl.col('created_at').is_not_null())
              .then(
                  pl.col('created_at')
                    .str.replace(r'\+\d{2}$', '')  # Remove +00 suffix
                    .str.strptime(pl.Datetime('us'), format='%Y-%m-%d %H:%M:%S%.f', strict=False)
                    .dt.replace_time_zone('UTC')
              )
              .otherwise(None)
              .alias('created_at'),
            pl.col('processing_time_ms').cast(pl.Int64, strict=False).fill_null(0),
            pl.col('tokens_used').cast(pl.Int64, strict=False).fill_null(0),
        ])
        
        return df
    
    @staticmethod
    def _split_sql_values(values_str: str) -> List[str]:
        """
        Split SQL VALUES string by comma, respecting quoted strings and JSON objects.
        
        Args:
            values_str: String like "'val1', 'val2', '{\"key\": \"val\"}'"
            
        Returns:
            List of individual values
        """
        parts = []
        current = ""
        in_quotes = False
        brace_depth = 0
        i = 0
        
        # Simple state machine to avoid ReDoS vulnerability
        while i < len(values_str):
            char = values_str[i]
            
            # Handle quote toggling (check for escape)
            if char == "'" and (i == 0 or values_str[i-1] != '\\'):
                in_quotes = not in_quotes
                current += char
            # Track brace depth only when inside quotes
            elif char == '{' and in_quotes:
                brace_depth += 1
                current += char
            elif char == '}' and in_quotes:
                if brace_depth > 0:
                    brace_depth -= 1
                current += char
            # Split on comma only when not in quotes and braces balanced
            elif char == ',' and not in_quotes and brace_depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += char
            
            i += 1
        
        # Add final part
        if current.strip():
            parts.append(current.strip())
        
        return parts
    
    @staticmethod
    def _clean_value(value: str) -> Optional[str]:
        """
        Clean SQL value by removing quotes and handling nulls.
        
        Args:
            value: Raw SQL value string
            
        Returns:
            Cleaned value or None
        """
        value = value.strip()
        
        # Handle NULL values
        if value.upper() in ('NULL', 'null'):
            return None
        
        # Remove surrounding quotes
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        
        # Unescape single quotes
        value = value.replace("''", "'")
        
        return value if value else None
    
    @staticmethod
    def _parse_metadata(df: pl.DataFrame) -> pl.DataFrame:
        """
        Parse JSON metadata field into structured columns.
        
        Args:
            df: DataFrame with metadata column
            
        Returns:
            DataFrame with extracted metadata fields
        """
        # Parse JSON metadata
        def safe_json_parse(json_str):
            """Safely parse JSON, handling errors."""
            if json_str is None or json_str == '':
                return {}
            try:
                return json.loads(json_str)
            except:
                return {}
        
        # Extract metadata fields
        df = df.with_columns([
            # Parse JSON metadata
            pl.col('metadata').map_elements(
                lambda x: safe_json_parse(x),
                return_dtype=pl.Object
            ).alias('meta_parsed')
        ])
        
        # Extract nested fields from metadata
        df = df.with_columns([
            pl.col('meta_parsed').map_elements(
                lambda x: x.get('urgenza') if isinstance(x, dict) else None,
                return_dtype=pl.Int64
            ).alias('urgenza_implicita'),
            
            pl.col('meta_parsed').map_elements(
                lambda x: x.get('triage_step') or x.get('step') or x.get('phase') if isinstance(x, dict) else None,
                return_dtype=pl.Utf8
            ).alias('fase_triage'),
            
            pl.col('meta_parsed').map_elements(
                lambda x: x.get('collected_data', {}) if isinstance(x, dict) else {},
                return_dtype=pl.Object
            ).alias('dati_paziente'),
            
            # Extract location from metadata
            pl.col('meta_parsed').map_elements(
                lambda x: (x.get('collected_data', {}).get('LOCATION') or 
                          x.get('location')) if isinstance(x, dict) else None,
                return_dtype=pl.Utf8
            ).alias('location_metadata'),
        ])
        
        return df
    
    @staticmethod
    def _aggregate_sessions(df: pl.DataFrame) -> pl.DataFrame:
        """
        Aggregate data by session_id, taking the last state of each session.
        
        Args:
            df: DataFrame with individual log entries
            
        Returns:
            DataFrame with one row per session
        """
        # Group by session and aggregate
        df_sessions = df.group_by('session_id').agg([
            pl.col('created_at').max().alias('timestamp_finale'),
            pl.col('created_at').min().alias('timestamp_inizio'),
            
            # Take last non-null triage code
            pl.col('triage_code').filter(
                (pl.col('triage_code').is_not_null()) & 
                (pl.col('triage_code') != 'N/D')
            ).last().alias('codice_triage_finale'),
            
            # Take last values
            pl.col('medical_specialty').last().alias('specialita'),
            pl.col('suggested_facility_type').last().alias('struttura_suggerita'),
            pl.col('detected_intent').last().alias('detected_intent'),
            pl.col('model_version').last().alias('model_version'),
            
            # Sum metrics
            pl.col('processing_time_ms').sum().alias('tempo_totale_ms'),
            pl.col('tokens_used').sum().alias('tokens_totali'),
            
            # Count messages
            pl.col('user_input').count().alias('num_messaggi'),
            
            # Detect abandonment (no final triage code)
            (pl.col('triage_code').filter(
                (pl.col('triage_code').is_not_null()) & 
                (pl.col('triage_code') != 'N/D')
            ).count() == 0).alias('sessione_abbandonata'),
            
            # Get location
            pl.col('location_metadata').filter(
                pl.col('location_metadata').is_not_null()
            ).last().alias('comune'),
            
            # Concatenate all user inputs for text analysis
            pl.col('user_input').filter(
                pl.col('user_input').is_not_null()
            ).str.concat(delimiter=' ').alias('user_input_completo'),
        ])
        
        # Calculate session duration
        df_sessions = df_sessions.with_columns([
            ((pl.col('timestamp_finale') - pl.col('timestamp_inizio')).dt.total_seconds())
            .alias('durata_sessione_secondi')
        ])
        
        # Extract month for temporal analysis
        df_sessions = df_sessions.with_columns([
            pl.col('timestamp_finale').dt.month().alias('mese'),
            pl.col('timestamp_finale').dt.year().alias('anno'),
        ])
        
        return df_sessions
    
    @staticmethod
    def _load_districts() -> Dict[str, Any]:
        """
        Load district mapping from JSON file or use hardcoded fallback.
        
        Returns:
            Dictionary with district data
        """
        districts_path = Path(__file__).parent.parent.parent / "distretti_sanitari_er.json"
        
        if districts_path.exists():
            try:
                with open(districts_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading districts JSON: {e}, using fallback")
        
        # Return fallback structure
        return {
            "health_districts": [
                {"ausl": ausl, "districts": [{"name": d} for d in dists]}
                for ausl, dists in DISTRETTI_ER.items()
            ],
            "comune_to_district_mapping": DataLoader._build_comune_mapping()
        }
    
    @staticmethod
    def _build_comune_mapping() -> Dict[str, str]:
        """
        Build mapping from comune names to district codes.
        
        Returns:
            Dictionary mapping comune -> district
        """
        # Simplified mapping based on major cities
        mapping = {
            "ravenna": "Ravenna",
            "faenza": "Faenza",
            "lugo": "Lugo",
            "forli": "Forlì",
            "forlì": "Forlì",
            "cesena": "Cesena - Valle Savio",
            "rimini": "Rimini",
            "riccione": "Riccione",
            "bologna": "Bologna Città",
            "imola": "Imola",
            "ferrara": "Centro-Nord",
            "modena": "Modena",
            "carpi": "Carpi",
            "sassuolo": "Sassuolo",
            "reggio emilia": "Reggio Emilia",
            "reggio nell'emilia": "Reggio Emilia",
            "parma": "Parma",
            "fidenza": "Fidenza",
            "piacenza": "Città di Piacenza",
        }
        return mapping
    
    @staticmethod
    def _enrich_with_districts(df: pl.DataFrame, districts_data: Dict) -> pl.DataFrame:
        """
        Add district and AUSL information based on comune.
        
        Args:
            df: DataFrame with comune column
            districts_data: District mapping data
            
        Returns:
            DataFrame enriched with district info
        """
        # Get mapping
        comune_to_district = districts_data.get('comune_to_district_mapping', {})
        
        # Create district lookup
        def get_district(comune_val):
            if comune_val is None:
                return None
            comune_lower = str(comune_val).lower().strip()
            return comune_to_district.get(comune_lower, "Non Identificato")
        
        # Create AUSL lookup
        district_to_ausl = {}
        for hd in districts_data.get('health_districts', []):
            ausl_name = hd.get('ausl', '')
            for district in hd.get('districts', []):
                dist_name = district.get('name', '')
                district_to_ausl[dist_name] = ausl_name
        
        def get_ausl(district_val):
            if district_val is None or district_val == "Non Identificato":
                return "Non Identificato"
            return district_to_ausl.get(district_val, "Non Identificato")
        
        # Add columns
        df = df.with_columns([
            pl.col('comune').map_elements(get_district, return_dtype=pl.Utf8).alias('distretto'),
        ])
        
        df = df.with_columns([
            pl.col('distretto').map_elements(get_ausl, return_dtype=pl.Utf8).alias('ausl'),
        ])
        
        return df
    
    @staticmethod
    def _load_geojson() -> Dict:
        """
        Load GeoJSON for map visualization.
        
        Returns:
            GeoJSON dictionary
        """
        geojson_path = Path(__file__).parent.parent.parent / "mappa_er.json"
        
        if geojson_path.exists():
            try:
                with open(geojson_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading GeoJSON: {e}")
        
        return {"type": "FeatureCollection", "features": []}
    
    @staticmethod
    def _create_empty_dataframe() -> pl.DataFrame:
        """
        Create empty DataFrame with expected schema.
        
        Returns:
            Empty Polars DataFrame with correct schema
        """
        schema = {
            'session_id': pl.Utf8,
            'timestamp_finale': pl.Datetime,
            'timestamp_inizio': pl.Datetime,
            'codice_triage_finale': pl.Utf8,
            'specialita': pl.Utf8,
            'struttura_suggerita': pl.Utf8,
            'detected_intent': pl.Utf8,
            'model_version': pl.Utf8,
            'tempo_totale_ms': pl.Int64,
            'tokens_totali': pl.Int64,
            'num_messaggi': pl.Int64,
            'sessione_abbandonata': pl.Boolean,
            'comune': pl.Utf8,
            'user_input_completo': pl.Utf8,
            'durata_sessione_secondi': pl.Float64,
            'mese': pl.Int32,
            'anno': pl.Int32,
            'distretto': pl.Utf8,
            'ausl': pl.Utf8,
        }
        
        return pl.DataFrame(schema=schema)
