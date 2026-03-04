"""
SIRAYA Health Navigator - Analytics Service
V2.0: Full KPI Migration from backend.py

This service:
- Reads logs from Supabase
- Calculates all clinical and operational KPIs
- Returns pure data (no Streamlit/Plotly dependencies)
- Provides filtering and aggregation functions
"""

import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from ..config.settings import SupabaseConfig, ClinicalMappings
from .metadata_parser import parse_metadata_enhanced
from .geographic_mapper import map_comune_to_district

logger = logging.getLogger(__name__)


# ============================================================================
# SUPABASE CLIENT
# ============================================================================

def _get_supabase_client():
    """Get Supabase client for analytics."""
    if not SupabaseConfig.is_configured():
        return None
    
    try:
        from supabase import create_client
        return create_client(SupabaseConfig.get_url(), SupabaseConfig.get_key())
    except Exception:
        return None


# ============================================================================
# ANALYTICS SERVICE CLASS
# ============================================================================

class AnalyticsService:
    """
    Analytics engine for triage data.
    
    Features:
    - Supabase log retrieval with pagination
    - 15+ KPI calculations
    - Data enrichment (NLP, temporal)
    - Pure data output (no UI dependencies)
    """
    
    def __init__(self):
        """Initialize analytics service."""
        self._client = _get_supabase_client()
        self._records_cache: Optional[List[Dict]] = None
        self._sessions_cache: Optional[Dict[str, List[Dict]]] = None
    
    # ========================================================================
    # DATA RETRIEVAL
    # ========================================================================
    
    def get_all_logs(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """
        Retrieve all logs from Supabase with pagination.
        
        Args:
            limit: Maximum records to retrieve
            
        Returns:
            List of log records (raw from Supabase)
        """
        if not self._client:
            return []
        
        try:
            all_records = []
            page_size = 1000
            offset = 0
            
            while offset < limit:
                response = (
                    self._client.table(SupabaseConfig.TABLE_LOGS)
                    .select("*")
                    .order("created_at", desc=False)
                    .range(offset, min(offset + page_size - 1, limit - 1))
                    .execute()
                )
                
                if not response.data:
                    break

                for item in response.data:
                    if isinstance(item, dict):
                        all_records.append(item)
                
                if len(response.data) < page_size:
                    break
                
                offset += page_size
            
            return all_records
            
        except Exception as e:
            print(f"❌ Error retrieving logs: {e}")
            return []
    
    def fetch_logs(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch logs with date filtering.
        
        Args:
            start_date: Filter start (inclusive)
            end_date: Filter end (inclusive)
            
        Returns:
            Filtered list of log records
        """
        if not self._client:
            return []
        
        try:
            query = self._client.table(SupabaseConfig.TABLE_LOGS).select("*")
            
            if start_date:
                query = query.gte("created_at", start_date.isoformat())
            
            if end_date:
                query = query.lte("created_at", end_date.isoformat())
            
            response = query.order("created_at", desc=False).execute()
            
            return response.data if response.data else []
            
        except Exception as e:
            print(f"❌ Error fetching logs: {e}")
            return []
    
    def get_enriched_records(self) -> Tuple[List[Dict], Dict[str, List[Dict]]]:
        """
        Get enriched records with computed fields.
        
        Returns:
            Tuple of (records_list, sessions_dict)
        """
        if self._records_cache is not None:
            return self._records_cache, self._sessions_cache
        
        raw_logs = self.get_all_logs()
        records = []
        sessions = defaultdict(list)

        for log in raw_logs:
            if not isinstance(log, dict):
                continue
            record = self._enrich_record(log)
            records.append(record)
            
            session_id = record.get("session_id")
            if session_id:
                sessions[session_id].append(record)
        
        self._records_cache = records
        self._sessions_cache = dict(sessions)
        
        return records, sessions
    
    def _enrich_record(self, log: Dict) -> Dict:
        """
        Arricchisce record log con metadata parsati e mappatura geografica.

        ENHANCED: Supporta formati v2.0, v3.0, v4.0 tramite metadata_parser.
        """
        record = {
            "id": log.get("id"),
            "timestamp": log.get("created_at"),
            "session_id": log.get("session_id"),
            "user_input": log.get("user_input", ""),
            "bot_response": log.get("bot_response", ""),
            "processing_time_ms": log.get("processing_time_ms", 0),
        }
        record.update(log)

        # Parse timestamp
        timestamp_str = log.get("created_at") or log.get("timestamp")
        dt = self._parse_timestamp(timestamp_str)
        if dt:
            record["datetime"] = dt
            record["date"] = dt.date()
            record["year"] = dt.year
            record["month"] = dt.month
            record["week"] = dt.isocalendar()[1]
            record["day_of_week"] = dt.weekday()
            record["hour"] = dt.hour
        else:
            now = datetime.now()
            record["datetime"] = now
            record["date"] = now.date()
            record["year"] = now.year
            record["month"] = now.month
            record["week"] = now.isocalendar()[1]
            record["day_of_week"] = now.weekday()
            record["hour"] = now.hour

        # METADATA PARSING AVANZATO
        metadata_raw = log.get("metadata", "{}")
        parsed = parse_metadata_enhanced(metadata_raw)
        record["urgenza"] = parsed["urgenza"]
        record["red_flags"] = list(parsed["red_flags"]) if parsed.get("red_flags") else []
        record["comune"] = parsed["comune"]
        record["specializzazione"] = parsed["specializzazione"]
        record["metadata_parsed"] = parsed
        record["metadata_raw"] = metadata_raw

        logger.debug(
            "[Session %s] Parsed metadata: urgenza=%s, comune=%s",
            str(record.get("session_id", ""))[:8],
            record["urgenza"],
            record["comune"],
        )

        # RED FLAGS AGGIUNTIVI DA NLP (MERGE)
        red_flags_nlp = set()
        user_input_lower = str(record.get("user_input", "") or "").lower()
        bot_response_lower = str(record.get("bot_response", "") or "").lower()
        combined_text = user_input_lower + " " + bot_response_lower

        red_flags_keywords = [
            "chest pain", "dolore toracico", "dyspnea", "dispnea",
            "altered consciousness", "coscienza alterata",
            "severe bleeding", "emorragia grave",
            "chest_pain", "dyspnea", "altered_consciousness",
            *ClinicalMappings.RED_FLAGS_KEYWORDS,
        ]
        for keyword in red_flags_keywords:
            if keyword in combined_text:
                red_flags_nlp.add(keyword.replace("_", " ").title())

        all_red_flags = set(record["red_flags"]) | red_flags_nlp
        record["red_flags"] = list(all_red_flags)
        record["has_red_flag"] = len(record["red_flags"]) > 0

        if record["red_flags"]:
            logger.warning(
                "[Session %s] Red flags detected: %s",
                str(record.get("session_id", ""))[:8],
                ", ".join(record["red_flags"]),
            )

        # Symptoms: merge metadata + NLP
        sintomi_meta = parsed.get("sintomi", []) or []
        sintomi_nlp = [s for s in ClinicalMappings.SINTOMI_COMUNI if s in combined_text]
        record["sintomi_rilevati"] = list(sintomi_meta) + sintomi_nlp

        # MAPPATURA GEOGRAFICA
        if record["comune"]:
            geo_result = map_comune_to_district(record["comune"])
            record["distretto"] = geo_result.get("distretto", "UNKNOWN")
            record["ausl"] = geo_result.get("ausl", "UNKNOWN")
            record["comune_matched"] = geo_result.get("comune_matched", record["comune"])
            record["geo_confidence"] = geo_result.get("confidence", 0.0)
            record["geo_match_type"] = geo_result.get("match_type", "unknown")
            if record["geo_match_type"] != "no_location":
                logger.info(
                    "[Session %s] Geo mapping: %s → %s (%s, conf=%s)",
                    str(record.get("session_id", ""))[:8],
                    record["comune"],
                    record["distretto"],
                    record["geo_match_type"],
                    record["geo_confidence"],
                )
        else:
            record["distretto"] = "UNKNOWN"
            record["ausl"] = "UNKNOWN"
            record["comune_matched"] = ""
            record["geo_confidence"] = 0.0
            record["geo_match_type"] = "no_location"

        return record
    
    def _parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """Parse ISO timestamp string."""
        if not timestamp_str:
            return None
        
        try:
            if timestamp_str.endswith("Z"):
                timestamp_str = timestamp_str[:-1] + "+00:00"
            
            dt = datetime.fromisoformat(timestamp_str)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            
            return dt
        except Exception:
            return None
    
    # ========================================================================
    # KPI CALCULATIONS - VOLUMETRIC
    # ========================================================================
    
    def calculate_kpi_volumetrici(self, records: List[Dict] = None) -> Dict[str, Any]:
        """
        Calculate volumetric KPIs.
        
        Args:
            records: Optional pre-filtered records
            
        Returns:
            Dict with volumetric KPIs
        """
        if records is None:
            records, sessions = self.get_enriched_records()
        else:
            sessions = defaultdict(list)
            for r in records:
                sid = r.get("session_id")
                if sid:
                    sessions[sid].append(r)
            sessions = dict(sessions)
        
        kpi = {}
        
        # Unique sessions
        kpi["sessioni_uniche"] = len(sessions)
        
        # Total interactions
        kpi["interazioni_totali"] = len(records)
        
        # Hourly throughput
        hours = [r.get("hour", 0) for r in records if r.get("hour") is not None]
        kpi["throughput_orario"] = dict(Counter(hours))
        
        # Completion rate
        completed_sessions = 0
        for sid, session_records in sessions.items():
            for r in session_records:
                bot_resp = str(r.get("bot_response", "")).lower()
                if "raccomand" in bot_resp or "disposition" in bot_resp or "pronto soccorso" in bot_resp:
                    completed_sessions += 1
                    break
        
        kpi["completion_rate"] = (
            (completed_sessions / len(sessions) * 100) if sessions else 0
        )
        
        # Median triage time
        session_durations = []
        for sid, session_records in sessions.items():
            if len(session_records) >= 2:
                timestamps = [r.get("datetime") for r in session_records if r.get("datetime")]
                if len(timestamps) >= 2:
                    duration = (max(timestamps) - min(timestamps)).total_seconds() / 60
                    if duration < 120:  # Exclude zombie sessions
                        session_durations.append(duration)
        
        if session_durations:
            session_durations.sort()
            median_idx = len(session_durations) // 2
            kpi["tempo_mediano_minuti"] = session_durations[median_idx]
        else:
            kpi["tempo_mediano_minuti"] = 0
        
        # Average depth
        kpi["profondita_media"] = (
            len(records) / len(sessions) if sessions else 0
        )
        
        return kpi
    
    # ========================================================================
    # KPI CALCULATIONS - CLINICAL
    # ========================================================================
    
    def calculate_kpi_clinici(self, records: List[Dict] = None) -> Dict[str, Any]:
        """
        Calculate clinical KPIs.
        
        Args:
            records: Optional pre-filtered records
            
        Returns:
            Dict with clinical KPIs
        """
        if records is None:
            records, _ = self.get_enriched_records()
        
        kpi = {}
        
        # Symptom spectrum
        all_sintomi = []
        for r in records:
            all_sintomi.extend(r.get("sintomi_rilevati", []))
        kpi["spettro_sintomi"] = dict(Counter(all_sintomi))
        
        # Urgency stratification
        urgenze = [r.get("urgenza", 3) for r in records]
        kpi["stratificazione_urgenza"] = dict(Counter(urgenze))
        
        # Red flags prevalence
        red_flags_count = sum(1 for r in records if r.get("has_red_flag", False))
        kpi["prevalenza_red_flags"] = (
            (red_flags_count / len(records) * 100) if records else 0
        )
        
        # Red flags by type
        all_red_flags = []
        for r in records:
            all_red_flags.extend(r.get("red_flags", []))
        kpi["red_flags_dettaglio"] = dict(Counter(all_red_flags))
        
        return kpi
    
    # ========================================================================
    # KPI CALCULATIONS - CONTEXT-AWARE
    # ========================================================================
    
    def calculate_kpi_context_aware(self, records: List[Dict] = None) -> Dict[str, Any]:
        """
        Calculate context-aware KPIs.
        
        Args:
            records: Optional pre-filtered records
            
        Returns:
            Dict with context-aware KPIs
        """
        if records is None:
            records, _ = self.get_enriched_records()
        
        kpi = {}
        
        # Urgency by specialization
        urgenza_per_spec = defaultdict(list)
        for r in records:
            spec = r.get("specializzazione", "Generale")
            urgenza = r.get("urgenza", 3)
            urgenza_per_spec[spec].append(urgenza)
        
        kpi["urgenza_media_per_spec"] = {
            spec: sum(urgs) / len(urgs) if urgs else 0
            for spec, urgs in urgenza_per_spec.items()
        }
        
        # PS deviation rate
        ps_keywords = ["pronto soccorso", "ps", "emergenza", "118"]
        territorial_keywords = ["cau", "guardia medica", "medico di base", "farmacia"]
        
        deviazione_ps = 0
        deviazione_territoriale = 0
        
        for r in records:
            bot_resp = str(r.get("bot_response", "")).lower()
            if any(kw in bot_resp for kw in ps_keywords):
                deviazione_ps += 1
            elif any(kw in bot_resp for kw in territorial_keywords):
                deviazione_territoriale += 1
        
        total_recommendations = deviazione_ps + deviazione_territoriale
        kpi["tasso_deviazione_ps"] = (
            (deviazione_ps / total_recommendations * 100) if total_recommendations > 0 else 0
        )
        kpi["tasso_deviazione_territoriale"] = (
            (deviazione_territoriale / total_recommendations * 100) if total_recommendations > 0 else 0
        )
        
        return kpi

    def _calc_geographic_kpi(self, records: List[Dict]) -> Dict[str, Any]:
        """
        Calcola KPI geografici: copertura distretti, distribuzione AUSL.

        Returns:
            Dict con copertura_geografica (distretti_attivi, distribuzione_distretti,
            distribuzione_ausl, comuni_unici, tasso_geo_matching)
        """
        distretti = Counter()
        ausls = Counter()
        comuni_set = set()
        matched = 0
        for r in records:
            dist = r.get("distretto", "UNKNOWN")
            ausl = r.get("ausl", "UNKNOWN")
            comune = r.get("comune_matched", "") or r.get("comune", "")
            if dist != "UNKNOWN":
                distretti[dist] += 1
            if ausl != "UNKNOWN":
                ausls[ausl] += 1
            if comune:
                comuni_set.add(comune)
            if r.get("geo_match_type") in ("exact", "fuzzy"):
                matched += 1
        return {
            "copertura_geografica": {
                "distretti_attivi": len(distretti),
                "distribuzione_distretti": dict(distretti.most_common(10)),
                "distribuzione_ausl": dict(ausls.most_common(10)),
                "comuni_unici": len(comuni_set),
                "tasso_geo_matching": (matched / len(records) * 100) if records else 0,
            }
        }

    # ========================================================================
    # KPI CALCULATIONS - COMPLETE (15 KPIs)
    # ========================================================================

    def calculate_kpi_completo(self, records: List[Dict] = None) -> Dict[str, Any]:
        """
        Calcola tutti i KPI del sistema SIRAYA.

        VERSIONE: 2.1 - Fixed metadata parsing

        Gestisce log multi-formato (v2.0, v3.0, v4.0) tramite metadata_parser.py
        e geographic_mapper.py.

        KPI Calcolati:
        - Volumetrici: sessioni, interazioni, completion rate, tempo mediano
        - Clinici: red flags, urgenza, sintomi, specializzazioni
        - Geografici: distretti, AUSL, copertura territoriale
        - Avanzati: accuracy, latency, sentiment, divergenza algoritmica

        Args:
            records: Optional pre-filtered records

        Returns:
            Dict con 15+ KPI strutturati

        Performance:
            - Con cache: ~10ms
            - Senza cache: ~500ms (331 record)
        """
        if records is None:
            records, sessions = self.get_enriched_records()
        else:
            sessions = defaultdict(list)
            for r in records:
                sid = r.get("session_id")
                if sid:
                    sessions[sid].append(r)
            sessions = dict(sessions)
        
        kpi = {}
        
        # 1. Clinical accuracy
        accurate_sessions = 0
        total_sessions_with_disposition = 0
        
        for sid, session_records in sessions.items():
            user_symptoms = []
            final_urgency = None
            
            for r in session_records:
                user_input = str(r.get("user_input", "")).lower()
                for symptom in ClinicalMappings.SINTOMI_COMUNI:
                    if symptom in user_input:
                        user_symptoms.append(symptom)
                
                final_urgency = r.get("urgenza", 3)
            
            if user_symptoms:
                total_sessions_with_disposition += 1
                has_red_flag = any(
                    rf in " ".join(user_symptoms) 
                    for rf in ClinicalMappings.RED_FLAGS_KEYWORDS
                )
                if (final_urgency >= 4 and has_red_flag) or (final_urgency <= 2 and not has_red_flag):
                    accurate_sessions += 1
        
        kpi["accuratezza_clinica"] = (
            (accurate_sessions / total_sessions_with_disposition * 100) 
            if total_sessions_with_disposition > 0 else 0
        )
        
        # 2. Average latency
        total_latency = 0
        latency_count = 0
        for sid, session_records in sessions.items():
            if len(session_records) >= 2:
                timestamps = [r.get("datetime") for r in session_records if r.get("datetime")]
                if len(timestamps) >= 2:
                    session_duration = (max(timestamps) - min(timestamps)).total_seconds()
                    total_latency += session_duration / len(session_records)
                    latency_count += 1
        
        kpi["latenza_media_secondi"] = total_latency / latency_count if latency_count > 0 else 0
        
        # 3. Completion rate
        completed = 0
        for sid, session_records in sessions.items():
            for r in session_records:
                bot_resp = str(r.get("bot_response", "")).lower()
                if "raccomand" in bot_resp or "disposition" in bot_resp:
                    completed += 1
                    break
        
        kpi["tasso_completamento"] = (completed / len(sessions) * 100) if sessions else 0
        
        # 4. Protocol adherence
        protocol_adherent = 0
        for sid, session_records in sessions.items():
            has_age = False
            has_location = False
            has_symptoms = False
            
            for r in session_records:
                user_input = str(r.get("user_input", "")).lower()
                if "età" in user_input or "anni" in user_input:
                    has_age = True
                if r.get("comune") or r.get("location") or r.get("comune_matched") or r.get("distretto", "UNKNOWN") != "UNKNOWN":
                    has_location = True
                if any(s in user_input for s in ClinicalMappings.SINTOMI_COMUNI):
                    has_symptoms = True
            
            if has_age and has_location and has_symptoms:
                protocol_adherent += 1
        
        kpi["aderenza_protocolli"] = (protocol_adherent / len(sessions) * 100) if sessions else 0
        
        # 5. User sentiment
        positive_keywords = ["grazie", "perfetto", "ottimo", "bene", "ok"]
        negative_keywords = ["male", "peggio", "preoccupato", "paura", "ansia"]
        urgent_keywords = ["subito", "immediato", "urgente", "emergenza", "ora"]
        
        sentiment_scores = []
        for r in records:
            user_input = str(r.get("user_input", "")).lower()
            score = 0
            if any(kw in user_input for kw in positive_keywords):
                score = 1
            elif any(kw in user_input for kw in negative_keywords):
                score = -1
            if any(kw in user_input for kw in urgent_keywords):
                score = -2
            sentiment_scores.append(score)
        
        kpi["sentiment_medio"] = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0
        
        # 6. Redirection efficiency
        non_urgent_to_territorial = 0
        non_urgent_to_ps = 0
        
        for r in records:
            urgency = r.get("urgenza", 3)
            if urgency <= 2:
                bot_resp = str(r.get("bot_response", "")).lower()
                if any(kw in bot_resp for kw in ["cau", "guardia medica", "medico di base"]):
                    non_urgent_to_territorial += 1
                elif any(kw in bot_resp for kw in ["pronto soccorso", "ps", "118"]):
                    non_urgent_to_ps += 1
        
        total_non_urgent = non_urgent_to_territorial + non_urgent_to_ps
        kpi["efficienza_reindirizzamento"] = (
            (non_urgent_to_territorial / total_non_urgent * 100) if total_non_urgent > 0 else 0
        )
        
        # 7-9. Basic stats
        kpi["sessioni_uniche"] = len(sessions)
        kpi["throughput_orario"] = dict(Counter(
            r.get("hour", 0) for r in records if r.get("hour") is not None
        ))
        
        session_durations = []
        for sid, session_records in sessions.items():
            if len(session_records) >= 2:
                timestamps = [r.get("datetime") for r in session_records if r.get("datetime")]
                if len(timestamps) >= 2:
                    duration = (max(timestamps) - min(timestamps)).total_seconds() / 60
                    if duration < 120:
                        session_durations.append(duration)
        
        if session_durations:
            session_durations.sort()
            kpi["tempo_mediano_triage_minuti"] = session_durations[len(session_durations) // 2]
        else:
            kpi["tempo_mediano_triage_minuti"] = 0
        
        # 10. Algorithmic divergence
        divergences = 0
        for r in records:
            ai_urgency = r.get("urgenza", 3)
            user_input = str(r.get("user_input", "")).lower()
            
            deterministic_urgency = 3
            if any(kw in user_input for kw in ClinicalMappings.RED_FLAGS_KEYWORDS):
                deterministic_urgency = 5
            elif any(kw in user_input for kw in ["dolore forte", "sangue", "svenimento"]):
                deterministic_urgency = 4
            elif any(kw in user_input for kw in ["lieve", "piccolo", "niente"]):
                deterministic_urgency = 2
            
            if abs(ai_urgency - deterministic_urgency) >= 2:
                divergences += 1
        
        kpi["tasso_divergenza_algoritmica"] = (divergences / len(records) * 100) if records else 0
        
        # 11. Red flag omission rate
        red_flags_mentioned = 0
        red_flags_captured = 0
        
        for r in records:
            user_input = str(r.get("user_input", "")).lower()
            mentioned_rf = [rf for rf in ClinicalMappings.RED_FLAGS_KEYWORDS if rf in user_input]
            if mentioned_rf:
                red_flags_mentioned += len(mentioned_rf)
                if r.get("has_red_flag", False):
                    red_flags_captured += len(mentioned_rf)
        
        kpi["tasso_omissione_red_flags"] = (
            ((red_flags_mentioned - red_flags_captured) / red_flags_mentioned * 100) 
            if red_flags_mentioned > 0 else 0
        )
        
        # 12. Funnel drop-off
        step_counts = defaultdict(int)
        for sid, session_records in sessions.items():
            if len(session_records) < 3:
                step_counts["early_abandon"] += 1
            else:
                step_counts["completed"] += 1
        
        kpi["funnel_dropoff"] = {
            "early_abandon": step_counts["early_abandon"],
            "completed": step_counts["completed"],
            "dropoff_rate": (step_counts["early_abandon"] / len(sessions) * 100) if sessions else 0
        }
        
        # 13. Hesitation index
        hesitation_times = []
        for sid, session_records in sessions.items():
            sorted_records = sorted(
                [r for r in session_records if r.get("datetime")],
                key=lambda x: x.get("datetime")
            )
            for i in range(len(sorted_records) - 1):
                if sorted_records[i].get("bot_response") and sorted_records[i + 1].get("user_input"):
                    time_diff = (
                        sorted_records[i + 1].get("datetime") - sorted_records[i].get("datetime")
                    ).total_seconds()
                    if 5 < time_diff < 300:
                        hesitation_times.append(time_diff)
        
        kpi["indice_esitazione_secondi"] = (
            sum(hesitation_times) / len(hesitation_times) if hesitation_times else 0
        )
        
        # 14. Fast track efficiency
        critical_durations = []
        standard_durations = []
        
        for sid, session_records in sessions.items():
            if len(session_records) >= 2:
                timestamps = [r.get("datetime") for r in session_records if r.get("datetime")]
                if len(timestamps) >= 2:
                    duration = (max(timestamps) - min(timestamps)).total_seconds() / 60
                    max_urgency = max(r.get("urgenza", 3) for r in session_records)
                    
                    if max_urgency >= 4:
                        critical_durations.append(duration)
                    else:
                        standard_durations.append(duration)
        
        avg_critical = sum(critical_durations) / len(critical_durations) if critical_durations else 0
        avg_standard = sum(standard_durations) / len(standard_durations) if standard_durations else 0
        
        kpi["fast_track_efficiency_ratio"] = avg_standard / avg_critical if avg_critical > 0 else 0

        # 15. Geographic coverage (delegato)
        geo_kpi = self._calc_geographic_kpi(records)
        kpi.update(geo_kpi)

        # Clinical KPIs (prevalenza red flags + dettaglio)
        red_flags_count = sum(1 for r in records if r.get("has_red_flag", False))
        kpi["prevalenza_red_flags"] = (red_flags_count / len(records) * 100) if records else 0
        all_red_flags = []
        for r in records:
            all_red_flags.extend(r.get("red_flags", []))
        kpi["red_flags_dettaglio"] = dict(Counter(all_red_flags))

        # Merge context-aware KPIs
        context_kpi = self.calculate_kpi_context_aware(records)
        kpi["tasso_deviazione_ps"] = context_kpi.get("tasso_deviazione_ps", 0)
        kpi["tasso_deviazione_territoriale"] = context_kpi.get("tasso_deviazione_territoriale", 0)
        kpi["urgenza_media_per_spec"] = context_kpi.get("urgenza_media_per_spec", {})

        return kpi
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def get_recent_critical_cases(self, hours: int = 1) -> List[Dict]:
        """
        Get critical cases from last N hours.
        
        Args:
            hours: Lookback period
            
        Returns:
            List of critical case records
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        records, _ = self.get_enriched_records()
        
        critical = []
        for record in records:
            dt = record.get("datetime")
            if dt and dt >= cutoff:
                urgency = record.get("urgenza", 3)
                if urgency >= 4:
                    critical.append({
                        "timestamp": dt,
                        "session_id": record.get("session_id"),
                        "urgency": urgency,
                        "user_input": str(record.get("user_input", ""))[:100],
                    })
        
        return sorted(critical, key=lambda x: x["timestamp"], reverse=True)
    
    def calculate_hourly_distribution(self, records: List[Dict] = None) -> Dict[int, int]:
        """
        Calculate hourly distribution of interactions.
        
        Args:
            records: Optional pre-filtered records
            
        Returns:
            Dict mapping hour (0-23) to count
        """
        if records is None:
            records, _ = self.get_enriched_records()
        
        hourly = Counter(r.get("hour", 0) for r in records if r.get("hour") is not None)
        return dict(hourly)
    
    def invalidate_cache(self) -> None:
        """Clear cached data for refresh."""
        self._records_cache = None
        self._sessions_cache = None


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_analytics_service: Optional[AnalyticsService] = None


def get_analytics_service() -> AnalyticsService:
    """Get singleton analytics service instance."""
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService()
    return _analytics_service
